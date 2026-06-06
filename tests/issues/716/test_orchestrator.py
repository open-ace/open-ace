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

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_preparation_persists_issue_number_from_url(self, mock_gh_cls):
        """When requirements_issue_url is set with non-empty requirements_text,
        the parsed issue number is persisted to the workflow."""
        wf = _make_workflow(
            current_phase="preparation",
            requirements_text="请处理该issue：https://github.com/user/repo/issues/718",
            requirements_issue_url="https://github.com/user/repo/issues/728",
        )
        orch, mock_repo = self._make_orchestrator(wf)

        mock_gh = MagicMock()
        mock_gh.create_branch.return_value = {"branch": "auto-dev/test-wf"}
        mock_gh_cls.return_value = mock_gh

        orch._do_preparation(wf)

        # github_issue_number should be persisted via _update_workflow
        update_calls = mock_repo.update_workflow.call_args_list
        issue_updates = [c for c in update_calls if "github_issue_number" in c[0][1]]
        assert len(issue_updates) == 1
        assert issue_updates[0][0][1]["github_issue_number"] == 728

        # Should also create a milestone for traceability
        # _create_milestone passes a single dict positional arg to repo.create_milestone
        milestone_calls = mock_repo.create_milestone.call_args_list
        linked_milestones = [
            c for c in milestone_calls if c[0][0].get("milestone_type") == "issue_linked"
        ]
        assert len(linked_milestones) == 1

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_preparation_issue_url_no_text_reads_issue(self, mock_gh_cls):
        """When requirements_issue_url is set with empty requirements_text,
        the issue body is read and issue number is persisted."""
        wf = _make_workflow(
            current_phase="preparation",
            requirements_text="",
            requirements_issue_url="https://github.com/user/repo/issues/99",
        )
        orch, mock_repo = self._make_orchestrator(wf)

        mock_gh = MagicMock()
        mock_gh.get_issue.return_value = {
            "number": 99,
            "title": "Test Issue",
            "body": "Issue body content",
        }
        mock_gh.create_branch.return_value = {"branch": "auto-dev/test-wf"}
        mock_gh_cls.return_value = mock_gh

        orch._do_preparation(wf)

        # Should persist issue number from URL
        update_calls = mock_repo.update_workflow.call_args_list
        issue_updates = [c for c in update_calls if "github_issue_number" in c[0][1]]
        # Both the URL parse persist and the elif block should set it
        issue_numbers = [c[0][1]["github_issue_number"] for c in issue_updates]
        assert 99 in issue_numbers


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
        wf = _make_workflow(current_phase="planning", current_round=0, max_plan_rounds=1)
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


# ── _do_development Tests ─────────────────────────────────────────────


