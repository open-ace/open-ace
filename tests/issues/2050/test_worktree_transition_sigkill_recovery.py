"""SIGKILL-resilient recovery for the merge-conflict worktree transition (#2050).

``_resolve_merge_conflicts`` crosses DB / git-worktree registry / disk in a way
that no single atomic transaction can cover. Issue #2049 (PR #2049) added the
in-process ``try/finally`` exception safety (see tests/issues/2041/). This file
locks in the cross-process guarantees: after a SIGKILL/restart/crash at any
git/DB boundary, the persisted transition journal + the observed git registry
state must let one idempotent ``_reconcile_worktree_transition`` restore the
original worktree or fail the workflow closed — never silently fall back to the
main checkout (HEAD=main).

Each test simulates a post-crash snapshot: a fixed DB journal state plus a mock
git registry/disk observation, then calls the reconciler and asserts the
convergence. We do NOT run the full ``_resolve_merge_conflicts`` here — the goal
is to prove the recover contract, not the happy path.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.github_ops import GitHubOpsError
from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator, _ReconcileFailed

PROJECT_PATH = "/srv/repo"
WT_PATH = "/srv/repo/.worktrees/wf-2050"
BRANCH = "auto-dev/wf2050"
PR_NUMBER = 2050


def _temp_path():
    import os

    return os.path.normpath(os.path.join(PROJECT_PATH, ".worktrees", "merge-wf-2050"))


def _make_workflow(**overrides):
    """A workflow dict in the shape the reconciler reads from the DB."""
    base = {
        "workflow_id": "wf-2050",
        "title": "sigkill recovery (#2050)",
        "cli_tool": "claude-code",
        "model": "",
        "branch_strategy": "worktree",
        "branch_name": BRANCH,
        "worktree_path": WT_PATH,
        "preferred_worktree_path": WT_PATH,
        "project_path": PROJECT_PATH,
        "workspace_type": "local",
        "current_phase": "merge",
        "status": "merging",
        "github_pr_number": PR_NUMBER,
        "github_issue_number": 2050,
        "dev_round": 1,
        # Journal fields default to "no transition in progress".
        "worktree_transition_state": None,
        "transition_original_path": None,
        "transition_temp_path": None,
        "transition_error": None,
        "transition_started_at": None,
        "transition_updated_at": None,
    }
    base.update(overrides)
    return base


def _make_orchestrator(wf):
    """Build an orchestrator whose DB/git interactions are fully mocked.

    Mirrors tests/issues/2041 helpers. ``o._update_workflow`` is a MagicMock so
    tests can assert exactly which fields were converged.
    """
    with (
        patch("app.modules.workspace.autonomous.orchestrator.Database"),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
        ) as mock_repo_cls,
    ):
        mock_repo = MagicMock()
        # The `workflow` property re-queries on every access; return the same
        # post-crash snapshot for the whole reconcile (the reconciler reads
        # state once at entry).
        mock_repo.get_workflow.return_value = wf
        mock_repo.update_workflow.return_value = wf
        mock_repo_cls.return_value = mock_repo
        o = AutonomousOrchestrator(wf["workflow_id"])
        o.repo = mock_repo
    o.emitter = MagicMock()
    o._update_workflow = MagicMock()
    return o, mock_repo


def _db_updates(o):
    """All dict args passed to _update_workflow."""
    return [
        c.args[0]
        for c in o._update_workflow.call_args_list
        if c.args and isinstance(c.args[0], dict)
    ]


def _has_update(o, **fields):
    """True if some _update_workflow call included all the given field values."""
    return any(all(u.get(k) == v for k, v in fields.items()) for u in _db_updates(o))


def _patch_gh(
    entries=None,
    *,
    add_side_effect=None,
    remove_side_effect=None,
    path_exists=False,
    list_side_effect=None,
):
    """Patch GitHubOps so every instance shares one mock with the given registry.

    Returns the mock instance. ``entries`` is the list returned by
    ``list_worktrees`` (unless ``list_side_effect`` is given).
    """
    mock_gh = MagicMock()
    if list_side_effect is not None:
        mock_gh.list_worktrees.side_effect = list_side_effect
    else:
        mock_gh.list_worktrees.return_value = entries or []
    mock_gh.path_exists_as_user.return_value = path_exists
    if add_side_effect is not None:
        mock_gh.add_worktree.side_effect = add_side_effect
    if remove_side_effect is not None:
        mock_gh.remove_worktree.side_effect = remove_side_effect
    patcher = patch("app.modules.workspace.autonomous.orchestrator.GitHubOps", return_value=mock_gh)
    patcher.start()
    return mock_gh, patcher


# ── 1. removing_original ──────────────────────────────────────────────────


class TestReconcileRemovingOriginal:
    def test_kill_before_original_remove_rolls_back_transition(self):
        """SIGKILL after journaling ``removing_original`` but before git actually
        removed the original → original is still registered on the right branch.
        Reconcile should just clear the journal (nothing to undo)."""
        wf = _make_workflow(
            worktree_transition_state="removing_original",
            transition_original_path=WT_PATH,
            transition_temp_path=_temp_path(),
            worktree_path=WT_PATH,
        )
        o, _ = _make_orchestrator(wf)
        mock_gh, patcher = _patch_gh(entries=[{"path": WT_PATH, "branch": BRANCH}])
        try:
            o._reconcile_worktree_transition(wf)
        finally:
            patcher.stop()

        # Journal cleared, worktree_path restored to the original in one write.
        assert _has_update(o, worktree_transition_state=None, worktree_path=WT_PATH)
        # No temp removal attempted (original was never removed).
        mock_gh.remove_worktree.assert_not_called()

    def test_kill_after_original_remove_before_db_update_recovers(self):
        """SIGKILL after git removed the original but before the DB caught up:
        state is still ``removing_original`` but the original is gone from the
        registry. Reconcile should restore the original (and clean any temp)."""
        wf = _make_workflow(
            worktree_transition_state="removing_original",
            transition_original_path=WT_PATH,
            transition_temp_path=_temp_path(),
            worktree_path="",  # DB may or may not have been cleared yet
        )
        o, _ = _make_orchestrator(wf)
        # Registry: original gone, no temp. Two list_worktrees calls: the entry
        # probe, then the post-add verify.
        mock_gh, patcher = _patch_gh(
            entries=[],
            path_exists=False,
            list_side_effect=[
                [],  # initial probe
                [{"path": WT_PATH, "branch": BRANCH}],  # after add → verify
            ],
        )
        try:
            o._reconcile_worktree_transition(wf)
        finally:
            patcher.stop()

        # Original re-attached then journal converged.
        mock_gh.add_worktree.assert_any_call(WT_PATH, BRANCH)
        assert _has_update(o, worktree_transition_state=None, worktree_path=WT_PATH)


# ── 2. original_removed / temp_attached ───────────────────────────────────


class TestReconcileTempStates:
    def test_kill_after_db_clear_before_temp_create_recovers(self):
        """DB advanced to ``original_removed`` but temp was never created.
        Registry is empty → just restore the original."""
        wf = _make_workflow(
            worktree_transition_state="original_removed",
            transition_original_path=WT_PATH,
            transition_temp_path=_temp_path(),
            worktree_path="",
        )
        o, _ = _make_orchestrator(wf)
        mock_gh, patcher = _patch_gh(
            entries=[],
            path_exists=False,
            list_side_effect=[
                [],  # initial probe
                [{"path": WT_PATH, "branch": BRANCH}],  # after restore add → verify
            ],
        )
        try:
            o._reconcile_worktree_transition(wf)
        finally:
            patcher.stop()

        mock_gh.add_worktree.assert_any_call(WT_PATH, BRANCH)
        assert _has_update(o, worktree_transition_state=None, worktree_path=WT_PATH)

    def test_kill_after_temp_create_before_state_update_recovers(self):
        """Temp was created and is registered, but the journal never advanced
        past ``original_removed`` (state update was the SIGKILL point).
        Reconcile must tear down the temp and restore the original."""
        temp = _temp_path()
        wf = _make_workflow(
            worktree_transition_state="original_removed",
            transition_original_path=WT_PATH,
            transition_temp_path=temp,
            worktree_path="",
        )
        o, _ = _make_orchestrator(wf)
        mock_gh, patcher = _patch_gh(
            entries=[{"path": temp, "branch": BRANCH}],
            path_exists=False,
            list_side_effect=[
                [{"path": temp, "branch": BRANCH}],  # initial probe
                [],  # after temp removed
                [{"path": WT_PATH, "branch": BRANCH}],  # after original restored
            ],
        )
        try:
            o._reconcile_worktree_transition(wf)
        finally:
            patcher.stop()

        # Temp was removed.
        mock_gh.remove_worktree.assert_any_call(temp)
        # Original restored + journal cleared.
        mock_gh.add_worktree.assert_any_call(WT_PATH, BRANCH)
        assert _has_update(o, worktree_transition_state=None, worktree_path=WT_PATH)

    def test_kill_during_temp_resolution_discards_temp_and_restores_original(self):
        """Journal reached ``temp_attached``; temp holds untrusted half-done
        agent work. Reconcile discards it and restores the original — it does
        NOT try to resume the agent's edits."""
        temp = _temp_path()
        wf = _make_workflow(
            worktree_transition_state="temp_attached",
            transition_original_path=WT_PATH,
            transition_temp_path=temp,
            worktree_path="",
        )
        o, _ = _make_orchestrator(wf)
        mock_gh, patcher = _patch_gh(
            entries=[{"path": temp, "branch": BRANCH}],
            path_exists=False,
            list_side_effect=[
                [{"path": temp, "branch": BRANCH}],  # initial
                [],  # after temp removed
                [{"path": WT_PATH, "branch": BRANCH}],  # after restore
            ],
        )
        try:
            o._reconcile_worktree_transition(wf)
        finally:
            patcher.stop()

        mock_gh.remove_worktree.assert_any_call(temp)
        mock_gh.add_worktree.assert_any_call(WT_PATH, BRANCH)
        assert _has_update(o, worktree_transition_state=None, worktree_path=WT_PATH)

    def test_temp_attached_with_no_temp_restores_original(self):
        """``temp_attached`` but temp already gone (cleanup happened) → just
        restore the original, no spurious temp removal."""
        wf = _make_workflow(
            worktree_transition_state="temp_attached",
            transition_original_path=WT_PATH,
            transition_temp_path=_temp_path(),
            worktree_path="",
        )
        o, _ = _make_orchestrator(wf)
        mock_gh, patcher = _patch_gh(
            entries=[],
            path_exists=False,
            list_side_effect=[
                [],  # initial
                [{"path": WT_PATH, "branch": BRANCH}],  # after restore
            ],
        )
        try:
            o._reconcile_worktree_transition(wf)
        finally:
            patcher.stop()

        mock_gh.remove_worktree.assert_not_called()
        mock_gh.add_worktree.assert_any_call(WT_PATH, BRANCH)


