"""Tests for test report format validation (Issue #1547).

Background: AI agent used non-standard Chinese format "X个测试全部通过"
which caused false-negative detection. This issue implements a fundamental
improvement: enforce standard format + validation + retry.

Fix components:
1. Prompt constraint: Force agent to use standard format
2. Format validation: _validate_test_report_format() method
3. Retry mechanism: Trigger retry when non-standard format detected
"""

import pytest


class TestStandardFormatDetection:
    """Verify that standard test output formats are correctly validated."""

    @pytest.mark.parametrize(
        "text,expected_valid",
        [
            # pytest standard formats (VALID)
            ("3 passed, 2 failed, 1 skipped", True),
            ("10 passed in 2.5s", True),
            ("5 passed", True),
            ("=== 3 passed in 2.50s ===", True),
            ("test session starts", True),
            ("collected 5 items", True),
            # Jest standard formats (VALID)
            ("5 tests passed, 2 tests failed", True),
            ("Test Suites: 3 passed", True),
            # Go test standard formats (VALID)
            ("PASS ok", True),
            ("FAIL FAIL", True),
            ("4 tests passed", True),
            # Rust cargo test standard formats (VALID)
            ("test result: ok. 3 passed; 1 failed", True),
            ("running 4 tests", True),
            ("test result: ok", True),
            # Java Maven/Gradle standard formats (VALID)
            ("Tests run: 10, Failures: 0", True),
            ("BUILD SUCCESS", True),
            ("BUILD SUCCESSFUL", True),
            ("BUILD FAILURE", True),
        ],
    )
    def test_standard_format_validation(self, text: str, expected_valid: bool):
        """Standard test output formats should be validated as valid."""
        # Import the validation function
        import sys
        import os

        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        )
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        # Create a mock instance to access the method
        orchestrator = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
        is_valid, reason = orchestrator._validate_test_report_format(text)
        assert is_valid == expected_valid, f"Expected {expected_valid} for '{text}', got {is_valid} ({reason})"


class TestNonStandardFormatDetection:
    """Verify that non-standard formats are correctly detected as invalid."""

    @pytest.mark.parametrize(
        "text,expected_valid",
        [
            # Chinese non-standard formats (INVALID)
            ("2216 个测试全部通过", False),
            ("所有 2216 个单元测试通过", False),
            ("10 个测试通过", False),
            ("通过: 2398 个", False),
            ("失败: 5 个", False),
            ("2398 个 通过", False),
            ("测试全部成功", False),
            ("所有测试都通过了", False),
            # Japanese non-standard formats (INVALID)
            ("通過: 2398 件", False),
            ("失敗: 5件", False),
            # Korean non-standard formats (INVALID)
            ("통과: 2398개", False),
            ("실패: 5개", False),
            # Hallucination patterns (INVALID)
            ("测试在后台运行中", False),
            ("测试进度约50%", False),
            ("测试运行完成，全部通过", False),
        ],
    )
    def test_non_standard_format_validation(self, text: str, expected_valid: bool):
        """Non-standard test output formats should be detected as invalid."""
        import sys
        import os

        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        )
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        orchestrator = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
        is_valid, reason = orchestrator._validate_test_report_format(text)
        assert is_valid == expected_valid, f"Expected {expected_valid} for '{text}', got {is_valid} ({reason})"


class TestMixedFormatHandling:
    """Verify that mixed format (standard + non-standard) is handled correctly."""

    @pytest.mark.parametrize(
        "text,expected_valid",
        [
            # Standard format dominates → VALID
            ("pytest output: 3 passed, 2 failed\n总结：测试大部分通过", True),
            ("10 passed in 2.5s\n所有测试都成功了", True),
            ("test result: ok\n测试全部通过", True),
            # Non-standard dominates without standard → INVALID
            ("测试执行完成，2216 个测试全部通过", False),
            ("运行结果：通过: 2398 个", False),
        ],
    )
    def test_mixed_format_validation(self, text: str, expected_valid: bool):
        """Mixed format should be valid if standard format is present."""
        import sys
        import os

        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        )
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        orchestrator = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
        is_valid, reason = orchestrator._validate_test_report_format(text)
        assert is_valid == expected_valid, f"Expected {expected_valid} for '{text}', got {is_valid} ({reason})"


class TestEdgeCases:
    """Verify edge case handling."""

    @pytest.mark.parametrize(
        "text,expected_valid,expected_reason_contains",
        [
            # Empty text → INVALID
            ("", False, "Empty"),
            # Whitespace only → INVALID
            ("   ", False, "Empty"),
            # No recognizable format → INVALID
            ("Some random text", False, "No test result format"),
            # Only hallucination → INVALID
            ("测试正在后台运行", False, "Non-standard"),
        ],
    )
    def test_edge_case_validation(
        self, text: str, expected_valid: bool, expected_reason_contains: str
    ):
        """Edge cases should be handled gracefully."""
        import sys
        import os

        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        )
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        orchestrator = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
        is_valid, reason = orchestrator._validate_test_report_format(text)
        assert is_valid == expected_valid
        assert expected_reason_contains in reason, f"Expected '{expected_reason_contains}' in reason, got '{reason}'"


class TestFormatValidationIntegration:
    """Verify format validation integrates with test result detection."""

    def test_format_validation_returns_tuple(self):
        """Format validation should return (bool, str) tuple."""
        import sys
        import os

        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        )
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        orchestrator = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
        result = orchestrator._validate_test_report_format("3 passed, 2 failed")

        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], bool)
        assert isinstance(result[1], str)

    def test_format_validation_case_insensitive(self):
        """Format validation should be case-insensitive."""
        import sys
        import os

        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        )
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        orchestrator = AutonomousOrchestrator.__new__(AutonomousOrchestrator)

        # Lowercase
        is_valid_lower, _ = orchestrator._validate_test_report_format("3 passed")
        # Uppercase
        is_valid_upper, _ = orchestrator._validate_test_report_format("3 PASSED")
        # Mixed case
        is_valid_mixed, _ = orchestrator._validate_test_report_format("Build Success")

        assert is_valid_lower == True
        assert is_valid_upper == True
        assert is_valid_mixed == True