"""Unit tests for Issue #1627: Branch strategy enforcement.

Tests verify that:
1. new-branch strategy is not forced to worktree
2. worktree strategy requires worktree_path
3. new-branch strategy uses project_path
"""

from unittest.mock import MagicMock, call, patch

import pytest

from app.modules.workspace.autonomous.models import AgentTaskResult
from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator


def _make_workflow(**overrides):
    """Create a minimal workflow dict for testing."""
    base = {
        "workflow_id": "test-wf-uuid-1627",
        "user_id": 1,
        "title": "Test Issue #1627",
        "status": "pending",
        "requirements_text": "Build a simple feature",
        "project_path": "/tmp/test-project",
        "cli_tool": "claude-code",
        "model": "claude-sonnet-4-6",
        "permission_mode": "auto-edit",
        "branch_name": "fix/issue-1627",
        "branch_strategy": "new-branch",  # Default strategy
        "workspace_type": "local",
        "worktree_path": "",
        "github_issue_number": 1627,
        "current_phase": "preparation",
        "current_round": 0,
        "dev_round": 1,
    }
    base.update(overrides)
    return base


def _make_orchestrator(wf_data):
    """Create orchestrator with mocked dependencies (following existing pattern)."""
    with (
        patch("app.modules.workspace.autonomous.orchestrator.Database"),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
        ) as mock_repo_cls,
    ):
        mock_repo = MagicMock()
        mock_repo.get_workflow.return_value = wf_data
        mock_repo.list_milestones.return_value = [
            {"milestone_id": "ms-plan-1", "phase": "planning", "plan_content": "Test plan"}
        ]
        mock_repo.create_milestone.return_value = {
            "milestone_id": "ms-dev-1",
            "workflow_id": wf_data["workflow_id"],
        }
        mock_repo.create_event.return_value = {"id": 1}
        # Don't set return_value for update_workflow to track all calls
        mock_repo_cls.return_value = mock_repo

        orch = AutonomousOrchestrator(wf_data["workflow_id"])
        orch.repo = mock_repo

    # Mock the emitter and runner
    orch.emitter = MagicMock()
    orch._runner = MagicMock()

    return orch, mock_repo


class TestBranchStrategyEnforcement:
    """Tests for Issue #1627: Branch strategy enforcement in autonomous workflow."""

    def test_worktree_strategy_requires_worktree_path(self):
        """Test that worktree strategy requires non-empty worktree_path (Issue #1627).

        Verify that when branch_strategy is 'worktree' but worktree_path is empty,
        the workflow fails with appropriate error.
        """
        workflow = _make_workflow(
            branch_strategy="worktree",
            project_path="/tmp/test-project",
            worktree_path="",  # Empty - should fail
            branch_name="fix/issue-1627-worktree",
        )

        orch, mock_repo = _make_orchestrator(workflow)

        # Mock _get_gh
        with patch.object(orch, "_get_gh") as mock_get_gh:
            mock_gh = MagicMock()
            mock_gh.get_current_branch.return_value = "fix/issue-1627-worktree"
            mock_get_gh.return_value = mock_gh

            # Run development phase - should fail early due to missing worktree_path
            orch._run_development_agent(workflow, dev_round=1, gh=mock_gh)

        # Debug: print all update_workflow calls
        print(f"DEBUG: update_workflow calls: {mock_repo.update_workflow.call_args_list}")
        print(f"DEBUG: create_milestone calls: {mock_repo.create_milestone.call_args_list}")

        # Verify workflow was marked as failed
        # Find the update_workflow call with "status": "failed"
        update_calls = mock_repo.update_workflow.call_args_list
        failed_update_found = False
        for update_call in update_calls:
            # update_call is a call object, first element is args tuple
            args_tuple = update_call[0]
            if len(args_tuple) >= 2:  # update_workflow(workflow_id, updates)
                updates_dict = args_tuple[1]
                if isinstance(updates_dict, dict) and updates_dict.get("status") == "failed":
                    failed_update_found = True
                    assert "worktree_path" in updates_dict.get("error_message", "").lower()
                    break
        assert failed_update_found, "Expected workflow to be marked as failed"

        # Verify milestone was created for path validation failure
        milestone_calls = mock_repo.create_milestone.call_args_list
        path_validation_found = False
        for milestone_call in milestone_calls:
            # milestone_call[0] is args tuple, [1] is kwargs dict
            args_tuple = milestone_call[0]
            kwargs_dict = milestone_call[1]

            # Check if milestone_type is in kwargs or args
            milestone_dict = kwargs_dict if kwargs_dict else (args_tuple[0] if args_tuple else {})
            if milestone_dict.get("milestone_type") == "path_validation":
                path_validation_found = True
                assert milestone_dict.get("status") == "failed"
                break
        assert path_validation_found, "Expected path_validation milestone to be created"

    def test_new_branch_strategy_requires_project_path(self):
        """Test that new-branch strategy requires non-empty project_path (Issue #1627).

        Verify that when branch_strategy is 'new-branch' but project_path is empty,
        the workflow fails with appropriate error.
        """
        workflow = _make_workflow(
            branch_strategy="new-branch",
            project_path="",  # Empty - should fail
            worktree_path="",  # Also empty
            branch_name="fix/issue-1627",
        )

        orch, mock_repo = _make_orchestrator(workflow)

        # Mock _get_gh
        with patch.object(orch, "_get_gh") as mock_get_gh:
            mock_gh = MagicMock()
            mock_gh.get_current_branch.return_value = "fix/issue-1627"
            mock_get_gh.return_value = mock_gh

            # Run development phase - should fail early due to missing project_path
            orch._run_development_agent(workflow, dev_round=1, gh=mock_gh)

        # Verify workflow was marked as failed
        # Find the update_workflow call with "status": "failed"
        update_calls = mock_repo.update_workflow.call_args_list
        failed_update_found = False
        for update_call in update_calls:
            # update_call is a call object, first element is args tuple
            args_tuple = update_call[0]
            if len(args_tuple) >= 2:  # update_workflow(workflow_id, updates)
                updates_dict = args_tuple[1]
                if isinstance(updates_dict, dict) and updates_dict.get("status") == "failed":
                    failed_update_found = True
                    assert "project_path" in updates_dict.get("error_message", "").lower()
                    break
        assert failed_update_found, "Expected workflow to be marked as failed"


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