class TestOrchestratorDevelopment:
    """Tests for the _do_development phase."""

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
            mock_repo.create_milestone.return_value = {
                "milestone_id": "ms-new",
                "workflow_id": wf_data["workflow_id"],
            }
            mock_repo.create_event.return_value = {"id": 1}
            mock_repo.update_workflow.return_value = wf_data
            mock_repo.update_milestone.return_value = {}
            mock_repo.update_workflow_tokens.return_value = None
            mock_repo_cls.return_value = mock_repo

            orch = AutonomousOrchestrator(wf_data["workflow_id"])
            orch.repo = mock_repo
            orch.emitter = MagicMock()
            orch._gh = MagicMock()

        return orch, mock_repo

    def test_development_success(self):
        """Successful development: creates milestones, runs agent, moves to pr_review."""
        plan_ms = {
            "milestone_id": "ms-plan-1",
            "plan_content": "1. Create hello.py\n2. Write tests",
            "status": "completed",
        }
        wf = _make_workflow(
            current_phase="development",
            status="developing",
            github_issue_number=42,
        )
        orch, mock_repo = self._make_orchestrator(wf, milestones=[plan_ms])
        orch._runner = MagicMock()
        orch._runner.run_agent_task.return_value = _make_agent_result(
            success=True, text="Development complete"
        )
        orch._gh.get_current_commit.return_value = "abc1234"
        orch._gh.get_diff_stats.return_value = {"additions": 50, "deletions": 10, "files": 3}

        orch._do_development(wf)

        # Agent called twice: dev + tests
        assert orch._runner.run_agent_task.call_count == 2

        # Milestones: dev_started, tests_run, dev_completed
        assert mock_repo.create_milestone.call_count == 3

        # Workflow moves to pr_review
        update_calls = mock_repo.update_workflow.call_args_list
        final_update = update_calls[-1]
        assert final_update[0][1]["current_phase"] == "pr_review"
        assert final_update[0][1]["status"] == "pr_review"

    def test_development_uses_plan_from_planning_milestones(self):
        """Development prompt includes the finalized plan from planning milestones."""
        plan_ms = {
            "milestone_id": "ms-plan-1",
            "plan_content": "Detailed implementation plan",
            "status": "completed",
        }
        wf = _make_workflow(current_phase="development", status="developing")
        orch, _ = self._make_orchestrator(wf, milestones=[plan_ms])
        orch._runner = MagicMock()
        orch._runner.run_agent_task.return_value = _make_agent_result()
        orch._gh.get_current_commit.return_value = "abc1234"
        orch._gh.get_diff_stats.return_value = {}

        orch._do_development(wf)

        # First agent call should use the plan in its prompt
        first_call = orch._runner.run_agent_task.call_args_list[0]
        prompt = first_call[1]["prompt"]
        assert "Detailed implementation plan" in prompt

    def test_development_fails_sets_error(self):
        """Failed development sets workflow to failed with error message."""
        wf = _make_workflow(current_phase="development", status="developing")
        orch, mock_repo = self._make_orchestrator(wf)
        orch._runner = MagicMock()
        orch._runner.run_agent_task.return_value = _make_agent_result(
            success=False, error="Build error"
        )
        orch._gh.get_current_commit.return_value = ""
        orch._gh.get_diff_stats.return_value = {}

        orch._do_development(wf)

        update_calls = mock_repo.update_workflow.call_args_list
        failed_update = update_calls[-1]
        assert failed_update[0][1]["status"] == "failed"
        assert "Development failed" in failed_update[0][1]["error_message"]

    def test_development_test_failure_sets_error(self):
        """When tests fail, workflow status becomes failed with test error."""
        wf = _make_workflow(current_phase="development", status="developing")
        orch, mock_repo = self._make_orchestrator(wf)
        orch._runner = MagicMock()
        orch._runner.run_agent_task.side_effect = [
            _make_agent_result(success=True, text="Dev done"),
            _make_agent_result(success=False, error="Tests failed"),
        ]
        orch._gh.get_current_commit.return_value = "abc1234"
        orch._gh.get_diff_stats.return_value = {}
        orch._gh.get_current_commit.return_value = "abc1234"
        orch._gh.get_diff_stats.return_value = {}

        orch._do_development(wf)

        update_calls = mock_repo.update_workflow.call_args_list
        failed_update = update_calls[-1]
        assert failed_update[0][1]["status"] == "failed"
        assert "Tests failed" in failed_update[0][1]["error_message"]

    def test_development_posts_to_issue(self):
        """Development completion posts status to GitHub issue."""
        wf = _make_workflow(
            current_phase="development",
            status="developing",
            github_issue_number=42,
        )
        orch, _ = self._make_orchestrator(wf)
        orch._runner = MagicMock()
        orch._runner.run_agent_task.return_value = _make_agent_result()
        orch._gh.get_current_commit.return_value = "abc1234"
        orch._gh.get_diff_stats.return_value = {}

        orch._do_development(wf)

        orch._gh.add_issue_comment.assert_called_once()
        call_args = orch._gh.add_issue_comment.call_args
        assert call_args[0][0] == 42
        assert "Development Round 1 Completed" in call_args[0][1]

    def test_development_no_issue_no_comment(self):
        """No issue comment when github_issue_number is not set."""
        wf = _make_workflow(
            current_phase="development",
            status="developing",
            github_issue_number=None,
        )
        orch, _ = self._make_orchestrator(wf)
        orch._runner = MagicMock()
        orch._runner.run_agent_task.return_value = _make_agent_result()
        orch._gh.get_current_commit.return_value = "abc1234"
        orch._gh.get_diff_stats.return_value = {}

        orch._do_development(wf)

        orch._gh.add_issue_comment.assert_not_called()

    def test_development_fallback_to_requirements(self):
        """When no plan exists, requirements_text is used as the plan."""
        wf = _make_workflow(
            current_phase="development",
            status="developing",
            requirements_text="Build a hello world feature",
        )
        orch, _ = self._make_orchestrator(wf, milestones=[])  # No plan milestones
        orch._runner = MagicMock()
        orch._runner.run_agent_task.return_value = _make_agent_result()
        orch._gh.get_current_commit.return_value = "abc1234"
        orch._gh.get_diff_stats.return_value = {}

        orch._do_development(wf)

        first_call = orch._runner.run_agent_task.call_args_list[0]
        prompt = first_call[1]["prompt"]
        assert "Build a hello world feature" in prompt


