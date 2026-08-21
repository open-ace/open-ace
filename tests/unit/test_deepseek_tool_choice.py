"""
Tests for Issue #2412: DeepSeek thinking mode rejects tool_choice

Tests that tool_choice is stripped only for DeepSeek models.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from app.routes.remote import remote_bp
from app.utils.llm_proxy_url_validator import LlmProxyValidationResult

_PROXY_PATH = "app.routes.remote.get_api_key_proxy_service"
_QUOTA_PATH = "app.modules.governance.quota_manager.QuotaManager"
_HTTP_PATH = "requests.request"
_VALIDATE_URL_PATH = "app.utils.llm_proxy_url_validator.validate_llm_proxy_url"


@pytest.fixture
def remote_app():
    """Flask app with remote blueprint for route-level handler tests."""
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(remote_bp, url_prefix="/api/remote")
    return app


def _mock_proxy_token(**overrides):
    """Build a mock token payload."""
    base = {
        "user_id": 1,
        "tenant_id": 1,
        "provider": "deepseek",
        "session_id": "sess-abc",
        "scope": "remote",
    }
    base.update(overrides)
    return base


def _make_quota_ok():
    mock = MagicMock()
    mock.check_quota.return_value = {"allowed": True}
    return mock


def _mock_upstream_response(status_code=200, content=b'{"ok":true}'):
    """Create a mock upstream response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content
    resp.headers = {"Content-Type": "application/json"}
    resp.iter_content.return_value = [content]
    resp.json.return_value = json.loads(content)
    return resp


def _mock_validate_llm_proxy_url(url, tenant_id, provider, *, resolver=None):
    """Mock validate_llm_proxy_url to return allowed=True."""
    return LlmProxyValidationResult(True)


class TestDeepSeekToolChoiceStripping:
    """Test tool_choice stripping for DeepSeek models."""

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    @patch(_VALIDATE_URL_PATH, side_effect=_mock_validate_llm_proxy_url)
    def test_strips_tool_choice_for_deepseek_model(
        self, mock_validate_url, mock_get_proxy, mock_quota_cls, mock_http, remote_app
    ):
        """Test that tool_choice is stripped for DeepSeek models."""
        # Setup mocks
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token(
            provider="deepseek",
        )
        mock_proxy.resolve_api_key_for_scope.return_value = (
            "sk-test",
            "https://api.deepseek.com/v1",
            1,
            None,
            None,  # resolved_ips
        )
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()

        captured_body = None

        def capture_request(**kwargs):
            nonlocal captured_body
            captured_body = kwargs.get("data")
            return _mock_upstream_response()

        mock_http.side_effect = capture_request

        # Make request with tool_choice
        client = remote_app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={
                "model": "deepseek-reasoner",
                "messages": [{"role": "user", "content": "test"}],
                "stream": True,
                "tool_choice": {"type": "function", "function": {"name": "shell"}},
            },
            headers={"Authorization": "Bearer tok"},
        )

        assert resp.status_code == 200

        # Verify tool_choice was stripped
        assert captured_body is not None
        data = json.loads(captured_body)
        assert "tool_choice" not in data
        assert data.get("stream") is True  # other fields preserved

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    @patch(_VALIDATE_URL_PATH, side_effect=_mock_validate_llm_proxy_url)
    def test_preserves_tool_choice_for_non_deepseek(
        self, mock_validate_url, mock_get_proxy, mock_quota_cls, mock_http, remote_app
    ):
        """Test that tool_choice is preserved for non-DeepSeek models."""
        # Setup mocks
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token(
            provider="openai",
        )
        mock_proxy.resolve_api_key_for_scope.return_value = (
            "sk-test",
            "https://api.openai.com/v1",
            1,
            None,
            None,  # resolved_ips
        )
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()

        captured_body = None

        def capture_request(**kwargs):
            nonlocal captured_body
            captured_body = kwargs.get("data")
            return _mock_upstream_response()

        mock_http.side_effect = capture_request

        # Make request with tool_choice
        client = remote_app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={
                "model": "gpt-4",
                "messages": [{"role": "user", "content": "test"}],
                "stream": True,
                "tool_choice": {"type": "function", "function": {"name": "shell"}},
            },
            headers={"Authorization": "Bearer tok"},
        )

        assert resp.status_code == 200

        # Verify tool_choice was preserved
        assert captured_body is not None
        data = json.loads(captured_body)
        assert "tool_choice" in data
        assert data["tool_choice"]["function"]["name"] == "shell"

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    @patch(_VALIDATE_URL_PATH, side_effect=_mock_validate_llm_proxy_url)
    def test_no_tool_choice_unchanged(
        self, mock_validate_url, mock_get_proxy, mock_quota_cls, mock_http, remote_app
    ):
        """Test that request without tool_choice is unchanged."""
        # Setup mocks
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token(
            provider="deepseek",
        )
        mock_proxy.resolve_api_key_for_scope.return_value = (
            "sk-test",
            "https://api.deepseek.com/v1",
            1,
            None,
            None,  # resolved_ips
        )
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()

        captured_body = None

        def capture_request(**kwargs):
            nonlocal captured_body
            captured_body = kwargs.get("data")
            return _mock_upstream_response()

        mock_http.side_effect = capture_request

        # Make request without tool_choice
        client = remote_app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={
                "model": "deepseek-reasoner",
                "messages": [{"role": "user", "content": "test"}],
                "stream": True,
            },
            headers={"Authorization": "Bearer tok"},
        )

        assert resp.status_code == 200

        # Verify request is valid JSON and stream preserved
        assert captured_body is not None
        data = json.loads(captured_body)
        assert data.get("stream") is True

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    @patch(_VALIDATE_URL_PATH, side_effect=_mock_validate_llm_proxy_url)
    def test_deepseek_case_insensitive(
        self, mock_validate_url, mock_get_proxy, mock_quota_cls, mock_http, remote_app
    ):
        """Test that DeepSeek detection is case-insensitive."""
        # Setup mocks
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token(
            provider="deepseek",
        )
        mock_proxy.resolve_api_key_for_scope.return_value = (
            "sk-test",
            "https://api.deepseek.com/v1",
            1,
            None,
            None,  # resolved_ips
        )
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()

        # Test various case variations
        for model_name in ["DeepSeek-Reasoner", "DEEPSEEK-V3", "deepseek-chat"]:
            captured_body = None

            def capture_request(**kwargs):
                nonlocal captured_body
                captured_body = kwargs.get("data")
                return _mock_upstream_response()

            mock_http.side_effect = capture_request

            client = remote_app.test_client()
            resp = client.post(
                "/api/remote/llm-proxy",
                json={
                    "model": model_name,
                    "messages": [{"role": "user", "content": "test"}],
                    "stream": True,
                    "tool_choice": "auto",
                },
                headers={"Authorization": "Bearer tok"},
            )

            assert resp.status_code == 200
            assert captured_body is not None
            data = json.loads(captured_body)
            assert "tool_choice" not in data, f"tool_choice should be stripped for {model_name}"
