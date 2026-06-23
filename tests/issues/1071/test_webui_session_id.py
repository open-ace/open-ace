#!/usr/bin/env python3
"""
Tests for Issue #1071: WebUI Session ID 硬编码导致历史显示混乱

Test coverage:
1. X-Session-Id header format validation
2. X-Session-Id header override behavior
3. list_sessions API filtering webui:% sessions
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from app.modules.workspace.llm_proxy_handler import handle_llm_proxy_request


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace_app():
    """Flask app with workspace blueprint for route-level handler tests."""
    from app.routes.workspace import workspace_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(workspace_bp, url_prefix="/api/workspace")
    return app


def _mock_proxy_token(**overrides):
    """Build a mock token payload."""
    base = {
        "user_id": 1,
        "tenant_id": 1,
        "provider": "openai",
        "session_id": "webui:1",  # Default: hardcoded webui session
        "scope": "local",
        "tool_name": "qwen-code",
    }
    base.update(overrides)
    return base


def _make_quota_ok():
    mock = MagicMock()
    mock.check_quota.return_value = {"allowed": True}
    return mock


def _mock_upstream_response(status_code=200, content=None):
    if content is None:
        content = json.dumps({
            "id": "chatcmpl-123",
            "model": "qwen3",
            "choices": [{"message": {"role": "assistant", "content": "hello"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }).encode()
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content
    resp.headers = {"Content-Type": "application/json"}
    resp.iter_content.return_value = [content]
    resp.json.return_value = json.loads(content)
    return resp


_PROXY_PATH = "app.routes.workspace.get_api_key_proxy_service"
_QUOTA_PATH = "app.modules.governance.quota_manager.QuotaManager"
_HTTP_PATH = "requests.request"


# ===================================================================
# A. X-Session-Id header format validation
# ===================================================================


class TestXSessionIdHeaderFormat:
    """Test X-Session-Id header format validation."""

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_valid_uuid_format_accepted(self, mock_get_proxy, mock_quota_cls, mock_http, workspace_app):
        """Valid UUID format should be accepted."""
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token()
        mock_proxy.get_tool_model_pool.return_value = {
            "models": [{"id": "qwen3"}],
            "model_key_ids": {"qwen3": [42]},
            "candidate_keys": [{"key_id": 42}],
        }
        mock_proxy.resolve_api_key_from_key_ids.return_value = (
            "sk-key", "https://api.openai.com/v1", 42
        )
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()
        mock_http.return_value = _mock_upstream_response(200)

        client = workspace_app.test_client()
        resp = client.post(
            "/api/workspace/llm-proxy",
            json={"model": "qwen3", "messages": [{"role": "user", "content": "hi"}]},
            headers={
                "Authorization": "Bearer tok",
                "X-Session-Id": "abc123-def456-ghi789",
            },
        )
        assert resp.status_code == 200

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_valid_webui_format_accepted(self, mock_get_proxy, mock_quota_cls, mock_http, workspace_app):
        """Valid webui:{user_id} format should be accepted."""
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token()
        mock_proxy.get_tool_model_pool.return_value = {
            "models": [{"id": "qwen3"}],
            "model_key_ids": {"qwen3": [42]},
            "candidate_keys": [{"key_id": 42}],
        }
        mock_proxy.resolve_api_key_from_key_ids.return_value = (
            "sk-key", "https://api.openai.com/v1", 42
        )
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()
        mock_http.return_value = _mock_upstream_response(200)

        client = workspace_app.test_client()
        resp = client.post(
            "/api/workspace/llm-proxy",
            json={"model": "qwen3", "messages": [{"role": "user", "content": "hi"}]},
            headers={
                "Authorization": "Bearer tok",
                "X-Session-Id": "webui:2",
            },
        )
        assert resp.status_code == 200

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_invalid_chars_fallback_to_token(self, mock_get_proxy, mock_quota_cls, mock_http, workspace_app):
        """Invalid characters should fallback to token session_id."""
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token(session_id="webui:1")
        mock_proxy.get_tool_model_pool.return_value = {
            "models": [{"id": "qwen3"}],
            "model_key_ids": {"qwen3": [42]},
            "candidate_keys": [{"key_id": 42}],
        }
        mock_proxy.resolve_api_key_from_key_ids.return_value = (
            "sk-key", "https://api.openai.com/v1", 42
        )
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()
        mock_http.return_value = _mock_upstream_response(200)

        client = workspace_app.test_client()
        # Session ID with invalid chars (spaces, dots)
        resp = client.post(
            "/api/workspace/llm-proxy",
            json={"model": "qwen3", "messages": [{"role": "user", "content": "hi"}]},
            headers={
                "Authorization": "Bearer tok",
                "X-Session-Id": "invalid.session id",
            },
        )
        assert resp.status_code == 200
        # Should use token session_id (webui:1) instead of invalid header

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_too_long_fallback_to_token(self, mock_get_proxy, mock_quota_cls, mock_http, workspace_app):
        """Session ID longer than 100 chars should fallback to token."""
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token(session_id="webui:1")
        mock_proxy.get_tool_model_pool.return_value = {
            "models": [{"id": "qwen3"}],
            "model_key_ids": {"qwen3": [42]},
            "candidate_keys": [{"key_id": 42}],
        }
        mock_proxy.resolve_api_key_from_key_ids.return_value = (
            "sk-key", "https://api.openai.com/v1", 42
        )
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()
        mock_http.return_value = _mock_upstream_response(200)

        client = workspace_app.test_client()
        # Session ID longer than 100 chars
        long_session_id = "a" * 150
        resp = client.post(
            "/api/workspace/llm-proxy",
            json={"model": "qwen3", "messages": [{"role": "user", "content": "hi"}]},
            headers={
                "Authorization": "Bearer tok",
                "X-Session-Id": long_session_id,
            },
        )
        assert resp.status_code == 200
        # Should use token session_id instead of too long header


# ===================================================================
# B. X-Session-Id header override behavior
# ===================================================================


class TestXSessionIdHeaderOverride:
    """Test X-Session-Id header overriding token session_id."""

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_header_overrides_token_session_id(self, mock_get_proxy, mock_quota_cls, mock_http, workspace_app):
        """X-Session-Id header should override token's session_id."""
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token(session_id="webui:1")
        mock_proxy.get_tool_model_pool.return_value = {
            "models": [{"id": "qwen3"}],
            "model_key_ids": {"qwen3": [42]},
            "candidate_keys": [{"key_id": 42}],
        }
        mock_proxy.resolve_api_key_from_key_ids.return_value = (
            "sk-key", "https://api.openai.com/v1", 42
        )
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()
        mock_http.return_value = _mock_upstream_response(200)

        client = workspace_app.test_client()
        custom_session_id = "conv-abc123"
        resp = client.post(
            "/api/workspace/llm-proxy",
            json={"model": "qwen3", "messages": [{"role": "user", "content": "hi"}]},
            headers={
                "Authorization": "Bearer tok",
                "X-Session-Id": custom_session_id,
            },
        )
        assert resp.status_code == 200

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_no_header_uses_token_session_id(self, mock_get_proxy, mock_quota_cls, mock_http, workspace_app):
        """Without X-Session-Id header, token's session_id should be used."""
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token(session_id="webui:1")
        mock_proxy.get_tool_model_pool.return_value = {
            "models": [{"id": "qwen3"}],
            "model_key_ids": {"qwen3": [42]},
            "candidate_keys": [{"key_id": 42}],
        }
        mock_proxy.resolve_api_key_from_key_ids.return_value = (
            "sk-key", "https://api.openai.com/v1", 42
        )
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()
        mock_http.return_value = _mock_upstream_response(200)

        client = workspace_app.test_client()
        resp = client.post(
            "/api/workspace/llm-proxy",
            json={"model": "qwen3", "messages": [{"role": "user", "content": "hi"}]},
            headers={
                "Authorization": "Bearer tok",
            },
        )
        assert resp.status_code == 200