# ── _do_pr_review Tests ───────────────────────────────────────────────


class TestOrchestratorPrReview:
    """Tests for the _do_pr_review phase."""

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
            mock_repo.update_milestone.return_value = {}
            mock_repo.update_workflow_tokens.return_value = None
            mock_repo_cls.return_value = mock_repo

            orch = AutonomousOrchestrator(wf_data["workflow_id"])
            orch.repo = mock_repo
            orch.emitter = MagicMock()
            orch._gh = MagicMock()
            # Default safe return values for GitHub ops
            orch._gh.get_current_commit.return_value = ""
            # Default: branch has changes (most tests expect PR creation to proceed)
            orch._gh.get_diff_stats.return_value = {
                "additions": 10,
                "deletions": 2,
                "files": 3,
                "commits": 1,
            }
            orch._gh.get_diff.return_value = "diff content"
            orch._gh.git_push.return_value = None
            orch._gh.create_pr.return_value = {"number": 99, "url": "https://github.com/pull/99"}

        return orch, mock_repo

    def test_pr_review_first_round_creates_pr(self):
        """First review round creates a PR on GitHub."""
        wf = _make_workflow(
            current_phase="pr_review",
            status="pr_review",
            current_round=0,
            github_issue_number=42,
        )
        orch, _ = self._make_orchestrator(wf)
        orch._runner = MagicMock()
        orch._gh.create_pr.return_value = {
            "number": 99,
            "url": "https://github.com/user/repo/pull/99",
        }
        orch._gh.get_diff.return_value = "diff content here"
        orch._runner.run_agent_task.return_value = _make_agent_result(text="Code review passed")

        orch._do_pr_review(wf)

        orch._gh.create_pr.assert_called_once()
        pr_call = orch._gh.create_pr.call_args
        assert pr_call[1]["head"] == wf["branch_name"]
        assert pr_call[1]["base"] == "main"
        assert "Closes #42" in pr_call[1]["body"]

    def test_pr_review_max_rounds_moves_to_report(self):
        """When max rounds reached, workflow moves to report phase."""
        wf = _make_workflow(
            current_phase="pr_review",
            status="pr_review",
            current_round=0,
            max_pr_review_rounds=1,
        )
        orch, mock_repo = self._make_orchestrator(wf)
        orch._runner = MagicMock()
        orch._gh.create_pr.return_value = {"number": 99, "url": "https://github.com/pull/99"}
        orch._gh.get_diff.return_value = "diff"
        orch._runner.run_agent_task.return_value = _make_agent_result(text="LGTM")

        orch._do_pr_review(wf)

        update_calls = mock_repo.update_workflow.call_args_list
        final_update = update_calls[-1]
        assert final_update[0][1]["current_phase"] == "report"
        assert final_update[0][1]["status"] == "reporting"

    def test_pr_review_below_max_starts_fix(self):
        """When below max rounds, agent is called to fix issues."""
        wf = _make_workflow(
            current_phase="pr_review",
            status="pr_review",
            current_round=0,
            max_pr_review_rounds=3,
            github_pr_number=99,
        )
        orch, _ = self._make_orchestrator(wf)
        orch._runner = MagicMock()
        orch._gh.get_diff.return_value = "diff"
        orch._runner.run_agent_task.return_value = _make_agent_result(text="Fix issues found")
        orch._gh.get_current_commit.return_value = "fix1234"

        orch._do_pr_review(wf)

        # Agent called twice: review + fixes
        assert orch._runner.run_agent_task.call_count == 2

    def test_pr_review_posts_comment_to_pr(self):
        """Review result is posted as a PR comment."""
        wf = _make_workflow(
            current_phase="pr_review",
            status="pr_review",
            current_round=0,
            github_pr_number=99,
        )
        orch, _ = self._make_orchestrator(wf)
        orch._runner = MagicMock()
        orch._gh.create_pr.return_value = {"number": 99, "url": "https://github.com/pull/99"}
        orch._gh.get_diff.return_value = "diff"
        orch._runner.run_agent_task.return_value = _make_agent_result(text="Looks good")

        orch._do_pr_review(wf)

        orch._gh.add_pr_comment.assert_called()
        comment_call = orch._gh.add_pr_comment.call_args_list[0]
        assert comment_call[0][0] == 99
        assert "Code Review" in comment_call[0][1]

    def test_pr_creation_failure_raises(self):
        """PR creation failure raises GitHubOpsError."""
        from app.modules.workspace.autonomous.github_ops import GitHubOpsError

        wf = _make_workflow(
            current_phase="pr_review",
            status="pr_review",
            current_round=0,
        )
        orch, _ = self._make_orchestrator(wf)
        orch._runner = MagicMock()
        orch._gh.create_pr.side_effect = GitHubOpsError("PR creation failed")

        with pytest.raises(GitHubOpsError, match="PR creation failed"):
            orch._do_pr_review(wf)

    def test_pr_review_accumulates_tokens(self):
        """Token counts from review are accumulated on the workflow."""
        wf = _make_workflow(
            current_phase="pr_review",
            status="pr_review",
            current_round=0,
            max_pr_review_rounds=1,
        )
        orch, mock_repo = self._make_orchestrator(wf)
        orch._runner = MagicMock()
        orch._gh.create_pr.return_value = {"number": 99, "url": "https://github.com/pull/99"}
        orch._gh.get_diff.return_value = "diff"
        orch._runner.run_agent_task.return_value = _make_agent_result(tokens=300)

        orch._do_pr_review(wf)

        mock_repo.update_workflow_tokens.assert_called()
        token_call = mock_repo.update_workflow_tokens.call_args
        assert token_call[0][1]["total_tokens"] == 300

    def test_pr_review_pushes_branch_before_pr(self):
        """Branch is pushed to remote before PR creation."""
        wf = _make_workflow(
            current_phase="pr_review",
            status="pr_review",
            current_round=0,
            branch_name="feature/test",
        )
        orch, _ = self._make_orchestrator(wf)
        orch._runner = MagicMock()
        orch._gh.create_pr.return_value = {"number": 99, "url": "https://github.com/pull/99"}
        orch._gh.get_diff.return_value = "diff"
        orch._runner.run_agent_task.return_value = _make_agent_result()

        orch._do_pr_review(wf)

        orch._gh.git_push.assert_called()

    def test_pr_review_no_changes_marks_completed(self):
        """When branch has no commits vs main, workflow completes gracefully."""
        wf = _make_workflow(
            current_phase="pr_review",
            status="pr_review",
            current_round=0,
            branch_name="auto-dev/test",
            github_issue_number=42,
        )
        orch, mock_repo = self._make_orchestrator(wf)

        # No commits on branch vs main
        orch._gh.get_diff_stats.return_value = {
            "additions": 0,
            "deletions": 0,
            "files": 0,
            "commits": 0,
        }

        orch._do_pr_review(wf)

        # Should NOT create a PR
        orch._gh.create_pr.assert_not_called()
        # Should NOT push
        orch._gh.git_push.assert_not_called()
        # Should post comment to issue
        orch._gh.add_issue_comment.assert_called_once()
        comment = orch._gh.add_issue_comment.call_args[0]
        assert comment[0] == 42
        assert "No Changes Detected" in comment[1]
        # Should mark workflow as completed (not failed)
        update_calls = mock_repo.update_workflow.call_args_list
        final_update = update_calls[-1][0][1]
        assert final_update["status"] == "completed"
        assert final_update["current_phase"] == "completed"

    def test_pr_review_no_changes_creates_milestone(self):
        """A 'no_changes' milestone is created when branch is empty."""
        wf = _make_workflow(
            current_phase="pr_review",
            status="pr_review",
            current_round=0,
            branch_name="auto-dev/test",
        )
        orch, mock_repo = self._make_orchestrator(wf)
        orch._gh.get_diff_stats.return_value = {
            "additions": 0,
            "deletions": 0,
            "files": 0,
            "commits": 0,
        }

        orch._do_pr_review(wf)

        # _create_milestone passes a single dict positional arg to repo.create_milestone
        milestone_calls = mock_repo.create_milestone.call_args_list
        no_change_ms = [c for c in milestone_calls if c[0][0].get("milestone_type") == "no_changes"]
        assert len(no_change_ms) == 1
        assert no_change_ms[0][0][0]["status"] == "completed"

    def test_pr_review_no_changes_no_issue_no_comment(self):
        """When no issue_number and no changes, skip issue comment gracefully."""
        wf = _make_workflow(
            current_phase="pr_review",
            status="pr_review",
            current_round=0,
            branch_name="auto-dev/test",
            github_issue_number=None,
        )
        orch, _ = self._make_orchestrator(wf)
        orch._gh.get_diff_stats.return_value = {
            "additions": 0,
            "deletions": 0,
            "files": 0,
            "commits": 0,
        }

        # Should not raise
        orch._do_pr_review(wf)

        orch._gh.add_issue_comment.assert_not_called()

    def test_pr_review_diff_stats_error_treats_as_no_changes(self):
        """When get_diff_stats raises, treat as no changes and complete gracefully."""
        wf = _make_workflow(
            current_phase="pr_review",
            status="pr_review",
            current_round=0,
            branch_name="auto-dev/test",
            github_issue_number=10,
        )
        orch, mock_repo = self._make_orchestrator(wf)
        orch._gh.get_diff_stats.side_effect = Exception("git error")

        orch._do_pr_review(wf)

        # Should still complete gracefully
        update_calls = mock_repo.update_workflow.call_args_list
        final_update = update_calls[-1][0][1]
        assert final_update["status"] == "completed"


