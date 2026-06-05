"""Unit tests for AutonomousOrchestrator state machine."""

import json
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from app.modules.workspace.autonomous.models import AgentTaskResult


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
        "total_tokens": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_requests": 0,
        "error_message": "",
    }
    base.update(overrides)
    return base


def _make_agent_result(success=True, text="Done", tokens=100, error=None):
    return AgentTaskResult(
        session_id="sess-1",
        response_text=text,
        total_tokens=tokens,
        total_input_tokens=tokens // 2,
        total_output_tokens=tokens // 2,
        success=success,
        error=error,
    )


class TestOrchestratorInit:
    """Tests for orchestrator initialization."""

    @patch("app.modules.workspace.autonomous.orchestrator.Database")
    @patch("app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository")
    def test_init(self, mock_repo_cls, mock_db_cls):
        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo
        mock_db_cls.return_value = MagicMock()

        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        orch = AutonomousOrchestrator("test-wf")
        assert orch._workflow_id == "test-wf"


class TestOrchestratorAdvance:
    """Tests for the advance() phase dispatch."""

    def _make_orchestrator(self, wf_data):
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
                "milestone_id": "ms-1",
                "workflow_id": wf_data["workflow_id"],
            }
            mock_repo.create_event.return_value = {"id": 1}
            mock_repo.update_workflow.return_value = wf_data
            mock_repo_cls.return_value = mock_repo

            orch = AutonomousOrchestrator(wf_data["workflow_id"])
            orch.repo = mock_repo

        # Also mock the emitter and runner
        orch.emitter = MagicMock()
        with patch.object(orch, "_runner") as mock_runner:
            orch._runner = mock_runner

        return orch, mock_repo, orch._runner

    def test_advance_skips_paused(self):
        wf = _make_workflow(status="paused", current_phase="planning")
        orch, mock_repo, _ = self._make_orchestrator(wf)
        # advance should return early
        orch.advance()
        # Should not create any milestones
        mock_repo.create_milestone.assert_not_called()

    def test_advance_nonexistent_workflow(self):
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        with (
            patch("app.modules.workspace.autonomous.orchestrator.Database"),
            patch(
                "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
            ) as mock_repo_cls,
        ):
            mock_repo = MagicMock()
            mock_repo.get_workflow.return_value = None
            mock_repo_cls.return_value = mock_repo

            orch = AutonomousOrchestrator("nonexistent")

        # Should return without error
        orch.advance()


