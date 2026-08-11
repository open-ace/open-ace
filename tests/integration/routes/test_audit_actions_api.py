from unittest.mock import patch

import pytest

MOCK_ADMIN_SESSION = {
    "user_id": 1,
    "username": "admin",
    "email": "admin@example.com",
    "role": "admin",
}


@pytest.fixture
def app():
    from flask import Flask

    from app.routes.governance import governance_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(governance_bp, url_prefix="/api")
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def test_audit_actions_api_includes_resource_mapping(client):
    with patch("app.auth.decorators._authenticate", return_value=(True, MOCK_ADMIN_SESSION)):
        resp = client.get("/api/audit-actions", headers={"Authorization": "Bearer t"})

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["actionToCategory"]["login"] == "auth"
    assert data["actionToResourceTypes"]["login"] == ["session"]
    assert data["actionToResourceTypes"]["user_create"] == ["user"]
    assert "data" in data["actionToResourceTypes"]["data_export"]
    assert "agent" in data["resourceToCategories"]["remote_machine"]

    auth_category = next(category for category in data["categories"] if category["key"] == "auth")
    assert auth_category["resource_types"] == ["session"]
    login_action = next(action for action in data["actions"] if action["value"] == "login")
    assert login_action["resource_types"] == ["session"]