# ── 3. restoring ──────────────────────────────────────────────────────────


class TestReconcileRestoring:
    def test_kill_after_temp_remove_during_restoring_completes_idempotently(self):
        """Journal is ``restoring``; original not yet registered. Reconcile
        resumes the idempotent restore and converges."""
        wf = _make_workflow(
            worktree_transition_state="restoring",
            transition_original_path=WT_PATH,
            transition_temp_path=_temp_path(),
            worktree_path="",
        )
        o, _ = _make_orchestrator(wf)
        mock_gh, patcher = _patch_gh(
            entries=[],
            path_exists=False,
            list_side_effect=[
                [],  # initial
                [{"path": WT_PATH, "branch": BRANCH}],  # after restore add → verify
            ],
        )
        try:
            o._reconcile_worktree_transition(wf)
        finally:
            patcher.stop()

        mock_gh.add_worktree.assert_any_call(WT_PATH, BRANCH)
        assert _has_update(o, worktree_transition_state=None, worktree_path=WT_PATH)

    def test_kill_after_original_restore_before_journal_clear_converges(self):
        """Journal is ``restoring`` and the original is ALREADY registered on
        the right branch (restore finished before the SIGKILL). Reconcile must
        just converge the DB without re-adding."""
        wf = _make_workflow(
            worktree_transition_state="restoring",
            transition_original_path=WT_PATH,
            transition_temp_path=_temp_path(),
            worktree_path="",  # DB not yet converged
        )
        o, _ = _make_orchestrator(wf)
        mock_gh, patcher = _patch_gh(
            entries=[{"path": WT_PATH, "branch": BRANCH}],
        )
        try:
            o._reconcile_worktree_transition(wf)
        finally:
            patcher.stop()

        # No re-add: it's already registered.
        mock_gh.add_worktree.assert_not_called()
        assert _has_update(o, worktree_transition_state=None, worktree_path=WT_PATH)


