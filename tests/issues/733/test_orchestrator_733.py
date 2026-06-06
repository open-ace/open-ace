"""Tests for Issue #733 — chain failure fixes.

Covers:
- AUTONOMOUS_CONTEXT encourages execution (not just outputting text)
- permission_mode passed to run_agent_task
- commit verification in _do_development
- planning prompt forbids full code output
- Final Plan annotates unaddressed review feedback
"""

import json
from unittest.mock import MagicMock, patch

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
        "branch_name": "auto-dev/test",
        "branch_strategy": "new-branch",
        "workspace_type": "local",
        "remote_machine_id": "",
        "worktree_path": "",
        "github_issue_number": None,
        "github_pr_number": None,
        "github_pr_url": "",
        "current_phase": "development",
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
            "milestone_id": "ms-1",
            "workflow_id": wf_data["workflow_id"],
        }
        mock_repo.create_event.return_value = {"id": 1}
        mock_repo.update_workflow.return_value = wf_data
        mock_repo_cls.return_value = mock_repo

        orch = AutonomousOrchestrator(wf_data["workflow_id"])
        orch.repo = mock_repo

    # Mock emitter and runner
    orch.emitter = MagicMock()
    orch._runner = MagicMock()

    # Mock GitHubOps
    with patch("app.modules.workspace.autonomous.orchestrator.GitHubOps") as mock_gh_cls:
        mock_gh = MagicMock()
        mock_gh.get_current_commit.return_value = "abc123"
        mock_gh.get_diff_stats.return_value = {
            "additions": 10,
            "deletions": 2,
            "files": 3,
            "commits": 1,
        }
        mock_gh.get_diff.return_value = "diff content"
        mock_gh.git_push.return_value = None
        mock_gh.has_uncommitted_changes.return_value = False
        mock_gh.git_add_all.return_value = None
        mock_gh.git_commit.return_value = {"sha": "auto-sha", "message": "auto-commit"}
        mock_gh.create_pr.return_value = {"number": 99, "url": "https://github.com/pull/99"}
        mock_gh_cls.return_value = mock_gh
        orch._gh = mock_gh

    return orch, mock_repo


class TestAutonomousContext:
    """Verify AUTONOMOUS_CONTEXT encourages execution, not just text output."""

    def test_context_encourages_execution(self):
        from app.modules.workspace.autonomous.orchestrator import AUTONOMOUS_CONTEXT

        # The OLD problematic full phrases should NOT appear
        assert "不要尝试执行需要权限的操作" not in AUTONOMOUS_CONTEXT
        assert "直接输出修改方案即可" not in AUTONOMOUS_CONTEXT

        # NEW encouraging phrases SHOULD appear
        assert "直接执行" in AUTONOMOUS_CONTEXT
        assert "文件修改" in AUTONOMOUS_CONTEXT
        assert "跳过该步骤继续执行" in AUTONOMOUS_CONTEXT

    def test_context_no_longer_says_do_not_execute(self):
        from app.modules.workspace.autonomous.orchestrator import AUTONOMOUS_CONTEXT

        # The old problematic rules should be gone
        lines = AUTONOMOUS_CONTEXT.strip().split("\n")
        rule3 = [l for l in lines if l.startswith("3.")]
        rule4 = [l for l in lines if l.startswith("4.")]

        assert len(rule3) == 1
        assert len(rule4) == 1
        # Rule 3 should now say "execute" not "just output"
        assert "直接执行文件修改" in rule3[0]
        assert "仅输出方案文本" in rule3[0]  # "不要仅输出方案文本"
        # Rule 4 should mention skipping on permission issues
        assert "跳过" in rule4[0]


