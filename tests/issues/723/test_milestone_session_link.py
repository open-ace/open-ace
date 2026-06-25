"""Tests for milestone card session-id linking (issue #723, group D).

The milestone card's "view session" should point to the REAL claude session id,
not the per-call wrapper uuid. Without this, all milestones sharing a session
line (e.g. plan_created/plan_refined/dev on the "main" line) showed DIFFERENT
ids (each call's wrapper uuid) even though they ran in the same claude session,
and the card pointed to a session with no transcript.

The fix: _run_agent links the milestone via result.source_session_id (the real
cli session) with fallback to result.session_id (wrapper).
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


class TestMilestoneLinksRealCliSession:
    def test_card_links_to_source_session_id_when_available(self):
        """When result.source_session_id (real cli session) is set, the milestone
        card is linked to it, not the wrapper uuid."""
        from app.modules.workspace.autonomous.models import AgentTaskResult

        wf = _make_workflow()
        orch, mock_repo = _make_orchestrator(wf)
        orch._runner = MagicMock()
        orch._runner._uses_sidebar_session_source.return_value = True
        # result carries BOTH: session_id (wrapper) and source_session_id (real cli)
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
        assert (
            linked_id == "real-cli-session-4448"
        ), "card must link to the real cli session id, not the wrapper uuid"

    def test_card_falls_back_to_wrapper_when_no_source(self):
        """When source_session_id is empty (e.g. session not yet resolved),
        fall back to the wrapper uuid so the card still links somewhere."""
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
        assert linked_id == "wrapper-uuid-2", "fallback to wrapper uuid when no real cli session"
