#!/usr/bin/env python3
"""
Unit tests for app.auth.decorators module.

Tests the unified auth decorator framework:
- Token extraction from cookie, header, and query param
- @auth_required: accepts any authenticated user
- @admin_required: rejects non-admin users
- @public_endpoint: marks route as intentionally public
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
# Helpers
# ---------------------------------------------------------------------------


def _make_app():
    """Create a minimal Flask app with test routes."""
    from flask import Flask, g, jsonify

    from app.auth.decorators import admin_required, auth_required, public_endpoint

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-key"

    @app.route("/api/public")
    @public_endpoint
    def public_route():
        return jsonify({"ok": True})

    @app.route("/api/user")
    @auth_required
    def user_route():
        return jsonify({"user_id": g.user_id, "role": g.user_role})

    @app.route("/api/admin")
    @admin_required
    def admin_route():
        return jsonify({"user_id": g.user_id, "role": g.user_role})

    return app


MOCK_USER = {
    "id": 42,
    "username": "testuser",
    "email": "test@example.com",
    "role": "user",
}

MOCK_ADMIN = {
    "id": 1,
    "username": "admin",
    "email": "admin@example.com",
    "role": "admin",
}

MOCK_SESSION = {
    "user_id": 42,
    "username": "testuser",
    "email": "test@example.com",
    "role": "user",
}

MOCK_ADMIN_SESSION = {
    "user_id": 1,
    "username": "admin",
    "email": "admin@example.com",
    "role": "admin",
}


# ---------------------------------------------------------------------------
# Token extraction tests
# ---------------------------------------------------------------------------


class TestExtractToken:
    """Test _extract_token helper."""

    def test_cookie_token(self):
        from app.auth.decorators import _extract_token

        app = _make_app()

        with app.test_request_context("/"):
            # Simulate cookie
            from flask import request

            with patch.object(request, "cookies", {"session_token": "tok-cookie"}):
                assert _extract_token() == "tok-cookie"

    def test_header_bearer_token(self):
        from app.auth.decorators import _extract_token

        app = _make_app()

        with app.test_request_context("/", headers={"Authorization": "Bearer tok-header"}):
            assert _extract_token() == "tok-header"

    def test_query_param_token(self):
        from app.auth.decorators import _extract_token

        app = _make_app()

        with app.test_request_context("/?token=tok-query"):
            assert _extract_token() == "tok-query"

    def test_no_token(self):
        from app.auth.decorators import _extract_token

        app = _make_app()

        with app.test_request_context("/"):
            assert _extract_token() == ""


# ---------------------------------------------------------------------------
# Decorator tests
# ---------------------------------------------------------------------------


class TestAuthRequired:
    """Test @auth_required decorator."""

    def _mock_auth(self, user_session):
        """Return a patcher that mocks _authenticate."""
        return patch(
            "app.auth.decorators._authenticate",
            return_value=(True, user_session),
        )

    def test_authenticated_user_gets_access(self):
        app = _make_app()

        with self._mock_auth(MOCK_SESSION):
            with app.test_client() as client:
                resp = client.get("/api/user", headers={"Authorization": "Bearer t"})
                assert resp.status_code == 200
                data = resp.get_json()
                assert data["user_id"] == 42
                assert data["role"] == "user"

    def test_no_token_returns_401(self):
        app = _make_app()

        with app.test_client() as client:
            resp = client.get("/api/user")
            assert resp.status_code == 401

    def test_invalid_token_returns_401(self):
        app = _make_app()

        with patch(
            "app.auth.decorators._authenticate",
            return_value=(False, {"error": "Invalid"}),
        ):
            with app.test_client() as client:
                resp = client.get("/api/user", headers={"Authorization": "Bearer bad"})
                assert resp.status_code == 401


class TestAdminRequired:
    """Test @admin_required decorator."""

    def test_admin_gets_access(self):
        app = _make_app()

        with patch(
            "app.auth.decorators._authenticate",
            return_value=(True, MOCK_ADMIN_SESSION),
        ):
            with app.test_client() as client:
                resp = client.get("/api/admin", headers={"Authorization": "Bearer t"})
                assert resp.status_code == 200
                data = resp.get_json()
                assert data["role"] == "admin"

    def test_non_admin_gets_403(self):
        app = _make_app()

        with patch(
            "app.auth.decorators._authenticate",
            return_value=(True, MOCK_SESSION),
        ):
            with app.test_client() as client:
                resp = client.get("/api/admin", headers={"Authorization": "Bearer t"})
                assert resp.status_code == 403

    def test_no_token_returns_401(self):
        app = _make_app()

        with app.test_client() as client:
            resp = client.get("/api/admin")
            assert resp.status_code == 401


class TestPublicEndpoint:
    """Test @public_endpoint decorator."""

    def test_public_no_auth_needed(self):
        app = _make_app()

        with app.test_client() as client:
            resp = client.get("/api/public")
            assert resp.status_code == 200

    def test_public_endpoint_has_marker(self):
        """Verify the _is_public_endpoint attribute is set."""
        from app.auth.decorators import public_endpoint

        @public_endpoint
        def my_route():
            pass

        assert my_route._is_public_endpoint is True


# ---------------------------------------------------------------------------
# g.user attributes tests
# ---------------------------------------------------------------------------


class TestGUserAttributes:
    """Verify that decorators set g.user, g.user_id, g.user_role correctly."""

    def test_g_attributes_set(self):
        app = _make_app()

        with patch(
            "app.auth.decorators._authenticate",
            return_value=(True, MOCK_SESSION),
        ):
            with app.test_client() as client:
                resp = client.get("/api/user", headers={"Authorization": "Bearer t"})
                assert resp.status_code == 200
                data = resp.get_json()
                assert data["user_id"] == 42
                assert data["role"] == "user"
