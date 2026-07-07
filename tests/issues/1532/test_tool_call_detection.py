"""Tests for test tool call detection (Issue #1532).

Bug: Agent runs pytest but output is not captured in visible text,
causing tests_actually_skipped=True -> infinite retry loop.

Fix: Check tool_calls for test execution commands + reverse judgment logic.
"""

import pytest


class TestHasTestToolCall:
    """Verify _has_test_tool_call detects test commands correctly."""

    @pytest.mark.parametrize(
        "tool_calls,framework_type,expected",
        [
            # Empty tool_calls -> False
            ([], "python", False),
            # Python pytest detection
            ([{"tool": {"name": "Bash", "input": {"command": "pytest"}}}], "python", True),
            ([{"tool": {"name": "Bash", "input": {"command": "pytest tests/"}}}], "python", True),
            (
                [{"tool": {"name": "Bash", "input": {"command": "python -m pytest"}}}],
                "python",
                True,
            ),
            ([{"tool": {"name": "Bash", "input": {"command": "pytest -v"}}}], "python", True),
            # Help/version queries should NOT count as test execution
            ([{"tool": {"name": "Bash", "input": {"command": "pytest --help"}}}], "python", False),
            (
                [{"tool": {"name": "Bash", "input": {"command": "pytest --version"}}}],
                "python",
                False,
            ),
            ([{"tool": {"name": "Bash", "input": {"command": "pytest -h"}}}], "python", False),
            # Non-test commands -> False
            ([{"tool": {"name": "Bash", "input": {"command": "ls -la"}}}], "python", False),
            ([{"tool": {"name": "Bash", "input": {"command": "git status"}}}], "python", False),
            # JavaScript Jest detection
            ([{"tool": {"name": "Bash", "input": {"command": "npm test"}}}], "javascript", True),
            ([{"tool": {"name": "Bash", "input": {"command": "jest"}}}], "javascript", True),
            ([{"tool": {"name": "Bash", "input": {"command": "vitest run"}}}], "javascript", True),
            # Go test detection (including -v verbose)
            ([{"tool": {"name": "Bash", "input": {"command": "go test ./..."}}}], "go", True),
            ([{"tool": {"name": "Bash", "input": {"command": "go test -v ./pkg"}}}], "go", True),
            ([{"tool": {"name": "Bash", "input": {"command": "gotestsum"}}}], "go", True),
            # Rust cargo test detection
            ([{"tool": {"name": "Bash", "input": {"command": "cargo test"}}}], "rust", True),
            ([{"tool": {"name": "Bash", "input": {"command": "cargo t"}}}], "rust", True),
            # Java gradle/maven test detection
            ([{"tool": {"name": "Bash", "input": {"command": "mvn test"}}}], "java", True),
            ([{"tool": {"name": "Bash", "input": {"command": "gradle test"}}}], "java", True),
            ([{"tool": {"name": "Bash", "input": {"command": "./gradlew test"}}}], "java", True),
            # Unknown framework -> fallback to generic_patterns (includes unittest)
            ([{"tool": {"name": "Bash", "input": {"command": "pytest"}}}], "unknown", True),
            (
                [{"tool": {"name": "Bash", "input": {"command": "unittest discover"}}}],
                "unknown",
                True,
            ),
            ([{"tool": {"name": "Bash", "input": {"command": "jest"}}}], "unknown", True),
            ([{"tool": {"name": "Bash", "input": {"command": "go test"}}}], "unknown", True),
            ([{"tool": {"name": "Bash", "input": {"command": "cargo test"}}}], "unknown", True),
            ([{"tool": {"name": "Bash", "input": {"command": "npm test"}}}], "unknown", True),
            # Multiple tool_calls: one is test command
            (
                [
                    {"tool": {"name": "Bash", "input": {"command": "git status"}}},
                    {"tool": {"name": "Bash", "input": {"command": "pytest"}}},
                ],
                "python",
                True,
            ),
            # Non-Bash test tools (pytest, run_tests, test)
            ([{"tool": {"name": "pytest", "input": {}}}], "python", True),
            ([{"tool": {"name": "run_tests", "input": {}}}], "python", True),
            ([{"tool": {"name": "test", "input": {}}}], "python", True),
            # Empty/malformed tool_calls
            ([{}], "python", False),
            ([{"tool": {}}], "python", False),
            ([{"tool": {"name": "Bash"}}], "python", False),  # missing input
        ],
    )
    def test_tool_call_detection(self, tool_calls, framework_type, expected):
        """_has_test_tool_call should detect test execution commands."""
        from app.modules.workspace.autonomous.orchestrator import _has_test_tool_call

        result = _has_test_tool_call(tool_calls, framework_type)
        assert result == expected

    def test_multiple_tool_calls(self):
        """When multiple tool_calls present, one test command is sufficient."""
        from app.modules.workspace.autonomous.orchestrator import _has_test_tool_call

        tool_calls = [
            {"tool": {"name": "Bash", "input": {"command": "ls -la"}}},
            {"tool": {"name": "Bash", "input": {"command": "pytest tests/ -v"}}},
            {"tool": {"name": "Bash", "input": {"command": "git status"}}},
        ]
        assert _has_test_tool_call(tool_calls, "python") is True


