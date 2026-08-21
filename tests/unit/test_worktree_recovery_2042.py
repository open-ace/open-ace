"""Contract tests for authoritative-commit worktree recovery (Issue #2042).

These cover the seven acceptance tests listed in the issue:

* missing worktree + missing branch recovers from base, not latest main
* existing PR recovers from the verified PR head
* a surviving local branch ahead of the verified head does NOT override it
* local/remote divergence returns indeterminate and pauses (fail closed)
* expected_head_sha is updated after a trusted commit
* missing recovery evidence fails closed (no guess, no origin/main fallback)
* the recovery milestone records the verified evidence trail

Tests follow ``test_autonomous_ci_guardrails.py``: ``AutonomousOrchestrator.__new__``
to skip ``__init__``, ``MagicMock`` for GitHubOps, method-level stubs.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.evidence import Evidence, Verdict


def _make_orch():
    """Build a minimal AutonomousOrchestrator via __new__ (skip __init__)."""
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._workflow_id = "wf-2042"
    orch.repo = MagicMock()
    orch.emitter = MagicMock()
    orch._create_milestone = MagicMock(return_value={"milestone_id": "ms-1"})
    orch._update_workflow = MagicMock()
    orch._gh = None
    return orch


def _set_evidence(orch, mock_svc):
    """Inject a mock EvidenceService (``_evidence`` is a read-only property)."""
    orch.__dict__["_evidence_instance"] = mock_svc


def _mock_gh_with_recovery(head_sha, verdict=Verdict.CONFIRMED):
    """Mock EvidenceService.resolve_verified_pr_head / verify_commit_available.

    Returns a CONFIRMED Evidence with commit_shas=(head_sha,) by default, so
    _resolve_recovery_head yields the given head. Override ``verdict`` to
    simulate indeterminate/rejected.
    """
    evidence = Evidence(
        source="github_api",
        subject="pr_head",
        verdict=verdict,
        observed_at=__import__("datetime").datetime(2026, 7, 26),
        verified_at=__import__("datetime").datetime(2026, 7, 26),
        verification_method="test",
        commit_shas=(head_sha,) if head_sha else (),
        reason="test",
    )
    mock_svc = MagicMock()
    mock_svc.resolve_verified_pr_head.return_value = evidence
    mock_svc.verify_commit_available.return_value = evidence
    return mock_svc


# ── _resolve_recovery_head decision tree ──────────────────────────────────


def test_existing_pr_recovers_from_verified_pr_head():
    """Decision 1: existing PR → resolve_verified_pr_head is the authority."""
    orch = _make_orch()
    _set_evidence(orch, _mock_gh_with_recovery("pr-head-sha"))
    wf = {"branch_name": "auto-dev/x", "github_pr_number": 42}
    head, decision, meta = orch._resolve_recovery_head(MagicMock(), wf)
    assert head == "pr-head-sha"
    assert decision == "verified_pr_head"
    orch._evidence.resolve_verified_pr_head.assert_called_once()


def test_missing_worktree_and_branch_recovers_from_base_not_latest_main():
    """Decision 3: no PR, no expected_head, has base_commit_sha → recover from base.

    The recovery must NOT silently use origin/main.
    """
    orch = _make_orch()
    _set_evidence(orch, _mock_gh_with_recovery("base-sha-abc"))
    wf = {"branch_name": "auto-dev/x", "base_commit_sha": "base-sha-abc"}
    # No github_pr_number, no expected_head_sha.
    head, decision, meta = orch._resolve_recovery_head(MagicMock(), wf)
    assert head == "base-sha-abc"
    assert decision == "base_commit"


def test_local_remote_divergence_returns_indeterminate_and_pauses():
    """When the verified signal is INDETERMINATE → fail closed (None, pause)."""
    orch = _make_orch()
    _set_evidence(orch, _mock_gh_with_recovery(None, verdict=Verdict.INDETERMINATE))
    wf = {"branch_name": "auto-dev/x", "github_pr_number": 42}
    head, decision, meta = orch._resolve_recovery_head(MagicMock(), wf)
    assert head is None
    assert decision == "indeterminate_pause"


def test_recovery_evidence_missing_fails_closed():
    """No PR, no expected_head, no base → None + indeterminate_pause (no guess)."""
    orch = _make_orch()
    _set_evidence(orch, MagicMock())
    head, decision, meta = orch._resolve_recovery_head(MagicMock(), {"branch_name": "x"})
    assert head is None
    assert decision == "indeterminate_pause"
    assert "no PR" in meta["reason"]


# ── _record_trusted_head ──────────────────────────────────────────────────


def test_expected_head_updates_after_trusted_commit():
    """A trusted commit persists expected_head_sha via _update_workflow."""
    orch = _make_orch()
    gh = MagicMock()
    gh.get_current_commit.return_value = "new-trusted-head"
    recorded = orch._record_trusted_head(gh)
    assert recorded == "new-trusted-head"
    orch._update_workflow.assert_called_once_with({"expected_head_sha": "new-trusted-head"})


def test_record_trusted_head_uses_provided_sha_without_rereading():
    """Passing sha= avoids a redundant get_current_commit call."""
    orch = _make_orch()
    gh = MagicMock()
    recorded = orch._record_trusted_head(gh, sha="provided-sha")
    assert recorded == "provided-sha"
    gh.get_current_commit.assert_not_called()
    orch._update_workflow.assert_called_once_with({"expected_head_sha": "provided-sha"})


# ── _ensure_worktree recovery branch (integration of decision + milestone) ──


def test_ensure_worktree_missing_dir_recovers_from_verified_head_not_main():
    """Missing worktree + missing branch → recovery head is the verified head.

    The recovery decision (_resolve_recovery_head) must yield the verified
    head_sha as the rebuild base, never origin/main. This test focuses on the
    decision contract that _ensure_worktree consumes, rather than the full
    filesystem-heavy _ensure_worktree path (which needs a real git repo).
    """
    orch = _make_orch()
    _set_evidence(orch, _mock_gh_with_recovery("verified-head-abc"))
    wf = {"branch_name": "auto-dev/x", "github_pr_number": 42}
    head, decision, meta = orch._resolve_recovery_head(MagicMock(), wf)
    # The rebuild base is the verified head, never origin/main.
    assert head == "verified-head-abc"
    assert head != "origin/main"
    assert decision == "verified_pr_head"


def test_recovery_milestone_records_verified_evidence():
    """The recovery milestone metadata carries the decision, head SHA, evidence.

    Uses _fail_recovery_closed directly (the fail-closed milestone path) to
    verify the audit trail is recorded.
    """
    orch = _make_orch()
    evidence_meta = {"decision": "indeterminate_pause", "evidence": {"verdict": "indeterminate"}}
    orch._fail_recovery_closed(
        wf={"current_phase": "development"},
        canonical="/tmp/wt",
        decision="indeterminate_pause",
        evidence_meta=evidence_meta,
        local_sha="local-abc",
        expected_sha="expected-def",
    )
    # Workflow marked failed.
    update_calls = [c.args[0] for c in orch._update_workflow.call_args_list]
    assert {"status": "failed"} in update_calls
    # Milestone records the evidence trail.
    orch._create_milestone.assert_called_once()
    ms_kwargs = orch._create_milestone.call_args.kwargs
    assert ms_kwargs["milestone_type"] == "recovery_evidence_missing"
    assert ms_kwargs["status"] == "failed"
    meta = json.loads(ms_kwargs["metadata"])
    assert meta["decision"] == "indeterminate_pause"
    assert meta["local_sha"] == "local-abc"
    assert meta["expected_sha"] == "expected-def"
    assert meta["evidence"] == evidence_meta


def _make_evidence(verdict, head_sha):
    """Build a real Evidence object for the CONFIRMED/INDETERMINATE cases."""
    import datetime

    now = datetime.datetime(2026, 7, 26)
    return Evidence(
        source="github_api",
        subject="pr_head",
        verdict=verdict,
        observed_at=now,
        verified_at=now,
        verification_method="test",
        commit_shas=(head_sha,) if head_sha else (),
        reason="test",
    )


def test_ensure_worktree_1999_guard_fails_closed_on_divergence():
    """A surviving local branch diverging from the verified head fails closed.

    Real _ensure_worktree integration: branch survives → attach → #1999 guard
    reads the attached HEAD and compares to the verified PR head. A mismatch
    (unpushed local commits) must raise, not silently continue.
    """
    orch = _make_orch()
    # verified PR head is CONFIRMED.
    _set_evidence(
        orch,
        MagicMock(
            resolve_verified_pr_head=MagicMock(
                return_value=_make_evidence(Verdict.CONFIRMED, "verified-head-abc")
            ),
            verify_commit_available=MagicMock(
                return_value=_make_evidence(Verdict.CONFIRMED, "verified-head-abc")
            ),
        ),
    )
    wf = {
        "workflow_id": "wf-2042",
        "branch_name": "auto-dev/x",
        "github_pr_number": 42,
        "worktree_path": "/private/tmp/repo/.worktrees/wf-2042",
        "project_path": "/private/tmp/repo",
        "branch_strategy": "worktree",
        "current_phase": "merge",
        "user_id": None,
    }
    main_gh = MagicMock()
    main_gh.path_exists_as_user.return_value = False  # worktree missing
    found = MagicMock(returncode=0)  # branch survives locally
    main_gh._run_git.side_effect = [
        MagicMock(),  # fetch origin main
        found,  # local branch exists
        found,  # remote branch exists
        MagicMock(),  # worktree add <canonical> <branch>
    ]
    # GitHubOps(canonical) for the #1999 guard returns a divergent local head.
    wt_gh = MagicMock()
    wt_gh.get_current_commit.return_value = "stale-local-unpushed"

    def fake_gh_ctor(path, **kw):
        return wt_gh if path.endswith("wf-2042") else main_gh

    # Use a MagicMock so classmethods called by _refresh_trusted_git_context
    # (clear/register_trusted_git_context) resolve to mock attrs instead of
    # raising AttributeError on a plain function. side_effect preserves the
    # path-conditional dispatch used by the test. (#2565)
    fake_gh_cls = MagicMock(side_effect=fake_gh_ctor)
    with (
        patch("app.modules.workspace.autonomous.orchestrator.GitHubOps", fake_gh_cls),
        patch("os.path.realpath", side_effect=lambda p: p),
    ):
        with pytest.raises(RuntimeError, match="diverges from verified head"):
            orch._ensure_worktree(wf)
    # Fail-closed milestone was recorded with the divergence detail.
    ms_kwargs = orch._create_milestone.call_args.kwargs
    assert ms_kwargs["milestone_type"] == "recovery_evidence_missing"
    meta = json.loads(ms_kwargs["metadata"])
    assert meta["local_sha"] == "stale-local-unpushed"
    assert meta["expected_sha"] == "verified-head-abc"


def test_ensure_worktree_never_uses_origin_main_for_missing_branch():
    """Branch + worktree both gone → rebuild from base_commit_sha, NOT origin/main.

    Real _ensure_worktree integration: asserts the `worktree add -b` command's
    last arg is the verified head_sha, never `origin/main`.
    """
    orch = _make_orch()
    _set_evidence(
        orch,
        MagicMock(
            resolve_verified_pr_head=MagicMock(
                return_value=_make_evidence(Verdict.CONFIRMED, "base-sha-verified")
            ),
            verify_commit_available=MagicMock(
                return_value=_make_evidence(Verdict.CONFIRMED, "base-sha-verified")
            ),
        ),
    )
    wf = {
        "workflow_id": "wf-2042",
        "branch_name": "auto-dev/x",
        "base_commit_sha": "base-sha-verified",
        "worktree_path": "/private/tmp/repo/.worktrees/wf-2042",
        "project_path": "/private/tmp/repo",
        "branch_strategy": "worktree",
        "current_phase": "preparation",
        "user_id": None,
    }
    main_gh = MagicMock()
    main_gh.path_exists_as_user.return_value = False  # worktree missing
    not_found = MagicMock(returncode=1)  # branch gone locally + remotely
    main_gh._run_git.side_effect = [
        MagicMock(),  # fetch origin main
        not_found,  # local branch missing
        not_found,  # remote branch missing
        MagicMock(),  # worktree add -b <branch> <path> <head>
    ]

    # MagicMock so _refresh_trusted_git_context's classmethods resolve (#2565);
    # side_effect preserves the original "always return main_gh" dispatch.
    fake_gh_cls = MagicMock(side_effect=lambda _p, **_kw: main_gh)
    with (
        patch("app.modules.workspace.autonomous.orchestrator.GitHubOps", fake_gh_cls),
        patch("os.path.realpath", side_effect=lambda p: p),
    ):
        result = orch._ensure_worktree(wf)

    assert result.endswith("wf-2042")
    # The worktree add -b command must use the verified head, never origin/main.
    add_calls = [
        c.args[0]
        for c in main_gh._run_git.call_args_list
        if c.args and c.args[0] and c.args[0][0:2] == ["worktree", "add"]
    ]
    assert add_calls, "expected a worktree add call"
    create_call = [c for c in add_calls if "-b" in c][0]
    assert create_call[-1] == "base-sha-verified"
    assert "origin/main" not in create_call


# ── post-merge-cleanup resume recreates the workspace (#322/#329/#340) ──────


def _cleared_wf(**overrides) -> dict:
    """A worktree-strategy workflow resumed AFTER merge cleanup: the merged
    delivery's cleanup cleared worktree_path/branch_name (the workflow was
    "done"), and a rejected-acceptance / new-requirements resume brought it
    back into a workspace-consuming phase."""
    wf = {
        "workflow_id": "wf-2042",
        "branch_strategy": "worktree",
        "project_path": "/private/tmp/repo",
        "worktree_path": "",
        "branch_name": "",
        "current_phase": "development",
        "github_pr_number": 42,
        "user_id": None,
        "preferred_worktree_path": "/private/tmp/repo/.worktrees/wf-2042",
    }
    wf.update(overrides)
    return wf


@pytest.mark.parametrize("phase", ["development", "pr_review"])
def test_ensure_worktree_recreates_cleared_workspace(phase):
    """Empty worktree_path in a workspace-consuming phase recreates the
    worktree (NOT a no-op returning the main checkout): `worktree add -b` at
    the preferred path from the verified PR head, fields written back, and a
    worktree_restored milestone recorded."""
    orch = _make_orch()
    _set_evidence(
        orch,
        MagicMock(
            resolve_verified_pr_head=MagicMock(
                return_value=_make_evidence(Verdict.CONFIRMED, "pr-head-sha")
            ),
            verify_commit_available=MagicMock(
                return_value=_make_evidence(Verdict.CONFIRMED, "pr-head-sha")
            ),
        ),
    )
    wf = _cleared_wf(current_phase=phase)
    canonical = "/private/tmp/repo/.worktrees/wf-2042"
    main_gh = MagicMock()
    main_gh.path_exists_as_user.return_value = False
    not_found = MagicMock(returncode=1)  # branch gone locally + remotely
    main_gh._run_git.side_effect = [
        MagicMock(),  # fetch origin main
        not_found,  # local branch missing
        not_found,  # remote branch missing
        MagicMock(),  # worktree add -b <branch> <path> <head>
    ]
    fake_gh_cls = MagicMock(side_effect=lambda _p, **_kw: main_gh)
    with (
        patch("app.modules.workspace.autonomous.orchestrator.GitHubOps", fake_gh_cls),
        patch("os.path.realpath", side_effect=lambda p: p),
    ):
        result = orch._ensure_worktree(wf)

    assert result == canonical
    add_calls = [
        c.args[0]
        for c in main_gh._run_git.call_args_list
        if c.args and c.args[0] and c.args[0][0:2] == ["worktree", "add"]
    ]
    assert add_calls, "expected a worktree add call"
    create_call = [c for c in add_calls if "-b" in c][0]
    # branch_name was cleared by cleanup → re-derived from workflow_id
    assert create_call == ["worktree", "add", "-b", "auto-dev/wf-2042", canonical, "pr-head-sha"]
    orch._update_workflow.assert_called_once_with(
        {"worktree_path": canonical, "branch_name": "auto-dev/wf-2042"}
    )
    ms_kwargs = orch._create_milestone.call_args.kwargs
    assert ms_kwargs["milestone_type"] == "worktree_restored"
    assert ms_kwargs["status"] == "completed"


def test_ensure_worktree_recreates_cleared_workspace_derives_path_without_preferred():
    """No surviving preferred_worktree_path → the preferred-path helper still
    derives the conventional {project_path}/.worktrees/{workflow_id} spot."""
    orch = _make_orch()
    _set_evidence(
        orch,
        MagicMock(
            resolve_verified_pr_head=MagicMock(
                return_value=_make_evidence(Verdict.CONFIRMED, "pr-head-sha")
            ),
            verify_commit_available=MagicMock(
                return_value=_make_evidence(Verdict.CONFIRMED, "pr-head-sha")
            ),
        ),
    )
    wf = _cleared_wf(preferred_worktree_path="")
    canonical = "/private/tmp/repo/.worktrees/wf-2042"
    main_gh = MagicMock()
    main_gh.path_exists_as_user.return_value = False
    not_found = MagicMock(returncode=1)
    main_gh._run_git.side_effect = [
        MagicMock(),  # fetch origin main
        not_found,  # local branch missing
        not_found,  # remote branch missing
        MagicMock(),  # worktree add -b
    ]
    fake_gh_cls = MagicMock(side_effect=lambda _p, **_kw: main_gh)
    with (
        patch("app.modules.workspace.autonomous.orchestrator.GitHubOps", fake_gh_cls),
        patch("os.path.realpath", side_effect=lambda p: p),
    ):
        result = orch._ensure_worktree(wf)

    assert result == canonical
    orch._update_workflow.assert_called_once_with(
        {"worktree_path": canonical, "branch_name": "auto-dev/wf-2042"}
    )


def test_ensure_worktree_attaches_surviving_branch_when_worktree_cleared():
    """Cleanup partial failure (worktree cleared, branch survives) → attach
    the surviving branch (`worktree add <path> <branch>`, NOT -b); the #1999
    guard verifies the attached HEAD matches the verified head."""
    orch = _make_orch()
    _set_evidence(
        orch,
        MagicMock(
            resolve_verified_pr_head=MagicMock(
                return_value=_make_evidence(Verdict.CONFIRMED, "verified-head-abc")
            ),
            verify_commit_available=MagicMock(
                return_value=_make_evidence(Verdict.CONFIRMED, "verified-head-abc")
            ),
        ),
    )
    wf = _cleared_wf(branch_name="auto-dev/survivor")
    canonical = "/private/tmp/repo/.worktrees/wf-2042"
    main_gh = MagicMock()
    main_gh.path_exists_as_user.return_value = False
    found = MagicMock(returncode=0)  # branch survives
    main_gh._run_git.side_effect = [
        MagicMock(),  # fetch origin main
        found,  # local branch exists
        found,  # remote branch exists
        MagicMock(),  # worktree add <canonical> <branch> (attach)
    ]
    wt_gh = MagicMock()
    wt_gh.get_current_commit.return_value = "verified-head-abc"  # guard passes

    def fake_gh_ctor(path, **kw):
        return wt_gh if path.endswith("wf-2042") else main_gh

    fake_gh_cls = MagicMock(side_effect=fake_gh_ctor)
    with (
        patch("app.modules.workspace.autonomous.orchestrator.GitHubOps", fake_gh_cls),
        patch("os.path.realpath", side_effect=lambda p: p),
    ):
        result = orch._ensure_worktree(wf)

    assert result == canonical
    add_calls = [
        c.args[0]
        for c in main_gh._run_git.call_args_list
        if c.args and c.args[0] and c.args[0][0:2] == ["worktree", "add"]
    ]
    assert add_calls == [["worktree", "add", canonical, "auto-dev/survivor"]]
    orch._update_workflow.assert_called_once_with(
        {"worktree_path": canonical, "branch_name": "auto-dev/survivor"}
    )


@pytest.mark.parametrize(
    "phase",
    ["wait", "merge", "planning", "report", "acceptance_verification", "preparation"],
)
def test_ensure_worktree_noop_for_non_workspace_phases_when_cleared(phase):
    """Phases that never touch the workspace keep the historical no-op when
    worktree_path is cleared after merge cleanup (e.g. a retried merge must
    NOT rebuild the worktree): returns project_path, no git calls at all."""
    orch = _make_orch()
    wf = _cleared_wf(current_phase=phase)
    main_gh = MagicMock()
    fake_gh_cls = MagicMock(side_effect=lambda _p, **_kw: main_gh)
    with patch("app.modules.workspace.autonomous.orchestrator.GitHubOps", fake_gh_cls):
        result = orch._ensure_worktree(wf)

    assert result == "/private/tmp/repo"
    main_gh._run_git.assert_not_called()
    orch._update_workflow.assert_not_called()
    orch._create_milestone.assert_not_called()


def test_ensure_worktree_noop_when_not_worktree_strategy():
    """Non-worktree strategies never recreate: empty path in development
    still returns project_path (branch-strategy workflows work in place)."""
    orch = _make_orch()
    wf = _cleared_wf(branch_strategy="new-branch")
    main_gh = MagicMock()
    fake_gh_cls = MagicMock(side_effect=lambda _p, **_kw: main_gh)
    with patch("app.modules.workspace.autonomous.orchestrator.GitHubOps", fake_gh_cls):
        result = orch._ensure_worktree(wf)

    assert result == "/private/tmp/repo"
    main_gh._run_git.assert_not_called()
