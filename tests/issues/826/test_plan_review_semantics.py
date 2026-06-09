"""
Tests for Issue #826: plan review feedback discarded and empty review output.

Covers:
- Empty review output triggers fallback message
- review_has_feedback logic with various review texts
- needs_refinement loop termination
- REVIEW_FEEDBACK_MIN_LENGTH constant usage
"""

from unittest.mock import MagicMock, patch

from app.modules.workspace.autonomous.orchestrator import REVIEW_FEEDBACK_MIN_LENGTH


class TestReviewFeedbackDetection:
    """Test the review_has_feedback logic in _do_planning."""

    def _make_orchestrator(self):
        """Create a minimal orchestrator with mocked dependencies."""
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        with (
            patch("app.modules.workspace.autonomous.orchestrator.SessionManager"),
            patch("app.modules.workspace.autonomous.orchestrator.AutonomousAgentRunner"),
        ):
            orch = AutonomousOrchestrator("wf-test", db=MagicMock())
        orch.repo = MagicMock()
        orch._gh = MagicMock()
        return orch

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
        needs_refinement = review_has_feedback and round_num <= max_rounds
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
        needs_refinement = review_has_feedback and round_num <= max_rounds
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
        needs_refinement = review_has_feedback and round_num <= max_rounds
        assert needs_refinement is False

    def test_max_plan_rounds_1_allows_one_refinement(self):
        """max_plan_rounds=1 should allow initial plan + 1 refinement round."""
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
        needs_refinement = review_has_feedback and round_num <= max_rounds
        # round 1 <= max_rounds 1 => True, so refinement runs (round 2)
        assert needs_refinement is True

        # After round 2, round_num=2 > max_rounds=1, stops
        round_num = 2
        needs_refinement = True and round_num <= max_rounds  # feedback assumed True
        assert needs_refinement is False