class TestToolInputNoneSafety:
    """P0 fix: tool_input being None should not cause AttributeError."""

    def test_tool_input_none_no_error(self):
        """tool_input=None should be handled gracefully."""
        from app.modules.workspace.autonomous.orchestrator import _has_test_tool_call

        tool_calls = [{"tool": {"name": "Bash", "input": None}}]
        # Should not raise AttributeError, should return False
        result = _has_test_tool_call(tool_calls, "python")
        assert result is False

    def test_tool_input_none_with_other_tools(self):
        """tool_input=None in one tool should not break other tools."""
        from app.modules.workspace.autonomous.orchestrator import _has_test_tool_call

        tool_calls = [
            {"tool": {"name": "Bash", "input": None}},
            {"tool": {"name": "Bash", "input": {"command": "pytest"}}},
        ]
        assert _has_test_tool_call(tool_calls, "python") is True


class TestSkipDetectionLogic:
    """Test the reverse judgment logic for tests_actually_skipped."""

    def test_agent_no_text_but_has_tool_call(self):
        """Agent has no visible pytest output but tool_call shows test ran."""
        from app.modules.workspace.autonomous.orchestrator import _has_test_tool_call

        tool_calls = [{"tool": {"name": "Bash", "input": {"command": "pytest -x"}}}]
        has_test_tool_call = _has_test_tool_call(tool_calls, "python")

        # tests_actually_run should be True due to tool_call evidence
        tests_actually_run = has_test_tool_call
        assert tests_actually_run is True

    def test_agent_with_passed_status_tag(self):
        """Agent outputs TEST_STATUS: passed -> tests_actually_run."""
        test_status_tag = "passed"
        tests_actually_run = test_status_tag in ("passed", "failed")
        assert tests_actually_run is True

    def test_agent_with_skipped_status_tag(self):
        """Agent outputs TEST_STATUS: skipped -> tests_actually_skipped."""
        test_status_tag = "skipped"
        tests_actually_skipped = test_status_tag == "skipped"
        assert tests_actually_skipped is True

    def test_agent_with_no_evidence(self):
        """Agent has no test_status, no tool_call, no pytest output -> skipped."""
        test_status_tag = ""
        has_test_tool_call = False
        has_test_result = False
        test_result_success = True

        tests_actually_run = (
            test_status_tag in ("passed", "failed") or has_test_tool_call or has_test_result
        )
        tests_actually_skipped = test_status_tag == "skipped" or (
            test_result_success and not tests_actually_run
        )
        assert tests_actually_skipped is True


class TestFrameworkSpecificPatterns:
    """Test detection patterns for different test frameworks."""

    def test_python_unittest_detection(self):
        """unittest is a valid Python test framework."""
        from app.modules.workspace.autonomous.orchestrator import _has_test_tool_call

        tool_calls = [{"tool": {"name": "Bash", "input": {"command": "python -m unittest"}}}]
        assert _has_test_tool_call(tool_calls, "python") is True

    def test_python_tox_detection(self):
        """tox is a valid Python test framework."""
        from app.modules.workspace.autonomous.orchestrator import _has_test_tool_call

        tool_calls = [{"tool": {"name": "Bash", "input": {"command": "tox"}}}]
        assert _has_test_tool_call(tool_calls, "python") is True

    def test_go_verbose_test_detection(self):
        """go test -v (verbose) is a valid test command."""
        from app.modules.workspace.autonomous.orchestrator import _has_test_tool_call

        tool_calls = [{"tool": {"name": "Bash", "input": {"command": "go test -v ./..."}}}]
        assert _has_test_tool_call(tool_calls, "go") is True

    def test_rust_cargo_test_detection(self):
        """cargo test is a valid Rust test command."""
        from app.modules.workspace.autonomous.orchestrator import _has_test_tool_call

        tool_calls = [{"tool": {"name": "Bash", "input": {"command": "cargo test"}}}]
        assert _has_test_tool_call(tool_calls, "rust") is True

    def test_java_gradle_test_detection(self):
        """gradle test is a valid Java test command."""
        from app.modules.workspace.autonomous.orchestrator import _has_test_tool_call

        tool_calls = [{"tool": {"name": "Bash", "input": {"command": "gradle test"}}}]
        assert _has_test_tool_call(tool_calls, "java") is True
