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

        o.advance()

        spies[method].assert_called_once()
        # No other _do_* fired.
        for name in (
            "_do_preparation",
            "_do_planning",
            "_do_development",
            "_do_pr_review",
            "_do_report",
            "_do_wait",
            "_do_merge",
        ):
            if name != method:
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

        o.advance()

        spies["_do_pr_review"].assert_called_once()
        spies["_do_development"].assert_not_called()


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