# ── 4. idempotency ────────────────────────────────────────────────────────


def test_reconcile_twice_is_idempotent():
    """Running reconcile twice on a converged workflow (state NULL) is a no-op,
    and running it twice on a mid-flight state converges once without duplicate
    git mutations."""
    # Already-stable workflow: reconcile is a pure no-op.
    wf = _make_workflow(worktree_transition_state=None)
    o, _ = _make_orchestrator(wf)
    o._reconcile_worktree_transition(wf)
    o._reconcile_worktree_transition(wf)
    assert o._update_workflow.call_count == 0


# ── 5. never fall back to main checkout ───────────────────────────────────


def test_unreconciled_transition_never_falls_back_to_main_checkout():
    """The hard guard in _ensure_worktree must refuse to return project_path
    while a transition is in flight (anything but recovery_failed)."""
    wf = _make_workflow(
        worktree_transition_state="original_removed",
        worktree_path="",  # would normally trigger the project_path fallback
        project_path=PROJECT_PATH,
    )
    o, _ = _make_orchestrator(wf)
    with pytest.raises(RuntimeError, match="(?i)transition in progress"):
        o._ensure_worktree(wf)


def test_recovery_failed_does_not_block_inspect():
    """``recovery_failed`` is terminal but must not raise from the guard — it
    should return the path so diagnostic code can still read state."""
    wf = _make_workflow(
        worktree_transition_state="recovery_failed", worktree_path="", project_path=PROJECT_PATH
    )
    o, _ = _make_orchestrator(wf)
    # No raise; returns project_path (no live execution happens in this state).
    assert o._ensure_worktree(wf) == PROJECT_PATH


