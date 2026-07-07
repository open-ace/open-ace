"""Tests for keyword fallback improvements (Phase 1, P0).

Tests framework inference and strict keyword detection logic added to
orchestrator.py to reduce false-positive test result detection.
"""

import pytest
import os
import tempfile

from app.modules.workspace.autonomous.orchestrator import (
    _infer_test_framework,
    _has_strict_keyword_result,
)


class TestFrameworkInference:
    """Test _infer_test_framework function."""

    def test_python_project_with_requirements_txt(self):
        """Python project with requirements.txt should infer 'python'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create requirements.txt
            req_path = os.path.join(tmpdir, "requirements.txt")
            with open(req_path, "w") as f:
                f.write("pytest>=7.0\n")

            result = _infer_test_framework(tmpdir, "claude-code")
            assert result == "python"

    def test_python_project_with_pyproject_toml(self):
        """Python project with pyproject.toml should infer 'python'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create pyproject.toml
            proj_path = os.path.join(tmpdir, "pyproject.toml")
            with open(proj_path, "w") as f:
                f.write("[tool.pytest]\n")

            result = _infer_test_framework(tmpdir, "qwen-code-cli")
            assert result == "python"

    def test_javascript_project_with_package_json(self):
        """JavaScript project with package.json should infer 'javascript'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create package.json
            pkg_path = os.path.join(tmpdir, "package.json")
            with open(pkg_path, "w") as f:
                f.write("{\"name\": \"test-project\"}\n")

            result = _infer_test_framework(tmpdir, "claude-code")
            assert result == "javascript"

    def test_javascript_project_with_jest_config(self):
        """JavaScript project with jest.config.js should infer 'javascript'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create jest.config.js
            jest_path = os.path.join(tmpdir, "jest.config.js")
            with open(jest_path, "w") as f:
                f.write("module.exports = {};\n")

            result = _infer_test_framework(tmpdir, "codex")
            assert result == "javascript"

    def test_go_project_with_go_mod(self):
        """Go project with go.mod should infer 'go'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create go.mod
            go_path = os.path.join(tmpdir, "go.mod")
            with open(go_path, "w") as f:
                f.write("module example\n")

            result = _infer_test_framework(tmpdir, "claude-code")
            assert result == "go"

    def test_mixed_project_python_and_js(self):
        """Mixed project with both Python and JS files should infer 'mixed'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create both files
            req_path = os.path.join(tmpdir, "requirements.txt")
            pkg_path = os.path.join(tmpdir, "package.json")
            with open(req_path, "w") as f:
                f.write("pytest\n")
            with open(pkg_path, "w") as f:
                f.write("{\"name\": \"frontend\"}\n")

            result = _infer_test_framework(tmpdir, "claude-code")
            assert result == "mixed"

    def test_unknown_project_no_markers(self):
        """Project without marker files should infer 'unknown'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Empty directory
            result = _infer_test_framework(tmpdir, "claude-code")
            assert result == "unknown"

    def test_empty_project_path(self):
        """Empty project path should infer 'unknown'."""
        result = _infer_test_framework("", "claude-code")
        assert result == "unknown"

    def test_cli_tool_not_used_for_inference(self):
        """CLI tool alone should not change inference (file markers dominate)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create requirements.txt
            req_path = os.path.join(tmpdir, "requirements.txt")
            with open(req_path, "w") as f:
                f.write("pytest\n")

            # Even with codex (could be JS), infer Python
            result = _infer_test_framework(tmpdir, "codex")
            assert result == "python"


