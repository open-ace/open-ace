#!/usr/bin/env python3
"""
Test that session tokens from query parameters are rejected.

Issue #1896: Session tokens should not be accepted from query parameters
to prevent credential leakage through URLs.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is on path
project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)


def _make_app():
    """Create a minimal Flask app with test routes."""
    from flask import Flask, g, jsonify

    from app.auth.decorators import auth_required

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-key"

    @app.route("/api/user")
    @auth_required
    def user_route():
        return jsonify({"user_id": g.user_id, "role": g.user_role})

    return app


MOCK_SESSION = {
    "user_id": 42,
    "username": "testuser",
    "email": "test@example.com",
    "role": "user",
    "must_change_password": False,
}


class TestSessionTokenQueryRejected:
    """Test that session tokens from query parameters are rejected."""

    def test_session_token_from_cookie_accepted(self):
        """Session token from cookie should be accepted."""
        app = _make_app()

        with patch(
            "app.auth.decorators._authenticate",
            return_value=(True, MOCK_SESSION),
        ):
            with app.test_client() as client:
                client.set_cookie("session_token", "valid-session-token")
                resp = client.get("/api/user")
                assert resp.status_code == 200
                data = resp.get_json()
                assert data["user_id"] == 42

    def test_session_token_from_header_accepted(self):
        """Session token from Authorization header should be accepted."""
        app = _make_app()

        with patch(
            "app.auth.decorators._authenticate",
            return_value=(True, MOCK_SESSION),
        ):
            with app.test_client() as client:
                resp = client.get(
                    "/api/user",
                    headers={"Authorization": "Bearer valid-session-token"},
                )
                assert resp.status_code == 200
                data = resp.get_json()
                assert data["user_id"] == 42

    def test_session_token_from_query_rejected(self):
        """Session token from query parameter should be rejected."""
        app = _make_app()

        with patch(
            "app.auth.decorators._authenticate",
            return_value=(True, MOCK_SESSION),
        ):
            with app.test_client() as client:
                resp = client.get("/api/user?token=valid-session-token")
                assert resp.status_code == 401

    def test_cookie_overrides_query_token(self):
        """When both cookie and query token are present, use cookie (ignore query)."""
        app = _make_app()

        mock_session_user = {
            "user_id": 99,
            "username": "cookieuser",
            "email": "cookie@example.com",
            "role": "user",
            "must_change_password": False,
        }

        with patch(
            "app.auth.decorators._authenticate",
            return_value=(True, mock_session_user),
        ):
            with app.test_client() as client:
                # Cookie should be used, query param should be ignored
                client.set_cookie("session_token", "valid-cookie-token")
                resp = client.get("/api/user?token=wrong-token")
                assert resp.status_code == 200
                data = resp.get_json()
                assert data["user_id"] == 99  # From cookie, not query

    def test_header_overrides_query_token(self):
        """When both header and query token are present, use header (ignore query)."""
        app = _make_app()

        mock_session_user = {
            "user_id": 88,
            "username": "headeruser",
            "email": "header@example.com",
            "role": "user",
            "must_change_password": False,
        }

        with patch(
            "app.auth.decorators._authenticate",
            return_value=(True, mock_session_user),
        ):
            with app.test_client() as client:
                # Header should be used, query param should be ignored
                resp = client.get(
                    "/api/user?token=wrong-token",
                    headers={"Authorization": "Bearer valid-header-token"},
                )
                assert resp.status_code == 200
                data = resp.get_json()
                assert data["user_id"] == 88  # From header, not query


if __name__ == "__main__":
    pytest.main([__file__, "-v"])