class TestOrchestratorPreparation:
    """Tests for the preparation phase."""

    def _make_orchestrator(self, wf_data):
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

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_preparation_creates_issue_and_branch(self, mock_gh_cls):
        wf = _make_workflow(current_phase="preparation")
        orch, mock_repo = self._make_orchestrator(wf)

        mock_gh = MagicMock()
        mock_gh.create_issue.return_value = {
            "number": 42,
            "url": "https://github.com/test/issues/42",
        }
        mock_gh.create_branch.return_value = {"branch": "auto-dev/test-wf"}
        mock_gh_cls.return_value = mock_gh

        orch._do_preparation(wf)

        # Should create issue
        mock_gh.create_issue.assert_called_once()
        # Should create branch
        mock_gh.create_branch.assert_called_once()
        # Should transition to planning
        update_calls = mock_repo.update_workflow.call_args_list
        phases = [c[0][1].get("current_phase") for c in update_calls if "current_phase" in c[0][1]]
        assert "planning" in phases

    def test_preparation_reads_existing_issue(self):
        wf = _make_workflow(
            current_phase="preparation",
            requirements_text="",  # Empty so issue URL branch is taken
            requirements_issue_url="https://github.com/user/repo/issues/99",
        )
        orch, mock_repo = self._make_orchestrator(wf)

        mock_gh = MagicMock()
        mock_gh.get_issue.return_value = {
            "number": 99,
            "title": "Test Issue",
            "body": "Issue body content",
            "state": "open",
        }
        mock_gh.create_branch.return_value = {"branch": "auto-dev/test-wf"}
        orch._gh = mock_gh

        with patch("app.modules.workspace.autonomous.orchestrator.GitHubOps", return_value=mock_gh):
            orch._do_preparation(wf)

        mock_gh.get_issue.assert_called_once_with(99)
        # Should update requirements_text from issue body
        update_calls = mock_repo.update_workflow.call_args_list
        req_updates = [c for c in update_calls if c[0][1].get("requirements_text")]
        assert len(req_updates) > 0
        assert req_updates[0][0][1]["requirements_text"] == "Issue body content"

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_preparation_worktree_strategy(self, mock_gh_cls):
        wf = _make_workflow(
            current_phase="preparation",
            branch_strategy="worktree",
        )
        orch, mock_repo = self._make_orchestrator(wf)

        mock_gh = MagicMock()
        mock_gh.create_issue.return_value = {"number": 1, "url": ""}
        mock_gh.create_worktree.return_value = {
            "worktree_path": "/tmp/wt-path",
            "branch": "auto-dev/test-wf",
        }
        mock_gh_cls.return_value = mock_gh

        orch._do_preparation(wf)

        mock_gh.create_worktree.assert_called_once()
        # Should set worktree_path
        update_calls = mock_repo.update_workflow.call_args_list
        wt_updates = [c for c in update_calls if c[0][1].get("worktree_path")]
        assert len(wt_updates) > 0

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_preparation_current_branch_strategy(self, mock_gh_cls):
        wf = _make_workflow(
            current_phase="preparation",
            branch_strategy="current",
        )
        orch, mock_repo = self._make_orchestrator(wf)

        mock_gh = MagicMock()
        mock_gh.create_issue.return_value = {"number": 1, "url": ""}
        mock_gh_cls.return_value = mock_gh

        orch._do_preparation(wf)

        # Should NOT create branch or worktree
        mock_gh.create_branch.assert_not_called()
        mock_gh.create_worktree.assert_not_called()
        # But should still transition to planning
        update_calls = mock_repo.update_workflow.call_args_list
        phases = [c[0][1].get("current_phase") for c in update_calls if "current_phase" in c[0][1]]
        assert "planning" in phases


