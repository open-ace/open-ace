"""Regression: _sync_usage_to_daily_usage must not mark sessions synced on failure.

Bug 8 (2026-08-21): increment_usage() failures were ignored and the session
was still flagged ``daily_usage_synced=True``, permanently discarding usage
data. The flag may only be set when the increment succeeds.
"""

from unittest.mock import MagicMock, patch

from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner
from app.modules.workspace.autonomous.models import AgentTaskResult


def _make_runner() -> tuple[AutonomousAgentRunner, MagicMock]:
    runner = AutonomousAgentRunner.__new__(AutonomousAgentRunner)
    session_manager = MagicMock()
    session_manager.get_session.return_value = None
    runner.session_manager = session_manager
    return runner, session_manager


def _make_result() -> AgentTaskResult:
    return AgentTaskResult(
        request_count=5,
        total_tokens=100,
        total_input_tokens=60,
        total_output_tokens=40,
    )


def test_increment_failure_leaves_synced_unset():
    runner, session_manager = _make_runner()

    with patch("app.modules.workspace.autonomous.agent_runner.UsageRepository") as repo_cls:
        repo_cls.return_value.increment_usage.return_value = False
        runner._sync_usage_to_daily_usage("sess-1", "qwen-code", 1, _make_result())

    session_manager.update_session_fields.assert_not_called()


def test_increment_success_marks_synced():
    runner, session_manager = _make_runner()

    with patch("app.modules.workspace.autonomous.agent_runner.UsageRepository") as repo_cls:
        repo_cls.return_value.increment_usage.return_value = True
        runner._sync_usage_to_daily_usage("sess-1", "qwen-code", 1, _make_result())

    session_manager.update_session_fields.assert_called_once_with(
        "sess-1", {"daily_usage_synced": True}, require_tenant=False
    )
