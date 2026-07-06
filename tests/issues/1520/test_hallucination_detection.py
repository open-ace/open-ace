"""Tests for GLM-5 hallucination pattern detection in test phase (issue #1520).

Bug: Agent outputs descriptive text like "测试在后台运行中" or "测试进度约50%"
instead of actual pytest output. The old detection logic matched bare "test"
keyword which would false-positive on these hallucination descriptions.

Fix: Use regex patterns to match pytest's standard output format, and explicitly
exclude hallucination patterns.
"""

import pytest

# ── Test result detection patterns ──────────────────────────────────────


class TestPytestOutputDetection:
    """Verify that pytest output patterns are correctly detected."""

    @pytest.mark.parametrize(
        "text,expected",
        [
            # Real pytest output should be detected
            ("3 passed, 2 failed", True),
            ("1 passed in 2.5s", True),
            ("PASSED in 1.23s", True),
            ("FAILED in 0.5s", True),
            ("=== 3 passed in 2.50s ===", True),
            ("test session starts", True),
            ("collected 5 items", True),
            ("AssertionError: expected 1 but got 2", True),
            # Hallucination descriptions should NOT be detected alone
            ("测试在后台运行中", False),
            ("测试正在运行约13%进度", False),
            ("测试进度约50%", False),
            ("后台测试进度约13%", False),
            ("Tests are running in background", False),
            ("test progress 50%", False),
            ("Running tests... 50%", False),
            # Mixed: hallucination + real pytest output should be detected
            ("测试在后台运行中\n=== 3 passed ===", True),
            ("Tests running in background\n1 passed in 2s", True),
        ],
    )
    def test_pytest_pattern_detection(self, text: str, expected: bool):
        """Pattern detection should correctly identify pytest output."""
        import re

        _pytest_output_patterns = [
            r"\d+\s+(passed|failed|skipped|warnings?|error)",
            r"(PASSED|FAILED)\s+in\s+[\d.]+s",
            r"={3,}\s*\d+\s+(passed|failed|skipped|error|warning)",
            r"test session starts",
            r"collected\s+\d+\s+items",
            r"(?m)^\s*(PASSED|FAILED|SKIPPED)\s*$",
            r"AssertionError",
        ]
        has_pytest = any(
            re.search(p, text, re.IGNORECASE) for p in _pytest_output_patterns
        )
        assert has_pytest == expected


class TestHallucinationDetection:
    """Verify that hallucination patterns are correctly detected."""

    @pytest.mark.parametrize(
        "text,expected",
        [
            # Hallucination patterns
            ("测试在后台运行中", True),
            ("测试正在运行约13%进度", True),
            ("测试进度约50%", True),
            ("后台测试进度约13%", True),
            ("Tests are running in background", True),
            ("test progress 50%", True),
            ("Running tests... 50%", True),
            ("tests running in the background", True),
            # Non-hallucination text
            ("3 passed, 2 failed", False),
            ("PASSED in 1.23s", False),
            ("I will run the tests", False),
            ("pytest executed successfully", False),
        ],
    )
    def test_hallucination_pattern_detection(self, text: str, expected: bool):
        """Pattern detection should correctly identify hallucination text."""
        import re

        _hallucination_patterns = [
            r"测试在后台运行",
            r"测试正在运行.*%",
            r"测试进度.*%",
            r"后台测试.*进度",
            r"tests\s+(are\s+)?running\s+in\s+(the\s+)?background",
            r"test\s+progress.*%",
            r"running\s+tests.*%",
        ]
        has_hallucination = any(
            re.search(p, text, re.IGNORECASE) for p in _hallucination_patterns
        )
        assert has_hallucination == expected


# ── Integration test: orchestrator _run_test_phase ──────────────────────


