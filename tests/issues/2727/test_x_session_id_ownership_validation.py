#!/usr/bin/env python3
"""
Tests for Issue #2727: X-Session-Id 跨用户/租户越权访问漏洞

Test coverage:
1. Cross-user access returns 404 (unauthorized)
2. Cross-tenant access returns 404 (unauthorized)
3. Non-existent session returns 404
4. Invalid format returns 400
5. Valid access succeeds
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from app.modules.workspace.session_manager import AgentSession


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
        "session_id": "webui:1",
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


def _make_session(
    session_id: str,
    user_id: int,
    tenant_id: int,
    **kwargs,
) -> AgentSession:
    """Create a mock AgentSession."""
    return AgentSession(
        session_id=session_id,
        user_id=user_id,
        tenant_id=tenant_id,
        created_at=kwargs.get("created_at", datetime.now()),
        updated_at=kwargs.get("updated_at", datetime.now()),
        **{k: v for k, v in kwargs.items() if k not in ("created_at", "updated_at")},
    )


_PROXY_PATH = "app.routes.workspace.get_api_key_proxy_service"
_QUOTA_PATH = "app.modules.governance.quota_manager.QuotaManager"
_HTTP_PATH = "requests.request"
_SESSION_MANAGER_PATH = "app.modules.workspace.session_manager.get_session_manager"


# ===================================================================
# 1. Cross-user access returns 404
# ===================================================================


class TestCrossUserAccess:
    """Test that accessing another user's session returns 404."""

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_SESSION_MANAGER_PATH)
    @patch(_PROXY_PATH)
    def test_cross_user_returns_404(
        self, mock_get_proxy, mock_sm, mock_quota_cls, mock_http, workspace_app
    ):
        """User 1 accessing User 2's session should return 404."""
        # Setup mocks
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token(
            user_id=1,  # Token belongs to user 1
            tenant_id=1,
        )
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
        mock_quota_cls.return_value = _make_quota_ok()

        # Mock session manager - session belongs to user 2
        mock_sm_instance = MagicMock()
        mock_sm.return_value = mock_sm_instance
        mock_sm_instance.get_session.return_value = _make_session(
            session_id="conv-cross-user",
            user_id=2,  # Session belongs to user 2, not user 1
            tenant_id=1,
        )

        # Make request
        client = workspace_app.test_client()
        resp = client.post(
            "/api/workspace/llm-proxy",
            json={"model": "qwen3", "messages": [{"role": "user", "content": "hi"}]},
            headers={
                "Authorization": "Bearer tok",
                "X-Session-Id": "conv-cross-user",
            },
        )

        # Should return 404 (session not found from user's perspective)
        assert resp.status_code == 404
        data = resp.get_json()
        assert data["error"]["type"] == "session_not_found"

        # Upstream should not be called
        mock_http.assert_not_called()


# ===================================================================
# 2. Cross-tenant access returns 404
# ===================================================================


class TestCrossTenantAccess:
    """Test that accessing another tenant's session returns 404."""

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_SESSION_MANAGER_PATH)
    @patch(_PROXY_PATH)
    def test_cross_tenant_returns_404(
        self, mock_get_proxy, mock_sm, mock_quota_cls, mock_http, workspace_app
    ):
        """Tenant 1 accessing Tenant 2's session should return 404."""
        # Setup mocks
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token(
            user_id=1,
            tenant_id=1,  # Token belongs to tenant 1
        )
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
        mock_quota_cls.return_value = _make_quota_ok()

        # Mock session manager - session belongs to tenant 2
        mock_sm_instance = MagicMock()
        mock_sm.return_value = mock_sm_instance
        mock_sm_instance.get_session.return_value = _make_session(
            session_id="conv-cross-tenant",
            user_id=1,
            tenant_id=2,  # Session belongs to tenant 2, not tenant 1
        )

        # Make request
        client = workspace_app.test_client()
        resp = client.post(
            "/api/workspace/llm-proxy",
            json={"model": "qwen3", "messages": [{"role": "user", "content": "hi"}]},
            headers={
                "Authorization": "Bearer tok",
                "X-Session-Id": "conv-cross-tenant",
            },
        )

        # Should return 404 (session not found from tenant's perspective)
        assert resp.status_code == 404
        data = resp.get_json()
        assert data["error"]["type"] == "session_not_found"

        # Upstream should not be called
        mock_http.assert_not_called()


# ===================================================================
# 3. Non-existent session returns 404
# ===================================================================


class TestNonExistentSession:
    """Test that accessing non-existent session returns 404."""

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_SESSION_MANAGER_PATH)
    @patch(_PROXY_PATH)
    def test_nonexistent_session_returns_404(
        self, mock_get_proxy, mock_sm, mock_quota_cls, mock_http, workspace_app
    ):
        """Accessing non-existent session should return 404."""
        # Setup mocks
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token(
            user_id=1,
            tenant_id=1,
        )
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
        mock_quota_cls.return_value = _make_quota_ok()

        # Mock session manager - session not found
        mock_sm_instance = MagicMock()
        mock_sm.return_value = mock_sm_instance
        mock_sm_instance.get_session.return_value = None  # Session does not exist

        # Make request
        client = workspace_app.test_client()
        resp = client.post(
            "/api/workspace/llm-proxy",
            json={"model": "qwen3", "messages": [{"role": "user", "content": "hi"}]},
            headers={
                "Authorization": "Bearer tok",
                "X-Session-Id": "conv-nonexistent",
            },
        )

        # Should return 404
        assert resp.status_code == 404
        data = resp.get_json()
        assert data["error"]["type"] == "session_not_found"

        # Upstream should not be called
        mock_http.assert_not_called()


# ===================================================================
# 4. Invalid format returns 400
# ===================================================================


