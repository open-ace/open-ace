"""Route tests for manual Feishu org sync admin API."""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture
def app():
    """Create a minimal Flask app with admin routes."""
    from flask import Flask

    from app.routes.admin import admin_bp

    app = Flask(__name__)
    app.register_blueprint(admin_bp, url_prefix="/api")
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-key"
    return app


@pytest.fixture
def admin_client(app):
    """Create an admin-authenticated client."""
    test_client = app.test_client()

    class AuthenticatedClient:
        def __init__(self, client):
            self._client = client

        def post(self, *args, **kwargs):
            with patch("app.auth.decorators._extract_token", return_value="test-token"):
                with patch(
                    "app.auth.decorators._load_user_from_token",
                    return_value={"id": 1, "role": "admin", "username": "test_admin"},
                ):
                    return self._client.post(*args, **kwargs)

    return AuthenticatedClient(test_client)


def test_manual_feishu_sync_returns_summary(admin_client):
    """Admin API should return the sync result payload."""

    class DummyResult:
        def to_dict(self):
            return {"tenant_id": 3, "users_seen": 2, "teams_created": 1}

    with patch(
        "app.services.feishu_org_sync.FeishuOrgSyncService.sync_org",
        return_value=DummyResult(),
    ) as sync_org:
        response = admin_client.post("/api/admin/feishu/sync", json={"tenant_id": 3})

    assert response.status_code == 200
    assert response.get_json() == {
        "success": True,
        "result": {"tenant_id": 3, "users_seen": 2, "teams_created": 1},
    }
    sync_org.assert_called_once_with(tenant_id=3)


def test_manual_feishu_sync_validates_tenant_id(admin_client):
    """Admin API should reject non-integer tenant_id values."""
    response = admin_client.post("/api/admin/feishu/sync", json={"tenant_id": "oops"})
    assert response.status_code == 400
    assert "tenant_id" in response.get_json()["error"]
