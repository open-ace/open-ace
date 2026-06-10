"""
Tests for PR #922 autonomous workflow comment quality and retry fixes (Issue #921).

Covers:
1. _clean_agent_text — Pass 1 (heading strip), Pass 2 (intro strip), Pass 3 (closing strip)
2. _is_rate_limited — 429 error detection
"""

import pytest

from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

# ── Test _clean_agent_text ──────────────────────────────────────────────


class TestCleanAgentTextPass1:
    """Pass 1: strip text before first markdown heading."""

    def test_strips_intro_before_heading(self):
        text = "我来为这个需求制定详细的实现方案。首先进入计划模式。\n\n## 方案总结\n内容..."
        result = AutonomousOrchestrator._clean_agent_text(text)
        assert result.startswith("## 方案总结")
        assert "我来为" not in result

    def test_empty_input(self):
        assert AutonomousOrchestrator._clean_agent_text("") == ""
        assert AutonomousOrchestrator._clean_agent_text(None) is None

    def test_no_heading_returns_as_is(self):
        text = "Just some text without headings."
        result = AutonomousOrchestrator._clean_agent_text(text)
        assert result == text


class TestCleanAgentTextPass2:
    """Pass 2: strip leading intro lines after first heading."""

    def test_strips_intro_after_heading(self):
        """Intro lines appearing right after a heading should be cleaned."""
        text = "## 实现方案\n\n" "我来为这个方案做最后的补充说明。\n\n" "### 步骤一\n实际内容..."
        result = AutonomousOrchestrator._clean_agent_text(text)
        assert "我来为" not in result
        assert "### 步骤一" in result

    def test_strips_multiple_intro_lines_after_heading(self):
        text = (
            "## 方案\n\n"
            "让我先检查一下代码结构。\n"
            "好的，现在开始编写方案。\n\n"
            "### 核心设计\n内容..."
        )
        result = AutonomousOrchestrator._clean_agent_text(text)
        assert "让我先检查" not in result
        assert "好的，现在开始" not in result
        assert "### 核心设计" in result

    def test_preserves_content_after_non_intro(self):
        """Non-intro lines after heading should be preserved."""
        text = "## 方案\n\n这是一个完整的实现方案。\n\n### 步骤\n内容..."
        result = AutonomousOrchestrator._clean_agent_text(text)
        assert "这是一个完整的实现方案" in result

    def test_strips_explore_and_analysis_complete(self):
        """'探索完成' and '分析完成' should be stripped."""
        text = "## Plan\n\n探索完成，现在编写方案。\n分析完成。\n\n### Design\ncontent..."
        result = AutonomousOrchestrator._clean_agent_text(text)
        assert "探索完成" not in result
        assert "分析完成" not in result
        assert "### Design" in result

    def test_does_not_strip_across_sub_headings(self):
        """Intro lines separated by sub-headings should not cause heading deletion.

        See PR #922 Review Round 2 Issue #1: the sub-heading between two
        intro blocks should be preserved — only the first contiguous intro
        block (before the first sub-heading) should be stripped.
        """
        text = (
            "## 实现方案\n\n"
            "我来为这个方案做补充。\n\n"
            "### 子标题\n\n"
            "让我再检查一下这个细节。\n\n"
            "### 核心内容\n实际内容..."
        )
        result = AutonomousOrchestrator._clean_agent_text(text)
        # First intro block should be stripped
        assert "我来为这个方案做补充" not in result
        # Sub-heading and second intro should be preserved
        assert "### 子标题" in result
        assert "让我再检查" in result
        assert "### 核心内容" in result


class TestCleanAgentTextPass3:
    """Pass 3: strip trailing closing lines."""

    def test_strips_closing_next_step(self):
        text = "## 方案\n\n核心内容。\n\n下一步是否需要开始实施？\n按照项目工作流程，我建议：\n1. 创建 Issue\n2. 开始实施"
        result = AutonomousOrchestrator._clean_agent_text(text)
        assert "下一步是否" not in result
        assert "按照项目工作流程" not in result
        assert "核心内容" in result

    def test_strips_closing_with_suggestion(self):
        text = "## Review\n\n审查结果。\n\n是否需要开始实施？"
        result = AutonomousOrchestrator._clean_agent_text(text)
        assert "是否需要开始" not in result
        assert "审查结果" in result

    def test_preserves_normal_ending(self):
        text = "## 方案\n\n核心内容。\n\n### 总结\n方案可行。"
        result = AutonomousOrchestrator._clean_agent_text(text)
        assert "方案可行" in result