class TestPermissionModePassthrough:
    """Verify permission_mode is passed from workflow to run_agent_task."""

    def test_planning_passes_permission_mode(self):
        wf = _make_workflow(
            current_phase="planning",
            status="planning",
            current_round=1,
            dev_round=1,
            permission_mode="bypass",
        )
        orch, mock_repo = _make_orchestrator(wf)
        orch._runner.run_agent_task.return_value = _make_agent_result(text="# Plan\nStep 1")

        mock_repo.list_milestones.return_value = []

        orch._do_planning(wf)

        # Check that ALL run_agent_task calls received permission_mode="bypass"
        for call in orch._runner.run_agent_task.call_args_list:
            pm = call.kwargs.get("permission_mode") or call[1].get("permission_mode")
            assert pm == "bypass"

    def test_development_passes_permission_mode(self):
        wf = _make_workflow(
            current_phase="development",
            status="developing",
            dev_round=1,
            permission_mode="auto",
        )
        orch, mock_repo = _make_orchestrator(wf)
        orch._runner.run_agent_task.return_value = _make_agent_result(text="Done")
        orch._gh.get_current_commit.side_effect = ["abc123", "def456"]

        mock_repo.list_milestones.return_value = [
            {"milestone_type": "plan_created", "plan_content": "Build feature X"},
        ]

        orch._do_development(wf)

        call_args = orch._runner.run_agent_task.call_args
        pm = call_args.kwargs.get("permission_mode") or call_args[1].get("permission_mode")
        assert pm == "auto"

    def test_default_permission_mode_is_auto_edit(self):
        wf = _make_workflow(
            current_phase="development",
            status="developing",
            dev_round=1,
        )
        # Remove permission_mode to test default
        wf.pop("permission_mode", None)
        orch, mock_repo = _make_orchestrator(wf)
        orch._runner.run_agent_task.return_value = _make_agent_result(text="Done")
        orch._gh.get_current_commit.side_effect = ["abc123", "def456"]

        mock_repo.list_milestones.return_value = [
            {"milestone_type": "plan_created", "plan_content": "Build feature X"},
        ]

        orch._do_development(wf)

        call_args = orch._runner.run_agent_task.call_args
        pm = call_args.kwargs.get("permission_mode") or call_args[1].get("permission_mode")
        assert pm == "auto-edit"


