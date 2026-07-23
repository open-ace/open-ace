"""
Tests for Issue #826: plan review feedback discarded and empty review output.

Covers:
- Empty review output triggers fallback message
- review_has_feedback logic with various review texts
- needs_refinement loop termination
- REVIEW_FEEDBACK_MIN_LENGTH constant usage
- Integration: _do_planning refinement loop behavior
"""

from unittest.mock import MagicMock, patch

from app.modules.workspace.autonomous.orchestrator import REVIEW_FEEDBACK_MIN_LENGTH


class TestReviewFeedbackDetection:
    """Test the review_has_feedback logic in _do_planning."""

    def test_short_review_is_not_feedback(self):
        """Review shorter than REVIEW_FEEDBACK_MIN_LENGTH is not feedback."""
        review_text = "OK"  # 2 chars, well below threshold
        has_feedback = bool(
            review_text
            and len(review_text.strip()) > REVIEW_FEEDBACK_MIN_LENGTH
            and "方案通过审查" not in review_text
        )
        assert has_feedback is False

    def test_approval_text_is_not_feedback(self):
        """Review containing '方案通过审查' is treated as approved (no feedback)."""
        review_text = "方案通过审查，没有重大问题。" * 5  # Well above threshold
        has_feedback = bool(
            review_text
            and len(review_text.strip()) > REVIEW_FEEDBACK_MIN_LENGTH
            and "方案通过审查" not in review_text
        )
        assert has_feedback is False

    def test_substantive_review_is_feedback(self):
        """Long review without approval keyword is feedback."""
        review_text = (
            "方案存在以下问题：1. 遗漏了错误处理 2. 架构风险较高 "
            "3. 需要补充测试策略 4. 实现难度被低估 5. 缺少回滚方案"
        )
        has_feedback = bool(
            review_text
            and len(review_text.strip()) > REVIEW_FEEDBACK_MIN_LENGTH
            and "方案通过审查" not in review_text
        )
        assert has_feedback is True

    def test_fallback_message_is_feedback(self):
        """Fallback message for empty reviews exceeds threshold and triggers refinement."""
        fallback = "Review agent produced no output. Plan should be reviewed manually."
        assert len(fallback) > REVIEW_FEEDBACK_MIN_LENGTH
        has_feedback = bool(
            fallback
            and len(fallback.strip()) > REVIEW_FEEDBACK_MIN_LENGTH
            and "方案通过审查" not in fallback
        )
        assert has_feedback is True


class TestEmptyReviewHandling:
    """Test that empty review output is replaced with fallback message."""

    def test_empty_string_replaced_with_fallback(self):
        """Empty review_text should be replaced, not treated as 'approved'."""
        review_text = ""
        # Simulate the logic in _do_planning
        if not review_text.strip():
            review_text = "Review agent produced no output. Plan should be reviewed manually."
        assert len(review_text) > REVIEW_FEEDBACK_MIN_LENGTH
        assert "方案通过审查" not in review_text

    def test_whitespace_only_replaced_with_fallback(self):
        """Whitespace-only review should be replaced with fallback."""
        review_text = "   \n\t  "
        if not review_text.strip():
            review_text = "Review agent produced no output. Plan should be reviewed manually."
        assert len(review_text) > REVIEW_FEEDBACK_MIN_LENGTH


class TestNeedsRefinementLogic:
    """Test the needs_refinement calculation and loop termination."""

    def test_refinement_continues_when_feedback_and_rounds_remain(self):
        """With feedback and rounds remaining, refinement should continue."""
        review_text = (
            "方案存在以下问题需要修改：遗漏了错误处理，架构风险较高，"
            "需要补充测试策略，实现难度被低估，缺少回滚方案"
        )
        round_num = 1
        max_rounds = 2

        review_has_feedback = bool(
            review_text
            and len(review_text.strip()) > REVIEW_FEEDBACK_MIN_LENGTH
            and "方案通过审查" not in review_text
        )
        needs_refinement = review_has_feedback and round_num < max_rounds
        assert needs_refinement is True

    def test_refinement_stops_at_max_rounds(self):
        """When round_num exceeds max_rounds, refinement stops."""
        review_text = (
            "方案存在以下问题需要修改：遗漏了错误处理，架构风险较高，"
            "需要补充测试策略，实现难度被低估，缺少回滚方案"
        )
        round_num = 3
        max_rounds = 2

        review_has_feedback = bool(
            review_text
            and len(review_text.strip()) > REVIEW_FEEDBACK_MIN_LENGTH
            and "方案通过审查" not in review_text
        )
        needs_refinement = review_has_feedback and round_num < max_rounds
        assert needs_refinement is False

    def test_refinement_stops_when_approved(self):
        """When review approves the plan, refinement stops."""
        review_text = "方案通过审查，没有重大问题。" * 5
        round_num = 1
        max_rounds = 3

        review_has_feedback = bool(
            review_text
            and len(review_text.strip()) > REVIEW_FEEDBACK_MIN_LENGTH
            and "方案通过审查" not in review_text
        )
        needs_refinement = review_has_feedback and round_num < max_rounds
        assert needs_refinement is False

    def test_max_plan_rounds_1_finalizes_at_round_one(self):
        """max_plan_rounds=1 runs exactly 1 review round; the loop does NOT
        continue (round 1 is NOT < max 1). The single review's feedback is
        acted on by the finalize-time plan_refined, not by an extra loop round.
        round 1 < max_rounds 1 => False, so needs_refinement is False."""
        review_text = (
            "方案存在以下问题需要修改：遗漏了错误处理，架构风险较高，"
            "需要补充测试策略，实现难度被低估，缺少回滚方案"
        )
        round_num = 1
        max_rounds = 1

        review_has_feedback = bool(
            review_text
            and len(review_text.strip()) > REVIEW_FEEDBACK_MIN_LENGTH
            and "方案通过审查" not in review_text
        )
        needs_refinement = review_has_feedback and round_num < max_rounds
        assert needs_refinement is False


