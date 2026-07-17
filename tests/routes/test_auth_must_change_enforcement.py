#!/usr/bin/env python3
"""
Route tests for must-change-password enforcement on auth blueprint endpoints.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

project_root = str(Path(__file__).resolve().parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)


@pytest.fixture
def app():
    from flask import Flask

    from app.routes.auth import auth_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(auth_bp, url_prefix="/api")
    return app


@pytest.fixture
def client(app):
    return app.test_client()


MUST_CHANGE_SESSION = {
    "user_id": 1,
    "username": "admin",
    "email": "admin@example.com",
    "role": "admin",
    "must_change_password": True,
}


class TestAuthPasswordChangeEnforcement:
    def test_auth_me_stays_accessible_during_forced_password_change(self, client):
        with patch(
            "app.auth.decorators._authenticate",
            return_value=(True, MUST_CHANGE_SESSION),
        ):
            with patch(
                "app.routes.auth.auth_service.get_user_profile",
                return_value={"id": 1, "username": "admin", "must_change_password": True},
            ):
                resp = client.get("/api/auth/me", headers={"Authorization": "Bearer t"})

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["user"]["must_change_password"] is True

    def test_change_password_stays_accessible_during_forced_password_change(self, client):
        with patch(
            "app.auth.decorators._authenticate",
            return_value=(True, MUST_CHANGE_SESSION),
        ):
            with patch(
                "app.routes.auth.auth_service.change_password",
                return_value=(True, None),
            ) as change_password:
                resp = client.post(
                    "/api/auth/change-password",
                    headers={"Authorization": "Bearer t"},
                    json={
                        "current_password": "admin123",
                        "new_password": "Betterpass123",
                    },
                )

        assert resp.status_code == 200
        assert resp.get_json()["success"] is True
        change_password.assert_called_once()

    def test_avatar_upload_is_blocked_until_password_changes(self, client):
        with patch(
            "app.auth.decorators._authenticate",
            return_value=(True, MUST_CHANGE_SESSION),
        ):
            resp = client.post("/api/user/avatar", headers={"Authorization": "Bearer t"})

        assert resp.status_code == 403
        data = resp.get_json()
        assert data["code"] == "password_change_required"

    def test_public_logout_still_succeeds(self, client):
        with patch(
            "app.routes.auth.auth_service.get_session",
            return_value=MUST_CHANGE_SESSION,
        ):
            with patch("app.routes.auth.auth_service.logout", return_value=True) as logout:
                resp = client.post("/api/auth/logout", headers={"Authorization": "Bearer t"})

        assert resp.status_code == 200
        assert resp.get_json()["success"] is True
        logout.assert_called_once_with("t")