class TestInvalidFormat:
    """Test that invalid X-Session-Id format returns 400."""

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_SESSION_MANAGER_PATH)
    @patch(_PROXY_PATH)
    def test_invalid_chars_returns_400(
        self, mock_get_proxy, mock_sm, mock_quota_cls, mock_http, workspace_app
    ):
        """X-Session-Id with invalid characters should return 400."""
        # Setup mocks
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token(
            user_id=1,
            tenant_id=1,
        )
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()

        # Make request with invalid characters (dots, spaces)
        client = workspace_app.test_client()
        resp = client.post(
            "/api/workspace/llm-proxy",
            json={"model": "qwen3", "messages": [{"role": "user", "content": "hi"}]},
            headers={
                "Authorization": "Bearer tok",
                "X-Session-Id": "invalid.session id",
            },
        )

        # Should return 400 (bad request)
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["error"]["type"] == "invalid_header"

        # Session manager should not be called
        mock_sm.assert_not_called()
        # Upstream should not be called
        mock_http.assert_not_called()

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_SESSION_MANAGER_PATH)
    @patch(_PROXY_PATH)
    def test_too_long_returns_400(
        self, mock_get_proxy, mock_sm, mock_quota_cls, mock_http, workspace_app
    ):
        """X-Session-Id longer than 100 chars should return 400."""
        # Setup mocks
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token(
            user_id=1,
            tenant_id=1,
        )
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()

        # Make request with too long session id
        client = workspace_app.test_client()
        long_session_id = "a" * 150
        resp = client.post(
            "/api/workspace/llm-proxy",
            json={"model": "qwen3", "messages": [{"role": "user", "content": "hi"}]},
            headers={
                "Authorization": "Bearer tok",
                "X-Session-Id": long_session_id,
            },
        )

        # Should return 400 (bad request)
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["error"]["type"] == "invalid_header"

        # Session manager should not be called
        mock_sm.assert_not_called()
        # Upstream should not be called
        mock_http.assert_not_called()


# ===================================================================
# 5. Valid access succeeds
# ===================================================================


class TestValidAccess:
    """Test that valid access with matching ownership succeeds."""

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_SESSION_MANAGER_PATH)
    @patch(_PROXY_PATH)
    def test_valid_access_succeeds(
        self, mock_get_proxy, mock_sm, mock_quota_cls, mock_http, workspace_app
    ):
        """Access with matching user_id and tenant_id should succeed."""
        # Setup mocks
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token(
            user_id=1,
            tenant_id=1,
        )
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
        mock_quota_cls.return_value = _make_quota_ok()
        mock_http.return_value = _mock_upstream_response(200)

        # Mock session manager - session belongs to same user and tenant
        mock_sm_instance = MagicMock()
        mock_sm.return_value = mock_sm_instance
        mock_sm_instance.get_session.return_value = _make_session(
            session_id="conv-valid",
            user_id=1,  # Same as token
            tenant_id=1,  # Same as token
        )

        # Make request
        client = workspace_app.test_client()
        resp = client.post(
            "/api/workspace/llm-proxy",
            json={"model": "qwen3", "messages": [{"role": "user", "content": "hi"}]},
            headers={
                "Authorization": "Bearer tok",
                "X-Session-Id": "conv-valid",
            },
        )

        # Should succeed
        assert resp.status_code == 200

        # Upstream should be called
        mock_http.assert_called_once()

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_SESSION_MANAGER_PATH)
    @patch(_PROXY_PATH)
    def test_valid_uuid_format_accepted(
        self, mock_get_proxy, mock_sm, mock_quota_cls, mock_http, workspace_app
    ):
        """Valid UUID format should be accepted."""
        # Setup mocks
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token(
            user_id=1,
            tenant_id=1,
        )
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
        mock_quota_cls.return_value = _make_quota_ok()
        mock_http.return_value = _mock_upstream_response(200)

        # Mock session manager
        mock_sm_instance = MagicMock()
        mock_sm.return_value = mock_sm_instance
        mock_sm_instance.get_session.return_value = _make_session(
            session_id="abc123-def456-ghi789",
            user_id=1,
            tenant_id=1,
        )

        # Make request
        client = workspace_app.test_client()
        resp = client.post(
            "/api/workspace/llm-proxy",
            json={"model": "qwen3", "messages": [{"role": "user", "content": "hi"}]},
            headers={
                "Authorization": "Bearer tok",
                "X-Session-Id": "abc123-def456-ghi789",
            },
        )

        # Should succeed
        assert resp.status_code == 200


# ===================================================================
# 6. Validation error handling
# ===================================================================


class TestValidationErrorHandling:
    """Test that exceptions during validation are handled gracefully."""

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_SESSION_MANAGER_PATH)
    @patch(_PROXY_PATH)
    def test_session_manager_exception_returns_500(
        self, mock_get_proxy, mock_sm, mock_quota_cls, mock_http, workspace_app
    ):
        """Exception from session manager should return 500."""
        # Setup mocks
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token(
            user_id=1,
            tenant_id=1,
        )
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()

        # Mock session manager - raise exception
        mock_sm_instance = MagicMock()
        mock_sm.return_value = mock_sm_instance
        mock_sm_instance.get_session.side_effect = Exception("Database connection failed")

        # Make request
        client = workspace_app.test_client()
        resp = client.post(
            "/api/workspace/llm-proxy",
            json={"model": "qwen3", "messages": [{"role": "user", "content": "hi"}]},
            headers={
                "Authorization": "Bearer tok",
                "X-Session-Id": "conv-error",
            },
        )

        # Should return 500 (internal server error)
        assert resp.status_code == 500
        data = resp.get_json()
        assert data["error"]["type"] == "validation_error"

        # Upstream should not be called
        mock_http.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])