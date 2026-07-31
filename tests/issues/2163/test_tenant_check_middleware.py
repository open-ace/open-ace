"""
Unit tests for tenant check middleware (Issue #2163).
"""

import pytest
from flask import Flask, g, jsonify
from werkzeug.test import Client

from app.middleware.tenant_check import (
    TenantMigratedError,
    SessionExpiredError,
    check_tenant_version,
    handle_tenant_migrated_error,
    handle_session_expired_error,
    init_tenant_check_middleware,
)


class TestTenantCheckMiddleware:
    """Test suite for tenant check middleware."""

    @pytest.fixture
    def app(self):
        """Create Flask app for testing."""
        app = Flask(__name__)
        app.config["TESTING"] = True
        init_tenant_check_middleware(app)
        return app

    @pytest.fixture
    def client(self, app):
        """Create test client."""
        return Client(app)

    def test_check_tenant_version_no_user(self, app):
        """Test check when no user context."""
        with app.test_request_context():
            g.user = None
            # Should not raise
            check_tenant_version()

    def test_check_tenant_version_no_session(self, app):
        """Test check when no session context."""
        with app.test_request_context():
            g.user = {"id": 1, "tenant_version": 1}
            g.session = None
            # Should not raise
            check_tenant_version()

    def test_check_tenant_version_match(self, app):
        """Test check when versions match."""
        with app.test_request_context():
            g.user = {"id": 1, "tenant_version": 1}
            g.session = {"id": 1, "tenant_version": 1}
            # Should not raise
            check_tenant_version()

    def test_check_tenant_version_mismatch(self, app):
        """Test check when versions mismatch."""
        with app.test_request_context():
            g.user = {"id": 1, "tenant_version": 2, "tenant_id": 2}
            g.session = {"id": 1, "tenant_version": 1, "tenant_id": 1}

            # Should raise TenantMigratedError
            with pytest.raises(TenantMigratedError):
                check_tenant_version()

    def test_tenant_migrated_error_response(self, app):
        """Test TenantMigratedError response."""
        error = TenantMigratedError(old_tenant_id=1, new_tenant_id=2)

        with app.app_context():
            response = handle_tenant_migrated_error(error)
            data = response[0].get_json()

            assert data["error"] == "TENANT_MIGRATED"
            assert data["code"] == "AUTH_002"
            assert "migrated to a new tenant" in data["message"]
            assert data["new_tenant_id"] == 2

    def test_session_expired_error_response(self, app):
        """Test SessionExpiredError response."""
        error = SessionExpiredError()

        with app.app_context():
            response = handle_session_expired_error(error)
            data = response[0].get_json()

            assert data["error"] == "SESSION_EXPIRED"
            assert data["code"] == "AUTH_001"
            assert "session has expired" in data["message"]

    def test_internationalization_tenant_migrated(self, app):
        """Test internationalization for tenant migrated."""
        error = TenantMigratedError(new_tenant_id=2)

        with app.app_context():
            response = handle_tenant_migrated_error(error)
            data = response[0].get_json()

            assert "message_zh" in data
            assert "message_ja" in data
            assert "message_ko" in data
            assert "迁移" in data["message_zh"]  # Chinese

    def test_internationalization_session_expired(self, app):
        """Test internationalization for session expired."""
        error = SessionExpiredError()

        with app.app_context():
            response = handle_session_expired_error(error)
            data = response[0].get_json()

            assert "message_zh" in data
            assert "message_ja" in data
            assert "message_ko" in data
            assert "过期" in data["message_zh"]  # Chinese


class TestTenantMigratedError:
    """Test suite for TenantMigratedError."""

    def test_error_creation(self):
        """Test creating TenantMigratedError."""
        error = TenantMigratedError(old_tenant_id=1, new_tenant_id=2)

        assert error.code == 401
        assert error.old_tenant_id == 1
        assert error.new_tenant_id == 2

    def test_error_description(self):
        """Test error description."""
        error = TenantMigratedError()

        assert error.description == "Tenant migrated"


class TestSessionExpiredError:
    """Test suite for SessionExpiredError."""

    def test_error_creation(self):
        """Test creating SessionExpiredError."""
        error = SessionExpiredError()

        assert error.code == 401
        assert error.description == "Session expired"