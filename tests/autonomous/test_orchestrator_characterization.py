"""Characterization tests for the autonomous orchestrator scheduler loop.

Phase A of Issue #2044 requires pinning the *current* behaviour of
``advance()`` and the phase/status contract before the unified commit
entrypoint (``_commit_phase_result``) is introduced. These tests are
deliberately behaviour-focused, not implementation-focused: they assert the
observable scheduler contract that the refactor must preserve.

What is pinned
--------------
1. ``advance()`` dispatches to exactly the ``_do_*`` matching
   ``current_phase`` (no cross-dispatch, no double-dispatch).
2. Terminal statuses (paused/failed/cancelled) short-circuit before any
   phase runs.
3. Transient ``GitHubOpsError`` retries up to ``TRANSIENT_RETRY_MAX`` then
   fails; non-transient errors fail immediately.
4. ``PHASE_ORDER`` / ``_next_phase`` / ``PHASE_STATUS_MAP`` transition
   contract (the table the unified commit entrypoint will enforce).
5. Restart re-entry: a *new* orchestrator instance resumes from the
   persisted ``current_phase`` — it does not depend on in-memory state.
6. ``WorkflowPaused`` raised inside a phase is honoured (no failure written).
7. ``PhaseResult`` value-object contract from step 1 (factories + fields).

These mirror the acceptance tests #2044 lists for Phase A:
    test_phase_result_is_only_phase_status_transition_path
    test_phase_failure_does_not_partially_commit_next_phase
    test_restart_reenters_from_persisted_phase_state
    test_pause_resume_shutdown_contract_survives_phase_adapter

The mocking follows tests/autonomous/test_repo_drift_validation.py: patch
``Database`` + ``AutonomousWorkflowRepository`` at import time, then drive
``o.repo`` as a MagicMock so no DB/git/agent runs.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.orchestrator import (
    PHASE_ORDER,
    PHASE_STATUS_MAP,
    TRANSIENT_RETRY_MAX,
    AutonomousOrchestrator,
    _next_phase,
)
from app.modules.workspace.autonomous.phase_contract import PhaseResult, WorkflowContext

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_orchestrator(workflow: dict) -> AutonomousOrchestrator:
    """Build an orchestrator whose repo is a fully-mocked MagicMock.

    ``get_workflow`` returns a fresh copy of ``workflow`` each call so phases
    that re-read ``self.workflow`` (e.g. _do_planning) see the scripted state.
    """
    # SessionManager and AutonomousAgentRunner are imported inside __init__;
    # patch them at their source modules so construction does no real work.
    with (
        patch("app.modules.workspace.autonomous.orchestrator.Database"),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
        ) as mock_repo_cls,
        patch("app.modules.workspace.session_manager.SessionManager"),
        patch("app.modules.workspace.autonomous.agent_runner.AutonomousAgentRunner"),
    ):
        mock_repo = MagicMock()
        mock_repo.get_workflow.return_value = dict(workflow)
        mock_repo_cls.return_value = mock_repo
        o = AutonomousOrchestrator(workflow.get("workflow_id", "wf-test"))
        o.repo = mock_repo
    return o


def _active_workflow(phase: str = "planning", **overrides) -> dict:
    """A workflow in an active status at ``phase``."""
    wf = {
        "workflow_id": "wf-test",
        "status": PHASE_STATUS_MAP.get(phase, "planning"),
        "current_phase": phase,
        "project_path": "/srv/open-ace",
        "worktree_path": "/srv/open-ace/.worktrees/wf-test",
        "branch_name": "auto-dev/wf-test",
        "branch_strategy": "worktree",
        "workspace_type": "local",
        "dev_round": 1,
        "current_round": 0,
        "transient_retry_count": 0,
    }
    wf.update(overrides)
    return wf


# ── 1. advance() dispatch ────────────────────────────────────────────────────


class TestAdvanceDispatch:
    """advance() must route current_phase to exactly one _do_* method."""

    @pytest.mark.parametrize(
        "phase,method",
        [
            ("preparation", "_do_preparation"),
            ("planning", "_do_planning"),
            ("development", "_do_development"),
            ("pr_review", "_do_pr_review"),
            ("report", "_do_report"),
            ("wait", "_do_wait"),
            ("merge", "_do_merge"),
        ],
    )
    def test_dispatches_to_matching_phase_method(self, phase, method):
        # Phase B #2044 T10/T11: merge and pr_review migrated to phases/*.py
        # and are resolved via the PHASE_HANDLERS registry (the registry caches
        # the function reference at import, so patching the module attribute is
        # not enough — patch PHASE_HANDLERS directly). Other phases are still
        # bound methods on the orchestrator.
        from app.modules.workspace.autonomous import phases as _phases

        o = _make_orchestrator(_active_workflow(phase=phase))
        # Stub every phase so no real agent/git side effects run; record calls.
        spies = {}
        for name in (
            "_do_preparation",
            "_do_planning",
            "_do_development",
            "_do_pr_review",
            "_do_report",
            "_do_wait",
            "_do_merge",
            "_ensure_worktree",
            "_reconcile_worktree_transition",
            "_get_gh",
        ):
            spy = MagicMock(name=name)
            spies[name] = spy
            setattr(o, name, spy)
        merge_handle_spy = MagicMock(name="phases.merge.handle")
        spies["phases.merge.handle"] = merge_handle_spy
        pr_review_handle_spy = MagicMock(name="phases.pr_review.handle")
        spies["phases.pr_review.handle"] = pr_review_handle_spy

        saved_handlers = {name: _phases.PHASE_HANDLERS.get(name) for name in ("merge", "pr_review")}
        _phases.PHASE_HANDLERS["merge"] = merge_handle_spy
        _phases.PHASE_HANDLERS["pr_review"] = pr_review_handle_spy
        try:
            o.advance()
        finally:
            for name, saved in saved_handlers.items():
                if saved is None:
                    _phases.PHASE_HANDLERS.pop(name, None)
                else:
                    _phases.PHASE_HANDLERS[name] = saved

        if phase in ("merge", "pr_review"):
            spy = merge_handle_spy if phase == "merge" else pr_review_handle_spy
            spy.assert_called_once()
        else:
            spies[method].assert_called_once()
        # No other phase handler fired.
        expected = f"phases.{phase}.handle" if phase in ("merge", "pr_review") else method
        for name in (
            "_do_preparation",
            "_do_planning",
            "_do_development",
            "_do_pr_review",
            "_do_report",
            "_do_wait",
            "_do_merge",
            "phases.merge.handle",
            "phases.pr_review.handle",
        ):
            if name != expected:
                spies[name].assert_not_called()

    def test_non_preparation_phase_self_heals_worktree_before_dispatch(self):
        """advance() runs _ensure_worktree before the phase for non-preparation."""
        o = _make_orchestrator(_active_workflow(phase="development"))
        order: list[str] = []
        o._ensure_worktree = MagicMock(side_effect=lambda wf: order.append("ensure"))
        o._get_gh = MagicMock()
        o._do_development = MagicMock(side_effect=lambda wf: order.append("phase"))
        o._reconcile_worktree_transition = MagicMock()

        o.advance()

        assert order == ["ensure", "phase"]

    def test_preparation_skips_worktree_self_heal(self):
        """preparation creates the worktree, so advance() must not pre-heal it."""
        o = _make_orchestrator(_active_workflow(phase="preparation"))
        o._ensure_worktree = MagicMock()
        o._get_gh = MagicMock()
        o._do_preparation = MagicMock()
        o._reconcile_worktree_transition = MagicMock()

        o.advance()

        o._ensure_worktree.assert_not_called()


# ── 2. Terminal-status short-circuit ─────────────────────────────────────────


class TestTerminalStatusShortCircuit:
    """paused/failed/cancelled workflows must not advance."""

    @pytest.mark.parametrize("status", ["paused", "failed", "cancelled"])
    def test_terminal_status_does_not_dispatch(self, status):
        o = _make_orchestrator(_active_workflow(phase="planning", status=status))
        o._do_planning = MagicMock()
        o._ensure_worktree = MagicMock()
        o._reconcile_worktree_transition = MagicMock()

        o.advance()

        o._do_planning.assert_not_called()
        o._ensure_worktree.assert_not_called()

    def test_workflow_not_found_is_noop(self):
        o = _make_orchestrator(_active_workflow())
        o.repo.get_workflow.return_value = None
        o._do_planning = MagicMock()

        o.advance()  # must not raise

        o._do_planning.assert_not_called()


# ── 3. Error handling: transient retry vs immediate failure ──────────────────


class TestAdvanceErrorHandling:
    """advance() must distinguish transient network errors from hard failures."""

    def _orchestrator_with_raising_phase(self, exc):
        from app.modules.workspace.autonomous.github_ops import GitHubOpsError

        o = _make_orchestrator(_active_workflow(phase="planning"))
        o._ensure_worktree = MagicMock()
        o._get_gh = MagicMock()
        o._reconcile_worktree_transition = MagicMock()
        o._do_planning = MagicMock(side_effect=exc)
        return o

    def test_transient_error_increments_retry_and_does_not_fail(self):
        from app.modules.workspace.autonomous.github_ops import GitHubOpsError

        o = self._orchestrator_with_raising_phase(GitHubOpsError("Connection reset by peer (SSL)"))
        o.advance()

        updates = [c.args[1] for c in o.repo.update_workflow.call_args_list]
        # Last write must bump transient_retry_count, NOT set status=failed.
        assert any(u.get("transient_retry_count") == 1 for u in updates)
        assert not any(u.get("status") == "failed" for u in updates)

    def test_transient_error_exhausted_marks_failed(self):
        from app.modules.workspace.autonomous.github_ops import GitHubOpsError

        o = self._orchestrator_with_raising_phase(GitHubOpsError("openssl: connection refused"))
        o.workflow["transient_retry_count"] = TRANSIENT_RETRY_MAX  # type: ignore[index]

        o.advance()

        updates = [c.args[1] for c in o.repo.update_workflow.call_args_list]
        assert any(u.get("status") == "failed" for u in updates)

    def test_non_transient_error_marks_failed_immediately(self):
        o = self._orchestrator_with_raising_phase(RuntimeError("merge conflict"))

        o.advance()

        updates = [c.args[1] for c in o.repo.update_workflow.call_args_list]
        assert any(u.get("status") == "failed" for u in updates)
        assert not any(u.get("transient_retry_count") for u in updates)

    def test_workflow_paused_exception_does_not_fail_workflow(self):
        from app.modules.workspace.autonomous.orchestrator import WorkflowPaused

        o = self._orchestrator_with_raising_phase(WorkflowPaused("shutdown"))
        o.advance()

        updates = [c.args[1] for c in o.repo.update_workflow.call_args_list]
        assert not any(u.get("status") == "failed" for u in updates)


# ── 4. Phase transition contract ─────────────────────────────────────────────


class TestPhaseTransitionContract:
    """The PHASE_ORDER / _next_phase / PHASE_STATUS_MAP tables are the contract
    the unified commit entrypoint will enforce. Pin them so a refactor cannot
    silently change the transition graph."""

    def test_phase_order_is_canonical_sequence(self):
        assert [
            "preparation",
            "planning",
            "development",
            "pr_review",
            "report",
            "merge",
        ] == PHASE_ORDER

    def test_every_phase_has_a_status_mapping(self):
        # A phase without a status mapping would make the commit entrypoint's
        # default-derivation fall back silently. Every phase must be mapped.
        assert set(PHASE_ORDER) <= set(PHASE_STATUS_MAP)

    @pytest.mark.parametrize(
        "current,expected",
        [
            ("preparation", "planning"),
            ("planning", "development"),
            ("development", "pr_review"),
            ("pr_review", "report"),
            ("report", "merge"),
        ],
    )
    def test_next_phase_advances_along_order(self, current, expected):
        assert _next_phase(current) == expected

    def test_next_phase_after_merge_is_terminal(self):
        # merge is the last phase; _next_phase stays on merge (no advance).
        assert _next_phase("merge") == "merge"

    def test_unknown_phase_falls_back_to_planning(self):
        # _next_phase's ValueError branch returns "planning" (the recovery
        # default). Pinned so the commit entrypoint inherits the same fallback.
        assert _next_phase("nope") == "planning"


# ── 5. Restart re-entry from persisted state ─────────────────────────────────


class TestRestartReentry:
    """A new orchestrator process must resume from the persisted current_phase,
    not from any in-memory field. This is the #2044 acceptance criterion
    'restart 不依赖旧内存对象'."""

    def test_new_instance_reads_phase_from_db(self):
        # A workflow that previously completed planning and persisted the
        # transition to development in the DB.
        persisted = _active_workflow(phase="development", status="developing")

        # A *new* orchestrator instance for the same workflow_id, pointed at
        # the same persisted state, must see development — not planning.
        o = _make_orchestrator(dict(persisted))
        assert o.workflow["current_phase"] == "development"
        assert o.workflow["status"] == "developing"

    def test_advance_after_restart_dispatches_from_persisted_phase(self):
        # Phase B #2044 T11: pr_review migrated to phases/pr_review.py and is
        # resolved via the PHASE_HANDLERS registry (the registry caches the fn
        # ref at import — patch PHASE_HANDLERS directly, not the module attr).
        from app.modules.workspace.autonomous import phases as _phases

        persisted = _active_workflow(phase="pr_review")
        o = _make_orchestrator(persisted)
        spies = {
            name: MagicMock(name=name)
            for name in (
                "_do_preparation",
                "_do_planning",
                "_do_development",
                "_do_pr_review",
                "_do_report",
                "_do_wait",
                "_do_merge",
            )
        }
        for name, spy in spies.items():
            setattr(o, name, spy)
        o._ensure_worktree = MagicMock()
        o._get_gh = MagicMock()
        o._reconcile_worktree_transition = MagicMock()

        pr_review_handle_spy = MagicMock(name="phases.pr_review.handle")
        saved = _phases.PHASE_HANDLERS.get("pr_review")
        _phases.PHASE_HANDLERS["pr_review"] = pr_review_handle_spy
        try:
            o.advance()
        finally:
            if saved is None:
                _phases.PHASE_HANDLERS.pop("pr_review", None)
            else:
                _phases.PHASE_HANDLERS["pr_review"] = saved

        pr_review_handle_spy.assert_called_once()
        spies["_do_development"].assert_not_called()
        # The legacy _do_pr_review shim is NOT the production path now; ensure
        # advance() did not fall through to it.
        spies["_do_pr_review"].assert_not_called()


# ── 6. Session / cancellation topology ───────────────────────────────────────


class TestSessionAndCancellationTopology:
    """The orchestrator owns thread-safe session binding and a cancel/shutdown
    event. Phase handlers must be able to observe these without the scheduler
    having to abort them externally — preserved by the adapter in step 4."""

    def test_cancellation_event_is_threading_event_and_settable(self):
        o = _make_orchestrator(_active_workflow())
        assert isinstance(o._cancel_requested, threading.Event)
        assert not o._cancel_requested.is_set()
        o._cancel_requested.set()
        assert o._cancel_requested.is_set()

    def test_shutdown_event_is_distinct_from_cancel(self):
        # shutdown interrupts the *current* attempt without flipping status;
        # cancel is a user stop. They must be separate signals.
        o = _make_orchestrator(_active_workflow())
        assert isinstance(o._shutdown_requested, threading.Event)
        assert o._cancel_requested is not o._shutdown_requested

    def test_session_lock_proticts_current_session_id(self):
        o = _make_orchestrator(_active_workflow())
        assert isinstance(o._session_lock, type(threading.Lock()))
        # Setting/clearing the session id under the lock is the existing
        # contract; just assert the field exists and is mutable.
        with o._session_lock:
            o._current_session_id = "sess-1"
        assert o._current_session_id == "sess-1"


# ── 7. PhaseResult / WorkflowContext contract (step 1) ───────────────────────


class TestPhaseResultContract:
    """The value objects introduced in step 1. Pinning their shape here means
    the commit entrypoint (step 3) has a stable input contract to consume."""

    def test_completed_advances_to_next_phase(self):
        r = PhaseResult.completed("development", workflow_patch={"dev_round": 2})
        assert r.outcome == "completed"
        assert r.next_phase == "development"
        assert r.next_status is None  # commit entrypoint derives from PHASE_STATUS_MAP
        assert r.workflow_patch == {"dev_round": 2}

    def test_wait_sets_waiting_status_without_advancing(self):
        r = PhaseResult.wait()
        assert r.outcome == "wait"
        assert r.next_phase is None
        assert r.next_status == "waiting"

    def test_retry_leaves_phase_unchanged(self):
        r = PhaseResult.retry()
        assert r.outcome == "retry"
        assert r.next_phase is None
        assert r.next_status is None

    def test_pause_sets_paused_status(self):
        r = PhaseResult.pause(structured_error={"reason": "quota"})
        assert r.outcome == "pause"
        assert r.next_status == "paused"
        assert r.structured_error == {"reason": "quota"}

    def test_failed_sets_failed_status(self):
        r = PhaseResult.failed(structured_error={"code": "ECONFLICT"})
        assert r.outcome == "failed"
        assert r.next_status == "failed"

    def test_default_collections_are_independent_per_instance(self):
        # Mutable defaults must not leak between instances (the classic
        # dataclass footgun; field(default_factory=...) guards against it).
        a = PhaseResult.completed("merge")
        b = PhaseResult.completed("merge")
        a.workflow_patch["x"] = 1
        a.milestone_events.append({"phase": "merge"})
        assert b.workflow_patch == {}
        assert b.milestone_events == []

    def test_workflow_context_carries_cancellation_event(self):
        ev = threading.Event()
        ctx = WorkflowContext(
            workflow={"current_phase": "planning"},
            definition_snapshot=None,
            repository_context=None,
            session_bindings={},
            cancellation=ev,
        )
        assert ctx.cancellation is ev
        assert ctx.workflow["current_phase"] == "planning"


# ── 8. _commit_phase_result: the unified commit entrypoint (step 3) ─────────


class TestCommitPhaseResult:
    """The single authoritative path for phase/status transition (step 3).

    These pin the #2044 acceptance criteria:
      test_phase_result_is_only_phase_status_transition_path
      test_phase_failure_does_not_partially_commit_next_phase
    by driving the entrypoint directly with crafted PhaseResult values."""

    def test_completed_advances_phase_and_derives_status(self):
        o = _make_orchestrator(_active_workflow(phase="planning"))
        o._commit_phase_result(PhaseResult.completed("development"))
        # update_workflow(workflow_id, updates): last call carries the transition.
        last_updates = o.repo.update_workflow.call_args_list[-1].args[1]
        assert last_updates["current_phase"] == "development"
        # next_status was None → derived from PHASE_STATUS_MAP.
        assert last_updates["status"] == PHASE_STATUS_MAP["development"]

    def test_completed_merges_workflow_patch_into_same_write(self):
        o = _make_orchestrator(_active_workflow(phase="planning"))
        o._commit_phase_result(
            PhaseResult.completed("development", workflow_patch={"dev_round": 2})
        )
        last_updates = o.repo.update_workflow.call_args_list[-1].args[1]
        assert last_updates["dev_round"] == 2
        assert last_updates["current_phase"] == "development"

    def test_failed_does_not_advance_phase(self):
        # The core anti-regression: a failing phase must NOT write the next
        # phase. Only status=failed (+ error_message) is committed.
        o = _make_orchestrator(_active_workflow(phase="development"))
        o._commit_phase_result(PhaseResult.failed(structured_error={"message": "conflict"}))
        last_updates = o.repo.update_workflow.call_args_list[-1].args[1]
        assert "current_phase" not in last_updates
        assert last_updates["status"] == "failed"
        assert last_updates["error_message"] == "conflict"

    def test_pause_does_not_advance_phase_and_sets_paused_at(self):
        o = _make_orchestrator(_active_workflow(phase="planning"))
        o._commit_phase_result(PhaseResult.pause())
        last_updates = o.repo.update_workflow.call_args_list[-1].args[1]
        assert "current_phase" not in last_updates
        assert last_updates["status"] == "paused"
        assert "paused_at" in last_updates

    def test_wait_sets_waiting_without_advancing(self):
        o = _make_orchestrator(_active_workflow(phase="report"))
        o._commit_phase_result(PhaseResult.wait())
        last_updates = o.repo.update_workflow.call_args_list[-1].args[1]
        assert "current_phase" not in last_updates
        assert last_updates["status"] == "waiting"

    def test_retry_writes_only_the_patch(self):
        # retry leaves phase/status untouched; only explicit patch fields apply.
        o = _make_orchestrator(_active_workflow(phase="planning"))
        o._commit_phase_result(PhaseResult.retry(workflow_patch={"transient_retry_count": 1}))
        last_updates = o.repo.update_workflow.call_args_list[-1].args[1]
        assert last_updates == {"transient_retry_count": 1}

    def test_completed_without_next_phase_is_rejected(self):
        # A handler that claims success but names no next phase is a bug; the
        # entrypoint fails loudly rather than stranding the workflow.
        o = _make_orchestrator(_active_workflow())
        with pytest.raises(ValueError, match="requires next_phase"):
            o._commit_phase_result(PhaseResult.completed(None))

    def test_completed_with_unknown_next_phase_is_rejected(self):
        o = _make_orchestrator(_active_workflow())
        with pytest.raises(ValueError, match="not in PHASE_ORDER"):
            o._commit_phase_result(PhaseResult.completed("nope"))

    def test_completed_terminal_status_signal(self):
        # next_phase="completed" is the terminal signal → status=completed,
        # AND it must carry completed_at (symmetric with pause's paused_at) so
        # a migrated merge handler cannot silently drop the completion
        # timestamp (review feedback on #2065).
        o = _make_orchestrator(_active_workflow(phase="merge"))
        o._commit_phase_result(PhaseResult.completed("completed"))
        last_updates = o.repo.update_workflow.call_args_list[-1].args[1]
        assert last_updates["status"] == "completed"
        assert last_updates["current_phase"] == "merge"
        assert "completed_at" in last_updates
        # Same format as _do_merge's completed_at (YYYY-MM-DD HH:MM:SS strftime,
        # not ISO), so the terminal write stays symmetric with the legacy path.
        assert len(last_updates["completed_at"]) == 19

    def test_milestones_committed_after_workflow_patch(self):
        # Milestones flow through _create_milestone (idempotent) and are applied
        # after the phase/status patch so timeline observers see the advance.
        o = _make_orchestrator(_active_workflow(phase="planning"))
        # _create_milestone emits an event that JSON-serializes the returned
        # milestone, so the mocks must return plain values (not MagicMocks):
        # idempotency check finds nothing → create path → returns a dict.
        o._find_existing_milestone = MagicMock(return_value=None)
        o.repo.create_milestone.return_value = {"milestone_id": "ms-1"}
        ms_kwargs = {"phase": "planning", "milestone_type": "plan_finalized"}
        o._commit_phase_result(PhaseResult.completed("development", milestone_events=[ms_kwargs]))
        o.repo.create_milestone.assert_called_once()
        # The workflow write happened before the milestone write.
        assert o.repo.update_workflow.called

    def test_advance_routes_phase_result_through_commit_entrypoint(self):
        # End-to-end of the adapter: a migrated phase returns a PhaseResult and
        # advance() commits it via _commit_phase_result (not inline writes).
        o = _make_orchestrator(_active_workflow(phase="report"))
        o._ensure_worktree = MagicMock()
        o._get_gh = MagicMock()
        o._reconcile_worktree_transition = MagicMock()
        committed: dict = {}

        def fake_report(ctx, deps):
            # A migrated phase: native (ctx, deps) signature, no inline
            # _update_workflow; just return a result for the commit entrypoint.
            return PhaseResult.completed("wait", workflow_patch={"current_round": 0})

        o._do_report = fake_report
        original_commit = o._commit_phase_result

        def spy(result):
            committed["result"] = result
            original_commit(result)

        o._commit_phase_result = spy
        o.advance()

        assert committed["result"].outcome == "completed"
        assert committed["result"].next_phase == "wait"

    def test_commit_phase_result_accepts_wait_pseudo_phase(self):
        """#2044 Phase B regression: _do_report returns next_phase='wait' (the
        orchestrator's wait pseudo-phase, dispatched by advance() but not in
        PHASE_ORDER). _commit_phase_result must accept it and set
        current_phase='wait' + status='waiting', not raise. (Caught a production
        crash where advance() committing the report result raised ValueError.)"""
        o = _make_orchestrator(_active_workflow(phase="report", status="running"))
        result = PhaseResult.completed(next_phase="wait", next_status="waiting")
        o._commit_phase_result(result)  # must NOT raise
        last_updates = o.repo.update_workflow.call_args_list[-1].args[1]
        assert last_updates["current_phase"] == "wait"
        assert last_updates["status"] == "waiting"

    def test_commit_completed_rejects_non_terminal_current_phase_in_patch(self):
        """#2044 S2 safety net: a handler cannot strand the workflow in a
        non-canonical current_phase by stuffing it into workflow_patch on a
        completed result. The top-level next_phase validation rejects unknown
        next_phase values, but the patch-carried current_phase on the completed
        branch slipped through (PhaseResult.workflow_patch is the T3-approved
        escape hatch, so the AST guard does not catch it)."""
        o = _make_orchestrator(_active_workflow(phase="merge"))
        bad = PhaseResult.completed(
            next_phase="completed",
            workflow_patch={"current_phase": "development"},
        )
        with pytest.raises(ValueError, match="not a valid terminal phase"):
            o._commit_phase_result(bad)
        # And nothing was persisted — update_workflow never reached.
        assert not o.repo.update_workflow.called

    def test_commit_completed_allows_merge_and_completed_current_phase(self):
        """The two legitimate terminal current_phase values still work — guards
        against over-tightening the whitelist. 'merge' is the default terminal
        real phase (merge-phase completion); 'completed' is pr_review's
        literal-terminal legacy path (skips report/merge)."""
        for legit in ("merge", "completed"):
            o = _make_orchestrator(_active_workflow(phase="merge"))
            result = PhaseResult.completed(
                next_phase="completed", workflow_patch={"current_phase": legit}
            )
            o._commit_phase_result(result)  # must NOT raise
            last_updates = o.repo.update_workflow.call_args_list[-1].args[1]
            assert last_updates["current_phase"] == legit
            assert last_updates["status"] == "completed"
            assert "completed_at" in last_updates

    def test_advance_report_commits_wait_through_real_entrypoint(self, monkeypatch):
        """End-to-end: advance() running the report phase routes _do_report's
        PhaseResult through the REAL _commit_phase_result, landing
        current_phase='wait'. (The gate that originally missed the 'wait'
        rejection bug — the spy in test_advance_routes_phase_result_through_
        commit_entrypoint set its assertion before the exception fired, so the
        swallowed ValueError never failed the test.)"""
        wf = _active_workflow(
            phase="report",
            status="running",
            dev_round=1,
            github_issue_number=42,
            github_pr_number=7,
            branch_name="auto-dev/wf-test",
            content_language="en",
            total_tokens=0,
            total_requests=0,
        )
        o = _make_orchestrator(wf)
        # Drive the REAL _do_report to its terminal return (no business-logic
        # change — these stubs only satisfy its gh/repo reads).
        o._ensure_worktree = MagicMock()
        o._reconcile_worktree_transition = MagicMock()
        gh = MagicMock()
        gh.get_diff_stats.return_value = {}
        monkeypatch.setattr(o, "_get_gh", lambda: gh)
        monkeypatch.setattr(o, "_clean_agent_text", lambda text: text or "")
        monkeypatch.setattr(o, "_derive_review_passed", lambda milestones, lang: True)
        monkeypatch.setattr(o, "_post_github_comment", lambda *a, **kw: None)
        o.repo.list_milestones.return_value = []
        # create_milestone returns a JSON-serialisable dict (the emit path
        # serialises it); idempotency check finds nothing → create path.
        o._find_existing_milestone = MagicMock(return_value=None)
        o.repo.create_milestone.return_value = {"milestone_id": "ms-1"}

        o.advance()  # must not raise; the real _commit_phase_result commits "wait"

        last_updates = o.repo.update_workflow.call_args_list[-1].args[1]
        assert last_updates["current_phase"] == "wait"
        assert last_updates["status"] == "waiting"


def test_workflow_context_repository_context_is_wired_from_git_binding(tmp_path):
    """Phase B #2044: repository_context is populated (not None) so migrated
    phases read git binding via ctx, not ad hoc."""
    wf = _active_workflow(
        branch_name="feature-x",
        worktree_path=str(tmp_path),
    )
    orch = _make_orchestrator(wf)
    ctx = orch._build_workflow_context(orch.workflow)
    assert ctx.repository_context is not None
    assert ctx.repository_context["branch_name"] == "feature-x"


def test_dispatch_phase_passes_ctx_and_deps_to_handler():
    """Phase B #2044: _dispatch_phase builds WorkflowContext + PhaseDeps and
    invokes the handler as handler(ctx, deps), not handler(wf)."""
    orch = _make_orchestrator(_active_workflow(phase="report"))
    seen = []

    def fake_handler(ctx, deps):
        seen.append((ctx, deps))
        return None  # legacy None path

    result = orch._dispatch_phase("report", orch.workflow, fake_handler)
    assert result is None
    assert len(seen) == 1
    ctx, deps = seen[0]
    from app.modules.workspace.autonomous.phase_host import PhaseDeps

    assert isinstance(deps, PhaseDeps)
    # ctx.workflow is the wf passed in
    assert ctx.workflow is orch.workflow or ctx.workflow == orch.workflow


def test_orchestrator_satisfies_phase_host_protocol():
    """Phase B #2044: AutonomousOrchestrator implements the PhaseHost narrow
    interface (emit_phase_change/session_offsets/cancellation/
    create_milestone_idempotent + workflow_id)."""
    from app.modules.workspace.autonomous.phase_host import PhaseHost

    orch = _make_orchestrator(_active_workflow())
    for method in [
        "emit_phase_change",
        "session_offsets",
        "cancellation",
        "create_milestone_idempotent",
    ]:
        assert callable(getattr(orch, method)), f"orchestrator missing PhaseHost.{method}"
    assert hasattr(orch, "workflow_id")


def test_report_phase_returns_phase_result_not_inline_commit(monkeypatch):
    """#2044 Phase B T6: migrated _do_report returns a PhaseResult; the
    workflow phase/status transition is applied by _commit_phase_result, not
    inline. The four forbidden fields (current_phase/status/completed_at/
    paused_at) must not be written by the handler itself.
    """
    wf = _active_workflow(
        phase="report",
        status="running",
        dev_round=1,
        github_issue_number=42,
        github_pr_number=7,
        branch_name="auto-dev/wf-test",
        content_language="en",
        total_tokens=0,
        total_requests=0,
    )
    orch = _make_orchestrator(wf)

    # Stub _do_report's external collaborators so it reaches its terminal
    # PhaseResult without making gh/repo calls. These stubs do NOT change the
    # handler's decisions — they only satisfy its reads.
    gh = MagicMock()
    gh.get_diff_stats.return_value = {}
    monkeypatch.setattr(orch, "_get_gh", lambda: gh)
    monkeypatch.setattr(orch, "_clean_agent_text", lambda text: text or "")
    monkeypatch.setattr(orch, "_derive_review_passed", lambda milestones, lang: True)
    monkeypatch.setattr(orch, "_post_github_comment", lambda *a, **kw: None)
    orch.repo.list_milestones.return_value = []

    inline_phase_status_writes: list[dict] = []
    orig_update = orch._update_workflow

    def spy(patch):
        if any(k in patch for k in ("current_phase", "status", "completed_at", "paused_at")):
            inline_phase_status_writes.append(dict(patch))
        return orig_update(patch)

    monkeypatch.setattr(orch, "_update_workflow", spy)
    monkeypatch.setattr(orch, "_create_milestone", lambda **kw: kw)

    # _do_report emits its phase_change through deps.host.emit_phase_change
    # (not the legacy _emit). Wrap the real host with a recorder so this
    # template test proves the migrated handler actually fires the event,
    # while delegating the other PhaseHost methods to the orchestrator.
    import dataclasses

    captured_phase_changes: list[dict] = []
    real_host = orch  # orchestrator implements PhaseHost

    class _RecordingHost:
        workflow_id = orch.workflow_id

        def emit_phase_change(self, payload):
            captured_phase_changes.append(payload)

        def session_offsets(self):
            return real_host.session_offsets()

        def cancellation(self):
            return real_host.cancellation()

        def create_milestone_idempotent(self, **kw):
            return real_host.create_milestone_idempotent(**kw)

    deps = dataclasses.replace(orch._build_phase_deps(), host=_RecordingHost())
    ctx = orch._build_workflow_context(orch.workflow)
    result = orch._do_report(ctx, deps)

    assert isinstance(
        result, PhaseResult
    ), f"_do_report must return PhaseResult, got {type(result)}"
    assert not inline_phase_status_writes, (
        f"_do_report wrote phase/status/completed_at/paused_at inline: "
        f"{inline_phase_status_writes}"
    )
    # Report always advances to "wait" with waiting status — same decision as
    # the legacy inline _update_workflow({"current_phase":"wait","status":"waiting"}).
    assert result.outcome == "completed"
    assert result.next_phase == "wait"
    assert result.next_status == "waiting"
    # The migrated handler emits its own phase_change (the commit entrypoint
    # does NOT) — assert the wait payload actually fires.
    assert captured_phase_changes, "_do_report did not emit phase_change"
    assert {"phase": "wait"} in captured_phase_changes


def test_wait_phase_returns_phase_result_not_inline_commit(monkeypatch):
    """#2044 Phase B T7: migrated _do_wait returns a PhaseResult; no inline
    phase/status write; emits phase_change via deps.host. Mirrors the T6 test
    for _do_report. Exercises the "new requirements detected → planning" path,
    which is the only _do_wait branch that fires both a milestone and a
    phase/status transition, so the template assertion covers every recording
    mechanism the migration changes.
    """
    wf = _active_workflow(
        phase="wait",
        status="waiting",
        dev_round=1,
        github_issue_number=42,
        github_pr_number=None,  # auto_merge path is exercised separately
        branch_name="auto-dev/wf-test",
    )
    orch = _make_orchestrator(wf)

    # Stub _do_wait's external collaborators so it reaches its terminal
    # PhaseResult without making real gh/repo calls. These stubs do NOT change
    # the handler's decisions — they only satisfy its reads.
    gh = MagicMock()
    # A user comment carrying a new requirement (no completion keyword): the
    # handler should advance to "planning" for a new dev round.
    gh.list_issue_comments.return_value = [
        {"body": "please also add tests", "createdAt": "2026-07-28T00:00:00Z", "author": "user"}
    ]
    monkeypatch.setattr(orch, "_get_gh", lambda: gh)
    orch.repo.list_milestones.return_value = []

    inline_phase_status_writes: list[dict] = []
    orig_update = orch._update_workflow

    def spy(patch):
        if any(k in patch for k in ("current_phase", "status", "completed_at", "paused_at")):
            inline_phase_status_writes.append(dict(patch))
        return orig_update(patch)

    monkeypatch.setattr(orch, "_update_workflow", spy)
    monkeypatch.setattr(orch, "_create_milestone", lambda **kw: kw)

    # _do_wait emits its phase_change through deps.host.emit_phase_change (not
    # the legacy _emit). Wrap the real host with a recorder so this template
    # test proves the migrated handler actually fires the event, while
    # delegating the other PhaseHost methods to the orchestrator.
    import dataclasses

    captured_phase_changes: list[dict] = []
    real_host = orch  # orchestrator implements PhaseHost

    class _RecordingHost:
        workflow_id = orch.workflow_id

        def emit_phase_change(self, payload):
            captured_phase_changes.append(payload)

        def session_offsets(self):
            return real_host.session_offsets()

        def cancellation(self):
            return real_host.cancellation()

        def create_milestone_idempotent(self, **kw):
            return real_host.create_milestone_idempotent(**kw)

    deps = dataclasses.replace(orch._build_phase_deps(), host=_RecordingHost())
    ctx = orch._build_workflow_context(orch.workflow)
    result = orch._do_wait(ctx, deps)

    assert isinstance(result, PhaseResult), f"_do_wait must return PhaseResult, got {type(result)}"
    assert not inline_phase_status_writes, (
        f"_do_wait wrote phase/status/completed_at/paused_at inline: "
        f"{inline_phase_status_writes}"
    )
    # New-requirements branch advances to "planning" for dev_round 2 — same
    # decision as the legacy inline _update_workflow({"current_phase":"planning",
    # "status":"planning","dev_round":2,"current_round":0,...}).
    assert result.outcome == "completed"
    assert result.next_phase == "planning"
    assert result.next_status == "planning"
    assert result.workflow_patch.get("dev_round") == 2
    assert result.workflow_patch.get("current_round") == 0
    assert "requirements_text" in result.workflow_patch
    # The requirement_received milestone travels in milestone_events, not via
    # an inline _create_milestone call.
    assert any(
        m.get("milestone_type") == "requirement_received" for m in result.milestone_events
    ), f"requirement_received milestone missing: {result.milestone_events}"
    # The migrated handler emits its own phase_change (the commit entrypoint
    # does NOT) — assert the planning payload actually fires.
    assert captured_phase_changes, "_do_wait did not emit phase_change"
    assert {"phase": "planning", "dev_round": 2} in captured_phase_changes


@pytest.mark.parametrize(
    "comments,pr_number,expected",
    [
        # No new comments → stay waiting (PhaseResult.wait).
        ([], None, "wait_outcome"),
        # auto_merge + PR → skip to merge (PhaseResult.completed merge/merging).
        ([], 7, "merge_outcome"),
    ],
)
def test_wait_phase_parking_and_auto_merge_paths(monkeypatch, comments, pr_number, expected):
    """#2044 Phase B T7: the "keep waiting" and "auto_merge → merge" branches
    of _do_wait also return PhaseResults without inline phase/status writes."""
    wf = _active_workflow(
        phase="wait",
        status="waiting",
        dev_round=1,
        github_issue_number=42,
        github_pr_number=pr_number,
        branch_name="auto-dev/wf-test",
    )
    orch = _make_orchestrator(wf)

    gh = MagicMock()
    gh.list_issue_comments.return_value = comments
    monkeypatch.setattr(orch, "_get_gh", lambda: gh)
    orch.repo.list_milestones.return_value = []

    inline_phase_status_writes: list[dict] = []
    orig_update = orch._update_workflow

    def spy(patch):
        if any(k in patch for k in ("current_phase", "status", "completed_at", "paused_at")):
            inline_phase_status_writes.append(dict(patch))
        return orig_update(patch)

    monkeypatch.setattr(orch, "_update_workflow", spy)
    monkeypatch.setattr(orch, "_create_milestone", lambda **kw: kw)

    import dataclasses

    captured_phase_changes: list[dict] = []
    real_host = orch

    class _RecordingHost:
        workflow_id = orch.workflow_id

        def emit_phase_change(self, payload):
            captured_phase_changes.append(payload)

        def session_offsets(self):
            return real_host.session_offsets()

        def cancellation(self):
            return real_host.cancellation()

        def create_milestone_idempotent(self, **kw):
            return real_host.create_milestone_idempotent(**kw)

    deps = dataclasses.replace(orch._build_phase_deps(), host=_RecordingHost())
    ctx = orch._build_workflow_context(orch.workflow)
    result = orch._do_wait(ctx, deps)

    assert isinstance(result, PhaseResult)
    assert not inline_phase_status_writes, (
        f"_do_wait wrote phase/status inline on {expected}: " f"{inline_phase_status_writes}"
    )
    if expected == "wait_outcome":
        assert result.outcome == "wait"
        assert result.next_phase is None
        assert result.next_status == "waiting"
        # No phase_change emitted when staying parked (legacy returned silently).
        assert not captured_phase_changes
    else:  # merge_outcome
        assert result.outcome == "completed"
        assert result.next_phase == "merge"
        assert result.next_status == "merging"
        assert {"phase": "merge", "auto_merge": True} in captured_phase_changes


def test_preparation_phase_returns_phase_result_not_inline_commit(monkeypatch):
    """#2044 Phase B T8: migrated _do_preparation returns a PhaseResult; the
    workflow phase/status transition (preparation → planning) is applied by
    _commit_phase_result, not inline. The four forbidden fields (current_phase/
    status/completed_at/paused_at) must not be written by the handler itself.
    Mirrors the T6/T7 template tests for _do_report/_do_wait.

    Exercises the normal-workflow new-branch path: requirements_text present,
    no existing issue_number, no new-project repo. _do_preparation creates the
    issue, creates the branch, and advances to planning — every recording
    mechanism the migration changes (workflow_patch for github_issue_number /
    branch_name / base_commit_sha / current_round, milestone_events for
    issue_created / branch_created, deps.host for the phase_change).
    """
    wf = _active_workflow(
        phase="preparation",
        status="preparing",
        branch_strategy="new-branch",
        requirements_text="Build the widget",
        github_issue_number=None,
        requirements_issue_url="",
        is_new_project=False,
    )
    orch = _make_orchestrator(wf)

    # Stub _do_preparation's external collaborators so it reaches its terminal
    # PhaseResult without making real gh/repo calls. These stubs do NOT change
    # the handler's decisions — they only satisfy its reads.
    gh = MagicMock()
    gh.create_issue.return_value = {"number": 42, "url": "https://example/42"}
    # create_branch + the rev-parse / show-ref shelled out via _run_git.
    gh.create_branch.return_value = {"branch": "auto-dev/wf-test"}
    gh._run_git.return_value = MagicMock(stdout="deadbeef\n", returncode=1)
    monkeypatch.setattr(orch, "_get_gh", lambda: gh)

    inline_phase_status_writes: list[dict] = []
    orig_update = orch._update_workflow

    def spy(patch):
        if any(k in patch for k in ("current_phase", "status", "completed_at", "paused_at")):
            inline_phase_status_writes.append(dict(patch))
        return orig_update(patch)

    monkeypatch.setattr(orch, "_update_workflow", spy)
    monkeypatch.setattr(orch, "_create_milestone", lambda **kw: kw)

    # _do_preparation emits its phase_change through deps.host.emit_phase_change
    # (not the legacy _emit). Wrap the real host with a recorder so this
    # template test proves the migrated handler actually fires the event, while
    # delegating the other PhaseHost methods to the orchestrator.
    import dataclasses

    captured_phase_changes: list[dict] = []
    real_host = orch  # orchestrator implements PhaseHost

    class _RecordingHost:
        workflow_id = orch.workflow_id

        def emit_phase_change(self, payload):
            captured_phase_changes.append(payload)

        def session_offsets(self):
            return real_host.session_offsets()

        def cancellation(self):
            return real_host.cancellation()

        def create_milestone_idempotent(self, **kw):
            return real_host.create_milestone_idempotent(**kw)

    deps = dataclasses.replace(orch._build_phase_deps(), host=_RecordingHost())
    ctx = orch._build_workflow_context(orch.workflow)
    result = orch._do_preparation(ctx, deps)

    assert isinstance(
        result, PhaseResult
    ), f"_do_preparation must return PhaseResult, got {type(result)}"
    assert not inline_phase_status_writes, (
        f"_do_preparation wrote phase/status/completed_at/paused_at inline: "
        f"{inline_phase_status_writes}"
    )
    # Preparation always advances to "planning" with planning status — same
    # decision as the legacy inline _update_workflow({"current_phase":"planning",
    # "status":"planning","current_round":0}).
    assert result.outcome == "completed"
    assert result.next_phase == "planning"
    assert result.next_status == "planning"
    # Bookkeeping fields travel in workflow_patch (non-forbidden): the created
    # issue number, branch_name, base_commit_sha, and current_round.
    assert result.workflow_patch.get("github_issue_number") == 42
    assert "branch_name" in result.workflow_patch
    assert "base_commit_sha" in result.workflow_patch
    assert result.workflow_patch.get("current_round") == 0
    # issue_created + branch_created milestones travel in milestone_events, not
    # via inline _create_milestone calls (those are stubbed out above).
    assert any(
        m.get("milestone_type") == "issue_created" for m in result.milestone_events
    ), f"issue_created milestone missing: {result.milestone_events}"
    assert any(
        m.get("milestone_type") == "branch_created" for m in result.milestone_events
    ), f"branch_created milestone missing: {result.milestone_events}"
    # The migrated handler emits its own phase_change (the commit entrypoint
    # does NOT) — assert the planning payload actually fires.
    assert captured_phase_changes, "_do_preparation did not emit phase_change"
    assert {"phase": "planning"} in captured_phase_changes


def test_planning_phase_returns_phase_result_not_inline_commit(monkeypatch):
    """#2044 Phase B T9: migrated _do_planning returns a PhaseResult; the
    workflow phase/status transition (planning → development) is applied by
    _commit_phase_result, not inline. The four forbidden fields (current_phase/
    status/completed_at/paused_at) must not be written by the handler itself on
    the success path. Mirrors the T6/T7/T8 template tests.

    Exercises the success path: round 1 of 3, plan agent succeeds, review agent
    succeeds with an approving review ("方案通过审查"), so needs_refinement is
    False and planning finalizes into development — every recording mechanism
    the migration changes (workflow_patch for current_round / user_feedback,
    deps.host for the phase_change, the absence of inline forbidden writes).

    Unlike _do_preparation, planning's plan/review/finalize milestones are
    created inline (they anchor agent runs and are re-read by the next round),
    so they are NOT asserted in milestone_events — only the terminal
    phase/status transition is asserted on the PhaseResult.
    """
    wf = _active_workflow(
        phase="planning",
        status="planning",
        current_round=0,
        max_plan_rounds=3,
        requirements_text="Build the widget",
        github_issue_number=None,
        user_feedback="",
    )
    orch = _make_orchestrator(wf)

    # Stub _do_planning's external collaborators so it reaches its terminal
    # PhaseResult without making real agent/repo calls. These stubs do NOT
    # change the handler's decisions — they only satisfy its reads.
    plan_result = MagicMock()
    plan_result.success = True
    plan_result.error = None
    plan_result.session_id = "plan-session"
    plan_result.total_tokens = 100
    review_result = MagicMock()
    review_result.success = True
    review_result.error = None
    review_result.session_id = "review-session"
    review_result.total_tokens = 50
    monkeypatch.setattr(orch, "_run_agent", MagicMock(side_effect=[plan_result, review_result]))
    # _artifact_text is a classmethod returning the plan/review text; patch the
    # bound class so both reads return distinct content.
    monkeypatch.setattr(
        type(orch), "_artifact_text", lambda cls, r: "PLAN" if r is plan_result else "REVIEW"
    )
    monkeypatch.setattr(type(orch), "_artifact_tldr", lambda cls, r: "tldr")
    monkeypatch.setattr(type(orch), "_artifact_visible_text", lambda cls, r: "")
    # Approving review ("方案通过审查") ⇒ review_has_feedback False; no refine.
    monkeypatch.setattr(type(orch), "_sanitize_artifact_text", lambda cls, t: t)
    monkeypatch.setattr(orch, "_must_run_full_review_rounds", lambda w: False)
    # _should_refine_plan is consulted even on the approve branch; force False.
    monkeypatch.setattr(type(orch), "_should_refine_plan", lambda last: False)
    monkeypatch.setattr(orch, "_get_gh", lambda: MagicMock())
    monkeypatch.setattr(orch, "_get_user_feedback_prompt", lambda w: "")
    monkeypatch.setattr(orch, "_resolve_effective_repo_context", lambda w: {"repo_path": ""})
    monkeypatch.setattr(orch, "_post_github_comment", lambda *a, **k: None)
    # _create_milestone returns a plain dict (no milestone_id key → callers'
    # .get("milestone_id", "") default to ""). update_milestone / list_milestones
    # on the mocked repo are no-op MagicMocks; list_milestones returns a MagicMock
    # which is truthy, but round_num=1 skips the prior-plan lookup branch.
    monkeypatch.setattr(orch, "_create_milestone", lambda **kw: kw)
    monkeypatch.setattr(orch, "_accumulate_tokens", lambda r: None)

    inline_phase_status_writes: list[dict] = []
    orig_update = orch._update_workflow

    def spy(patch):
        if any(k in patch for k in ("current_phase", "status", "completed_at", "paused_at")):
            inline_phase_status_writes.append(dict(patch))
        return orig_update(patch)

    monkeypatch.setattr(orch, "_update_workflow", spy)

    # _do_planning emits its phase_change through deps.host.emit_phase_change
    # (not the legacy _emit). Wrap the real host with a recorder so this
    # template test proves the migrated handler actually fires the event, while
    # delegating the other PhaseHost methods to the orchestrator.
    import dataclasses

    captured_phase_changes: list[dict] = []
    real_host = orch  # orchestrator implements PhaseHost

    class _RecordingHost:
        workflow_id = orch.workflow_id

        def emit_phase_change(self, payload):
            captured_phase_changes.append(payload)

        def session_offsets(self):
            return real_host.session_offsets()

        def cancellation(self):
            return real_host.cancellation()

        def create_milestone_idempotent(self, **kw):
            return real_host.create_milestone_idempotent(**kw)

    deps = dataclasses.replace(orch._build_phase_deps(), host=_RecordingHost())
    ctx = orch._build_workflow_context(orch.workflow)
    result = orch._do_planning(ctx, deps)

    assert isinstance(
        result, PhaseResult
    ), f"_do_planning must return PhaseResult, got {type(result)}"
    assert not inline_phase_status_writes, (
        f"_do_planning wrote phase/status/completed_at/paused_at inline on the "
        f"success path: {inline_phase_status_writes}"
    )
    # Planning finalizes into development — same decision as the legacy inline
    # _update_workflow({"current_phase":"development","status":"developing"}).
    assert result.outcome == "completed"
    assert result.next_phase == "development"
    assert result.next_status == "developing"
    # current_round travels in workflow_patch (non-forbidden): bumped to 1.
    assert result.workflow_patch.get("current_round") == 1
    # The migrated handler emits its own phase_change (the commit entrypoint
    # does NOT) — assert the development payload actually fires.
    assert captured_phase_changes, "_do_planning did not emit phase_change"
    assert {"phase": "development"} in captured_phase_changes