class TestPlanningIntegration:
    """Integration tests: call _do_planning and verify refinement loop behavior."""

    def _make_orchestrator(self, wf_data):
        """Create orchestrator with mocked dependencies for _do_planning."""
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
                "milestone_id": "ms-plan-1",
                "workflow_id": wf_data["workflow_id"],
            }
            mock_repo.create_event.return_value = {"id": 1}
            mock_repo.update_workflow.return_value = wf_data
            mock_repo_cls.return_value = mock_repo
            orch = AutonomousOrchestrator(wf_data["workflow_id"])
            orch.repo = mock_repo

        orch.emitter = MagicMock()
        orch._runner = MagicMock()
        orch._runner._uses_sidebar_session_source.return_value = False
        # These tests exercise planning semantics, not the privileged Git
        # boundary. Keep them hermetic on developer machines that do not have
        # the production repository-owner account configured.
        orch._snapshot_repo_context = MagicMock(
            return_value={
                "effective": {
                    "repo_path": "/tmp/test",
                    "git_dir": "/tmp/test.git",
                    "git_identity": "test-git",
                    "common_dir": "/tmp/test.git",
                    "common_identity": "test-common",
                    "origin": "",
                },
                "main": {},
            }
        )
        orch._validate_repo_context_after_run = MagicMock(return_value="")
        orch._get_gh = MagicMock()
        return orch, mock_repo

    def _make_workflow(self, **overrides):
        """Create a minimal workflow dict for planning tests."""
        base = {
            "workflow_id": "test-wf-826",
            "user_id": 1,
            "title": "Test Plan Review",
            "status": "planning",
            "requirements_text": "Fix the review bug",
            "requirements_issue_url": "",
            "project_path": "/tmp/test",
            "project_repo_url": "",
            "is_new_project": False,
            "cli_tool": "claude-code",
            "model": "claude-sonnet-4-6",
            "permission_mode": "auto-edit",
            "branch_name": "test-branch",
            "branch_strategy": "new-branch",
            "workspace_type": "local",
            "remote_machine_id": "",
            "worktree_path": "",
            "github_issue_number": None,
            "github_pr_number": None,
            "github_pr_url": "",
            "current_phase": "planning",
            "current_round": 1,
            "dev_round": 1,
            "max_plan_rounds": 2,
            "max_pr_review_rounds": 5,
            "total_tokens": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_requests": 0,
            "error_message": "",
        }
        base.update(overrides)
        return base

    def test_substantive_review_triggers_refinement(self):
        """When review has substantive feedback and another round remains,
        _do_planning emits round_end for refinement."""
        from app.modules.workspace.autonomous.models import AgentTaskResult

        wf = self._make_workflow(current_round=0, max_plan_rounds=2)
        orch, mock_repo = self._make_orchestrator(wf)

        plan_result = AgentTaskResult(
            session_id="sess-plan",
            response_text="# Plan\nStep 1: Implement fix",
            total_tokens=100,
            total_input_tokens=50,
            total_output_tokens=50,
            success=True,
        )
        review_result = AgentTaskResult(
            session_id="sess-review",
            response_text=(
                "方案存在以下问题需要修改：1. 遗漏了错误处理 "
                "2. 架构风险较高 3. 需要补充测试策略 4. 缺少回滚方案"
            ),
            total_tokens=80,
            total_input_tokens=40,
            total_output_tokens=40,
            success=True,
        )

        orch._runner.run_agent_task.side_effect = [plan_result, review_result]

        orch._do_planning(wf)

        # One round of _do_planning = 1 plan + 1 review = 2 agent calls
        assert orch._runner.run_agent_task.call_count == 2

        # The key behavior: emits "round_end" (not "phase_change"),
        # signaling the scheduler that refinement is needed
        round_end_calls = [c for c in orch.emitter.emit.call_args_list if c[0][1] == "round_end"]
        phase_change_calls = [
            c for c in orch.emitter.emit.call_args_list if c[0][1] == "phase_change"
        ]
        assert len(round_end_calls) == 1
        assert len(phase_change_calls) == 0

        # Workflow should still be in planning (not moved to development)
        status_updates = [
            c[0][1] for c in mock_repo.update_workflow.call_args_list if "status" in c[0][1]
        ]
        assert not any(u.get("status") == "developing" for u in status_updates)

    def test_approved_review_skips_refinement(self):
        """When review approves the plan, _do_planning transitions to development."""
        from app.modules.workspace.autonomous.models import AgentTaskResult

        wf = self._make_workflow(max_plan_rounds=2)
        orch, mock_repo = self._make_orchestrator(wf)

        plan_result = AgentTaskResult(
            session_id="sess-plan",
            response_text="# Plan\nStep 1: Implement fix",
            total_tokens=100,
            total_input_tokens=50,
            total_output_tokens=50,
            success=True,
        )
        review_result = AgentTaskResult(
            session_id="sess-review",
            response_text="方案通过审查，没有重大问题。" * 10,
            total_tokens=80,
            total_input_tokens=40,
            total_output_tokens=40,
            success=True,
        )

        orch._runner.run_agent_task.side_effect = [plan_result, review_result]

        orch._do_planning(wf)

        # 1 plan + 1 review = 2 agent calls, no refinement round
        assert orch._runner.run_agent_task.call_count == 2

        # Key behavior: emits "phase_change" to development (not "round_end")
        phase_change_calls = [
            c for c in orch.emitter.emit.call_args_list if c[0][1] == "phase_change"
        ]
        assert len(phase_change_calls) == 1
        assert phase_change_calls[0][0][2].get("phase") == "development"

        # Workflow should be moved to development
        status_updates = [
            c[0][1]
            for c in mock_repo.update_workflow.call_args_list
            if c[0][1].get("status") == "developing"
        ]
        assert len(status_updates) >= 1

        finalized_calls = [
            c[0][0]
            for c in mock_repo.create_milestone.call_args_list
            if c[0][0].get("milestone_type") == "plan_finalized"
        ]
        assert len(finalized_calls) == 1
        assert finalized_calls[0]["session_id"] == "sess-plan"

    def test_plan_review_final_refine_failure_blocks_development(self):
        """If the final plan_refined (that acts on the Nth review's feedback)
        fails, planning must NOT silently proceed to development with the old
        plan — it blocks (failed) for retry, so the last review is not dropped
        quietly (#1200 review)."""
        from app.modules.workspace.autonomous.models import AgentTaskResult

        wf = self._make_workflow(max_plan_rounds=2)  # current_round=1 -> round 2
        orch, mock_repo = self._make_orchestrator(wf)
        # Finalize gathers the latest plan + review from milestones.
        mock_repo.list_milestones.return_value = [
            {"milestone_type": "plan_created", "plan_content": "# Plan\nStep 1"},
            {"milestone_type": "plan_reviewed", "review_content": "需要补充错误处理和测试策略"},
        ]

        plan_result = AgentTaskResult(session_id="s", response_text="# Plan refined", success=True)
        review_result = AgentTaskResult(
            session_id="s",
            response_text=(
                "方案存在以下问题需要修改：1. 遗漏了错误处理 2. 架构风险较高 "
                "3. 需要补充测试策略 4. 缺少回滚方案"
            ),
            success=True,
        )
        refine_result = AgentTaskResult(
            session_id="s", response_text="", success=False, error="model error"
        )
        orch._runner.run_agent_task.side_effect = [plan_result, review_result, refine_result]

        orch._do_planning(wf)

        # Blocked: status failed (not developing), no plan_finalized, no dev move.
        status_updates = [
            c[0][1] for c in mock_repo.update_workflow.call_args_list if "status" in c[0][1]
        ]
        assert any(u.get("status") == "failed" for u in status_updates)
        assert not any(u.get("status") == "developing" for u in status_updates)
        milestone_types = [
            c[0][0]["milestone_type"] for c in mock_repo.create_milestone.call_args_list
        ]
        assert "plan_finalized" not in milestone_types
        assert not any(
            c[0][2].get("phase") == "development"
            for c in orch.emitter.emit.call_args_list
            if c[0][1] == "phase_change"
        )
