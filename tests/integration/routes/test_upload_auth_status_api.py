#!/usr/bin/env python3
"""
Route tests for upload auth status API (Issue #3327).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

project_root = str(Path(__file__).resolve().parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)


# Mock sessions for _authenticate
MOCK_ADMIN_SESSION = {
    "user_id": 1,
    "username": "admin",
    "email": "admin@example.com",
    "role": "admin",
}


@pytest.fixture
def app():
    """Create a Flask app with the governance blueprint registered."""
    from flask import Flask

    from app.routes.governance import governance_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(governance_bp, url_prefix="/api")
    return app


@pytest.fixture
def client(app):
    """Create a test client."""
    return app.test_client()


def _mock_admin_auth():
    """Mock admin authentication for testing."""
    return patch("app.auth.decorators._authenticate", return_value=(True, MOCK_ADMIN_SESSION))


class TestUploadAuthStatusAPI:
    """Tests for upload authentication status API endpoint (Issue #3327)."""

    def test_get_upload_auth_status_not_configured(self, client, monkeypatch):
        """Should return disabled status when UPLOAD_AUTH_KEY not set."""
        monkeypatch.delenv("UPLOAD_AUTH_KEY", raising=False)
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "development")

        # Reset security mode cache
        from app.utils.security_mode import reset_security_mode_cache

        reset_security_mode_cache()

        with _mock_admin_auth():
            with patch("app.utils.security_env.get_upload_auth_key", return_value=None):
                with patch("app.utils.security_mode.get_security_mode") as mock_mode:
                    mock_mode.return_value = MagicMock(value="development")

                    resp = client.get(
                        "/api/security-settings/upload-auth-status",
                        headers={"Authorization": "Bearer test"},
                    )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["upload_auth_enabled"] is False
        assert data["key_length"] is None
        assert data["is_valid"] is True
        assert data["config_source"] == "environment_variable"

    def test_get_upload_auth_status_enabled(self, client, monkeypatch):
        """Should return enabled status when valid key is set."""
        test_key = "a" * 64  # 64 character key

        with _mock_admin_auth():
            with patch("app.utils.security_env.get_upload_auth_key", return_value=test_key):
                with patch("app.utils.security_mode.get_security_mode") as mock_mode:
                    mock_mode.return_value = MagicMock(value="production")

                    resp = client.get(
                        "/api/security-settings/upload-auth-status",
                        headers={"Authorization": "Bearer test"},
                    )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["upload_auth_enabled"] is True
        assert data["key_length"] == 64
        assert data["is_valid"] is True
        assert data["security_mode"] == "production"

    def test_get_upload_auth_status_weak_key_development(self, client, monkeypatch):
        """Should return invalid status for weak key in development mode."""
        monkeypatch.setenv("UPLOAD_AUTH_KEY", "dev-secret-key")
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "development")

        from app.utils.security_mode import reset_security_mode_cache

        reset_security_mode_cache()

        with _mock_admin_auth():
            with patch("app.utils.security_env.get_upload_auth_key", return_value=None):
                with patch("app.utils.security_mode.get_security_mode") as mock_mode:
                    mock_mode.return_value = MagicMock(value="development")
                    with patch("app.utils.security_mode.is_weak_secret_value", return_value=True):

                        resp = client.get(
                            "/api/security-settings/upload-auth-status",
                            headers={"Authorization": "Bearer test"},
                        )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["upload_auth_enabled"] is False
        assert data["is_valid"] is False
        assert data["validation_error"] is not None

    def test_get_upload_auth_status_production_weak_key(self, client, monkeypatch):
        """Should return 500 for weak key in production mode."""
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "production")

        from app.utils.security_mode import reset_security_mode_cache

        reset_security_mode_cache()

        with _mock_admin_auth():
            with patch("app.utils.security_env.get_upload_auth_key") as mock_get_key:
                mock_get_key.side_effect = RuntimeError(
                    "UPLOAD_AUTH_KEY uses an insecure placeholder value"
                )
                with patch("app.utils.security_mode.get_security_mode") as mock_mode:
                    mock_mode.return_value = MagicMock(value="production")

                    resp = client.get(
                        "/api/security-settings/upload-auth-status",
                        headers={"Authorization": "Bearer test"},
                    )

        assert resp.status_code == 500
        data = resp.get_json()
        assert "error" in data
        assert "invalid" in data["error"]

    def test_get_upload_auth_status_no_key_leak(self, client, monkeypatch):
        """Should not expose the actual key value in response."""
        test_key = "super_secret_key_value_that_should_not_be_exposed_12345"

        with _mock_admin_auth():
            with patch("app.utils.security_env.get_upload_auth_key", return_value=test_key):
                with patch("app.utils.security_mode.get_security_mode") as mock_mode:
                    mock_mode.return_value = MagicMock(value="production")

                    resp = client.get(
                        "/api/security-settings/upload-auth-status",
                        headers={"Authorization": "Bearer test"},
                    )

        assert resp.status_code == 200
        data = resp.get_json()

        # Should not contain the actual key value
        assert "super_secret_key_value" not in str(data)
        assert test_key not in str(data)

        # Should only contain length, not the key itself
        assert "key_length" in data
        assert data["key_length"] == len(test_key)

    def test_get_upload_auth_status_includes_fix_suggestion(self, client, monkeypatch):
        """Should include fix suggestion when key not configured."""
        with _mock_admin_auth():
            with patch("app.utils.security_env.get_upload_auth_key", return_value=None):
                with patch("app.utils.security_mode.get_security_mode") as mock_mode:
                    mock_mode.return_value = MagicMock(value="development")
                    with patch("app.utils.security_mode.is_weak_secret_value", return_value=False):

                        resp = client.get(
                            "/api/security-settings/upload-auth-status",
                            headers={"Authorization": "Bearer test"},
                        )

        assert resp.status_code == 200
        data = resp.get_json()
        assert "fix_suggestion" in data
        assert "部署文档" in data["fix_suggestion"] or "UPLOAD_AUTH_KEY" in data["fix_suggestion"]

    def test_get_upload_auth_status_includes_checked_at(self, client, monkeypatch):
        """Should include checked_at timestamp in response."""
        with _mock_admin_auth():
            with patch("app.utils.security_env.get_upload_auth_key", return_value="a" * 64):
                with patch("app.utils.security_mode.get_security_mode") as mock_mode:
                    mock_mode.return_value = MagicMock(value="development")

                    resp = client.get(
                        "/api/security-settings/upload-auth-status",
                        headers={"Authorization": "Bearer test"},
                    )

        assert resp.status_code == 200
        data = resp.get_json()
        assert "checked_at" in data
        # Should be ISO format with Z suffix
        assert data["checked_at"].endswith("Z")

    def test_unauthenticated_returns_401(self, client):
        """Unauthenticated requests should get 401."""
        resp = client.get("/api/security-settings/upload-auth-status")
        assert resp.status_code == 401

    def test_regular_user_cannot_access(self, client):
        """Regular users should get 403 from upload auth status."""
        mock_user_session = {
            "user_id": 42,
            "username": "testuser",
            "email": "testuser@example.com",
            "role": "user",
        }

        with patch(
            "app.auth.decorators._authenticate",
            return_value=(True, mock_user_session),
        ):
            resp = client.get(
                "/api/security-settings/upload-auth-status",
                headers={"Authorization": "Bearer test"},
            )

        assert resp.status_code == 403