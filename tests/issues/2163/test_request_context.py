"""
Unit tests for request context utilities (Issue #2163).
"""

import pytest
from flask import Flask, g
from werkzeug.exceptions import BadRequest

from app.utils.request_context import (
    get_current_user,
    get_current_tenant_id,
    require_tenant_id,
    get_current_tenant_version,
    get_current_user_id,
)


class TestRequestContext:
    """Test suite for request context utilities."""

    @pytest.fixture
    def app(self):
        """Create Flask app for testing."""
        app = Flask(__name__)
        app.config["TESTING"] = True
        return app

    def test_get_current_user_no_context(self, app):
        """Test get_current_user when no user context."""
        with app.test_request_context():
            result = get_current_user()
            assert result is None

    def test_get_current_user_with_context(self, app):
        """Test get_current_user with user context."""
        with app.test_request_context():
            g.user = {"id": 123, "username": "test"}
            result = get_current_user()
            assert result == {"id": 123, "username": "test"}

    def test_get_current_user_invalid_type(self, app):
        """Test get_current_user with invalid type."""
        with app.test_request_context():
            g.user = "invalid"
            result = get_current_user()
            assert result is None

    def test_get_current_tenant_id_no_user(self, app):
        """Test get_current_tenant_id without user."""
        with app.test_request_context():
            result = get_current_tenant_id()
            assert result is None

    def test_get_current_tenant_id_with_tenant(self, app):
        """Test get_current_tenant_id with tenant."""
        with app.test_request_context():
            g.user = {"id": 1, "tenant_id": 42}
            result = get_current_tenant_id()
            assert result == 42

    def test_get_current_tenant_id_no_tenant(self, app):
        """Test get_current_tenant_id without tenant in user."""
        with app.test_request_context():
            g.user = {"id": 1, "username": "test"}
            result = get_current_tenant_id()
            assert result is None

    def test_require_tenant_id_success(self, app):
        """Test require_tenant_id with tenant available."""
        with app.test_request_context():
            g.user = {"id": 1, "tenant_id": 99}
            result = require_tenant_id()
            assert result == 99

    def test_require_tenant_id_failure(self, app):
        """Test require_tenant_id raises error when not available."""
        with app.test_request_context():
            with pytest.raises(BadRequest) as exc_info:
                require_tenant_id()
            assert "Tenant context required" in str(exc_info.value)

    def test_get_current_tenant_version_no_user(self, app):
        """Test get_current_tenant_version without user."""
        with app.test_request_context():
            result = get_current_tenant_version()
            assert result is None

    def test_get_current_tenant_version_with_version(self, app):
        """Test get_current_tenant_version with version."""
        with app.test_request_context():
            g.user = {"id": 1, "tenant_version": 5}
            result = get_current_tenant_version()
            assert result == 5

    def test_get_current_user_id_no_user(self, app):
        """Test get_current_user_id without user."""
        with app.test_request_context():
            result = get_current_user_id()
            assert result is None

    def test_get_current_user_id_with_user(self, app):
        """Test get_current_user_id with user."""
        with app.test_request_context():
            g.user = {"id": 456, "username": "test"}
            result = get_current_user_id()
            assert result == 456