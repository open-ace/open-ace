#!/usr/bin/env python3
"""
Tests for API Security Scanner functionality.

Issue #1897: Verify scanner correctly identifies secured endpoints.

These tests focus on the scanner's ability to recognize security annotations
and ownership patterns without requiring full Flask application context.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure project root is on path
project_root = str(Path(__file__).resolve().parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)


class TestSecurityAnnotatedDecorator:
    """Test @security_annotated decorator functionality."""

    def test_decorator_sets_attribute(self):
        """Test that @security_annotated sets _security_annotation attribute."""
        from app.auth.decorators import security_annotated

        @security_annotated(reason="Test reason")
        def test_func():
            return "test"

        assert hasattr(test_func, "_security_annotation")
        assert test_func._security_annotation == "Test reason"

    def test_decorator_preserves_function(self):
        """Test that @security_annotated preserves function behavior."""
        from app.auth.decorators import security_annotated

        @security_annotated(reason="Test reason")
        def test_func(x):
            return x * 2

        assert test_func(5) == 10


class TestScannerRecognition:
    """Test scanner's recognition of security patterns."""

    def test_scanner_recognizes_security_annotated(self):
        """Test that scanner recognizes @security_annotated in AUTH_DECORATORS."""
        from scripts.lint.api_security_scanner import AUTH_DECORATORS

        assert "security_annotated" in AUTH_DECORATORS

    def test_scanner_recognizes_ownership_patterns(self):
        """Test that scanner recognizes extended ownership patterns."""
        from scripts.lint.api_security_scanner import APISecurityScanner

        scanner = APISecurityScanner()

        # Check that the _has_ownership_check method recognizes the patterns
        # We can't easily test this without a full AST, but we can verify
        # the patterns are in the code
        import inspect

        source = inspect.getsource(scanner._has_ownership_check)

        assert "get_user_project" in source
        assert "revoke_share" in source
        assert "delete_template" in source
        assert "author_id" in source
        assert "is_public" in source

    def test_baseline_has_single_suppression(self):
        """Test that baseline has been reduced to 1 suppression."""
        import json

        from scripts.lint.api_security_scanner import BASELINE_PATH

        if BASELINE_PATH.exists():
            data = json.loads(BASELINE_PATH.read_text())
            assert len(data) == 1
            assert data[0]["endpoint"] == "/api/workspace/knowledge/<entry_id>"
            assert "metadata" in data[0]

    def test_baseline_metadata_complete(self):
        """Test that baseline suppression has complete metadata."""
        import json

        from scripts.lint.api_security_scanner import BASELINE_PATH

        if BASELINE_PATH.exists():
            data = json.loads(BASELINE_PATH.read_text())
            for item in data:
                assert "metadata" in item
                metadata = item["metadata"]
                assert "owner" in metadata
                assert "justification" in metadata
                assert "test_coverage" in metadata
                assert metadata["owner"]  # non-empty
                assert metadata["justification"]  # non-empty


class TestValidateBaselineMetadata:
    """Test baseline metadata validation script."""

    def test_validate_script_exists(self):
        """Test that validation script exists."""
        from pathlib import Path

        script_path = Path(__file__).parent.parent.parent / "scripts" / "lint" / "validate_baseline_metadata.py"
        assert script_path.exists()

    def test_validation_passes(self):
        """Test that validation passes for current baseline."""
        from scripts.lint.validate_baseline_metadata import validate_baseline

        success, errors = validate_baseline()
        assert success, f"Validation failed with errors: {errors}"


class TestFeatureFlag:
    """Test ENFORCE_PROMPT_OWNERSHIP feature flag."""

    def test_feature_flag_exists(self):
        """Test that feature flag code exists in workspace.py."""
        import inspect

        from app.routes import workspace

        source = inspect.getsource(workspace)
        assert "ENFORCE_PROMPT_OWNERSHIP" in source

    def test_check_prompt_ownership_exists(self):
        """Test that _check_prompt_ownership function exists."""
        from app.routes.workspace import _check_prompt_ownership

        assert callable(_check_prompt_ownership)


class TestProjectsAnnotations:
    """Test projects.py security annotations."""

    def test_get_project_has_annotation(self):
        """Test that api_get_project has @security_annotated."""
        import inspect

        from app.routes.projects import api_get_project

        assert hasattr(api_get_project, "_security_annotation")

    def test_daily_stats_has_annotation(self):
        """Test that api_get_project_daily_stats has @security_annotated."""
        import inspect

        from app.routes.projects import api_get_project_daily_stats

        assert hasattr(api_get_project_daily_stats, "_security_annotation")


class TestSharesAnnotations:
    """Test shares endpoint security annotations."""

    def test_revoke_share_has_annotation(self):
        """Test that revoke_share has @security_annotated."""
        import inspect

        from app.routes.workspace import revoke_share

        assert hasattr(revoke_share, "_security_annotation")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])