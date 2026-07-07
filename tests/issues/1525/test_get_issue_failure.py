"""Test for Issue #1525: get_issue failure handling.

When GitHubOps.get_issue() fails, the workflow should:
1. Create a failed milestone with issue_linked type
2. Raise exception to terminate workflow (not silently continue)
"""

from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.github_ops import GitHubOpsError


def _make_workflow(**overrides):
    """Create a minimal workflow dict for testing."""
    base = {
        "workflow_id": "test-wf-uuid",
        "user_id": 1,
        "title": "Test Workflow",
        "status": "pending",
        "requirements_text": "Build a simple feature",
        "requirements_issue_url": "",
        "project_path": "/tmp/test-project",
        "project_repo_url": "",
        "is_new_project": False,
        "cli_tool": "claude-code",
        "model": "claude-sonnet-4-6",
        "permission_mode": "auto-edit",
        "branch_name": "",
        "branch_strategy": "new-branch",
        "workspace_type": "local",
        "remote_machine_id": "",
        "worktree_path": "",
        "github_issue_number": None,
        "github_pr_number": None,
        "github_pr_url": "",
        "current_phase": "preparation",
        "current_round": 0,
        "dev_round": 1,
        "max_plan_rounds": 3,
        "max_pr_review_rounds": 5,
        "require_full_review_rounds": False,
        "total_tokens": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_requests": 0,
        "error_message": "",
    }
    base.update(overrides)
    return base


def _make_orchestrator(wf_data):
    """Create orchestrator with mocked dependencies."""
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    with (
        patch("app.modules.workspace.autonomous.orchestrator.Database"),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
        ) as mock_repo_cls,
    ):
        mock_repo = MagicMock()
        mock_repo.get_workflow.return_value = wf_data
        mock_repo.list_milestones.return_value = []
        mock_repo.create_milestone.return_value = {
            "milestone_id": "ms-new",
            "workflow_id": wf_data["workflow_id"],
        }
        mock_repo.create_event.return_value = {"id": 1}
        mock_repo.update_workflow.return_value = wf_data
        mock_repo_cls.return_value = mock_repo

        orch = AutonomousOrchestrator(wf_data["workflow_id"])
        orch.repo = mock_repo
        orch.emitter = MagicMock()

    return orch, mock_repo


class TestGetIssueFailureHandling:
    """Tests for Issue #1525: get_issue failure creates milestone and raises."""

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_get_issue_failure_creates_milestone_and_raises(self, mock_gh_cls):
        """When get_issue() fails, should create failed milestone and raise
        to terminate workflow."""
        wf = _make_workflow(
            current_phase="preparation",
            requirements_text="",  # Empty so issue URL branch is taken
            requirements_issue_url="https://github.com/user/repo/issues/99",
        )
        orch, mock_repo = _make_orchestrator(wf)

        mock_gh = MagicMock()
        mock_gh.get_issue.side_effect = GitHubOpsError("HTTP 422: Invalid request")
        mock_gh_cls.return_value = mock_gh
        orch._gh = mock_gh

        with pytest.raises(GitHubOpsError, match="HTTP 422"):
            orch._do_preparation(wf)

        # Should create failed milestone with correct parameters
        milestone_calls = mock_repo.create_milestone.call_args_list
        failed_milestones = [
            c
            for c in milestone_calls
            if c[0][0].get("milestone_type") == "issue_linked" and c[0][0].get("status") == "failed"
        ]
        assert len(failed_milestones) == 1
        ms = failed_milestones[0][0][0]
        assert ms["phase"] == "preparation"
        assert ms["title"] == "Failed to read issue #99"
        assert ms["github_issue_number"] == 99
        assert "HTTP 422" in ms["error_message"]

        # Workflow should NOT transition to planning (terminated early)
        update_calls = mock_repo.update_workflow.call_args_list
        phases = [c[0][1].get("current_phase") for c in update_calls if "current_phase" in c[0][1]]
        assert "planning" not in phases