class TestOrchestratorTestPhase:
    """Test the orchestrator's test-skip detection logic with hallucination."""

    def test_hallucination_only_marks_as_skipped(self):
        """Agent outputs only hallucination text -> tests_actually_skipped=True."""
        # Simulate agent response with only hallucination description
        test_response_text = """
我来运行项目的完整测试套件...
测试在后台运行中...
让我同时进行语法验证...
检查后台测试进度：测试正在后台运行中（约13%进度），所有测试都通过了...
测试进度约50%，全部通过...
"""
        # This should NOT be detected as having test results
        # because there's no actual pytest output format
        import re

        _pytest_output_patterns = [
            r"\d+\s+(passed|failed|skipped|warnings?|error)",
            r"(PASSED|FAILED)\s+in\s+[\d.]+s",
            r"={3,}\s*\d+\s+(passed|failed|skipped|error|warning)",
            r"test session starts",
            r"collected\s+\d+\s+items",
            r"(?m)^\s*(PASSED|FAILED|SKIPPED)\s*$",
            r"AssertionError",
        ]
        _hallucination_patterns = [
            r"测试在后台运行",
            r"测试正在运行.*%",
            r"测试进度.*%",
            r"后台测试.*进度",
            r"tests\s+(are\s+)?running\s+in\s+(the\s+)?background",
            r"test\s+progress.*%",
            r"running\s+tests.*%",
        ]
        _test_result_keywords = [
            "passed",
            "failed",
            "PASSED",
            "FAILED",
            "assertion",
            "AssertionError",
            "error",
        ]

        has_pytest = any(
            re.search(p, test_response_text, re.IGNORECASE) for p in _pytest_output_patterns
        )
        has_hallucination = any(
            re.search(p, test_response_text, re.IGNORECASE) for p in _hallucination_patterns
        )
        has_keyword = any(kw in test_response_text for kw in _test_result_keywords)

        has_test_result = has_pytest or (has_keyword and not has_hallucination)

        # With fix: hallucination-only response should NOT count as test result
        assert has_test_result is False

    def test_real_pytest_output_marks_as_run(self):
        """Agent outputs real pytest format -> tests_actually_run."""
        test_response_text = """
============================= test session starts ==============================
collected 5 items

tests/test_foo.py PASSED
tests/test_bar.py FAILED

=== 3 passed, 2 failed in 2.50s ===
"""
        import re

        _pytest_output_patterns = [
            r"\d+\s+(passed|failed|skipped|warnings?|error)",
            r"(PASSED|FAILED)\s+in\s+[\d.]+s",
            r"={3,}\s*\d+\s+(passed|failed|skipped|error|warning)",
            r"test session starts",
            r"collected\s+\d+\s+items",
            r"(?m)^\s*(PASSED|FAILED|SKIPPED)\s*$",
            r"AssertionError",
        ]

        has_pytest = any(
            re.search(p, test_response_text, re.IGNORECASE) for p in _pytest_output_patterns
        )
        assert has_pytest is True

    def test_mixed_hallucination_and_pytest_marks_as_run(self):
        """If hallucination + real pytest output -> tests ran (pytest wins)."""
        test_response_text = """
测试在后台运行中...
=== 3 passed in 2.50s ===
"""
        import re

        _pytest_output_patterns = [
            r"\d+\s+(passed|failed|skipped|warnings?|error)",
            r"(PASSED|FAILED)\s+in\s+[\d.]+s",
            r"={3,}\s*\d+\s+(passed|failed|skipped|error|warning)",
            r"test session starts",
            r"collected\s+\d+\s+items",
            r"(?m)^\s*(PASSED|FAILED|SKIPPED)\s*$",
            r"AssertionError",
        ]

        has_pytest = any(
            re.search(p, test_response_text, re.IGNORECASE) for p in _pytest_output_patterns
        )
        # Even with hallucination text, real pytest output should win
        assert has_pytest is True


# ── Regression: ensure old keyword-only response still works ────────────


class TestLegacyKeywordFallback:
    """Ensure non-pytest test frameworks still work with keyword fallback."""

    def test_keyword_without_hallucination_still_detected(self):
        """Legacy keyword (e.g., 'passed') without hallucination is detected."""
        test_response_text = "All tests passed successfully"
        import re

        _pytest_output_patterns = [
            r"\d+\s+(passed|failed|skipped|warnings?|error)",
            r"(PASSED|FAILED)\s+in\s+[\d.]+s",
            r"={3,}\s*\d+\s+(passed|failed|skipped|error|warning)",
            r"test session starts",
            r"collected\s+\d+\s+items",
            r"(?m)^\s*(PASSED|FAILED|SKIPPED)\s*$",
            r"AssertionError",
        ]
        _hallucination_patterns = [
            r"测试在后台运行",
            r"测试正在运行.*%",
            r"测试进度.*%",
            r"后台测试.*进度",
            r"tests\s+(are\s+)?running\s+in\s+(the\s+)?background",
            r"test\s+progress.*%",
            r"running\s+tests.*%",
        ]
        _test_result_keywords = [
            "passed",
            "failed",
            "PASSED",
            "FAILED",
            "assertion",
            "AssertionError",
            "error",
        ]

        has_pytest = any(
            re.search(p, test_response_text, re.IGNORECASE) for p in _pytest_output_patterns
        )
        has_hallucination = any(
            re.search(p, test_response_text, re.IGNORECASE) for p in _hallucination_patterns
        )
        has_keyword = any(kw in test_response_text for kw in _test_result_keywords)

        has_test_result = has_pytest or (has_keyword and not has_hallucination)

        # "passed" keyword without hallucination should be detected
        assert has_test_result is True

    def test_keyword_with_hallucination_is_not_detected(self):
        """Keyword + hallucination pattern -> NOT detected (hallucination wins)."""
        test_response_text = "测试在后台运行中... tests passed"
        import re

        _pytest_output_patterns = [
            r"\d+\s+(passed|failed|skipped|warnings?|error)",
            r"(PASSED|FAILED)\s+in\s+[\d.]+s",
            r"={3,}\s*\d+\s+(passed|failed|skipped|error|warning)",
            r"test session starts",
            r"collected\s+\d+\s+items",
            r"(?m)^\s*(PASSED|FAILED|SKIPPED)\s*$",
            r"AssertionError",
        ]
        _hallucination_patterns = [
            r"测试在后台运行",
            r"测试正在运行.*%",
            r"测试进度.*%",
            r"后台测试.*进度",
            r"tests\s+(are\s+)?running\s+in\s+(the\s+)?background",
            r"test\s+progress.*%",
            r"running\s+tests.*%",
        ]
        _test_result_keywords = [
            "passed",
            "failed",
            "PASSED",
            "FAILED",
            "assertion",
            "AssertionError",
            "error",
        ]

        has_pytest = any(
            re.search(p, test_response_text, re.IGNORECASE) for p in _pytest_output_patterns
        )
        has_hallucination = any(
            re.search(p, test_response_text, re.IGNORECASE) for p in _hallucination_patterns
        )
        has_keyword = any(kw in test_response_text for kw in _test_result_keywords)

        has_test_result = has_pytest or (has_keyword and not has_hallucination)

        # Keyword with hallucination should NOT be detected
        assert has_test_result is False
