"""Regression tests for upstream provider quota exhaustion handling."""

import threading
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.models import AgentTaskResult
from app.modules.workspace.autonomous.orchestrator import (
    UPSTREAM_QUOTA_PAUSE_REASON_PREFIX,
    AutonomousOrchestrator,
    UpstreamQuotaPaused,
    WorkflowPaused,
)


def _result(**updates) -> AgentTaskResult:
    values = {
        "session_id": "session-1",
        "success": False,
        "error": "API Error: 429 · Platform quota exceeded. Please wait.",
    }
    values.update(updates)
    return AgentTaskResult(**values)


def _orchestrator_for_run(result: AgentTaskResult) -> AutonomousOrchestrator:
    orchestrator = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orchestrator._workflow_id = "workflow-1"
    orchestrator.repo = MagicMock()
    orchestrator.emitter = MagicMock()
    orchestrator._runner = MagicMock()
    orchestrator._runner._uses_sidebar_session_source.return_value = True
    orchestrator._runner.run_agent_task.return_value = result
    orchestrator._resolve_session_line = MagicMock(return_value=("session-1", "", False))
    orchestrator._resolve_effective_repo_context = MagicMock(
        return_value={"repo_path": "/tmp/worktree"}
    )
    orchestrator._resolve_system_account = MagicMock(return_value="openace-agent")
    orchestrator._snapshot_repo_context = MagicMock(return_value=None)
    orchestrator._validate_repo_context_after_run = MagicMock(return_value="")
    orchestrator._select_project_python_runtime = MagicMock(return_value=(None, ""))
    orchestrator._link_session_to_current_milestone = MagicMock()
    orchestrator._write_phase_usage = MagicMock()
    orchestrator._clear_session_usage_offsets = MagicMock()
    orchestrator._build_repo_execution_contract = MagicMock(return_value="")
    orchestrator._session_lock = threading.Lock()
    orchestrator._session_usage_offsets = {}
    orchestrator._current_session_id = None
    orchestrator._cancel_requested = threading.Event()
    return orchestrator


def test_provider_allocation_exhaustion_is_not_transient_retry():
    result = _result()

    assert AutonomousOrchestrator._is_upstream_quota_exhausted(result)
    assert not AutonomousOrchestrator._should_retry_transient_api_failure(result)


def test_zero_token_provider_envelope_is_detected():
    result = _result(
        success=True,
        error=None,
        total_tokens=0,
        response_text="usage allocated quota exceeded. please try again later.",
    )

    assert AutonomousOrchestrator._is_upstream_quota_exhausted(result)


def test_token_bearing_design_text_does_not_pause():
    result = _result(
        success=True,
        error=None,
        total_tokens=120,
        response_text="Handle 'Platform quota exceeded' by showing a banner.",
    )

    assert not AutonomousOrchestrator._is_upstream_quota_exhausted(result)


def test_pause_persists_workflow_milestone_and_event():
    orchestrator = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orchestrator._workflow_id = "workflow-1"
    orchestrator.repo = MagicMock()
    orchestrator.emitter = MagicMock()
    result = _result()

    orchestrator._pause_for_upstream_quota(result, "milestone-1")

    milestone_update = orchestrator.repo.update_milestone.call_args.args[1]
    assert milestone_update["status"] == "failed"
    assert milestone_update["error_message"].startswith(UPSTREAM_QUOTA_PAUSE_REASON_PREFIX)

    workflow_update = orchestrator.repo.update_workflow.call_args.args[1]
    assert workflow_update["status"] == "paused"
    assert workflow_update["agent_pid"] is None
    assert workflow_update["error_message"].startswith(UPSTREAM_QUOTA_PAUSE_REASON_PREFIX)
    assert result.error_code == "upstream_quota_exhausted"
    emitted_types = [call.args[1] for call in orchestrator.emitter.emit.call_args_list]
    assert emitted_types == ["workflow_updated", "upstream_quota_paused"]


def test_run_agent_pauses_immediately_without_retry():
    orchestrator = _orchestrator_for_run(_result())

    with pytest.raises(UpstreamQuotaPaused):
        orchestrator._run_agent(
            wf={"user_id": 1, "content_language": "en"},
            session_line="main",
            milestone_id="milestone-1",
            workspace_type="remote",
            project_path="/tmp/worktree",
            prompt="do work",
        )

    orchestrator._runner.run_agent_task.assert_called_once()
    updates = [call.args[1] for call in orchestrator.repo.update_workflow.call_args_list]
    assert any(update.get("status") == "paused" for update in updates)


def test_manual_pause_interrupts_backoff_before_second_agent_attempt():
    result = _result(
        error="API Error: 503 Service unavailable",
        response_text="",
    )
    orchestrator = _orchestrator_for_run(result)
    orchestrator.repo.get_workflow.side_effect = [
        {"status": "developing"},
        {"status": "paused"},
    ]

    with patch("app.modules.workspace.autonomous.orchestrator.time.sleep"):
        with pytest.raises(WorkflowPaused, match="Workflow paused during API error retry"):
            orchestrator._run_agent(
                wf={"user_id": 1, "content_language": "en"},
                session_line="main",
                milestone_id="milestone-1",
                workspace_type="remote",
                project_path="/tmp/worktree",
                prompt="do work",
            )

    orchestrator._runner.run_agent_task.assert_called_once()
    milestone_update = orchestrator.repo.update_milestone.call_args.args[1]
    assert milestone_update["status"] == "cancelled"


def test_advance_treats_quota_pause_as_control_flow():
    orchestrator = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orchestrator._workflow_id = "workflow-1"
    orchestrator.repo = MagicMock()
    orchestrator.repo.get_workflow.return_value = {
        "workflow_id": "workflow-1",
        "status": "developing",
        "current_phase": "development",
        "worktree_path": "/tmp/worktree",
    }
    orchestrator._ensure_worktree = MagicMock()
    orchestrator._do_development = MagicMock(side_effect=UpstreamQuotaPaused("provider quota"))

    orchestrator.advance()

    assert not any(
        call.args[1].get("status") == "failed"
        for call in orchestrator.repo.update_workflow.call_args_list
    )


def test_postgres_alert_dedup_casts_text_metadata_to_jsonb():
    from app.modules.governance.alert_notifier import AlertNotifier

    notifier = AlertNotifier.__new__(AlertNotifier)
    connection = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.return_value = {"count": 0}
    connection.cursor.return_value = cursor
    notifier._get_connection = MagicMock(return_value=connection)

    with patch("app.modules.governance.alert_notifier.is_postgresql", return_value=True):
        assert not notifier.has_recent_quota_alert(1, "platform")

    query = cursor.execute.call_args.args[0]
    assert "(metadata::jsonb)->>'quota_type'" in query
