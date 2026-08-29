#!/usr/bin/env python3
"""
Tests for llm_proxy_handler — standalone helpers, auth/HA routing,
Responses API conversion, and error handling.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from app.modules.workspace.llm_proxy_handler import (
    _determine_target_url,
    _extract_requested_model,
    _resolve_allowed_key_ids,
    handle_llm_proxy_request,
)

pytestmark = [pytest.mark.regression, pytest.mark.issue(604)]

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def flask_app():
    """Minimal Flask app for request-context tests."""
    app = Flask(__name__)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def remote_app():
    """Flask app with remote blueprint for route-level handler tests."""
    from app.routes.remote import remote_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(remote_bp, url_prefix="/api/remote")
    return app


def _mock_proxy_token(**overrides):
    """Build a mock token payload."""
    base = {
        "user_id": 1,
        "tenant_id": 1,
        "provider": "openai",
        "session_id": "sess-abc",
    }
    base.update(overrides)
    return base


def _make_quota_ok():
    mock = MagicMock()
    mock.check_quota.return_value = {"allowed": True}
    return mock


def _mock_upstream_response(status_code=200, content=b'{"ok":true}'):
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content
    resp.headers = {"Content-Type": "application/json"}
    resp.iter_content.return_value = [content]
    resp.json.return_value = json.loads(content)
    return resp


_PROXY_PATH = "app.routes.remote.get_api_key_proxy_service"
_QUOTA_PATH = "app.modules.governance.quota_manager.QuotaManager"
_HTTP_PATH = "requests.request"


# ===================================================================
# A. _extract_requested_model
# ===================================================================


class TestExtractRequestedModel:
    def test_extract_model_valid(self, flask_app):
        body = json.dumps({"model": "qwen3-coder", "messages": []}).encode()
        with flask_app.test_request_context(
            "/", method="POST", data=body, content_type="application/json"
        ):
            assert _extract_requested_model() == "qwen3-coder"

    def test_extract_model_missing_field(self, flask_app):
        body = json.dumps({"messages": []}).encode()
        with flask_app.test_request_context(
            "/", method="POST", data=body, content_type="application/json"
        ):
            assert _extract_requested_model() is None

    def test_extract_model_invalid_json(self, flask_app):
        with flask_app.test_request_context(
            "/", method="POST", data=b"not-json{", content_type="application/json"
        ):
            assert _extract_requested_model() is None

    def test_extract_model_non_string(self, flask_app):
        body = json.dumps({"model": 123}).encode()
        with flask_app.test_request_context(
            "/", method="POST", data=body, content_type="application/json"
        ):
            assert _extract_requested_model() is None

    def test_extract_model_empty_string(self, flask_app):
        body = json.dumps({"model": ""}).encode()
        with flask_app.test_request_context(
            "/", method="POST", data=body, content_type="application/json"
        ):
            assert _extract_requested_model() is None


# ===================================================================
# B. _resolve_allowed_key_ids
# ===================================================================


class TestResolveAllowedKeyIds:
    def test_no_ha_metadata_returns_none(self):
        assert _resolve_allowed_key_ids({}, "model-a") is None

    def test_model_with_matching_keys(self):
        payload = {
            "ha_candidate_keys": [{"key_id": 11}, {"key_id": 22}],
            "ha_model_key_ids": {"model-a": [11], "model-b": [22]},
        }
        assert _resolve_allowed_key_ids(payload, "model-a") == [11]

    def test_model_not_in_model_key_ids_returns_empty(self):
        payload = {
            "ha_candidate_keys": [{"key_id": 11}],
            "ha_model_key_ids": {"model-a": [11]},
        }
        assert _resolve_allowed_key_ids(payload, "model-c") == []

    def test_no_requested_model_returns_all(self):
        payload = {
            "ha_candidate_keys": [{"key_id": 11}, {"key_id": 22}],
            "ha_model_key_ids": {},
        }
        assert _resolve_allowed_key_ids(payload, None) == [11, 22]

    def test_malformed_entries_skipped(self):
        payload = {
            "ha_candidate_keys": [
                {"key_id": 11},
                {"key_id": None},
                "not-a-dict",
                {"not_key_id": 99},
            ],
            "ha_model_key_ids": {},
        }
        assert _resolve_allowed_key_ids(payload, None) == [11]


# ===================================================================
# C. _determine_target_url
# ===================================================================


class TestDetermineTargetUrl:
    def test_base_url_v1_path_v1_stripped(self, flask_app):
        """base_url ending /v1 + path starting v1/ → strip v1/ from path."""
        with flask_app.test_request_context("/"):
            result = _determine_target_url(
                "openai", "https://custom.api.com/v1", "v1/chat/completions"
            )
            assert result == "https://custom.api.com/v1/chat/completions"

    def test_base_url_v4_path_v1_stripped(self, flask_app):
        """base_url ending /v4 + path starting v1/ → strip v1/ to avoid /v4/v1/."""
        with flask_app.test_request_context("/"):
            result = _determine_target_url(
                "openai", "https://custom.api.com/v4", "v1/chat/completions"
            )
            assert result == "https://custom.api.com/v4/chat/completions"

    def test_versionless_base_url_keeps_v1_in_path(self, flask_app):
        """base_url without version suffix → keep v1/ in path."""
        with flask_app.test_request_context("/"):
            result = _determine_target_url(
                "openai", "https://custom.api.com", "v1/chat/completions"
            )
            assert result == "https://custom.api.com/v1/chat/completions"

    def test_base_url_v1_without_v1_path(self, flask_app):
        """base_url ending /v1 but path does not start with v1/ → no stripping."""
        with flask_app.test_request_context("/"):
            result = _determine_target_url(
                "openai", "https://custom.api.com/v1", "chat/completions"
            )
            assert result == "https://custom.api.com/v1/chat/completions"

    def test_provider_fallback_anthropic(self, flask_app):
        with flask_app.test_request_context("/"):
            result = _determine_target_url("anthropic", None, "messages")
            assert result == "https://api.anthropic.com/messages"

    def test_path_traversal_rejected(self, flask_app):
        with flask_app.test_request_context("/"):
            result = _determine_target_url("openai", None, "/../../internal")
            assert isinstance(result, tuple)
            response, status = result
            assert status == 400
            data = json.loads(response.data)
            assert "Invalid path" in data["error"]["message"]

    def test_path_without_traversal_ok(self, flask_app):
        """Flask <path:path> has no leading slash — function adds it."""
        with flask_app.test_request_context("/"):
            result = _determine_target_url("openai", None, "chat/completions")
            assert result == "https://api.openai.com/chat/completions"

    def test_empty_path_uses_request_path(self, flask_app):
        with flask_app.test_request_context("/api/workspace/llm-proxy/v1/chat"):
            result = _determine_target_url("openai", None, "")
            assert result == "https://api.openai.com/v1/chat"

    def test_provider_fallback_google(self, flask_app):
        with flask_app.test_request_context("/"):
            result = _determine_target_url("google", None, "v1/generate")
            assert result == "https://generativelanguage.googleapis.com/v1/generate"


# ===================================================================
# D. handle_llm_proxy_request — auth / HA routing / errors
# ===================================================================


class TestHandleLlmProxyRequest:
    """Route-level tests via remote blueprint (same pattern as test_llm_proxy_failover.py)."""

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_missing_auth_returns_401(self, mock_get_proxy, mock_quota_cls, mock_http, remote_app):
        client = remote_app.test_client()
        resp = client.post("/api/remote/llm-proxy", json={"model": "gpt-4"})
        assert resp.status_code == 401
        data = resp.get_json()
        assert "Missing authorization token" in data["error"]["message"]

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_invalid_token_returns_401(self, mock_get_proxy, mock_quota_cls, mock_http, remote_app):
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = None
        mock_get_proxy.return_value = mock_proxy

        client = remote_app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={"model": "gpt-4"},
            headers={"Authorization": "Bearer bad-token"},
        )
        assert resp.status_code == 401
        assert "Invalid or expired" in resp.get_json()["error"]["message"]

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_scope_mismatch_returns_403(
        self, mock_get_proxy, mock_quota_cls, mock_http, remote_app
    ):
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token(scope="local")
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()

        client = remote_app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={"model": "gpt-4"},
            headers={"Authorization": "Bearer tok"},
        )
        assert resp.status_code == 403
        assert "scoped for" in resp.get_json()["error"]["message"]

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_quota_exceeded_returns_403(
        self, mock_get_proxy, mock_quota_cls, mock_http, remote_app
    ):
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token(scope="remote")
        mock_get_proxy.return_value = mock_proxy

        mock_quota = MagicMock()
        mock_quota.check_quota.return_value = {"allowed": False, "reason": "Daily quota exceeded"}
        mock_quota_cls.return_value = mock_quota

        client = remote_app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={"model": "gpt-4"},
            headers={"Authorization": "Bearer tok"},
        )
        assert resp.status_code == 403
        assert "Quota exceeded" in resp.get_json()["error"]["message"]

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_dynamic_local_pool_fallback(
        self, mock_get_proxy, mock_quota_cls, mock_http, remote_app
    ):
        """Local scope + openai + qwen-code without HA metadata triggers dynamic pool lookup."""
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token(
            scope="local",
            provider="openai",
            session_type="webui",
            session_id="webui:1",
            tool_name="qwen-code",
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
            None,  # resolved_ips (Issue #1894)
        )
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()
        mock_http.return_value = _mock_upstream_response(200)

        # Use workspace blueprint which passes scope="local"
        from app.routes.workspace import workspace_bp

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(workspace_bp, url_prefix="/api/workspace")
        _ws_proxy_path = "app.routes.workspace.get_api_key_proxy_service"
        _ws_quota_path = "app.modules.governance.quota_manager.QuotaManager"

        with (
            patch(_ws_proxy_path, return_value=mock_proxy),
            patch(_ws_quota_path, return_value=_make_quota_ok()),
            patch(_HTTP_PATH, return_value=_mock_upstream_response(200)),
        ):
            client = app.test_client()
            resp = client.post(
                "/api/workspace/llm-proxy",
                json={"model": "qwen3", "messages": [{"role": "user", "content": "hi"}]},
                headers={"Authorization": "Bearer tok"},
            )
            assert resp.status_code == 200
            mock_proxy.get_tool_model_pool.assert_called_once_with(
                tenant_id=1, tool_name="qwen-code", scope="local", provider="openai"
            )
            mock_proxy.resolve_api_key_from_key_ids.assert_called()

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_no_ha_uses_resolve_for_scope(
        self, mock_get_proxy, mock_quota_cls, mock_http, remote_app
    ):
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token(scope="remote")
        mock_proxy.resolve_api_key_for_scope.return_value = (
            "sk-key",
            "https://api.openai.com/v1",
            1,
            None,
            None,  # resolved_ips (Issue #1894)
        )
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()
        mock_http.return_value = _mock_upstream_response(200)

        client = remote_app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer tok"},
        )
        assert resp.status_code == 200
        mock_proxy.resolve_api_key_for_scope.assert_called()
        mock_proxy.resolve_api_key_from_key_ids.assert_not_called()

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_empty_allowed_keys_returns_500(
        self, mock_get_proxy, mock_quota_cls, mock_http, remote_app
    ):
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token(
            scope="remote",
            ha_candidate_keys=[{"key_id": 11}],
            ha_model_key_ids={"model-a": [11]},
        )
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()

        client = remote_app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={"model": "unsupported-model", "messages": []},
            headers={"Authorization": "Bearer tok"},
        )
        assert resp.status_code == 500
        assert "No configured API key supports model" in resp.get_json()["error"]["message"]

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_head_request_no_auth_returns_401(
        self, mock_get_proxy, mock_quota_cls, mock_http, remote_app
    ):
        client = remote_app.test_client()
        resp = client.head("/api/remote/llm-proxy")
        assert resp.status_code == 401
        assert resp.data == b""

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_successful_proxy_returns_upstream(
        self, mock_get_proxy, mock_quota_cls, mock_http, remote_app
    ):
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token(scope="remote")
        mock_proxy.resolve_api_key_for_scope.return_value = (
            "sk-key",
            "https://api.openai.com/v1",
            1,
            None,
            None,  # resolved_ips (Issue #1894)
        )
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()

        body = b'{"choices": [{"message": {"content": "hello"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}'
        mock_http.return_value = _mock_upstream_response(200, body)

        client = remote_app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer tok"},
        )
        assert resp.status_code == 200
        assert b"hello" in resp.data

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_internal_error_returns_generic_message(
        self, mock_get_proxy, mock_quota_cls, mock_http, remote_app
    ):
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token(scope="remote")
        mock_proxy.resolve_api_key_for_scope.return_value = (
            "sk-key",
            "https://api.openai.com/v1",
            1,
            None,
            None,  # resolved_ips (Issue #1894)
        )
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()
        mock_http.side_effect = ConnectionError("DNS resolution failed for secret-host")

        client = remote_app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={"model": "gpt-4", "messages": []},
            headers={"Authorization": "Bearer tok"},
        )
        assert resp.status_code == 502
        data = resp.get_json()
        assert data["error"]["message"] == "Internal proxy error"
        # Ensure no internal info leaked
        assert "secret-host" not in json.dumps(data)


# ===================================================================
# E. Responses API conversion
# ===================================================================


class TestResponsesApiConversion:
    """Test Responses API → Chat Completions conversion in the proxy handler."""

    def _make_ha_proxy(self, base_url="https://example.com/v1"):
        """Create mock proxy with HA token.

        Note: Uses a valid public URL format. SSRF validation may reject if DNS fails.
        Tests that need to bypass SSRF should set OPENACE_LLM_PROXY_DISABLE_SSRF_CHECK=true
        or use the default provider URL.
        """
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token(
            scope="remote",
            provider="openai",
            ha_candidate_keys=[{"key_id": 1}],
            ha_model_key_ids={"model-a": [1]},
        )
        mock_proxy.resolve_api_key_from_key_ids.return_value = (
            "sk-key",
            base_url,
            1,
            None,
            None,  # resolved_ips (Issue #1894)
        )
        return mock_proxy

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_responses_string_input_conversion(
        self, mock_get_proxy, mock_quota_cls, mock_http, remote_app
    ):
        mock_proxy = self._make_ha_proxy()
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()

        # Upstream returns a chat completion response
        upstream_body = json.dumps(
            {
                "id": "chatcmpl-123",
                "model": "model-a",
                "choices": [{"message": {"role": "assistant", "content": "Hi there"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            }
        ).encode()
        mock_http.return_value = _mock_upstream_response(200, upstream_body)

        client = remote_app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy/v1/responses",
            json={"model": "model-a", "input": "Hello"},
            headers={"Authorization": "Bearer tok"},
        )
        assert resp.status_code == 200

        # Verify upstream was called with /chat/completions instead of /responses
        call_args = mock_http.call_args
        assert "/chat/completions" in call_args.kwargs.get("url", call_args[1].get("url", ""))
        # Verify stream=False in converted body
        sent_body = json.loads(call_args.kwargs.get("data", call_args[1].get("data", "")))
        assert sent_body["stream"] is False
        assert sent_body["messages"] == [{"role": "user", "content": "Hello"}]

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_responses_none_text_no_crash(
        self, mock_get_proxy, mock_quota_cls, mock_http, remote_app
    ):
        mock_proxy = self._make_ha_proxy()
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()

        upstream_body = json.dumps(
            {
                "id": "chatcmpl-123",
                "model": "model-a",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        ).encode()
        mock_http.return_value = _mock_upstream_response(200, upstream_body)

        # Content parts with null text
        client = remote_app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy/v1/responses",
            json={
                "model": "model-a",
                "input": [{"role": "user", "content": [{"type": "text", "text": None}]}],
            },
            headers={"Authorization": "Bearer tok"},
        )
        # Should not crash — text defaults to empty string
        assert resp.status_code == 200

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_responses_real_openai_not_converted(
        self, mock_get_proxy, mock_quota_cls, mock_http, remote_app
    ):
        """Real api.openai.com should NOT have Responses API conversion."""
        mock_proxy = self._make_ha_proxy(base_url="https://api.openai.com/v1")
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()
        mock_http.return_value = _mock_upstream_response(200)

        client = remote_app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy/v1/responses",
            json={"model": "model-a", "input": "Hello"},
            headers={"Authorization": "Bearer tok"},
        )
        assert resp.status_code == 200

        # URL should still contain /responses (not converted to /chat/completions)
        call_args = mock_http.call_args
        url = call_args.kwargs.get("url", call_args[1].get("url", ""))
        assert "/responses" in url
        assert "/chat/completions" not in url

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_responses_developer_role_to_system(
        self, mock_get_proxy, mock_quota_cls, mock_http, remote_app
    ):
        mock_proxy = self._make_ha_proxy()
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()

        upstream_body = json.dumps(
            {
                "id": "chatcmpl-123",
                "model": "model-a",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {},
            }
        ).encode()
        mock_http.return_value = _mock_upstream_response(200, upstream_body)

        client = remote_app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy/v1/responses",
            json={
                "model": "model-a",
                "input": [{"role": "developer", "content": "be helpful"}],
            },
            headers={"Authorization": "Bearer tok"},
        )
        assert resp.status_code == 200

        sent_body = json.loads(mock_http.call_args.kwargs["data"])
        assert any(
            m["role"] == "system" and m["content"] == "be helpful" for m in sent_body["messages"]
        )


# ===================================================================
# G. Performance Recording with tenant_id (Issue #3201)
# ===================================================================


class TestPerformanceRecordingWithTenantId:
    """
    Tests for tenant_id propagation in performance recording.
    Issue #3201: Response time metrics missing due to tenant_id=None.
    """

    def test_forward_via_gateway_with_tenant_id(self, flask_app):
        """
        Test that _forward_via_gateway correctly passes tenant_id to _finalize_upstream_response.
        This ensures the Gateway path (model gateway) properly records performance data.

        Note: This test verifies the parameter passing through the call chain.
        The actual recorder behavior is tested in test_finalize_upstream_response_with_tenant_id.
        """
        from unittest.mock import MagicMock, patch

        from app.modules.workspace.llm_proxy_handler import _forward_via_gateway

        # Setup mock HTTP response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"ok":true}'
        mock_response.headers = {"Content-Type": "application/json"}

        # Setup mock plan
        class MockPlan:
            target_url = "https://api.example.com/v1/chat/completions"
            path = "v1/chat/completions"
            gateway_key = "test-key"
            headers = {}
            ssrf_blocked = False

            @staticmethod
            def body_transformer(data):
                return data

        plan = MockPlan()

        # Create a mock for _finalize_upstream_response
        finalize_mock = MagicMock(return_value=MagicMock(status_code=200))

        with patch(
            "app.modules.workspace.llm_proxy_handler._finalize_upstream_response", finalize_mock
        ):
            with patch("requests.request", return_value=mock_response):
                with flask_app.test_request_context(
                    "/", method="POST", data=b'{"model":"test"}', content_type="application/json"
                ):
                    _ = _forward_via_gateway(
                        plan,
                        session_id="session-1",
                        user_id=1,
                        provider="openai",
                        tenant_id=1,  # Should be passed to _finalize_upstream_response
                        requested_model="test-model",
                    )

        # Verify _finalize_upstream_response was called
        assert finalize_mock.called, "_finalize_upstream_response should have been called"

        # Verify tenant_id was passed
        call_kwargs = finalize_mock.call_args[1]
        assert "tenant_id" in call_kwargs, "tenant_id should be in call arguments"
        assert call_kwargs["tenant_id"] == 1, "tenant_id should be passed correctly"

    def test_finalize_upstream_response_with_tenant_id(self, flask_app):
        """
        Test that _finalize_upstream_response correctly passes tenant_id to the recorder.
        This ensures the direct connection path properly records performance data.
        """
        from unittest.mock import MagicMock, patch

        from app.modules.workspace.llm_proxy_handler import _finalize_upstream_response

        # Setup mock recorder instance
        mock_recorder_instance = MagicMock()

        # Setup mock response with all required attributes
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"ok":true}'
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.iter_content.return_value = [b'{"ok":true}']

        # Patch the recorder class at the source
        with patch(
            "app.utils.request_performance.RequestPerformanceRecorder"
        ) as mock_recorder_class:
            mock_recorder_class.return_value = mock_recorder_instance
            mock_recorder_class._instance = None  # Reset singleton

            with flask_app.test_request_context("/"):
                _ = _finalize_upstream_response(
                    mock_response,
                    b'{"model":"test"}',
                    session_id="session-1",
                    user_id=1,
                    provider="openai",
                    tenant_id=2,  # Should be passed to recorder
                )

        # Verify recorder was called with correct tenant_id
        assert (
            mock_recorder_instance.record_request_start.called
        ), "record_request_start should have been called"
        call_kwargs = mock_recorder_instance.record_request_start.call_args[1]
        assert call_kwargs["tenant_id"] == 2, "tenant_id should be passed to recorder"

    @patch(_HTTP_PATH)
    @patch("app.utils.request_performance.RequestPerformanceRecorder")
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_llm_proxy_request_performance_recording(
        self, mock_get_proxy, mock_quota_cls, mock_recorder_class, mock_http, remote_app
    ):
        """
        End-to-end test for performance recording through handle_llm_proxy_request.
        Verifies that the entire request flow correctly records performance data.
        """
        # Setup mock recorder instance
        mock_recorder_instance = MagicMock()
        mock_recorder_class.return_value = mock_recorder_instance
        mock_recorder_class._instance = None  # Reset singleton

        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token()
        mock_proxy.resolve_api_key_for_scope.return_value = (
            "key",
            "https://api.openai.com",
            1,
            {},
            "",
        )
        mock_get_proxy.return_value = mock_proxy

        mock_quota_cls.return_value = _make_quota_ok()

        # Setup mock HTTP response
        upstream_body = json.dumps(
            {
                "id": "chatcmpl-123",
                "model": "gpt-4",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
        ).encode()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = upstream_body
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.iter_content.return_value = [upstream_body]
        mock_http.return_value = mock_response

        client = remote_app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy/v1/chat/completions",
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "test"}]},
            headers={"Authorization": "Bearer valid-token"},
        )

        assert resp.status_code == 200

        # Verify performance recording was initiated
        assert mock_recorder_instance.record_request_start.called
        call_kwargs = mock_recorder_instance.record_request_start.call_args[1]
        # tenant_id should be 1 (from mock token)
        assert call_kwargs["tenant_id"] == 1, "tenant_id should be extracted from token and passed"

    @patch(_HTTP_PATH)
    @patch("app.utils.request_performance.RequestPerformanceRecorder")
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_tenant_isolation_in_performance_data(
        self, mock_get_proxy, mock_quota_cls, mock_recorder_class, mock_http, remote_app
    ):
        """
        Test that different tenant_ids result in separate performance records.
        This is a critical security requirement to prevent cross-tenant data leakage.
        """
        # Setup mock recorder instance
        mock_recorder_instance = MagicMock()
        mock_recorder_class.return_value = mock_recorder_instance
        mock_recorder_class._instance = None  # Reset singleton

        mock_proxy = MagicMock()

        # First request for tenant 1
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token(tenant_id=1)
        mock_proxy.resolve_api_key_for_scope.return_value = (
            "key",
            "https://api.openai.com",
            1,
            {},
            "",
        )
        mock_get_proxy.return_value = mock_proxy

        mock_quota_cls.return_value = _make_quota_ok()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"ok":true}'
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.iter_content.return_value = [b'{"ok":true}']
        mock_http.return_value = mock_response

        client = remote_app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy/v1/chat/completions",
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "test"}]},
            headers={"Authorization": "Bearer tenant1-token"},
        )
        assert resp.status_code == 200

        # Verify tenant_id=1 was recorded
        call_kwargs_1 = mock_recorder_instance.record_request_start.call_args[1]
        assert call_kwargs_1["tenant_id"] == 1

        # Reset for second request
        mock_recorder_instance.reset_mock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token(tenant_id=2)

        resp = client.post(
            "/api/remote/llm-proxy/v1/chat/completions",
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "test"}]},
            headers={"Authorization": "Bearer tenant2-token"},
        )
        assert resp.status_code == 200

        # Verify tenant_id=2 was recorded
        call_kwargs_2 = mock_recorder_instance.record_request_start.call_args[1]
        assert call_kwargs_2["tenant_id"] == 2

        # Ensure tenant IDs are different
        assert call_kwargs_1["tenant_id"] != call_kwargs_2["tenant_id"]

    def test_performance_recording_error_handling(self, flask_app):
        """
        Test that performance recording failures do not affect the main request.
        The recorder should fail gracefully without blocking the response.
        """
        from unittest.mock import MagicMock, patch

        from app.modules.workspace.llm_proxy_handler import _finalize_upstream_response

        # Make recorder class raise an exception
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"ok":true}'
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.iter_content.return_value = [b'{"ok":true}']

        with patch(
            "app.utils.request_performance.RequestPerformanceRecorder"
        ) as mock_recorder_class:
            # Make the recorder raise an exception
            mock_recorder_class.side_effect = Exception("Recorder not initialized")

            with flask_app.test_request_context("/"):
                # This should not raise an exception
                result = _finalize_upstream_response(
                    mock_response,
                    b'{"model":"test"}',
                    session_id="session-1",
                    user_id=1,
                    provider="openai",
                    tenant_id=1,
                )

        # Verify the response was still returned
        assert result.status_code == 200

    def test_tenant_id_zero_handling(self, flask_app):
        """
        Test that tenant_id=0 (default tenant) is handled correctly.
        This ensures the system can record performance data for the default tenant.
        """
        from unittest.mock import MagicMock, patch

        from app.modules.workspace.llm_proxy_handler import _finalize_upstream_response

        # Setup mock recorder instance
        mock_recorder_instance = MagicMock()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"ok":true}'
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.iter_content.return_value = [b'{"ok":true}']

        # Patch the recorder class at the source
        with patch(
            "app.utils.request_performance.RequestPerformanceRecorder"
        ) as mock_recorder_class:
            mock_recorder_class.return_value = mock_recorder_instance
            mock_recorder_class._instance = None  # Reset singleton

            with flask_app.test_request_context("/"):
                _ = _finalize_upstream_response(
                    mock_response,
                    b'{"model":"test"}',
                    session_id="session-1",
                    user_id=1,
                    provider="openai",
                    tenant_id=0,  # Default tenant
                )

        # Verify recorder was called with tenant_id=0
        assert mock_recorder_instance.record_request_start.called
        call_kwargs = mock_recorder_instance.record_request_start.call_args[1]
        assert call_kwargs["tenant_id"] == 0, "tenant_id=0 should be recorded correctly"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