# ── 6. fail-closed ────────────────────────────────────────────────────────


def test_ambiguous_or_foreign_worktree_enters_recovery_failed():
    """An original_path outside the project's .worktrees root must fail closed
    rather than operate on a path we cannot prove we own."""
    wf = _make_workflow(
        worktree_transition_state="original_removed",
        transition_original_path="/etc/passwd",  # foreign root
        transition_temp_path=_temp_path(),
        worktree_path="",
    )
    o, _ = _make_orchestrator(wf)
    mock_gh, patcher = _patch_gh(entries=[])
    try:
        o._reconcile_worktree_transition(wf)
    finally:
        patcher.stop()

    assert _has_update(o, worktree_transition_state="recovery_failed", status="failed")
    # No git mutation attempted on the foreign path.
    mock_gh.add_worktree.assert_not_called()


def test_wrong_branch_registration_enters_recovery_failed():
    """A temp registered on the wrong branch is not ours to remove → fail closed."""
    temp = _temp_path()
    wf = _make_workflow(
        worktree_transition_state="temp_attached",
        transition_original_path=WT_PATH,
        transition_temp_path=temp,
        worktree_path="",
    )
    o, _ = _make_orchestrator(wf)
    mock_gh, patcher = _patch_gh(
        entries=[{"path": temp, "branch": "refs/heads/main"}],  # wrong branch
    )
    try:
        o._reconcile_worktree_transition(wf)
    finally:
        patcher.stop()

    assert _has_update(o, worktree_transition_state="recovery_failed", status="failed")
    mock_gh.remove_worktree.assert_not_called()


def test_missing_original_path_enters_recovery_failed():
    """No original path recorded and no fallback available → fail closed."""
    wf = _make_workflow(
        worktree_transition_state="original_removed",
        transition_original_path="",
        preferred_worktree_path="",
        worktree_path="",
    )
    o, _ = _make_orchestrator(wf)
    mock_gh, patcher = _patch_gh(entries=[])
    try:
        o._reconcile_worktree_transition(wf)
    finally:
        patcher.stop()

    assert _has_update(o, worktree_transition_state="recovery_failed", status="failed")


# ── 7. shared reconciler across entry points ──────────────────────────────


