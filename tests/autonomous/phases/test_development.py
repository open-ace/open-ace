"""DevelopmentPhase handler unit tests (#2044 Phase B T12).

These tests prove the handler is independently testable: they pass fakes via
``PhaseDeps`` and NEVER construct ``AutonomousOrchestrator``. The decoupling
surface they exercise (the host aliases the handler actually calls) is the real
measure of how far the top-level orchestration of the development phase has been
decoupled from the ~10k-line orchestrator concrete class.

Coverage:
- registered + resolves to phases.development.handle
- dev-failure path → run_development_agent parks status=failed → PhaseResult.retry
  + post_dev_completion_comment NOT called (the #525 / #1140 guard)
- test-only retry (test_retries>0) → skips dev agent → runs only test phase
- skip-retry (skip_retries>0) → skips dev agent → runs only test phase
- test-phase failure → status=failed → PhaseResult.retry
- test-phase success → status=pr_review + current_phase=pr_review →
  PhaseResult.completed(next_phase="pr_review", next_status="pr_review",
  workflow_patch={"current_round": 0})
- test-phase parks on development (retry/skip/dev-retry bump) →
  PhaseResult.retry (phase unchanged, scheduler re-enters development)
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

from app.modules.workspace.autonomous import phases as phases_pkg
from app.modules.workspace.autonomous.phase_contract import PhaseResult, WorkflowContext
from app.modules.workspace.autonomous.phases import development as development_phase


def _ctx(workflow: dict) -> WorkflowContext:
    return WorkflowContext(
        workflow=workflow,
        definition_snapshot=None,
        repository_context=None,
        session_bindings={},
        cancellation=threading.Event(),
    )


def _host(**overrides) -> MagicMock:
    """Build a host fake. Defaults satisfy the dev-failure path; tests override
    the methods/return values they need."""
    host = MagicMock(name="host")
    host.workflow_id = "wf-test"
    host.run_development_agent.return_value = None
    host.post_dev_completion_comment.return_value = None
    host.run_test_phase.return_value = None
    # Default refreshed workflow: dev succeeded, test phase advanced to pr_review.
    host.refresh_workflow_snapshot.return_value = {
        "status": "pr_review",
        "current_phase": "pr_review",
    }
    for k, v in overrides.items():
        if isinstance(v, MagicMock) or callable(v):
            setattr(host, k, v)
        else:
            setattr(host, k, MagicMock(return_value=v))
    return host


def _deps(host: MagicMock) -> MagicMock:
    deps = MagicMock(name="deps")
    deps.host = host
    deps.gh = MagicMock(name="gh")
    return deps


def _workflow(**overrides) -> dict:
    wf = {
        "workflow_id": "wf-test",
        "current_phase": "development",
        "status": "developing",
        "dev_round": 1,
        "test_retries": 0,
        "skip_retries": 0,
        "branch_name": "auto-dev/wf-test",
    }
    wf.update(overrides)
    return wf


# ── registration ─────────────────────────────────────────────────────────


def test_development_handle_is_registered():
    """The development phase must resolve to phases.development.handle."""
    assert phases_pkg.resolve_phase_handler("development") is development_phase.handle


# ── dev-failure path → retry + skip completion comment ────────────────────


def test_dev_failure_returns_retry_and_skips_completion_comment():
    """When run_development_agent parks status=failed, the handler must return
    PhaseResult.retry (phase unchanged; advance()'s convergence point reclaims
    the worktree) and MUST NOT call post_dev_completion_comment (the #525/#1140
    guard — a 'Completed' comment with a stale commit is false advertising)."""
    host = _host(
        refresh_workflow_snapshot=MagicMock(
            side_effect=[
                # After run_development_agent: status=failed (written inline by
                # the dev sub-method).
                {"status": "failed", "current_phase": "development"},
            ]
        )
    )
    deps = _deps(host)

    result = development_phase.handle(_ctx(_workflow()), deps)

    assert isinstance(result, PhaseResult)
    assert result.outcome == "retry"
    assert result.next_phase is None
    host.run_development_agent.assert_called_once()
    host.post_dev_completion_comment.assert_not_called()
    host.run_test_phase.assert_not_called()


# ── test-only retry skips dev agent ───────────────────────────────────────


def test_test_retry_skips_dev_agent_and_runs_only_test_phase():
    """test_retries>0 means the dev phase already completed; only the test step
    is re-run. The handler must skip run_development_agent AND
    post_dev_completion_comment, and call run_test_phase."""
    host = _host(
        refresh_workflow_snapshot=MagicMock(
            return_value={"status": "pr_review", "current_phase": "pr_review"}
        )
    )
    deps = _deps(host)

    result = development_phase.handle(_ctx(_workflow(test_retries=1)), deps)

    assert result.outcome == "completed"
    assert result.next_phase == "pr_review"
    assert result.next_status == "pr_review"
    host.run_development_agent.assert_not_called()
    host.post_dev_completion_comment.assert_not_called()
    host.run_test_phase.assert_called_once()


def test_skip_retry_skips_dev_agent_and_runs_only_test_phase():
    """skip_retries>0 means tests were skipped; the dev agent already ran. Same
    contract as test_retries: skip dev, run only the test phase."""
    host = _host(
        refresh_workflow_snapshot=MagicMock(
            return_value={"status": "pr_review", "current_phase": "pr_review"}
        )
    )
    deps = _deps(host)

    result = development_phase.handle(_ctx(_workflow(skip_retries=1)), deps)

    assert result.outcome == "completed"
    host.run_development_agent.assert_not_called()
    host.post_dev_completion_comment.assert_not_called()
    host.run_test_phase.assert_called_once()


# ── success path: dev + comment + test-phase advances to pr_review ────────


def test_dev_success_then_test_success_advances_to_pr_review():
    """Dev succeeds (status stays non-failed), completion comment posted, test
    phase advances the workflow to pr_review → PhaseResult.completed(
    next_phase='pr_review', next_status='pr_review', current_round=0).

    The test-phase sub-method already wrote current_phase=pr_review +
    status=pr_review inline; the PhaseResult mirrors that transition
    idempotently through the unified-commit entrypoint."""
    host = _host(
        refresh_workflow_snapshot=MagicMock(
            side_effect=[
                # After run_development_agent: dev succeeded (status unchanged).
                {"status": "developing", "current_phase": "development"},
                # After run_test_phase: advanced to pr_review.
                {"status": "pr_review", "current_phase": "pr_review"},
            ]
        )
    )
    deps = _deps(host)

    result = development_phase.handle(_ctx(_workflow()), deps)

    assert isinstance(result, PhaseResult)
    assert result.outcome == "completed"
    assert result.next_phase == "pr_review"
    assert result.next_status == "pr_review"
    assert result.workflow_patch == {"current_round": 0}
    host.run_development_agent.assert_called_once()
    host.post_dev_completion_comment.assert_called_once()
    host.run_test_phase.assert_called_once()


# ── test-phase failure → retry ────────────────────────────────────────────


def test_test_phase_failure_returns_retry():
    """When run_test_phase parks status=failed (test agent exhausted retries /
    unfixable failures / skipped-after-retry), the handler returns
    PhaseResult.retry — phase unchanged, advance()'s convergence point reclaims
    the worktree."""
    host = _host(
        refresh_workflow_snapshot=MagicMock(
            side_effect=[
                # After run_development_agent: dev succeeded.
                {"status": "developing", "current_phase": "development"},
                # After run_test_phase: failed.
                {"status": "failed", "current_phase": "development"},
            ]
        )
    )
    deps = _deps(host)

    result = development_phase.handle(_ctx(_workflow()), deps)

    assert result.outcome == "retry"
    assert result.next_phase is None
    host.run_development_agent.assert_called_once()
    host.post_dev_completion_comment.assert_called_once()
    host.run_test_phase.assert_called_once()


# ── test-phase retry bump → phase stays on development ────────────────────


def test_test_phase_retry_bump_keeps_phase_on_development():
    """When run_test_phase bumps test_retries/skip_retries/dev_round and leaves
    the phase on development (no advance), the handler returns
    PhaseResult.retry — phase unchanged, scheduler re-enters development."""
    host = _host(
        refresh_workflow_snapshot=MagicMock(
            side_effect=[
                # After run_development_agent: dev succeeded.
                {"status": "developing", "current_phase": "development"},
                # After run_test_phase: parked for another cycle (test retry).
                {"status": "developing", "current_phase": "development"},
            ]
        )
    )
    deps = _deps(host)

    result = development_phase.handle(_ctx(_workflow()), deps)

    assert result.outcome == "retry"
    assert result.next_phase is None
    host.run_test_phase.assert_called_once()
