"""
Tests for PR #907 code review feedback fixes (Issue #910).

Covers:
1. _clean_plan_output strips agent intro text (including H2+ headings)
2. _should_show_review_warning — correct warning trigger logic
3. _should_refine_plan — refinement trigger logic
"""

import pytest

from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

# ── Test _clean_plan_output ──────────────────────────────────────────────


class TestCleanPlanOutput:
    """Test the _clean_plan_output static method."""

    def test_strips_intro_text_before_h1_heading(self):
        """Intro text like '我来分析代码库...' should be removed."""
        text = (
            "我来分析代码库，制定详细的实现方案。先探索相关文件。分析完成。\n\n"
            "---\n\n"
            "# Token 趋势工具下拉列表动态化 — 实现方案\n\n"
            "## 一、需求分析\n内容..."
        )
        result = AutonomousOrchestrator._clean_plan_output(text)
        assert result.startswith("# Token 趋势工具下拉列表动态化")
        assert "我来分析" not in result
        assert "需求分析" in result

    def test_strips_intro_text_before_h2_heading(self):
        """H2 headings (## Section) should also be detected as start of plan."""
        text = "好的，让我来分析这个问题。\n\n" "## 实现方案\n\n" "### 步骤一\n内容..."
        result = AutonomousOrchestrator._clean_plan_output(text)
        assert result.startswith("## 实现方案")
        assert "让我来分析" not in result
        assert "步骤一" in result

    def test_strips_intro_text_before_h3_heading(self):
        """H3 headings (### Subsection) should also be detected."""
        text = "Intro text\n\n### Sub-section Plan\n\nContent here."
        result = AutonomousOrchestrator._clean_plan_output(text)
        assert result.startswith("### Sub-section Plan")
        assert "Intro text" not in result

    def test_keeps_text_starting_with_heading(self):
        """Text that already starts with # should be unchanged."""
        text = "# Implementation Plan\n\n## Step 1\nDo something."
        result = AutonomousOrchestrator._clean_plan_output(text)
        assert result == text

    def test_empty_string(self):
        assert AutonomousOrchestrator._clean_plan_output("") == ""

    def test_none_input(self):
        assert AutonomousOrchestrator._clean_plan_output(None) is None

    def test_no_heading_found(self):
        """Text without any markdown heading is returned as-is."""
        text = "This is a plan without any headings."
        result = AutonomousOrchestrator._clean_plan_output(text)
        assert result == text

    def test_heading_in_middle(self):
        """Heading not at the start should still be found."""
        text = "Some intro text\nMore intro\n\n# Real Plan\n\nContent here."
        result = AutonomousOrchestrator._clean_plan_output(text)
        assert result.startswith("# Real Plan")
        assert "Some intro" not in result
        assert "Content here" in result

    def test_preserves_content_after_heading(self):
        """All content after the first heading should be preserved."""
        text = (
            "Intro text\n\n"
            "# Plan Title\n\n"
            "## Section 1\nContent 1\n\n"
            "## Section 2\nContent 2\n\n"
            "```\ncode block\n```\n"
        )
        result = AutonomousOrchestrator._clean_plan_output(text)
        assert "## Section 1" in result
        assert "## Section 2" in result
        assert "code block" in result

    def test_h4_heading_detected(self):
        """H4 headings should also trigger cleanup."""
        text = "Preamble\n\n#### Deep Section\n\nBody"
        result = AutonomousOrchestrator._clean_plan_output(text)
        assert result.startswith("#### Deep Section")
        assert "Preamble" not in result


# ── Test _should_show_review_warning ─────────────────────────────────────


class TestShouldShowReviewWarning:
    """Test the extracted _should_show_review_warning static method."""

    def test_warning_not_shown_when_review_approved(self):
        """When review contains '方案通过审查', no warning should be shown."""
        last_review = "审查结论：方案通过审查。建议：可以使用 formatToolName。"
        result = AutonomousOrchestrator._should_show_review_warning(3, 3, last_review)
        assert result is False

    def test_warning_shown_when_max_rounds_reached_and_not_approved(self):
        """Warning should show when max rounds reached and review didn't approve."""
        last_review = "方案有以下问题需要修复：遗漏了文件。"
        result = AutonomousOrchestrator._should_show_review_warning(3, 3, last_review)
        assert result is True

    def test_warning_not_shown_when_no_review(self):
        """No warning when there's no review."""
        result = AutonomousOrchestrator._should_show_review_warning(1, 3, "")
        assert result is False

    def test_warning_not_shown_before_max_rounds(self):
        """Warning should not show when we haven't reached max rounds yet."""
        last_review = "方案有问题"
        result = AutonomousOrchestrator._should_show_review_warning(1, 3, last_review)
        assert result is False

    def test_warning_shown_with_none_review(self):
        """None review should not trigger warning."""
        result = AutonomousOrchestrator._should_show_review_warning(3, 3, None)
        assert result is False


# ── Test _should_refine_plan ─────────────────────────────────────────────


class TestShouldRefinePlan:
    """Test the extracted _should_refine_plan static method."""

    def test_refinement_triggered_when_approved_with_suggestions(self):
        """When review approves but is long (>200 chars), refinement runs."""
        last_review = (
            "审查结论：方案通过审查。\n\n"
            "不过有以下改进建议：\n"
            "1. 使用 formatToolName 替代手动索引，"
            "formatToolName 已包含 fallback 逻辑（首字母大写），更健壮。\n"
            "2. 对动态工具列表排序，确保下拉列表顺序稳定：\n"
            "   const sortedTools = useMemo(() => [...tools].sort(), [tools]);\n"
            "3. 明确排除 ToolAccountsEditor.tsx 不纳入改造范围及理由："
            "其 TOOL_TYPES 是工具类型（用于工具账号分类配置），"
            "语义不同于可用工具筛选，应从后端 TOOL_TYPES 常量获取。\n"
            "4. 统一 i18n 处理：Dashboard.tsx 的硬编码使用了 i18n key，"
            "改造后统一使用 formatToolName，多余的 i18n key 可以清理。\n"
            "5. 考虑 enabled 条件：由于这些组件只在管理模式下渲染，"
            "useTools() 不涉及权限判断，所以不需要额外处理。\n"
            "以上建议属于锦上添花，不构成方案阻塞。"
        )
        result = AutonomousOrchestrator._should_refine_plan(last_review)
        assert result is True

    def test_no_refinement_when_approved_briefly(self):
        """Short approved review (<200 chars) should not trigger refinement."""
        last_review = "审查结论：方案通过审查。方案整体分析准确，无重大问题。"
        result = AutonomousOrchestrator._should_refine_plan(last_review)
        assert result is False

    def test_no_refinement_when_no_review(self):
        """No review at all means no refinement."""
        result = AutonomousOrchestrator._should_refine_plan("")
        assert result is False

    def test_no_refinement_when_none_review(self):
        """None review means no refinement."""
        result = AutonomousOrchestrator._should_refine_plan(None)
        assert result is False

    def test_no_refinement_when_not_approved(self):
        """Review without approval should not trigger refinement."""
        last_review = "方案有以下问题需要修复：遗漏了文件，需要补充更多测试覆盖。"
        result = AutonomousOrchestrator._should_refine_plan(last_review)
        assert result is False
