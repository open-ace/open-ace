#!/usr/bin/env python3
"""Integration tests for the model-gateway seam in handle_llm_proxy_request.

Mirrors tests/issues/604/test_llm_proxy_handler.py: remote blueprint + mocked
upstream HTTP. The planner is patched at the handler's import site
(app.modules.workspace.llm_proxy_handler.get_gateway_planner) for determinism.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from app.modules.workspace.model_gateway.config import GatewayConfig
from app.modules.workspace.model_gateway.planner import (
    LitellmGatewayPlanner,
    NullGatewayPlanner,
    get_gateway_planner,
    reset_gateway_planner_for_tests,
)
from app.utils.llm_proxy_url_validator import LlmProxyValidationResult


def _mock_validate_llm_proxy_url(url, tenant_id, provider, *, resolver=None):
    """Mock validator that allows all URLs for testing."""
    return LlmProxyValidationResult(True)


@pytest.fixture
def remote_app():
    from app.routes.remote import remote_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(remote_bp, url_prefix="/api/remote")
    return app


@pytest.fixture(autouse=True)
def mock_url_validator():
    """Auto-mock URL validator for all tests in this module."""
    with patch("app.utils.llm_proxy_url_validator.validate_llm_proxy_url", _mock_validate_llm_proxy_url):
        yield


def _mock_proxy_token(**overrides):
    base = {
        "user_id": 1,
        "tenant_id": 1,
        "provider": "openai",
        "session_id": "sess-abc",
        "scope": "remote",
    }
    base.update(overrides)
    return base


def _quota_ok():
    m = MagicMock()
    m.check_quota.return_value = {"allowed": True}
    return m


def _mock_proxy():
    mock = MagicMock()
    mock.validate_proxy_token.return_value = _mock_proxy_token()
    mock.resolve_api_key_for_scope.return_value = ("sk-key", "https://api.openai.com/v1", 1, None, None)
    return mock


def _real_planner(**cfg_overrides):
    cfg = GatewayConfig(
        base_url="https://gateway.example.com/v1",
        api_key="gw-secret-key-123",
        **cfg_overrides,
    )
    return LitellmGatewayPlanner(cfg)


def _mock_upstream(status_code=200, content=b'{"ok":true}', content_type="application/json"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content
    resp.headers = {"Content-Type": content_type}
    resp.iter_content.return_value = [content]
    resp.json.return_value = json.loads(content) if content else {}
    return resp


_PROXY_PATH = "app.routes.remote.get_api_key_proxy_service"
_QUOTA_PATH = "app.modules.governance.quota_manager.QuotaManager"
_HTTP_PATH = "requests.request"
_PLANNER_PATH = "app.modules.workspace.llm_proxy_handler.get_gateway_planner"
_RECORD_PATH = "app.modules.workspace.llm_proxy_handler._record_llm_usage"

_HEADERS = {"Authorization": "Bearer tok"}


# ── Direct mode regression through the seam ─────────────────────────────


class TestGatewayDisabled:
    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    @patch(_PLANNER_PATH, return_value=NullGatewayPlanner())
    def test_null_planner_runs_direct_path(
        self, mock_planner, mock_get_proxy, mock_quota_cls, mock_http, remote_app
    ):
        mock_proxy = _mock_proxy()
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _quota_ok()
        mock_http.return_value = _mock_upstream(200)

        client = remote_app.test_client()
        resp = client.post("/api/remote/llm-proxy", json={"model": "gpt-4"}, headers=_HEADERS)
        assert resp.status_code == 200
        # Direct path resolved a provider key, not the gateway
        mock_proxy.resolve_api_key_for_scope.assert_called()
        url = mock_http.call_args.kwargs["url"]
        assert "api.openai.com" in url
        assert "gateway.example.com" not in url


# ── Gateway success ─────────────────────────────────────────────────────


class TestGatewaySuccess:
    @patch(_RECORD_PATH)
    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    @patch(_PLANNER_PATH)
    def test_gateway_forward_success(
        self, mock_planner, mock_get_proxy, mock_quota_cls, mock_http, mock_record, remote_app
    ):
        mock_planner.return_value = _real_planner()
        mock_get_proxy.return_value = _mock_proxy()
        mock_quota_cls.return_value = _quota_ok()
        body = json.dumps(
            {
                "choices": [{"message": {"content": "hi"}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 3},
            }
        ).encode()
        mock_http.return_value = _mock_upstream(200, body)

        client = remote_app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        assert b"hi" in resp.data

        # Single attempt, gateway URL + gateway key + attribution headers
        mock_http.assert_called_once()
        sent_headers = mock_http.call_args.kwargs["headers"]
        assert sent_headers["Authorization"] == "Bearer gw-secret-key-123"
        assert sent_headers["X-OpenACE-User-Id"] == "1"
        assert sent_headers["X-OpenACE-Model"] == "gpt-4"
        assert "gateway.example.com" in mock_http.call_args.kwargs["url"]

        # Body carries merged metadata
        sent_body = json.loads(mock_http.call_args.kwargs["data"])
        assert sent_body["metadata"]["openace_user_id"] == 1

        # Usage recorded via the shared tail
        mock_record.assert_called_once()
        assert mock_record.call_args.args[0] == body  # response content

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    @patch(_PLANNER_PATH)
    def test_single_attempt_no_failover(
        self, mock_planner, mock_get_proxy, mock_quota_cls, mock_http, remote_app
    ):
        mock_planner.return_value = _real_planner()
        mock_get_proxy.return_value = _mock_proxy()
        mock_quota_cls.return_value = _quota_ok()
        mock_http.return_value = _mock_upstream(200)

        client = remote_app.test_client()
        client.post("/api/remote/llm-proxy", json={"model": "gpt-4"}, headers=_HEADERS)
        mock_http.assert_called_once()


# ── Misconfigured -> 503, no fallback ────────────────────────────────────


class TestGatewayMisconfigured:
    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    @patch(_PLANNER_PATH)
    def test_misconfigured_returns_503_no_fallback(
        self, mock_planner, mock_get_proxy, mock_quota_cls, mock_http, remote_app
    ):
        # Enabled but no config -> plan() returns None
        mock_planner.return_value = LitellmGatewayPlanner(None)
        mock_proxy = _mock_proxy()
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _quota_ok()

        client = remote_app.test_client()
        resp = client.post("/api/remote/llm-proxy", json={"model": "gpt-4"}, headers=_HEADERS)
        assert resp.status_code == 503
        assert resp.get_json()["error"]["type"] == "gateway_misconfigured"
        # No HTTP call and no direct-provider key resolution (no silent fallback)
        mock_http.assert_not_called()
        mock_proxy.resolve_api_key_for_scope.assert_not_called()


# ── Quota gate runs before any forwarding ────────────────────────────────


class TestGatewayQuota:
    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    @patch(_PLANNER_PATH)
    def test_quota_exceeded_before_http(
        self, mock_planner, mock_get_proxy, mock_quota_cls, mock_http, remote_app
    ):
        mock_planner.return_value = _real_planner()
        mock_get_proxy.return_value = _mock_proxy()
        mock_quota = MagicMock()
        mock_quota.check_quota.return_value = {"allowed": False, "reason": "Daily quota exceeded"}
        mock_quota_cls.return_value = mock_quota

        client = remote_app.test_client()
        resp = client.post("/api/remote/llm-proxy", json={"model": "gpt-4"}, headers=_HEADERS)
        assert resp.status_code == 429
        mock_http.assert_not_called()


# ── Responses API conversion on the gateway ─────────────────────────────


class TestGatewayResponsesApi:
    @patch(_RECORD_PATH)
    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    @patch(_PLANNER_PATH)
    def test_responses_converted_to_chat_completions(
        self, mock_planner, mock_get_proxy, mock_quota_cls, mock_http, mock_record, remote_app
    ):
        mock_planner.return_value = _real_planner()
        mock_get_proxy.return_value = _mock_proxy()
        mock_quota_cls.return_value = _quota_ok()
        upstream = json.dumps(
            {
                "id": "chatcmpl-1",
                "model": "model-a",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        ).encode()
        mock_http.return_value = _mock_upstream(200, upstream)

        client = remote_app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy/v1/responses",
            json={"model": "model-a", "input": "Hello"},
            headers=_HEADERS,
        )
        assert resp.status_code == 200

        url = mock_http.call_args.kwargs["url"]
        assert "/chat/completions" in url
        sent = json.loads(mock_http.call_args.kwargs["data"])
        assert sent["messages"] == [{"role": "user", "content": "Hello"}]
        assert sent["stream"] is False
        assert sent["metadata"]["openace_user_id"] == 1  # metadata not dropped


# ── Model prefix ────────────────────────────────────────────────────────


class TestGatewayModelPrefix:
    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    @patch(_PLANNER_PATH)
    def test_prefix_rewrites_model(
        self, mock_planner, mock_get_proxy, mock_quota_cls, mock_http, remote_app
    ):
        mock_planner.return_value = _real_planner(model_prefix_mode=True)
        mock_get_proxy.return_value = _mock_proxy()
        mock_quota_cls.return_value = _quota_ok()
        mock_http.return_value = _mock_upstream(200)

        client = remote_app.test_client()
        client.post(
            "/api/remote/llm-proxy",
            json={"model": "glm-5"},
            headers=_HEADERS,
        )
        sent = json.loads(mock_http.call_args.kwargs["data"])
        assert sent["model"] == "openai/glm-5"


# ── Error leak prevention ──────────────────────────────────────────────


class TestGatewayErrorLeak:
    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    @patch(_PLANNER_PATH)
    def test_upstream_error_redacts_key(
        self, mock_planner, mock_get_proxy, mock_quota_cls, mock_http, remote_app
    ):
        mock_planner.return_value = _real_planner()
        mock_get_proxy.return_value = _mock_proxy()
        mock_quota_cls.return_value = _quota_ok()
        # Upstream error body literally contains the gateway key
        mock_http.return_value = _mock_upstream(500, b'{"error":"key gw-secret-key-123 rejected"}')

        client = remote_app.test_client()
        resp = client.post("/api/remote/llm-proxy", json={"model": "gpt-4"}, headers=_HEADERS)
        assert resp.status_code == 500
        # Key must not appear anywhere in the response
        assert b"gw-secret-key-123" not in resp.data
        data = resp.get_json()
        assert "gw-secret-key-123" not in json.dumps(data)
        assert "[REDACTED]" in data["error"]["message"]


# ── Factory + env override ──────────────────────────────────────────────


class TestGatewayFactoryEnvOverride:
    def test_env_override_activates_gateway(self, monkeypatch):
        reset_gateway_planner_for_tests()
        monkeypatch.setenv("OPENACE_MODEL_GATEWAY_MODE", "gateway")
        monkeypatch.setenv("OPENACE_MODEL_GATEWAY_BASE_URL", "https://gw.example.com/v1")
        monkeypatch.setenv("OPENACE_MODEL_GATEWAY_API_KEY", "k")
        try:
            planner = get_gateway_planner()
            assert planner.is_noop is False
        finally:
            reset_gateway_planner_for_tests()

    def test_no_env_no_flag_returns_null(self, monkeypatch):
        reset_gateway_planner_for_tests()
        monkeypatch.delenv("OPENACE_MODEL_GATEWAY_MODE", raising=False)
        try:
            assert get_gateway_planner().is_noop is True
        finally:
            reset_gateway_planner_for_tests()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