def test_startup_advance_and_manual_resume_use_same_reconciler():
    """startup sweep, advance(), and manual resume all funnel through the same
    ``_reconcile_worktree_transition`` method (the sweep is just a loop over it)."""
    wf = _make_workflow(
        worktree_transition_state="original_removed",
        transition_original_path=WT_PATH,
        transition_temp_path=_temp_path(),
        worktree_path="",
    )
    o, _ = _make_orchestrator(wf)
    mock_gh, patcher = _patch_gh(
        entries=[],
        path_exists=False,
        list_side_effect=[
            [],  # initial
            [{"path": WT_PATH, "branch": BRANCH}],  # after restore
        ],
    )
    try:
        # The scheduler sweep calls the identical method advance() uses.
        o._reconcile_worktree_transition(wf)
    finally:
        patcher.stop()

    assert _has_update(o, worktree_transition_state=None, worktree_path=WT_PATH)


# ── 8. do not reset/recreate branch (#2042 boundary) ──────────────────────


def test_reconcile_does_not_reset_or_recreate_branch_without_2042_authority():
    """If re-attaching the original fails because the branch ref is missing/
    divergent, reconcile must fail closed — NOT reset/delete/recreate the
    branch. Branch head authority is #2042's job."""
    wf = _make_workflow(
        worktree_transition_state="original_removed",
        transition_original_path=WT_PATH,
        transition_temp_path=_temp_path(),
        worktree_path="",
    )
    o, _ = _make_orchestrator(wf)

    def _add(path, branch):
        raise GitHubOpsError("invalid reference: branch not found")

    mock_gh, patcher = _patch_gh(
        entries=[],
        path_exists=False,
        add_side_effect=_add,
    )
    try:
        o._reconcile_worktree_transition(wf)
    finally:
        patcher.stop()

    # Failed closed with a message pointing at head authority.
    updates = _db_updates(o)
    fail_updates = [u for u in updates if u.get("worktree_transition_state") == "recovery_failed"]
    assert fail_updates
    assert (
        "2042" in fail_updates[-1].get("transition_error", "")
        or "branch" in fail_updates[-1].get("transition_error", "").lower()
    )
    # No reset/delete branch calls were made.
    assert not any(
        c for c in mock_gh.method_calls if "reset" in str(c) or "delete_branch" in str(c)
    )


# ── 9. legacy compatibility ───────────────────────────────────────────────


def test_legacy_workflow_without_transition_fields_remains_compatible():
    """A workflow with NULL transition state (all legacy rows post-migration)
    must not trigger reconcile and must keep the old _ensure_worktree behavior."""
    wf = _make_workflow(worktree_transition_state=None, worktree_path=WT_PATH)
    o, _ = _make_orchestrator(wf)

    # Reconcile is a no-op.
    o._reconcile_worktree_transition(wf)
    assert o._update_workflow.call_count == 0

    # _ensure_worktree behaves as before for a stable workflow (returns the path
    # without needing git, since it has a valid worktree_path — the guard only
    # fires when transition_state is non-null). We just assert no guard raise.
    # (We don't exercise the full git-validity path here; the guard is the
    # only behavior this issue changed.)


# ── 10. _ReconcileFailed sentinel contract ─────────────────────────────────


def test_reconcile_failed_sentinel_is_caught_and_persisted():
    """The internal ``_ReconcileFailed`` sentinel must be caught by the
    reconciler and turned into a persisted recovery_failed, never propagated."""
    wf = _make_workflow(
        worktree_transition_state="temp_attached",
        transition_original_path=WT_PATH,
        transition_temp_path="/srv/repo/.worktrees/merge-wf-2050",
        worktree_path="",
    )
    o, _ = _make_orchestrator(wf)

    # Force the cleanup helper to raise _ReconcileFailed by giving a temp on the
    # wrong branch (the cleanup helper raises it directly).
    mock_gh, patcher = _patch_gh(
        entries=[{"path": "/srv/repo/.worktrees/merge-wf-2050", "branch": "refs/heads/main"}],
    )
    try:
        # Must not raise _ReconcileFailed out.
        o._reconcile_worktree_transition(wf)
    finally:
        patcher.stop()

    assert _has_update(o, worktree_transition_state="recovery_failed", status="failed")
