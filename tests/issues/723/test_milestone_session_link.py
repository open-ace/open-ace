"""Tests for milestone session identity on autonomous session lines.

Milestones should persist the stable workflow tracking session id for the
main/review/test line. The UI resolves the real provider transcript later via
agent_sessions.cli_session_id, so workflow_milestones never mixes tracking ids
with provider ids.
"""

from unittest.mock import MagicMock, patch

import pytest


def _make_workflow(**overrides):
    base = {
        "workflow_id": "wf-d",
        "user_id": 1,
        "title": "T",
        "status": "developing",
        "requirements_text": "x",
        "cli_tool": "claude-code",
        "model": "claude-sonnet-4-6",
        "permission_mode": "auto-edit",
        "branch_name": "b",
        "worktree_path": "/tmp/p",
        "project_path": "/tmp/p",
        "workspace_type": "local",
        "current_phase": "development",
        "current_round": 1,
        "dev_round": 1,
        "error_message": "",
    }
    base.update(overrides)
    return base


def _make_orchestrator(wf_data, milestones=None):
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    with (
        patch("app.modules.workspace.autonomous.orchestrator.Database"),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
        ) as mock_repo_cls,
    ):
        mock_repo = MagicMock()
        mock_repo.get_workflow.return_value = wf_data
        mock_repo.list_milestones.return_value = milestones or []
        mock_repo.create_milestone.return_value = {
            "milestone_id": "ms-1",
            "workflow_id": wf_data["workflow_id"],
        }
        mock_repo.update_workflow.return_value = wf_data
        mock_repo.update_milestone.return_value = {}
        mock_repo.update_workflow_tokens.return_value = None
        mock_repo_cls.return_value = mock_repo

        orch = AutonomousOrchestrator(wf_data["workflow_id"])
        orch.repo = mock_repo
        orch.emitter = MagicMock()
        orch._gh = MagicMock()
        return orch, mock_repo


class TestMilestoneTracksWorkflowSession:
    def test_sidebar_sessions_link_milestone_to_tracking_id(self):
        """Claude workflow milestones keep the stable tracking session id."""
        from app.modules.workspace.autonomous.models import AgentTaskResult

        wf = _make_workflow()
        orch, mock_repo = _make_orchestrator(wf)
        orch._runner = MagicMock()
        orch._runner._uses_sidebar_session_source.return_value = True
        # result carries BOTH: session_id (tracking wrapper) and
        # source_session_id (real provider session)
        orch._runner.run_agent_task.return_value = AgentTaskResult(
            session_id="wrapper-uuid-1",
            tracking_session_id="wrapper-uuid-1",
            source_session_id="real-cli-session-4448",
            response_text="done",
            visible_response_text="done",
            success=True,
        )

        # Create an in_progress milestone for _link_session_to_current_milestone
        mock_repo.list_milestones.return_value = [
            {"milestone_id": "ms-active", "milestone_type": "dev_started", "status": "in_progress"}
        ]

        orch._run_agent(
            wf=wf,
            workflow_id=wf["workflow_id"],
            cli_tool="claude-code",
            model="m",
            project_path="/tmp/p",
            prompt="do it",
            workspace_type="local",
            session_line="main",
            milestone_id="ms-active",
        )

        # Find the update_milestone call that set session_id on the milestone.
        link_calls = [
            c
            for c in mock_repo.update_milestone.call_args_list
            if len(c[0]) > 1
            and isinstance(c[0][1], dict)
            and ("session_id" in c[0][1] or "review_session_id" in c[0][1])
        ]
        assert link_calls, "milestone should have been linked to a session id"
        linked_id = link_calls[0][0][1].get("session_id") or link_calls[0][0][1].get(
            "review_session_id"
        )
        assert linked_id == "wrapper-uuid-1"

    def test_sidebar_sessions_keep_tracking_id_when_provider_not_resolved(self):
        """If provider resolution is missing, milestone still keeps tracking id."""
        from app.modules.workspace.autonomous.models import AgentTaskResult

        wf = _make_workflow()
        orch, mock_repo = _make_orchestrator(wf)
        orch._runner = MagicMock()
        orch._runner._uses_sidebar_session_source.return_value = True
        orch._runner.run_agent_task.return_value = AgentTaskResult(
            session_id="wrapper-uuid-2",
            tracking_session_id="wrapper-uuid-2",
            source_session_id="",  # not resolved
            response_text="done",
            visible_response_text="done",
            success=True,
        )
        mock_repo.list_milestones.return_value = [
            {"milestone_id": "ms-active", "milestone_type": "dev_started", "status": "in_progress"}
        ]

        orch._run_agent(
            wf=wf,
            workflow_id=wf["workflow_id"],
            cli_tool="claude-code",
            model="m",
            project_path="/tmp/p",
            prompt="do it",
            workspace_type="local",
            session_line="main",
            milestone_id="ms-active",
        )

        link_calls = [
            c
            for c in mock_repo.update_milestone.call_args_list
            if len(c[0]) > 1
            and isinstance(c[0][1], dict)
            and ("session_id" in c[0][1] or "review_session_id" in c[0][1])
        ]
        assert link_calls
        linked_id = link_calls[0][0][1].get("session_id") or link_calls[0][0][1].get(
            "review_session_id"
        )
        assert linked_id == "wrapper-uuid-2"