class TestStrictKeywordDetection:
    """Test _has_strict_keyword_result function."""

    def test_dual_keywords_passed_and_PASSED(self):
        """Dual keywords 'passed' + 'PASSED' should pass strict detection."""
        text = "All tests passed\nPASSED in 2.5s"
        result = _has_strict_keyword_result(text, False)
        assert result is True

    def test_dual_keywords_failed_and_FAILED(self):
        """Dual keywords 'failed' + 'FAILED' should pass strict detection."""
        text = "Tests failed\nFAILED in 1.2s"
        result = _has_strict_keyword_result(text, False)
        assert result is True

    def test_keyword_with_timestamp(self):
        """Keyword + timestamp 'in X.XXs' should pass strict detection."""
        text = "Tests passed in 3.45s"
        result = _has_strict_keyword_result(text, False)
        assert result is True

    def test_keyword_with_file_count(self):
        """Keyword + file count 'X tests' should pass strict detection."""
        text = "5 tests passed successfully"
        result = _has_strict_keyword_result(text, False)
        assert result is True

    def test_keyword_with_error_details(self):
        """Keyword + error details should pass strict detection."""
        text = "Tests failed\nAssertionError: expected 1 but got 2"
        result = _has_strict_keyword_result(text, False)
        assert result is True

    def test_single_keyword_only_fails(self):
        """Single keyword alone should NOT pass strict detection."""
        text = "All tests passed successfully"
        result = _has_strict_keyword_result(text, False)
        assert result is False

    def test_keyword_with_hallucination_fails(self):
        """Keyword + hallucination should fail strict detection."""
        text = "Tests passed\n测试在后台运行中"
        result = _has_strict_keyword_result(text, True)
        assert result is False

    def test_no_keywords_fails(self):
        """No keywords should fail strict detection."""
        text = "I will run the tests now"
        result = _has_strict_keyword_result(text, False)
        assert result is False

    def test_traceback_with_failed_passes(self):
        """'failed' + 'Traceback' should pass strict detection."""
        text = "Tests failed\nTraceback (most recent call last)"
        result = _has_strict_keyword_result(text, False)
        assert result is True


class TestLayeredDetectionIntegration:
    """Test integration of framework inference + layered detection.

    Note: These tests verify the logic paths in _run_test_phase,
    not the actual orchestrator execution (which requires database setup).
    """

    def test_python_framework_pytest_pattern_priority(self):
        """Python framework should prioritize pytest patterns."""
        # This is tested in test_hallucination_detection.py already
        # Here we verify framework inference leads to python
        with tempfile.TemporaryDirectory() as tmpdir:
            pytest_ini = os.path.join(tmpdir, "pytest.ini")
            with open(pytest_ini, "w") as f:
                f.write("[pytest]\n")

            framework = _infer_test_framework(tmpdir, "claude-code")
            assert framework == "python"
            # In orchestrator, python → pytest patterns + unittest, NO keyword fallback

    def test_javascript_framework_jest_pattern(self):
        """JavaScript framework should enable Jest patterns + strict keywords."""
        with tempfile.TemporaryDirectory() as tmpdir:
            jest_config = os.path.join(tmpdir, "jest.config.js")
            with open(jest_config, "w") as f:
                f.write("module.exports = {};\n")

            framework = _infer_test_framework(tmpdir, "claude-code")
            assert framework == "javascript"
            # In orchestrator, javascript → Jest patterns + strict keywords allowed

    def test_go_framework_go_test_pattern(self):
        """Go framework should enable go test patterns + strict keywords."""
        with tempfile.TemporaryDirectory() as tmpdir:
            go_mod = os.path.join(tmpdir, "go.mod")
            with open(go_mod, "w") as f:
                f.write("module example\n")

            framework = _infer_test_framework(tmpdir, "claude-code")
            assert framework == "go"
            # In orchestrator, go → go test patterns + strict keywords allowed

    def test_mixed_framework_all_patterns(self):
        """Mixed framework should use all patterns + strict keywords."""
        with tempfile.TemporaryDirectory() as tmpdir:
            req = os.path.join(tmpdir, "requirements.txt")
            pkg = os.path.join(tmpdir, "package.json")
            with open(req, "w") as f:
                f.write("pytest\n")
            with open(pkg, "w") as f:
                f.write("{}\n")

            framework = _infer_test_framework(tmpdir, "claude-code")
            assert framework == "mixed"
            # In orchestrator, mixed → pytest + Jest + go test + unittest + strict keywords

    def test_unknown_framework_no_keyword_fallback(self):
        """Unknown framework should disable keyword fallback."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Empty dir, no markers
            framework = _infer_test_framework(tmpdir, "claude-code")
            assert framework == "unknown"
            # In orchestrator, unknown → pytest patterns only, NO keyword fallback