class TestCleanAgentTextRealWorld:
    """Test with real-world examples from Issue #829."""

    def test_issue829_implementation_plan(self):
        """Simulates the actual Implementation Plan comment from Issue #829."""
        text = (
            "我来为这个需求制定详细的实现方案。首先进入计划模式进行代码分析和方案设计。"
            "现在开始分析代码库，探索对话历史页面的结构和数据流。"
            "探索完成，现在启动 Plan agent 设计实现方案。"
            "方案已完成，现在提交计划请求用户批准。\n\n"
            "## 方案总结\n\n"
            "### 核心要点\n\n需求：添加 Session ID 列。\n\n"
            "下一步是否需要开始实施？按照项目工作流程，我建议：\n"
            "1. 首先创建 GitHub Issue 记录完整方案\n"
            "2. 然后进行代码实现"
        )
        result = AutonomousOrchestrator._clean_agent_text(text)
        assert "我来为" not in result
        assert "探索完成" not in result
        assert "方案已完成" not in result
        assert "下一步是否" not in result
        assert "## 方案总结" in result
        assert "### 核心要点" in result

    def test_issue829_plan_review_intro(self):
        """Simulates the actual Plan Review comment from Issue #829."""
        text = (
            "我来严格审查这个实现方案。首先让我验证一些关键假设。"
            "让我继续检查 toast 的使用方式和其他细节："
            "现在我有足够的信息来提供完整的审查报告。\n\n"
            "## 方案审查报告\n\n"
            "### 1. 遗漏的需求\n\n字段名混淆问题。\n\n"
            "### 总结\n方案整体可行。"
        )
        result = AutonomousOrchestrator._clean_agent_text(text)
        assert "我来严格审查" not in result
        assert "让我继续检查" not in result
        assert "## 方案审查报告" in result

    def test_issue829_code_review_intro(self):
        """Simulates the actual Code Review comment from Issue #829."""
        text = (
            "我来审查这个 PR 的代码变更，检查上一轮审查意见的落实情况。\n\n"
            "## 审查结果\n\n"
            "代码质量良好。\n\n"
            "代码审查通过。"
        )
        result = AutonomousOrchestrator._clean_agent_text(text)
        assert "我来审查" not in result
        assert "## 审查结果" in result
        assert "代码审查通过" in result


# ── Test _is_rate_limited ──────────────────────────────────────────────


class TestIsRateLimited:
    """Test 429 rate limit detection."""

    def test_detects_429_code(self):
        assert AutonomousOrchestrator._is_rate_limited("API Error: Request rejected (429)")

    def test_detects_quota_exceeded(self):
        assert AutonomousOrchestrator._is_rate_limited(
            "usage allocated quota exceeded. please try again later."
        )

    def test_detects_rate_limit(self):
        assert AutonomousOrchestrator._is_rate_limited("Rate limit reached for default model")

    def test_detects_too_many_requests(self):
        assert AutonomousOrchestrator._is_rate_limited("Too Many Requests - try again later")

    def test_case_insensitive(self):
        assert AutonomousOrchestrator._is_rate_limited("QUOTA EXCEEDED for this request")

    def test_normal_error_not_detected(self):
        assert not AutonomousOrchestrator._is_rate_limited("File not found: config.json")

    def test_empty_string(self):
        assert not AutonomousOrchestrator._is_rate_limited("")

    def test_none(self):
        assert not AutonomousOrchestrator._is_rate_limited(None)

    def test_issue829_actual_error(self):
        """Test with the actual error from PR #915 Code Review (Round 2)."""
        assert AutonomousOrchestrator._is_rate_limited(
            "API Error: Request rejected (429) · usage allocated quota exceeded. "
            "please try again later."
        )