class TestDevelopmentCommitVerification:
    """Verify _do_development detects when agent produces no code changes."""

    def _get_failed_updates(self, mock_repo):
        """Extract update_workflow calls where status=failed."""
        # repo.update_workflow is called as (workflow_id, updates_dict)
        return [
            c for c in mock_repo.update_workflow.call_args_list if c[0][1].get("status") == "failed"
        ]

    def test_detects_no_code_changes(self):
        """When commit SHA unchanged and no uncommitted files, workflow should fail."""
        wf = _make_workflow(
            current_phase="development",
            status="developing",
            dev_round=1,
        )
        orch, mock_repo = _make_orchestrator(wf)
        orch._runner.run_agent_task.return_value = _make_agent_result(
            success=True, text="I have completed the implementation"
        )
        # Same commit before and after = no new commits
        orch._gh.get_current_commit.return_value = "abc123"
        # No uncommitted changes either
        orch._gh.has_uncommitted_changes.return_value = False

        mock_repo.list_milestones.return_value = [
            {"milestone_type": "plan_created", "plan_content": "Build feature X"},
        ]

        orch._do_development(wf)

        failed_updates = self._get_failed_updates(mock_repo)
        assert len(failed_updates) >= 1
        assert "no code changes" in failed_updates[-1][0][1]["error_message"].lower()

    def test_allows_different_commits(self):
        """When commit SHA changes, workflow should proceed normally."""
        wf = _make_workflow(
            current_phase="development",
            status="developing",
            dev_round=1,
        )
        orch, mock_repo = _make_orchestrator(wf)
        orch._runner.run_agent_task.return_value = _make_agent_result(
            success=True, text="Implementation done"
        )
        # Different commits before and after
        orch._gh.get_current_commit.side_effect = ["abc123", "def456"]

        mock_repo.list_milestones.return_value = [
            {"milestone_type": "plan_created", "plan_content": "Build feature X"},
        ]

        orch._do_development(wf)

        failed_updates = self._get_failed_updates(mock_repo)
        assert len(failed_updates) == 0

    def test_auto_commits_uncommitted_changes(self):
        """When commit SHA unchanged but files were modified, auto-commit and continue."""
        wf = _make_workflow(
            current_phase="development",
            status="developing",
            dev_round=1,
        )
        orch, mock_repo = _make_orchestrator(wf)
        orch._runner.run_agent_task.return_value = _make_agent_result(
            success=True, text="Implementation done"
        )
        # Same commit before and after agent runs, then changes after auto-commit
        orch._gh.get_current_commit.side_effect = ["abc123", "abc123", "def456"]
        orch._gh.has_uncommitted_changes.return_value = True

        mock_repo.list_milestones.return_value = [
            {"milestone_type": "plan_created", "plan_content": "Build feature X"},
        ]

        orch._do_development(wf)

        # Should have auto-committed
        orch._gh.git_add_all.assert_called_once()
        orch._gh.git_commit.assert_called_once()
        assert "auto: development changes" in orch._gh.git_commit.call_args[0][0]
        # Should NOT have failed
        failed_updates = self._get_failed_updates(mock_repo)
        assert len(failed_updates) == 0

    def test_auto_commit_failure_falls_back_to_fail(self):
        """When auto-commit fails, workflow should still be marked as failed."""
        wf = _make_workflow(
            current_phase="development",
            status="developing",
            dev_round=1,
        )
        orch, mock_repo = _make_orchestrator(wf)
        orch._runner.run_agent_task.return_value = _make_agent_result(
            success=True, text="Implementation done"
        )
        orch._gh.get_current_commit.return_value = "abc123"
        orch._gh.has_uncommitted_changes.return_value = True
        orch._gh.git_commit.side_effect = Exception("pre-commit hook rejected")

        mock_repo.list_milestones.return_value = [
            {"milestone_type": "plan_created", "plan_content": "Build feature X"},
        ]

        orch._do_development(wf)

        # Should have attempted auto-commit
        orch._gh.git_commit.assert_called_once()
        # Should have fallen back to failure
        failed_updates = self._get_failed_updates(mock_repo)
        assert len(failed_updates) >= 1
        assert "no code changes" in failed_updates[-1][0][1]["error_message"].lower()

    def test_skip_check_when_commit_unavailable(self):
        """When commit SHA is empty, skip the verification gracefully."""
        wf = _make_workflow(
            current_phase="development",
            status="developing",
            dev_round=1,
        )
        orch, mock_repo = _make_orchestrator(wf)
        orch._runner.run_agent_task.return_value = _make_agent_result(success=True, text="Done")
        # Empty commit SHA
        orch._gh.get_current_commit.return_value = ""

        mock_repo.list_milestones.return_value = [
            {"milestone_type": "plan_created", "plan_content": "Build feature X"},
        ]

        orch._do_development(wf)

        failed_updates = self._get_failed_updates(mock_repo)
        assert len(failed_updates) == 0

    def test_skip_check_when_agent_fails(self):
        """When agent itself fails, no need for commit verification."""
        wf = _make_workflow(
            current_phase="development",
            status="developing",
            dev_round=1,
        )
        orch, mock_repo = _make_orchestrator(wf)
        orch._runner.run_agent_task.return_value = _make_agent_result(
            success=False, text="", error="Timeout"
        )
        orch._gh.get_current_commit.return_value = "abc123"

        mock_repo.list_milestones.return_value = [
            {"milestone_type": "plan_created", "plan_content": "Build feature X"},
        ]

        orch._do_development(wf)

        failed_updates = self._get_failed_updates(mock_repo)
        assert len(failed_updates) >= 1
        assert "timeout" in failed_updates[-1][0][1]["error_message"].lower()


