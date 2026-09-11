"""Unit tests for the workspace isolation capability endpoint (Issue #3374)."""

from unittest.mock import patch

import pytest
from flask import Flask

from app.services import workspace_isolation_contract as wic

pytestmark = [pytest.mark.issue(3374)]

MOCK_SESSION = (True, {"user_id": 42, "username": "alice", "role": "user", "tenant_id": 1})


class _StubConfig:
    enabled = True
    multi_user_mode = True


class _StubManager:
    config = _StubConfig()


@pytest.fixture
def isolation_app():
    """Flask app with only the workspace_isolation blueprint."""
    from app.routes.workspace_isolation import workspace_isolation_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(workspace_isolation_bp)
    return app


@pytest.fixture
def stub_snapshot():
    return wic.build_workspace_isolation_snapshot(_StubManager())


def test_requires_authentication(isolation_app):
    resp = isolation_app.test_client().get("/workspace/isolation-capabilities")
    assert resp.status_code == 401


def test_returns_contract_for_authenticated_user(isolation_app, stub_snapshot):
    client = isolation_app.test_client()
    with (
        patch(
            "app.routes.workspace_isolation.build_workspace_isolation_snapshot",
            return_value=stub_snapshot,
        ),
        patch("app.auth.decorators._authenticate", return_value=MOCK_SESSION),
    ):
        resp = client.get(
            "/workspace/isolation-capabilities",
            headers={"Authorization": "Bearer test-token"},
        )
    assert resp.status_code == 200
    assert resp.get_json() == stub_snapshot.public_dict()


def test_internal_error_is_structured(isolation_app):
    client = isolation_app.test_client()
    with (
        patch(
            "app.routes.workspace_isolation.build_workspace_isolation_snapshot",
            side_effect=RuntimeError("boom"),
        ),
        patch("app.auth.decorators._authenticate", return_value=MOCK_SESSION),
    ):
        resp = client.get(
            "/workspace/isolation-capabilities",
            headers={"Authorization": "Bearer test-token"},
        )
    assert resp.status_code == 500
    assert resp.get_json() == {"error": "Internal server error"}
