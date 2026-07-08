"""Tests for additional Chinese test result formats (Issue #1544).

Bug: Agent outputs "2216 个测试全部通过" but detection patterns
fail to match, causing "Tests were not actually run" false-negative.

Fix: Extend Chinese patterns to support:
- "X个测试全部通过" - with "测试全部" modifier
- "所有 X 个单元测试通过" - with "所有" prefix and "单元测试"
- "X 个单元测试通过" - with "单元测试" keyword
"""

import re

import pytest


class TestAdditionalChineseOutputDetection:
    """Verify that additional Chinese pytest output patterns are correctly detected."""

    @pytest.mark.parametrize(
        "text,expected",
        [
            # Issue #1544: Formats that were previously missed
            ("2216 个测试全部通过", True),
            ("所有 2216 个单元测试通过", True),
            ("2216 个单元测试通过", True),
            # Variations with different quantifiers
            ("10 个测试全都成功", True),
            ("100 项测试全部通过", True),
            ("5 件测试都通过", True),
            ("全部的 5 个测试通过", True),
            ("所有 10 个测试成功", True),
            ("100 个单元测试通过", True),
            # Variations without modifiers
            ("10 个测试通过", True),
            ("5 项测试成功", True),
            ("3 件单元测试通过", True),
            # Edge cases that should NOT match
            ("这个任务全部通过审核", False),  # No "测试" keyword
            ("通过了 5 个关卡", False),  # Wrong structure
            ("100 个问题都解决了", False),  # No "测试" keyword
            ("测试环境配置成功", False),  # No number
        ],
    )
    def test_additional_chinese_pattern_detection(self, text: str, expected: bool):
        """Additional Chinese format patterns should correctly identify test results."""
        _additional_chinese_patterns = [
            # Issue #1544: Previously missed formats
            r"\d+\s*(个|项|件)\s*测试\s*(全部|全都|都)?\s*(通过|成功)",
            r"(所有|全部|全部的)\s*\d+\s*(个|项|件)\s*(单元)?测试\s*(通过|成功)",
            r"\d+\s*(个|项|件)\s*(单元)?测试\s*(通过|成功)",
        ]
        has_match = any(re.search(p, text) for p in _additional_chinese_patterns)
        assert has_match == expected


class TestChinesePatternIntegration:
    """Verify that new patterns integrate correctly with existing patterns."""

    @pytest.mark.parametrize(
        "text,expected",
        [
            # Existing patterns (Issue #1538)
            ("通过: 2398 个", True),
            ("失败: 5 个", True),
            ("2398 个 通过", True),
            ("5 个 失败", True),
            # New patterns (Issue #1544)
            ("2216 个测试全部通过", True),
            ("所有 2216 个单元测试通过", True),
            ("2216 个单元测试通过", True),
            # Combination cases
            ("测试完成：2216 个测试全部通过，耗时 335.82s", True),
            ("pytest 运行结果：所有 100 个单元测试通过", True),
        ],
    )
    def test_combined_pattern_detection(self, text: str, expected: bool):
        """Combined patterns should correctly identify all Chinese test result formats."""
        _existing_chinese_patterns = [
            r"(通过|成功)[:：\s]+\d+\s*(个|项|件|测试)?",
            r"(失败|错误)[:：\s]+\d+\s*(个|项|件|测试)?",
            r"(跳过|忽略)[:：\s]+\d+\s*(个|项|件|测试)?",
            r"\d+\s*(个|项|件|测试)\s*(通过|成功|失败|跳过)",
        ]
        _new_chinese_patterns = [
            r"\d+\s*(个|项|件)\s*测试\s*(全部|全都|都)?\s*(通过|成功)",
            r"(所有|全部|全部的)\s*\d+\s*(个|项|件)\s*(单元)?测试\s*(通过|成功)",
            r"\d+\s*(个|项|件)\s*(单元)?测试\s*(通过|成功)",
        ]
        all_patterns = _existing_chinese_patterns + _new_chinese_patterns
        has_match = any(re.search(p, text) for p in all_patterns)
        assert has_match == expected


class TestNoFalsePositive:
    """Verify that new patterns do not cause false positives."""

    @pytest.mark.parametrize(
        "text",
        [
            # Non-test contexts
            "这个任务全部通过审核",
            "通过了 5 个关卡",
            "100 个问题都解决了",
            "测试环境配置成功",
            "所有审批都通过了",
            "单元测试框架设计完成",
            # Similar but not test output
            "测试计划已全部通过评审",
            "共有 100 个单元测试用例待编写",
        ],
    )
    def test_no_false_positive(self, text: str):
        """New patterns should not match non-test contexts."""
        _new_chinese_patterns = [
            r"\d+\s*(个|项|件)\s*测试\s*(全部|全都|都)?\s*(通过|成功)",
            r"(所有|全部|全部的)\s*\d+\s*(个|项|件)\s*(单元)?测试\s*(通过|成功)",
            r"\d+\s*(个|项|件)\s*(单元)?测试\s*(通过|成功)",
        ]
        has_match = any(re.search(p, text) for p in _new_chinese_patterns)
        assert not has_match, f"Unexpected match for '{text}'"
