#!/usr/bin/env python3
"""
Tests for Issue #2464 / #3025: WebUI aggregate session fallback behavior.

Issue #2464 originally added fallback from webui:* aggregate to user's active session.
Issue #3025 removed that fallback because it caused cross-session data contamination.

Test coverage (post #3025 fix):
1. LLM proxy uses webui aggregate session directly (no active-session lookup)
2. LLM proxy does NOT call get_active_sessions for webui:* tokens without X-Session-Id
3. X-Session-Id header still takes precedence (ownership validation unchanged)
4. webui:* aggregate sessions are used as-is, never replaced by regular sessions
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
_GATEWAY_PATH = "app.modules.workspace.llm_proxy_handler.get_gateway_planner"


def _make_noop_gateway():
    """Return a mock gateway planner with is_noop=True (direct-provider mode)."""
    gw = MagicMock()
    gw.is_noop = True
    return gw


# ===================================================================
# A. No X-Session-Id → use webui aggregate directly (no fallback)
# ===================================================================


class TestNoActiveNonWebuiSession:
    """Test LLM proxy behavior when webui:* token has no X-Session-Id header.

    Issue #3025: The old fallback to active sessions was removed.
    Now, requests always use the webui:* aggregate session directly.
    """

    @patch(_GATEWAY_PATH, side_effect=lambda: _make_noop_gateway())
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
        mock_gateway,
        workspace_app,
    ):
        """Should use webui aggregate session directly without calling get_active_sessions."""
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

        mock_sm = MagicMock()
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
        # Issue #3025: get_active_sessions must NOT be called (fallback removed)
        mock_sm.get_active_sessions.assert_not_called()


# ===================================================================
# B. Active non-webui session exists → still use webui aggregate (no fallback)
# ===================================================================


class TestActiveNonWebuiSession:
    """Test LLM proxy behavior when user has an active non-webui session.

    Issue #3025: Even when active sessions exist, webui:* tokens without
    X-Session-Id must NOT route to them. The old fallback was removed.
    """

    @patch(_GATEWAY_PATH, side_effect=lambda: _make_noop_gateway())
    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_SESSION_MGR_PATH)
    @patch(_PROXY_PATH)
    @patch(
        "app.utils.llm_proxy_url_validator.validate_llm_proxy_url",
        _mock_validate_llm_proxy_url,
    )
    def test_does_not_fallback_to_active_session(
        self,
        mock_get_proxy,
        mock_session_mgr,
        mock_quota_cls,
        mock_http,
        mock_gateway,
        workspace_app,
    ):
        """Should NOT call get_active_sessions; must use webui aggregate directly."""
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

        # User has one active non-webui session (but it must be ignored)
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
        # Issue #3025: get_active_sessions must NOT be called (fallback removed)
        mock_sm.get_active_sessions.assert_not_called()


# ===================================================================
# C. Multiple active sessions → still use webui aggregate (no fallback)
# ===================================================================


class TestMultipleActiveSessions:
    """Test LLM proxy behavior when user has multiple active non-webui sessions.

    Issue #3025: Multiple active sessions must NOT trigger fallback selection.
    The old behavior of picking the most recently updated session was removed.
    """

    @patch(_GATEWAY_PATH, side_effect=lambda: _make_noop_gateway())
    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_SESSION_MGR_PATH)
    @patch(_PROXY_PATH)
    @patch(
        "app.utils.llm_proxy_url_validator.validate_llm_proxy_url",
        _mock_validate_llm_proxy_url,
    )
    def test_does_not_select_from_multiple_sessions(
        self,
        mock_get_proxy,
        mock_session_mgr,
        mock_quota_cls,
        mock_http,
        mock_gateway,
        workspace_app,
    ):
        """Should NOT call get_active_sessions even when multiple sessions exist."""
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

        # User has multiple active non-webui sessions (all must be ignored)
        now = datetime.now(timezone.utc)
        older_session = _mock_session("uuid-session-old", updated_at=now)
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
        # Issue #3025: get_active_sessions must NOT be called (fallback removed)
        mock_sm.get_active_sessions.assert_not_called()


# ===================================================================
# D. X-Session-Id header still works
# ===================================================================


class TestXSessionIdHeaderOverride:
    """Test that X-Session-Id header still takes precedence."""

    @patch(_GATEWAY_PATH, side_effect=lambda: _make_noop_gateway())
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
        mock_gateway,
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
# E. Webui aggregate used directly (no session lookup)
# ===================================================================


class TestWebuiSessionFilter:
    """Test that webui:* tokens without X-Session-Id use aggregate session directly.

    Issue #3025: No active-session lookup is performed for webui:* tokens.
    """

    @patch(_GATEWAY_PATH, side_effect=lambda: _make_noop_gateway())
    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_SESSION_MGR_PATH)
    @patch(_PROXY_PATH)
    @patch(
        "app.utils.llm_proxy_url_validator.validate_llm_proxy_url",
        _mock_validate_llm_proxy_url,
    )
    def test_no_session_lookup_for_webui_aggregate(
        self,
        mock_get_proxy,
        mock_session_mgr,
        mock_quota_cls,
        mock_http,
        mock_gateway,
        workspace_app,
    ):
        """Webui:* tokens without X-Session-Id must not trigger session lookup."""
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

        mock_sm = MagicMock()
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
        # Issue #3025: No session lookup for webui:* tokens without X-Session-Id
        mock_sm.get_active_sessions.assert_not_called()