class TestPlanningPromptConstraints:
    """Verify planning prompts forbid full code output."""

    def test_initial_plan_prompt_forbids_full_code(self):
        """Initial plan prompt should contain constraint against full code."""
        wf = _make_workflow(
            current_phase="planning",
            status="planning",
            current_round=0,
            dev_round=1,
        )
        orch, mock_repo = _make_orchestrator(wf)
        orch._runner.run_agent_task.return_value = _make_agent_result(text="# Plan\nStep 1")
        mock_repo.list_milestones.return_value = []

        orch._do_planning(wf)

        # _do_planning calls run_agent_task twice: plan agent then review agent.
        # The constraint should be in the FIRST call (plan agent).
        calls = orch._runner.run_agent_task.call_args_list
        assert len(calls) >= 1
        plan_prompt = calls[0].kwargs.get("prompt") or calls[0][1].get("prompt")
        assert plan_prompt is not None
        assert "不要输出完整的代码实现" in plan_prompt
        assert "具体代码将在后续开发阶段编写" in plan_prompt

    def test_refined_plan_prompt_forbids_full_code(self):
        """Refined plan prompt should also contain constraint."""
        wf = _make_workflow(
            current_phase="planning",
            status="planning",
            current_round=1,
            dev_round=1,
        )
        orch, mock_repo = _make_orchestrator(wf)
        orch._runner.run_agent_task.return_value = _make_agent_result(text="# Refined Plan\nStep 1")

        # Return existing plan + review for round 2+ (round_num=2)
        mock_repo.list_milestones.return_value = [
            {
                "milestone_type": "plan_created",
                "plan_content": "Original plan",
                "round_number": 1,
            },
            {
                "milestone_type": "plan_reviewed",
                "review_content": "Fix the architecture",
                "round_number": 1,
            },
        ]

        orch._do_planning(wf)

        # First call is the refine-plan agent
        calls = orch._runner.run_agent_task.call_args_list
        plan_prompt = calls[0].kwargs.get("prompt") or calls[0][1].get("prompt")
        assert plan_prompt is not None
        assert "不要输出完整的代码实现" in plan_prompt


class TestFinalPlanAnnotation:
    """Verify Final Plan includes unaddressed review feedback."""

    def _get_last_comment(self, mock_gh):
        """Get the last add_issue_comment call's comment text."""
        assert mock_gh.add_issue_comment.called
        return mock_gh.add_issue_comment.call_args_list[-1][0][1]

    def test_final_plan_includes_last_review(self):
        """When max_rounds reached, Final Plan should annotate last review."""
        wf = _make_workflow(
            current_phase="planning",
            status="planning",
            current_round=3,
            dev_round=1,
            max_plan_rounds=3,
            github_issue_number=42,
        )
        orch, mock_repo = _make_orchestrator(wf)
        orch._runner.run_agent_task.return_value = _make_agent_result(
            text="# Refined Plan\nStep 1\nStep 2"
        )

        # Simulate milestones with plan + review from last round
        mock_repo.list_milestones.return_value = [
            {
                "milestone_type": "plan_created",
                "plan_content": "Build feature X with approach Y",
                "round_number": 1,
            },
            {
                "milestone_type": "plan_reviewed",
                "review_content": "Consider error handling for edge cases",
                "round_number": 1,
            },
            {
                "milestone_type": "plan_refined",
                "plan_content": "Build feature X with approach Y (refined)",
                "round_number": 2,
            },
            {
                "milestone_type": "plan_reviewed",
                "review_content": "Fix data race condition in step 3",
                "round_number": 2,
            },
            {
                "milestone_type": "plan_refined",
                "plan_content": "Final refined plan content",
                "round_number": 3,
            },
            {
                "milestone_type": "plan_reviewed",
                "review_content": "Still need to handle timeout edge case",
                "round_number": 3,
            },
        ]

        orch._do_planning(wf)

        # add_issue_comment is called 3 times: plan, review, final
        # Check the last call (Final Plan)
        comment = self._get_last_comment(orch._gh)
        assert "Final Implementation Plan" in comment
        assert "not yet addressed" in comment
        assert "Still need to handle timeout edge case" in comment

    def test_final_plan_without_review(self):
        """When no review content exists, Final Plan is posted without annotation."""
        wf = _make_workflow(
            current_phase="planning",
            status="planning",
            current_round=3,
            dev_round=1,
            max_plan_rounds=3,
            github_issue_number=42,
        )
        orch, mock_repo = _make_orchestrator(wf)
        orch._runner.run_agent_task.return_value = _make_agent_result(text="# Refined Plan\nStep 1")

        # Only plan, no reviews
        mock_repo.list_milestones.return_value = [
            {
                "milestone_type": "plan_created",
                "plan_content": "Build feature X",
                "round_number": 1,
            },
        ]

        orch._do_planning(wf)

        comment = self._get_last_comment(orch._gh)
        assert "Final Implementation Plan" in comment
        # Should NOT contain review annotation
        assert "not yet addressed" not in comment