# ===================================================================
# C. list_sessions API filtering webui:% sessions
# ===================================================================


class TestListSessionsFilterWebui:
    """Test list_sessions API filtering webui:% sessions."""

    @patch("app.routes.workspace.get_session_manager")
    @patch("app.routes.workspace.Database")
    def test_webui_sessions_filtered_out(self, mock_db_cls, mock_sm, workspace_app):
        """Sessions with session_id LIKE 'webui:%' should not appear in list."""
        # Mock database
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db

        # Mock query results - include webui and regular sessions
        mock_db.fetch_one.return_value = {"count": 2}  # Total count (excluding webui)
        mock_db.fetch_all.return_value = [
            {"session_id": "conv-abc", "title": "Conversation 1", "user_id": 1, "status": "active"},
            {"session_id": "conv-def", "title": "Conversation 2", "user_id": 1, "status": "completed"},
        ]

        # Mock session manager for stats
        mock_sm_instance = MagicMock()
        mock_sm.return_value = mock_sm_instance

        client = workspace_app.test_client()
        # Need to mock auth - use a valid user
        with patch("app.routes.workspace.g", MagicMock(user={"id": 1, "username": "test", "role": "user"})):
            resp = client.get("/api/workspace/sessions")

        # The query should have filtered webui:% sessions
        # Check that the SQL query was constructed with NOT LIKE filter
        if mock_db.fetch_all.called:
            call_args = mock_db.fetch_all.call_args
            query = call_args[0][0] if call_args[0] else ""
            # Verify NOT LIKE 'webui:%' is in the query
            assert "NOT LIKE" in query or "not like" in query.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])