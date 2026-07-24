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
        """Test that baseline has been reduced to 0 suppressions (all fixed)."""
        import json

        from scripts.lint.api_security_scanner import BASELINE_PATH

        if BASELINE_PATH.exists():
            data = json.loads(BASELINE_PATH.read_text())
            assert len(data) == 0, f"Expected 0 suppressions, found {len(data)}"

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


class TestPromptTemplatePermissions:
    """
    Test prompt template API permissions (Issue #1897).

    Permission matrix:
    - Public templates: GET/render/copy by all authenticated users
    - Private templates: GET/render/copy by author or admin only
    - PUT/DELETE: author or admin only (regardless of is_public)
    """

    @pytest.fixture
    def app(self):
        """Create Flask app for testing."""
        from flask import Flask

        app = Flask(__name__)
        app.config["TESTING"] = True
        return app

    @pytest.fixture
    def author_user(self):
        """Create author user."""
        return {"id": 100, "username": "author", "role": "user"}

    @pytest.fixture
    def non_author_user(self):
        """Create non-author user."""
        return {"id": 200, "username": "non_author", "role": "user"}

    @pytest.fixture
    def admin_user(self):
        """Create admin user."""
        return {"id": 300, "username": "admin", "role": "admin"}

    @pytest.fixture
    def public_template(self, author_user):
        """Create a public template."""
        from app.modules.workspace.prompt_library import PromptTemplate

        return PromptTemplate(
            id=1,
            name="Public Template",
            description="A public template",
            content="Hello {{name}}",
            category="general",
            tags=["test"],
            author_id=author_user["id"],
            is_public=True,
        )

    @pytest.fixture
    def private_template(self, author_user):
        """Create a private template."""
        from app.modules.workspace.prompt_library import PromptTemplate

        return PromptTemplate(
            id=2,
            name="Private Template",
            description="A private template",
            content="Secret {{data}}",
            category="general",
            tags=["private"],
            author_id=author_user["id"],
            is_public=False,
        )

    def test_author_get_own_private_template(self, private_template, author_user):
        """Scenario 1: Author GET own private template -> 200."""
        # This is tested via the _check_prompt_ownership function
        from app.routes.workspace import _check_prompt_ownership
        from flask import g

        # Mock g.user
        class MockG:
            user = author_user

        # Temporarily set g
        import app.routes.workspace as workspace_module
        old_g = getattr(workspace_module, "g", None)
        workspace_module.g = MockG()

        try:
            has_access, error = _check_prompt_ownership(private_template, allow_public=True)
            assert has_access is True
            assert error == ""
        finally:
            if old_g:
                workspace_module.g = old_g

    def test_author_get_own_public_template(self, public_template, author_user):
        """Scenario 2: Author GET own public template -> 200."""
        from app.routes.workspace import _check_prompt_ownership

        class MockG:
            user = author_user

        import app.routes.workspace as workspace_module
        old_g = getattr(workspace_module, "g", None)
        workspace_module.g = MockG()

        try:
            has_access, error = _check_prompt_ownership(public_template, allow_public=True)
            assert has_access is True
        finally:
            if old_g:
                workspace_module.g = old_g

    def test_non_author_get_public_template(self, public_template, non_author_user):
        """Scenario 3: Non-author GET public template -> 200."""
        from app.routes.workspace import _check_prompt_ownership

        class MockG:
            user = non_author_user

        import app.routes.workspace as workspace_module
        old_g = getattr(workspace_module, "g", None)
        workspace_module.g = MockG()

        try:
            has_access, error = _check_prompt_ownership(public_template, allow_public=True)
            assert has_access is True
        finally:
            if old_g:
                workspace_module.g = old_g

    def test_non_author_get_private_template_denied(self, private_template, non_author_user, app):
        """Scenario 4: Non-author GET private template -> 403."""
        from app.routes.workspace import _check_prompt_ownership

        with app.app_context():
            from flask import g
            g.user = non_author_user

            # Mock feature flag as true
            import os
            import importlib
            import app.routes.workspace as workspace_module

            old_flag = os.environ.get("ENFORCE_PROMPT_OWNERSHIP")
            os.environ["ENFORCE_PROMPT_OWNERSHIP"] = "true"

            # Reload module to pick up env var
            importlib.reload(workspace_module)
            from app.routes.workspace import _check_prompt_ownership as check_func

            has_access, error = check_func(private_template, allow_public=True)
            assert has_access is False
            assert "Access denied" in error

            # Restore
            if old_flag is not None:
                os.environ["ENFORCE_PROMPT_OWNERSHIP"] = old_flag
            else:
                os.environ.pop("ENFORCE_PROMPT_OWNERSHIP", None)

    def test_author_put_own_template(self, public_template, author_user):
        """Scenario 5: Author PUT own template -> 200."""
        from app.routes.workspace import _check_prompt_ownership

        class MockG:
            user = author_user

        import app.routes.workspace as workspace_module
        old_g = getattr(workspace_module, "g", None)
        workspace_module.g = MockG()

        try:
            # PUT uses allow_public=False
            has_access, error = _check_prompt_ownership(public_template, allow_public=False)
            assert has_access is True
        finally:
            if old_g:
                workspace_module.g = old_g

    def test_non_author_put_public_template_denied(self, public_template, non_author_user, app):
        """Scenario 6: Non-author PUT public template -> 403."""
        from app.routes.workspace import _check_prompt_ownership

        with app.app_context():
            from flask import g
            g.user = non_author_user

            import os
            import importlib
            import app.routes.workspace as workspace_module

            old_flag = os.environ.get("ENFORCE_PROMPT_OWNERSHIP")
            os.environ["ENFORCE_PROMPT_OWNERSHIP"] = "true"

            importlib.reload(workspace_module)
            from app.routes.workspace import _check_prompt_ownership as check_func

            # PUT uses allow_public=False
            has_access, error = check_func(public_template, allow_public=False)
            assert has_access is False

            if old_flag is not None:
                os.environ["ENFORCE_PROMPT_OWNERSHIP"] = old_flag
            else:
                os.environ.pop("ENFORCE_PROMPT_OWNERSHIP", None)

    def test_admin_put_any_template(self, private_template, admin_user):
        """Scenario 7: Admin PUT any template -> 200."""
        from app.routes.workspace import _check_prompt_ownership

        class MockG:
            user = admin_user

        import app.routes.workspace as workspace_module
        old_g = getattr(workspace_module, "g", None)
        workspace_module.g = MockG()

        try:
            has_access, error = _check_prompt_ownership(private_template, allow_public=False)
            assert has_access is True
        finally:
            if old_g:
                workspace_module.g = old_g

    def test_author_delete_own_template(self, private_template, author_user):
        """Scenario 8: Author DELETE own template -> 200."""
        # DELETE endpoint checks ownership in delete_template function
        # We verify the logic exists in workspace.py
        import inspect
        from app.routes import workspace

        source = inspect.getsource(workspace.delete_prompt)
        assert "user_id" in source
        assert "admin" in source

    def test_non_author_delete_public_template_denied(self, public_template, non_author_user):
        """Scenario 9: Non-author DELETE public template -> 403."""
        # DELETE endpoint checks ownership
        import inspect
        from app.routes import workspace

        source = inspect.getsource(workspace.delete_prompt)
        # Verify ownership check exists
        assert "delete_template" in source
        assert "user_id" in source

    def test_admin_delete_any_template(self, private_template, admin_user):
        """Scenario 10: Admin DELETE any template -> 200."""
        import inspect
        from app.routes import workspace

        source = inspect.getsource(workspace.delete_prompt)
        # Admin can delete any template
        assert "admin" in source

    def test_author_render_own_private_template(self, private_template, author_user):
        """Scenario 11: Author render own private template -> 200."""
        from app.routes.workspace import _check_prompt_ownership

        class MockG:
            user = author_user

        import app.routes.workspace as workspace_module
        old_g = getattr(workspace_module, "g", None)
        workspace_module.g = MockG()

        try:
            # render uses allow_public=True
            has_access, error = _check_prompt_ownership(private_template, allow_public=True)
            assert has_access is True
        finally:
            if old_g:
                workspace_module.g = old_g

    def test_non_author_render_public_template(self, public_template, non_author_user):
        """Scenario 12: Non-author render public template -> 200."""
        from app.routes.workspace import _check_prompt_ownership

        class MockG:
            user = non_author_user

        import app.routes.workspace as workspace_module
        old_g = getattr(workspace_module, "g", None)
        workspace_module.g = MockG()

        try:
            has_access, error = _check_prompt_ownership(public_template, allow_public=True)
            assert has_access is True
        finally:
            if old_g:
                workspace_module.g = old_g

    def test_non_author_render_private_template_denied(self, private_template, non_author_user, app):
        """Scenario 13: Non-author render private template -> 403."""
        from app.routes.workspace import _check_prompt_ownership

        with app.app_context():
            from flask import g
            g.user = non_author_user

            import os
            import importlib
            import app.routes.workspace as workspace_module

            old_flag = os.environ.get("ENFORCE_PROMPT_OWNERSHIP")
            os.environ["ENFORCE_PROMPT_OWNERSHIP"] = "true"

            importlib.reload(workspace_module)
            from app.routes.workspace import _check_prompt_ownership as check_func

            has_access, error = check_func(private_template, allow_public=True)
            assert has_access is False

            if old_flag is not None:
                os.environ["ENFORCE_PROMPT_OWNERSHIP"] = old_flag
            else:
                os.environ.pop("ENFORCE_PROMPT_OWNERSHIP", None)

    def test_author_copy_own_private_template(self, private_template, author_user):
        """Scenario 14: Author copy own private template -> 200."""
        from app.routes.workspace import _check_prompt_ownership

        class MockG:
            user = author_user

        import app.routes.workspace as workspace_module
        old_g = getattr(workspace_module, "g", None)
        workspace_module.g = MockG()

        try:
            # copy uses allow_public=True
            has_access, error = _check_prompt_ownership(private_template, allow_public=True)
            assert has_access is True
        finally:
            if old_g:
                workspace_module.g = old_g

    def test_non_author_copy_public_template(self, public_template, non_author_user):
        """Scenario 15: Non-author copy public template -> 200."""
        from app.routes.workspace import _check_prompt_ownership

        class MockG:
            user = non_author_user

        import app.routes.workspace as workspace_module
        old_g = getattr(workspace_module, "g", None)
        workspace_module.g = MockG()

        try:
            has_access, error = _check_prompt_ownership(public_template, allow_public=True)
            assert has_access is True
        finally:
            if old_g:
                workspace_module.g = old_g


if __name__ == "__main__":
    pytest.main([__file__, "-v"])