# ── _do_report Tests ──────────────────────────────────────────────────


class TestOrchestratorReport:
    """Tests for the _do_report phase."""

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
            orch._gh = MagicMock()

        return orch, mock_repo

    def test_report_generates_summary(self):
        """Report phase creates a progress report milestone."""
        wf = _make_workflow(
            current_phase="report",
            status="reporting",
            github_issue_number=42,
            github_pr_number=99,
            total_tokens=5000,
            total_requests=10,
        )
        completed_milestones = [
            {
                "status": "completed",
                "title": "Development round 1",
                "result_summary": "Built hello world feature",
            },
            {
                "status": "completed",
                "title": "Tests run",
                "result_summary": "All 5 tests passed",
            },
        ]
        orch, mock_repo = self._make_orchestrator(wf, milestones=completed_milestones)
        orch._gh.get_diff_stats.return_value = {"additions": 100, "deletions": 20, "files": 5}

        orch._do_report(wf)

        # Two milestones: progress_reported + round_completed
        assert mock_repo.create_milestone.call_count == 2
        # _create_milestone calls repo.create_milestone(kwargs_dict) as positional arg
        report_ms_dict = mock_repo.create_milestone.call_args_list[0][0][0]
        assert report_ms_dict["milestone_type"] == "progress_reported"
        assert "Progress report" in report_ms_dict["title"]

    def test_report_posts_to_issue(self):
        """Report is posted as a GitHub issue comment."""
        wf = _make_workflow(
            current_phase="report",
            status="reporting",
            github_issue_number=42,
            github_pr_number=99,
        )
        orch, _ = self._make_orchestrator(wf)
        orch._gh.get_diff_stats.return_value = {}

        orch._do_report(wf)

        orch._gh.add_issue_comment.assert_called_once()
        call_args = orch._gh.add_issue_comment.call_args
        assert call_args[0][0] == 42
        assert "Progress Report" in call_args[0][1]

    def test_report_includes_stats(self):
        """Report includes token counts and diff stats."""
        wf = _make_workflow(
            current_phase="report",
            status="reporting",
            total_tokens=5000,
            total_requests=10,
            github_pr_number=99,
        )
        orch, _ = self._make_orchestrator(wf)
        orch._gh.get_diff_stats.return_value = {"additions": 100, "deletions": 20, "files": 5}

        orch._do_report(wf)

        # _create_milestone passes dict as positional arg to repo.create_milestone
        report_ms_dict = orch.repo.create_milestone.call_args_list[0][0][0]
        summary = report_ms_dict.get("result_summary", "")
        assert "5,000" in summary  # formatted token count
        assert "99" in summary  # PR number

    def test_report_moves_to_wait_phase(self):
        """Report phase transitions workflow to wait phase."""
        wf = _make_workflow(current_phase="report", status="reporting")
        orch, mock_repo = self._make_orchestrator(wf)
        orch._gh.get_diff_stats.return_value = {}

        orch._do_report(wf)

        update_calls = mock_repo.update_workflow.call_args_list
        final_update = update_calls[-1]
        assert final_update[0][1]["current_phase"] == "wait"
        assert final_update[0][1]["status"] == "waiting"

    def test_report_no_issue_no_comment(self):
        """No issue comment when github_issue_number is not set."""
        wf = _make_workflow(
            current_phase="report",
            status="reporting",
            github_issue_number=None,
        )
        orch, _ = self._make_orchestrator(wf)
        orch._gh.get_diff_stats.return_value = {}

        orch._do_report(wf)

        orch._gh.add_issue_comment.assert_not_called()

    def test_report_no_diff_stats_graceful(self):
        """Report handles missing diff stats without error."""
        wf = _make_workflow(current_phase="report", status="reporting")
        orch, mock_repo = self._make_orchestrator(wf)
        orch._gh.get_diff_stats.side_effect = Exception("git error")

        # Should not raise
        orch._do_report(wf)

        # Should still create milestones and move to wait
        assert mock_repo.create_milestone.call_count == 2
