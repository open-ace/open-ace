#!/usr/bin/env python3
"""
Tests for GET /api/workspace/session-models route.

Tests local, remote (by session_id), and remote (by machine_id) paths.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask, g

from app.routes.workspace import workspace_bp

_PROXY_PATH = "app.routes.workspace.get_api_key_proxy_service"
_SESSION_MGR_PATH = "app.routes.workspace.get_session_manager"
_AUTH_PATH = "app.routes.workspace._load_user_from_token"
_EXTRACT_PATH = "app.routes.workspace._extract_token"
_AGENT_MGR_PATH = "app.modules.workspace.remote_agent_manager.get_remote_agent_manager"


@pytest.fixture
def app():
    """Flask app with workspace blueprint."""
    _app = Flask(__name__)
    _app.config["TESTING"] = True
    _app.register_blueprint(workspace_bp, url_prefix="/api/workspace")
    return _app


def _mock_user(user_id=1, role="admin"):
    return {"id": user_id, "username": "testuser", "role": role}


def _mock_proxy():
    """Return a basic mock proxy for all routes."""
    m = MagicMock()
    m.get_tool_model_pool.return_value = {
        "models": [{"id": "qwen3"}],
        "candidate_keys": [],
        "model_key_ids": {},
        "settings": {},
        "empty_reason": None,
    }
    m.generate_proxy_token.return_value = "ha-pool-tok"
    return m


class TestSessionModelsLocal:
    """Test GET /session-models?workspace_type=local."""

    @patch(_PROXY_PATH)
    @patch(_AUTH_PATH)
    @patch(_EXTRACT_PATH)
    def test_local_returns_models(self, mock_extract, mock_auth, mock_get_proxy, app):
        mock_extract.return_value = "tok"
        mock_auth.return_value = _mock_user()
        mock_proxy = _mock_proxy()
        mock_proxy.get_tool_model_pool.return_value = {
            "models": [{"id": "qwen3", "name": "Qwen3"}],
            "empty_reason": None,
        }
        mock_get_proxy.return_value = mock_proxy

        client = app.test_client()
        resp = client.get(
            "/api/workspace/session-models?workspace_type=local",
            headers={"Authorization": "Bearer tok"},
        )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["models"]) == 1
        assert data["models"][0]["id"] == "qwen3"
        mock_proxy.get_tool_model_pool.assert_called_once_with(
            tenant_id=1, tool_name="qwen-code", scope="local", provider="openai"
        )


class TestSessionModelsRemoteBySession:
    """Test GET /session-models?workspace_type=remote&session_id=xxx."""

    @patch(_PROXY_PATH)
    @patch(_SESSION_MGR_PATH)
    @patch(_AUTH_PATH)
    @patch(_EXTRACT_PATH)
    def test_remote_by_session_id(
        self, mock_extract, mock_auth, mock_get_smgr, mock_get_proxy, app
    ):
        mock_extract.return_value = "tok"
        mock_auth.return_value = _mock_user(user_id=1)

        mock_session = MagicMock()
        mock_session.user_id = 1
        mock_session.context = {
            "ha_pool": {
                "models": [{"id": "qwen3", "name": "Qwen3"}],
                "empty_reason": None,
            }
        }
        mock_smgr = MagicMock()
        mock_smgr.get_session.return_value = mock_session
        mock_get_smgr.return_value = mock_smgr
        mock_get_proxy.return_value = _mock_proxy()

        client = app.test_client()
        resp = client.get(
            "/api/workspace/session-models?workspace_type=remote&session_id=sess-123",
            headers={"Authorization": "Bearer tok"},
        )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["models"]) == 1

    @patch(_PROXY_PATH)
    @patch(_SESSION_MGR_PATH)
    @patch(_AUTH_PATH)
    @patch(_EXTRACT_PATH)
    def test_remote_session_not_found(
        self, mock_extract, mock_auth, mock_get_smgr, mock_get_proxy, app
    ):
        mock_extract.return_value = "tok"
        mock_auth.return_value = _mock_user()

        mock_smgr = MagicMock()
        mock_smgr.get_session.return_value = None
        mock_get_smgr.return_value = mock_smgr
        mock_get_proxy.return_value = _mock_proxy()

        client = app.test_client()
        resp = client.get(
            "/api/workspace/session-models?workspace_type=remote&session_id=nonexistent",
            headers={"Authorization": "Bearer tok"},
        )

        assert resp.status_code == 404

    @patch(_PROXY_PATH)
    @patch(_SESSION_MGR_PATH)
    @patch(_AUTH_PATH)
    @patch(_EXTRACT_PATH)
    def test_remote_session_wrong_user(
        self, mock_extract, mock_auth, mock_get_smgr, mock_get_proxy, app
    ):
        mock_extract.return_value = "tok"
        mock_auth.return_value = _mock_user(user_id=1)

        mock_session = MagicMock()
        mock_session.user_id = 999  # different user
        mock_session.context = {"ha_pool": {"models": []}}
        mock_smgr = MagicMock()
        mock_smgr.get_session.return_value = mock_session
        mock_get_smgr.return_value = mock_smgr
        mock_get_proxy.return_value = _mock_proxy()

        client = app.test_client()
        resp = client.get(
            "/api/workspace/session-models?workspace_type=remote&session_id=sess-123",
            headers={"Authorization": "Bearer tok"},
        )

        assert resp.status_code == 403


class TestSessionModelsRemoteByMachine:
    """Test GET /session-models?workspace_type=remote&machine_id=xxx."""

    @patch(_PROXY_PATH)
    @patch(_AGENT_MGR_PATH)
    @patch(_AUTH_PATH)
    @patch(_EXTRACT_PATH)
    def test_remote_by_machine_generates_token(
        self, mock_extract, mock_auth, mock_get_aggr, mock_get_proxy, app
    ):
        mock_extract.return_value = "tok"
        mock_auth.return_value = _mock_user(user_id=1)

        mock_agent_mgr = MagicMock()
        mock_agent_mgr.check_user_access.return_value = True
        mock_agent_mgr.get_machine.return_value = {"tenant_id": 1}
        mock_get_aggr.return_value = mock_agent_mgr

        mock_proxy = _mock_proxy()
        mock_proxy.get_tool_model_pool.return_value = {
            "models": [{"id": "qwen3"}],
            "candidate_keys": [{"key_id": 10}],
            "model_key_ids": {"qwen3": [10]},
            "settings": {},
            "empty_reason": None,
        }
        mock_proxy.generate_proxy_token.return_value = "ha-pool-tok-123"
        mock_get_proxy.return_value = mock_proxy

        client = app.test_client()
        resp = client.get(
            "/api/workspace/session-models?workspace_type=remote&machine_id=mac-1",
            headers={"Authorization": "Bearer tok"},
        )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["ha_pool_token"] == "ha-pool-tok-123"
        mock_proxy.generate_proxy_token.assert_called_once()
        gen_kwargs = mock_proxy.generate_proxy_token.call_args.kwargs
        assert gen_kwargs["session_type"] == "ha_pool"
        assert "ha_candidate_keys" in gen_kwargs["extra_payload"]

    @patch(_PROXY_PATH)
    @patch(_AGENT_MGR_PATH)
    @patch(_AUTH_PATH)
    @patch(_EXTRACT_PATH)
    def test_remote_machine_no_access(
        self, mock_extract, mock_auth, mock_get_aggr, mock_get_proxy, app
    ):
        mock_extract.return_value = "tok"
        mock_auth.return_value = _mock_user(user_id=1)

        mock_agent_mgr = MagicMock()
        mock_agent_mgr.check_user_access.return_value = False
        mock_get_aggr.return_value = mock_agent_mgr
        mock_get_proxy.return_value = _mock_proxy()

        client = app.test_client()
        resp = client.get(
            "/api/workspace/session-models?workspace_type=remote&machine_id=mac-1",
            headers={"Authorization": "Bearer tok"},
        )

        assert resp.status_code == 404


class TestSessionModelsValidation:
    """Test input validation for session-models route."""

    @patch(_PROXY_PATH)
    @patch(_AUTH_PATH)
    @patch(_EXTRACT_PATH)
    def test_remote_no_machine_no_session(self, mock_extract, mock_auth, mock_get_proxy, app):
        mock_extract.return_value = "tok"
        mock_auth.return_value = _mock_user()
        mock_get_proxy.return_value = _mock_proxy()

        client = app.test_client()
        resp = client.get(
            "/api/workspace/session-models?workspace_type=remote",
            headers={"Authorization": "Bearer tok"},
        )

        assert resp.status_code == 400
        assert "machine_id or session_id" in resp.get_json()["error"]

    @patch(_PROXY_PATH)
    @patch(_AUTH_PATH)
    @patch(_EXTRACT_PATH)
    def test_invalid_workspace_type(self, mock_extract, mock_auth, mock_get_proxy, app):
        mock_extract.return_value = "tok"
        mock_auth.return_value = _mock_user()
        mock_get_proxy.return_value = _mock_proxy()

        client = app.test_client()
        resp = client.get(
            "/api/workspace/session-models?workspace_type=invalid",
            headers={"Authorization": "Bearer tok"},
        )

        assert resp.status_code == 400


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
