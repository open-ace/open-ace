"""Systematic resume no-op backstop in _run_agent_with_context_recovery.

A --resume'd session line whose last turn already ended can return
success=True with ~0 tokens and empty artifact text when a stale
background-shell notification makes the model emit a result-without-turn.
Callers then terminal-fail the workflow with "<agent> returned no result".
This suite pins the Fix B backstop: when a RESUMED named line returns
success-but-empty, the method retries once with force_fresh=True on the
same session line. Fresh runs, genuine failures, and overflow/integrity
paths are never retried. Refs #2570.
"""

from unittest.mock import MagicMock

import pytest

from app.modules.workspace.autonomous.models import AgentTaskResult

WF = {
    "workflow_id": "wf-resume-noop",
    "cli_tool": "claude-code",
    "worktree_path": "/tmp/repo",
    "project_path": "/tmp/repo",
}


def _make_orchestrator(*, resume: bool = True):
    """Bare orchestrator with the agent-run internals mocked.

    _resolve_session_line reports whether the upcoming run would resume an
    established session line (the precondition for the no-op retry).
    """
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._workflow_id = "wf-resume-noop"
    orch._is_context_overflow = MagicMock(return_value=False)
    orch._accumulate_tokens = MagicMock()
    orch._resolve_session_line = MagicMock(return_value=("tracking-main", "cli-main", resume))
    return orch


@pytest.mark.regression
@pytest.mark.issue(2570)
def test_resumed_empty_success_retries_once_fresh_and_returns_second_result():
    """Resumed 'main' line + success + 0 tokens + empty text -> retried once
    with force_fresh=True (same session_line/prompt); the second result is
    returned to the caller."""
    orch = _make_orchestrator(resume=True)
    empty_noop = AgentTaskResult(success=True, response_text="", total_tokens=0)
    recovered = AgentTaskResult(
        success=True, response_text="Completed the requested work.", total_tokens=120
    )
    orch._run_agent = MagicMock(side_effect=[empty_noop, recovered])

    result = orch._run_agent_with_context_recovery(
        wf=dict(WF), session_line="main", milestone_id="ms-1", prompt="do the work"
    )

    # The recovered (non-empty) result is what the caller receives.
    assert result is recovered
    assert result.response_text == "Completed the requested work."
    # Exactly one retry — no loop.
    assert orch._run_agent.call_count == 2
    first_kwargs = orch._run_agent.call_args_list[0].kwargs
    second_kwargs = orch._run_agent.call_args_list[1].kwargs
    # The retry forces a fresh provider transcript on the SAME session line.
    assert second_kwargs.get("force_fresh") is True
    assert second_kwargs.get("session_line") == "main"
    # Same prompt (same kwargs contract) is replayed.
    assert second_kwargs.get("prompt") == "do the work"
    assert second_kwargs.get("milestone_id") == "ms-1"
    # The first attempt was the plain (resumed) run.
    assert first_kwargs.get("force_fresh") is not True


@pytest.mark.regression
@pytest.mark.issue(2570)
def test_fresh_session_line_empty_success_is_not_retried():
    """session_line='fresh' + success-but-empty -> NOT retried; the first
    (empty) result is returned so the caller's fail-closed path still sees
    the emptiness."""
    orch = _make_orchestrator()
    empty_noop = AgentTaskResult(success=True, response_text="", total_tokens=0)
    orch._run_agent = MagicMock(return_value=empty_noop)

    result = orch._run_agent_with_context_recovery(
        wf=dict(WF), session_line="fresh", prompt="do the work"
    )

    assert result is empty_noop
    assert orch._run_agent.call_count == 1


@pytest.mark.regression
@pytest.mark.issue(2570)
def test_force_fresh_run_empty_success_is_not_retried():
    """An explicit force_fresh=True run cannot be a resume no-op and must not
    be retried even when the named line would otherwise resolve to resume."""
    orch = _make_orchestrator(resume=True)
    empty_noop = AgentTaskResult(success=True, response_text="", total_tokens=0)
    orch._run_agent = MagicMock(return_value=empty_noop)

    result = orch._run_agent_with_context_recovery(
        wf=dict(WF), session_line="main", prompt="do the work", force_fresh=True
    )

    assert result is empty_noop
    assert orch._run_agent.call_count == 1
    # The single call forwarded the caller's force_fresh=True.
    assert orch._run_agent.call_args.kwargs.get("force_fresh") is True


@pytest.mark.regression
@pytest.mark.issue(2570)
def test_named_line_without_resume_target_empty_success_is_not_retried():
    """A named line whose mapping is lost (resume=False) starts fresh on the
    same line — that run cannot resume-no-op and must not be retried."""
    orch = _make_orchestrator(resume=False)
    empty_noop = AgentTaskResult(success=True, response_text="", total_tokens=0)
    orch._run_agent = MagicMock(return_value=empty_noop)

    result = orch._run_agent_with_context_recovery(
        wf=dict(WF), session_line="review", prompt="review the PR"
    )

    assert result is empty_noop
    assert orch._run_agent.call_count == 1


@pytest.mark.regression
@pytest.mark.issue(2570)
def test_genuine_failure_is_not_retried():
    """success=False (agent process failure / integrity path) takes the
    existing failure path — the agent runs exactly ONCE."""
    orch = _make_orchestrator(resume=True)
    failed = AgentTaskResult(success=False, error="agent process exited 1")
    orch._run_agent = MagicMock(return_value=failed)

    result = orch._run_agent_with_context_recovery(
        wf=dict(WF), session_line="main", prompt="do the work"
    )

    assert result is failed
    assert result.success is False
    assert orch._run_agent.call_count == 1


@pytest.mark.regression
@pytest.mark.issue(2570)
def test_fresh_retry_also_empty_surfaces_empty_result_without_second_retry():
    """Fail-closed preserved: if the force_fresh retry is ALSO empty, the
    method returns that empty-but-successful result (the caller's existing
    'returned no result' path still sees emptiness) and does not retry again."""
    orch = _make_orchestrator(resume=True)
    empty_noop = AgentTaskResult(success=True, response_text="", total_tokens=0)
    empty_retry = AgentTaskResult(success=True, response_text="   ", total_tokens=0)
    orch._run_agent = MagicMock(side_effect=[empty_noop, empty_retry])

    result = orch._run_agent_with_context_recovery(
        wf=dict(WF), session_line="main", prompt="do the work"
    )

    # The empty retry result is surfaced — not a fabricated success.
    assert result is empty_retry
    assert result.success is True
    assert not (result.response_text or "").strip()
    # Exactly one retry — no second retry loop.
    assert orch._run_agent.call_count == 2