class TestOrchestratorPlanning:
    """Tests for the planning phase."""

    def _make_orchestrator(self, wf_data, milestones=None):
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
            mock_repo.create_milestone.side_effect = [
                {"milestone_id": f"ms-plan-{i}", "workflow_id": wf_data["workflow_id"]}
                for i in range(20)
            ]
            mock_repo.create_event.return_value = {"id": 1}
            mock_repo.update_workflow.return_value = wf_data
            mock_repo.update_milestone.return_value = {}
            mock_repo_cls.return_value = mock_repo

            orch = AutonomousOrchestrator(wf_data["workflow_id"])
            orch.repo = mock_repo
            orch.emitter = MagicMock()

        return orch, mock_repo

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_planning_approved_first_round(self, mock_gh_cls):
        wf = _make_workflow(current_phase="planning", current_round=0, max_plan_rounds=3)
        orch, mock_repo = self._make_orchestrator(wf)

        mock_gh = MagicMock()
        mock_gh.add_issue_comment.return_value = {"id": 1}
        mock_gh_cls.return_value = mock_gh

        # Mock agent runner
        plan_result = _make_agent_result(text="Here is the implementation plan...")
        review_result = _make_agent_result(text="The plan looks good. 方案通过审查。")
        orch._runner = MagicMock()
        orch._runner.run_agent_task.side_effect = [plan_result, review_result]
        orch._gh = mock_gh

        orch._do_planning(wf)

        # Should call agent twice (plan + review)
        assert orch._runner.run_agent_task.call_count == 2
        # Should transition to development
        update_calls = mock_repo.update_workflow.call_args_list
        phases = [c[0][1].get("current_phase") for c in update_calls if "current_phase" in c[0][1]]
        assert "development" in phases

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_planning_needs_refinement(self, mock_gh_cls):
        wf = _make_workflow(current_phase="planning", current_round=0, max_plan_rounds=3)
        orch, mock_repo = self._make_orchestrator(wf)

        mock_gh = MagicMock()
        mock_gh.add_issue_comment.return_value = {"id": 1}
        mock_gh_cls.return_value = mock_gh

        # Review says not approved
        plan_result = _make_agent_result(text="Plan v1")
        review_result = _make_agent_result(text="There are issues with this plan. Needs work.")
        orch._runner = MagicMock()
        orch._runner.run_agent_task.side_effect = [plan_result, review_result]
        orch._gh = mock_gh

        orch._do_planning(wf)

        # Should NOT transition to development
        update_calls = mock_repo.update_workflow.call_args_list
        phases = [c[0][1].get("current_phase") for c in update_calls if "current_phase" in c[0][1]]
        assert "development" not in phases
        # Should increment round
        round_updates = [c for c in update_calls if "current_round" in c[0][1]]
        assert len(round_updates) > 0
        assert round_updates[0][0][1]["current_round"] == 1

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_planning_max_rounds_forces_transition(self, mock_gh_cls):
        wf = _make_workflow(current_phase="planning", current_round=2, max_plan_rounds=3)
        orch, mock_repo = self._make_orchestrator(wf)

        mock_gh = MagicMock()
        mock_gh_cls.return_value = mock_gh

        # Plan and review, round_num=3 >= max_rounds=3
        plan_result = _make_agent_result(text="Plan v3")
        review_result = _make_agent_result(text="Still issues but max rounds reached.")
        orch._runner = MagicMock()
        orch._runner.run_agent_task.side_effect = [plan_result, review_result]
        orch._gh = mock_gh

        orch._do_planning(wf)

        # Should force transition to development
        update_calls = mock_repo.update_workflow.call_args_list
        phases = [c[0][1].get("current_phase") for c in update_calls if "current_phase" in c[0][1]]
        assert "development" in phases

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_planning_failure_stops_workflow(self, mock_gh_cls):
        wf = _make_workflow(current_phase="planning", current_round=0)
        orch, mock_repo = self._make_orchestrator(wf)

        mock_gh = MagicMock()
        mock_gh_cls.return_value = mock_gh

        # Plan fails
        plan_result = _make_agent_result(success=False, text="", error="Model unavailable")
        orch._runner = MagicMock()
        orch._runner.run_agent_task.return_value = plan_result
        orch._gh = mock_gh

        orch._do_planning(wf)

        # Should set status to failed
        update_calls = mock_repo.update_workflow.call_args_list
        status_updates = [c for c in update_calls if c[0][1].get("status") == "failed"]
        assert len(status_updates) > 0


class TestOrchestratorWait:
    """Tests for the wait phase."""

    def _make_orchestrator(self, wf_data):
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
                "milestone_id": "ms-1",
                "workflow_id": wf_data["workflow_id"],
            }
            mock_repo.create_event.return_value = {"id": 1}
            mock_repo.update_workflow.return_value = wf_data
            mock_repo_cls.return_value = mock_repo

            orch = AutonomousOrchestrator(wf_data["workflow_id"])
            orch.repo = mock_repo
            orch.emitter = MagicMock()

        return orch, mock_repo

    def test_wait_no_issue_returns(self):
        wf = _make_workflow(current_phase="wait", github_issue_number=None)
        orch, _ = self._make_orchestrator(wf)
        # Should return without error
        orch._do_wait(wf)

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_wait_detects_completion(self, mock_gh_cls):
        wf = _make_workflow(current_phase="wait", github_issue_number=42)
        orch, mock_repo = self._make_orchestrator(wf)

        mock_gh = MagicMock()
        mock_gh.list_issue_comments.return_value = [
            {"body": "开发完成", "created_at": "2026-06-05T14:00:00Z"},
        ]
        mock_gh_cls.return_value = mock_gh
        orch._gh = mock_gh

        orch._do_wait(wf)

        # Should transition to merge
        update_calls = mock_repo.update_workflow.call_args_list
        phases = [c[0][1].get("current_phase") for c in update_calls if "current_phase" in c[0][1]]
        assert "merge" in phases

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_wait_detects_new_requirements(self, mock_gh_cls):
        wf = _make_workflow(current_phase="wait", github_issue_number=42)
        orch, mock_repo = self._make_orchestrator(wf)

        mock_gh = MagicMock()
        mock_gh.list_issue_comments.return_value = [
            {"body": "Please also add a dark mode feature", "created_at": "2026-06-05T14:00:00Z"},
        ]
        mock_gh_cls.return_value = mock_gh
        orch._gh = mock_gh

        orch._do_wait(wf)

        # Should transition to planning with new requirements
        update_calls = mock_repo.update_workflow.call_args_list
        phases = [c[0][1].get("current_phase") for c in update_calls if "current_phase" in c[0][1]]
        assert "planning" in phases
        req_updates = [c for c in update_calls if "requirements_text" in c[0][1]]
        assert len(req_updates) > 0

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_wait_no_new_comments(self, mock_gh_cls):
        wf = _make_workflow(current_phase="wait", github_issue_number=42)
        orch, mock_repo = self._make_orchestrator(wf)

        mock_gh = MagicMock()
        mock_gh.list_issue_comments.return_value = []
        mock_gh_cls.return_value = mock_gh
        orch._gh = mock_gh

        orch._do_wait(wf)

        # Should NOT update workflow
        mock_repo.update_workflow.assert_not_called()


