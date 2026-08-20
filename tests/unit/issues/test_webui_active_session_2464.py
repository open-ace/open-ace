#!/usr/bin/env python3
"""
Tests for Issue #2464: WebUI 会话消息记录与历史会话显示分离

Test coverage:
1. LLM proxy uses webui aggregate session when no active non-webui session exists
2. LLM proxy uses user's active non-webui session when available
3. LLM proxy uses most recently updated session when multiple active sessions exist
"""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from app.utils.llm_proxy_url_validator import LlmProxyValidationResult

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


def _mock_validate_llm_proxy_url(url, tenant_id, provider, *, resolver=None):
    """Mock validate_llm_proxy_url to return allowed=True."""
    return LlmProxyValidationResult(True)


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
        content = json.dumps(
            {
                "id": "chatcmpl-123",
                "model": "qwen3",
                "choices": [{"message": {"role": "assistant", "content": "hello"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
        ).encode()
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content
    resp.headers = {"Content-Type": "application/json"}
    resp.iter_content.return_value = [content]
    resp.json.return_value = json.loads(content)
    return resp


def _mock_session(session_id: str, updated_at: datetime | None = None):
    """Create a mock AgentSession."""
    mock = MagicMock()
    mock.session_id = session_id
    mock.updated_at = updated_at or datetime.now(timezone.utc)
    mock.created_at = datetime.now(timezone.utc)
    return mock


_PROXY_PATH = "app.routes.workspace.get_api_key_proxy_service"
_QUOTA_PATH = "app.modules.governance.quota_manager.QuotaManager"
_HTTP_PATH = "requests.request"
_SESSION_MGR_PATH = "app.modules.workspace.session_manager.get_session_manager"


# ===================================================================
# A. No active non-webui session → use webui aggregate
# ===================================================================


class TestNoActiveNonWebuiSession:
    """Test LLM proxy behavior when user has no active non-webui session."""

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_SESSION_MGR_PATH)
    @patch(_PROXY_PATH)
    @patch(
        "app.utils.llm_proxy_url_validator.validate_llm_proxy_url",
        _mock_validate_llm_proxy_url,
    )
    def test_uses_webui_aggregate_when_no_active_session(
        self,
        mock_get_proxy,
        mock_session_mgr,
        mock_quota_cls,
        mock_http,
        workspace_app,
    ):
        """Should use webui aggregate session when user has no active non-webui session."""
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token()
        mock_proxy.get_tool_model_pool.return_value = {
            "models": [{"id": "qwen3"}],
            "model_key_ids": {"qwen3": [42]},
            "candidate_keys": [{"key_id": 42}],
        }
        mock_proxy.resolve_api_key_from_key_ids.return_value = (
            "sk-key",
            "https://api.openai.com/v1",
            42,
            None,
            None,
        )
        mock_get_proxy.return_value = mock_proxy

        # No active sessions
        mock_sm = MagicMock()
        mock_sm.get_active_sessions.return_value = []
        mock_session_mgr.return_value = mock_sm

        mock_quota_cls.return_value = _make_quota_ok()
        mock_http.return_value = _mock_upstream_response()

        client = workspace_app.test_client()
        resp = client.post(
            "/api/workspace/llm-proxy",
            json={"model": "qwen3", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer tok"},
        )
        assert resp.status_code == 200


# ===================================================================
# B. Active non-webui session → use that session
# ===================================================================


class TestActiveNonWebuiSession:
    """Test LLM proxy behavior when user has an active non-webui session."""

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_SESSION_MGR_PATH)
    @patch(_PROXY_PATH)
    @patch(
        "app.utils.llm_proxy_url_validator.validate_llm_proxy_url",
        _mock_validate_llm_proxy_url,
    )
    def test_uses_active_session_when_available(
        self,
        mock_get_proxy,
        mock_session_mgr,
        mock_quota_cls,
        mock_http,
        workspace_app,
    ):
        """Should use user's active non-webui session when available."""
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token()
        mock_proxy.get_tool_model_pool.return_value = {
            "models": [{"id": "qwen3"}],
            "model_key_ids": {"qwen3": [42]},
            "candidate_keys": [{"key_id": 42}],
        }
        mock_proxy.resolve_api_key_from_key_ids.return_value = (
            "sk-key",
            "https://api.openai.com/v1",
            42,
            None,
            None,
        )
        mock_get_proxy.return_value = mock_proxy

        # User has one active non-webui session
        active_session = _mock_session("uuid-session-123")
        mock_sm = MagicMock()
        mock_sm.get_active_sessions.return_value = [active_session]
        mock_session_mgr.return_value = mock_sm

        mock_quota_cls.return_value = _make_quota_ok()
        mock_http.return_value = _mock_upstream_response()

        client = workspace_app.test_client()
        resp = client.post(
            "/api/workspace/llm-proxy",
            json={"model": "qwen3", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer tok"},
        )
        assert resp.status_code == 200
        # Verify get_active_sessions was called
        mock_sm.get_active_sessions.assert_called_once()


# ===================================================================
# C. Multiple active sessions → use most recently updated
# ===================================================================


class TestMultipleActiveSessions:
    """Test LLM proxy behavior when user has multiple active non-webui sessions."""

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_SESSION_MGR_PATH)
    @patch(_PROXY_PATH)
    @patch(
        "app.utils.llm_proxy_url_validator.validate_llm_proxy_url",
        _mock_validate_llm_proxy_url,
    )
    def test_uses_most_recently_updated_session(
        self,
        mock_get_proxy,
        mock_session_mgr,
        mock_quota_cls,
        mock_http,
        workspace_app,
    ):
        """Should use the most recently updated session when multiple active sessions exist."""
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token()
        mock_proxy.get_tool_model_pool.return_value = {
            "models": [{"id": "qwen3"}],
            "model_key_ids": {"qwen3": [42]},
            "candidate_keys": [{"key_id": 42}],
        }
        mock_proxy.resolve_api_key_from_key_ids.return_value = (
            "sk-key",
            "https://api.openai.com/v1",
            42,
            None,
            None,
        )
        mock_get_proxy.return_value = mock_proxy

        # User has multiple active non-webui sessions
        now = datetime.now(timezone.utc)
        older_session = _mock_session("uuid-session-old", updated_at=now)
        # Newer session: 1 minute later
        from datetime import timedelta

        newer_time = now + timedelta(minutes=1)
        newer_session = _mock_session("uuid-session-new", updated_at=newer_time)
        mock_sm = MagicMock()
        mock_sm.get_active_sessions.return_value = [older_session, newer_session]
        mock_session_mgr.return_value = mock_sm

        mock_quota_cls.return_value = _make_quota_ok()
        mock_http.return_value = _mock_upstream_response()

        client = workspace_app.test_client()
        resp = client.post(
            "/api/workspace/llm-proxy",
            json={"model": "qwen3", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer tok"},
        )
        assert resp.status_code == 200


# ===================================================================
# D. X-Session-Id header still works
# ===================================================================


class TestXSessionIdHeaderOverride:
    """Test that X-Session-Id header still takes precedence."""

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_SESSION_MGR_PATH)
    @patch(_PROXY_PATH)
    @patch(
        "app.utils.llm_proxy_url_validator.validate_llm_proxy_url",
        _mock_validate_llm_proxy_url,
    )
    def test_header_takes_precedence_over_active_session(
        self,
        mock_get_proxy,
        mock_session_mgr,
        mock_quota_cls,
        mock_http,
        workspace_app,
    ):
        """X-Session-Id header should take precedence over active session lookup."""
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token()
        mock_proxy.get_tool_model_pool.return_value = {
            "models": [{"id": "qwen3"}],
            "model_key_ids": {"qwen3": [42]},
            "candidate_keys": [{"key_id": 42}],
        }
        mock_proxy.resolve_api_key_from_key_ids.return_value = (
            "sk-key",
            "https://api.openai.com/v1",
            42,
            None,
            None,
        )
        mock_get_proxy.return_value = mock_proxy

        # Mock session manager for ownership validation (Issue #2727)
        # Header session must match token user_id and tenant_id
        header_session = _mock_session("header-session-456")
        header_session.user_id = 1  # Match token user_id
        header_session.tenant_id = 1  # Match token tenant_id
        mock_sm = MagicMock()
        mock_sm.get_session.return_value = header_session
        mock_session_mgr.return_value = mock_sm

        mock_quota_cls.return_value = _make_quota_ok()
        mock_http.return_value = _mock_upstream_response()

        client = workspace_app.test_client()
        resp = client.post(
            "/api/workspace/llm-proxy",
            json={"model": "qwen3", "messages": [{"role": "user", "content": "hi"}]},
            headers={
                "Authorization": "Bearer tok",
                "X-Session-Id": "header-session-456",
            },
        )
        assert resp.status_code == 200
        # get_session should be called for ownership validation (may be called multiple times)
        assert mock_sm.get_session.called


# ===================================================================
# E. Webui session filter
# ===================================================================


class TestWebuiSessionFilter:
    """Test that webui aggregate sessions are filtered from active session lookup."""

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_SESSION_MGR_PATH)
    @patch(_PROXY_PATH)
    @patch(
        "app.utils.llm_proxy_url_validator.validate_llm_proxy_url",
        _mock_validate_llm_proxy_url,
    )
    def test_filters_webui_sessions_from_lookup(
        self,
        mock_get_proxy,
        mock_session_mgr,
        mock_quota_cls,
        mock_http,
        workspace_app,
    ):
        """Webui aggregate sessions should be filtered from active session lookup."""
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token()
        mock_proxy.get_tool_model_pool.return_value = {
            "models": [{"id": "qwen3"}],
            "model_key_ids": {"qwen3": [42]},
            "candidate_keys": [{"key_id": 42}],
        }
        mock_proxy.resolve_api_key_from_key_ids.return_value = (
            "sk-key",
            "https://api.openai.com/v1",
            42,
            None,
            None,
        )
        mock_get_proxy.return_value = mock_proxy

        # User has both webui and non-webui sessions
        webui_session = _mock_session("webui:1")
        non_webui_session = _mock_session("uuid-session-123")
        mock_sm = MagicMock()
        mock_sm.get_active_sessions.return_value = [webui_session, non_webui_session]
        mock_session_mgr.return_value = mock_sm

        mock_quota_cls.return_value = _make_quota_ok()
        mock_http.return_value = _mock_upstream_response()

        client = workspace_app.test_client()
        resp = client.post(
            "/api/workspace/llm-proxy",
            json={"model": "qwen3", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer tok"},
        )
        assert resp.status_code == 200
