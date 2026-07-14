#!/usr/bin/env python3
"""
Tests for password policy API endpoint permissions (Issue #1647).

These tests verify the core access-control guarantees of the fix:
1. Regular users can access /api/password-policy (200)
2. Regular users cannot access /api/security-settings (403)
3. Admins can access both endpoints (200)
4. Unauthenticated users get 401 for both endpoints
5. The password-policy response only contains password fields

They run under CI's default pytest collection (tests/routes/ is collected,
unlike tests/e2e/). They use the Flask test client with mocked auth instead
of hitting an external server, so they do not depend on a running instance.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is on path
project_root = str(Path(__file__).resolve().parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)


# ---------------------------------------------------------------------------
# Mock sessions for _authenticate
# ---------------------------------------------------------------------------

MOCK_USER_SESSION = {
    "user_id": 42,
    "username": "testuser",
    "email": "testuser@example.com",
    "role": "user",
}

MOCK_ADMIN_SESSION = {
    "user_id": 1,
    "username": "admin",
    "email": "admin@example.com",
    "role": "admin",
}

# Password policy fields returned by the endpoint
PASSWORD_POLICY_FIELDS = {
    "password_min_length",
    "password_require_uppercase",
    "password_require_lowercase",
    "password_require_number",
    "password_require_special",
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


def _mock_repo():
    """Create a mock governance repo backing the routes.

    The governance blueprint instantiates a module-level ``governance_repo``;
    we patch its methods rather than the class so the route handlers use our
    canned data.
    """
    repo = MagicMock()
    repo.get_password_policy.return_value = {
        "password_min_length": 8,
        "password_require_uppercase": True,
        "password_require_lowercase": True,
        "password_require_number": True,
        "password_require_special": False,
    }
    repo.get_security_settings.return_value = {
        "password_min_length": 8,
        "password_require_uppercase": True,
        "password_require_lowercase": True,
        "password_require_number": True,
        "password_require_special": False,
        "session_timeout": 60,
        "max_login_attempts": 5,
        "two_factor_enabled": False,
        "ip_whitelist": [],
    }
    return repo


class TestPasswordPolicyPermissions:
    """Access-control tests for /api/password-policy and /api/security-settings."""

    def test_regular_user_can_access_password_policy(self, client):
        """Regular users should get 200 from /api/password-policy."""
        repo = _mock_repo()
        with patch(
            "app.auth.decorators._authenticate",
            return_value=(True, MOCK_USER_SESSION),
        ):
            with patch("app.routes.governance.governance_repo", repo):
                resp = client.get("/api/password-policy", headers={"Authorization": "Bearer t"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert set(data.keys()) == PASSWORD_POLICY_FIELDS

    def test_regular_user_cannot_access_security_settings(self, client):
        """Regular users should get 403 from /api/security-settings."""
        repo = _mock_repo()
        with patch(
            "app.auth.decorators._authenticate",
            return_value=(True, MOCK_USER_SESSION),
        ):
            with patch("app.routes.governance.governance_repo", repo):
                resp = client.get("/api/security-settings", headers={"Authorization": "Bearer t"})
        assert resp.status_code == 403

    def test_admin_can_access_password_policy(self, client):
        """Admins should get 200 from /api/password-policy."""
        repo = _mock_repo()
        with patch(
            "app.auth.decorators._authenticate",
            return_value=(True, MOCK_ADMIN_SESSION),
        ):
            with patch("app.routes.governance.governance_repo", repo):
                resp = client.get("/api/password-policy", headers={"Authorization": "Bearer t"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "password_min_length" in data

    def test_admin_can_access_security_settings(self, client):
        """Admins should get 200 from /api/security-settings."""
        repo = _mock_repo()
        with patch(
            "app.auth.decorators._authenticate",
            return_value=(True, MOCK_ADMIN_SESSION),
        ):
            with patch("app.routes.governance.governance_repo", repo):
                resp = client.get("/api/security-settings", headers={"Authorization": "Bearer t"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "password_min_length" in data
        assert "session_timeout" in data

    def test_unauthenticated_password_policy_returns_401(self, client):
        """Unauthenticated requests to /api/password-policy should get 401."""
        resp = client.get("/api/password-policy")
        assert resp.status_code == 401

    def test_unauthenticated_security_settings_returns_401(self, client):
        """Unauthenticated requests to /api/security-settings should get 401."""
        resp = client.get("/api/security-settings")
        assert resp.status_code == 401

    def test_password_policy_excludes_sensitive_fields(self, client):
        """password-policy response must not leak non-password settings."""
        repo = _mock_repo()
        with patch(
            "app.auth.decorators._authenticate",
            return_value=(True, MOCK_USER_SESSION),
        ):
            with patch("app.routes.governance.governance_repo", repo):
                resp = client.get("/api/password-policy", headers={"Authorization": "Bearer t"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "session_timeout" not in data
        assert "max_login_attempts" not in data
        assert "two_factor_enabled" not in data
        assert "ip_whitelist" not in data


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