class TestOrchestratorMerge:
    """Tests for the merge phase."""

    def _make_orchestrator(self, wf_data):
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        with (
            patch("app.modules.workspace.autonomous.orchestrator.Database"),
            patch(
                "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
            ) as mock_repo_cls,
        ):
            mock_repo = MagicMock()
            mock_repo.get_workflow.return_value = wf_data
            mock_repo.create_milestone.return_value = {
                "milestone_id": "ms-1",
                "workflow_id": wf_data["workflow_id"],
            }
            mock_repo.create_event.return_value = {"id": 1}
            mock_repo.update_workflow.return_value = wf_data
            mock_repo_cls.return_value = mock_repo

            orch = AutonomousOrchestrator(wf_data["workflow_id"])
            orch.repo = mock_repo
            orch.emitter = MagicMock()

        return orch, mock_repo

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_merge_pr_and_cleanup(self, mock_gh_cls):
        wf = _make_workflow(
            current_phase="merge",
            github_pr_number=10,
            branch_name="feature/test",
        )
        orch, mock_repo = self._make_orchestrator(wf)

        mock_gh = MagicMock()
        mock_gh.merge_pr.return_value = {"merged": True}
        mock_gh_cls.return_value = mock_gh
        orch._gh = mock_gh

        orch._do_merge(wf)

        mock_gh.merge_pr.assert_called_once_with(10, strategy="merge")
        mock_gh.delete_branch.assert_called_once_with("feature/test")

        # Should mark workflow completed
        update_calls = mock_repo.update_workflow.call_args_list
        status_updates = [c for c in update_calls if c[0][1].get("status") == "completed"]
        assert len(status_updates) > 0

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_merge_with_worktree_cleanup(self, mock_gh_cls):
        wf = _make_workflow(
            current_phase="merge",
            github_pr_number=10,
            branch_name="feature/wt",
            worktree_path="/tmp/wt-path",
        )
        orch, _ = self._make_orchestrator(wf)

        mock_gh = MagicMock()
        mock_gh.merge_pr.return_value = {"merged": True}
        mock_gh_cls.return_value = mock_gh
        orch._gh = mock_gh

        orch._do_merge(wf)

        mock_gh.remove_worktree.assert_called_once_with("/tmp/wt-path")

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_merge_no_pr(self, mock_gh_cls):
        wf = _make_workflow(
            current_phase="merge",
            github_pr_number=None,
            branch_name="feature/test",
        )
        orch, mock_repo = self._make_orchestrator(wf)

        mock_gh = MagicMock()
        mock_gh_cls.return_value = mock_gh
        orch._gh = mock_gh

        orch._do_merge(wf)

        mock_gh.merge_pr.assert_not_called()
        # Should still complete workflow
        update_calls = mock_repo.update_workflow.call_args_list
        status_updates = [c for c in update_calls if c[0][1].get("status") == "completed"]
        assert len(status_updates) > 0
