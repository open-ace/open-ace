#!/usr/bin/env python3
"""
Tests for LLM proxy multi-key failover behavior.

Validates that llm_proxy retries with the next candidate key when upstream
returns 401/403/429 or gateway errors (502/503/504), and exhausts all keys
before giving up.

Issue #1995: Added 502/503/504 gateway error failover support.
"""

import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def app():
    """Create a Flask test app with the remote blueprint."""
    from flask import Flask

    from app.routes.remote import remote_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(remote_bp, url_prefix="/api/remote")
    return app


def _mock_proxy_token(user_id=1, tenant_id=1, provider="openai", session_id="sess-abc"):
    """Build a mock token payload."""
    return {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "provider": provider,
        "session_id": session_id,
    }


def _make_quota_ok():
    """Return a mock QuotaManager that allows requests."""
    mock = MagicMock()
    mock.check_quota.return_value = {"allowed": True}
    return mock


def _mock_upstream_response(status_code, content=b'{"ok":true}'):
    """Create a mock upstream HTTP response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content
    resp.headers = {"Content-Type": "application/json"}
    resp.iter_content.return_value = [content]
    resp.json.return_value = json.loads(content)
    return resp


# Patch targets — all module-level imports in app.routes.remote
_PROXY_PATH = "app.routes.remote.get_api_key_proxy_service"
_QUOTA_PATH = "app.modules.governance.quota_manager.QuotaManager"
_HTTP_PATH = "requests.request"


class TestLLMProxyFailover:
    """Test failover behavior when upstream returns auth/rate-limit errors."""

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_failover_on_401(self, mock_get_proxy, mock_quota_cls, mock_http_req, app):
        """First key returns 401, second key returns 200 → succeeds."""
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token()
        mock_proxy.resolve_api_key_for_scope.side_effect = [
            ("sk-key1", "https://api.openai.com/v1", 1, None, None),
            ("sk-key2", "https://api.openai.com/v1", 2, None, None),
            None,
        ]
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()

        mock_http_req.side_effect = [
            _mock_upstream_response(401, b'{"error":{"message":"invalid key"}}'),
            _mock_upstream_response(200),
        ]

        client = app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer test-token"},
        )

        assert resp.status_code == 200
        # Second resolve call should have key 1 excluded
        calls = mock_proxy.resolve_api_key_for_scope.call_args_list
        assert len(calls) == 2
        second_exclude = calls[1].kwargs.get("exclude_key_ids", set())
        assert 1 in second_exclude

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_failover_on_429(self, mock_get_proxy, mock_quota_cls, mock_http_req, app):
        """First key rate-limited (429), second key succeeds."""
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token()
        mock_proxy.resolve_api_key_for_scope.side_effect = [
            ("sk-key1", "https://api.openai.com/v1", 1, None, None),
            ("sk-key2", "https://api.openai.com/v1", 2, None, None),
            None,
        ]
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()

        mock_http_req.side_effect = [
            _mock_upstream_response(429, b'{"error":{"message":"rate limited"}}'),
            _mock_upstream_response(200),
        ]

        client = app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer test-token"},
        )

        assert resp.status_code == 200

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_all_keys_exhausted(self, mock_get_proxy, mock_quota_cls, mock_http_req, app):
        """All 4 keys fail with 401 → returns 502 with exhaustion message."""
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token()
        mock_proxy.resolve_api_key_for_scope.side_effect = [
            ("sk-1", "https://api.openai.com/v1", 1, None, None),
            ("sk-2", "https://api.openai.com/v1", 2, None, None),
            ("sk-3", "https://api.openai.com/v1", 3, None, None),
            ("sk-4", "https://api.openai.com/v1", 4, None, None),
            None,
        ]
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()

        mock_http_req.return_value = _mock_upstream_response(
            401, b'{"error":{"message":"invalid"}}'
        )

        client = app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer test-token"},
        )

        assert resp.status_code == 502
        data = resp.get_json()
        assert "4 API key(s) failed" in data["error"]["message"]

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_fourth_key_succeeds(self, mock_get_proxy, mock_quota_cls, mock_http_req, app):
        """4 keys: first 3 return 429, 4th returns 200 → succeeds."""
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token()
        mock_proxy.resolve_api_key_for_scope.side_effect = [
            ("sk-1", "https://api.openai.com/v1", 1, None, None),
            ("sk-2", "https://api.openai.com/v1", 2, None, None),
            ("sk-3", "https://api.openai.com/v1", 3, None, None),
            ("sk-4", "https://api.openai.com/v1", 4, None, None),
            None,
        ]
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()

        resp_429 = _mock_upstream_response(429, b'{"error":{"message":"rate limited"}}')
        resp_200 = _mock_upstream_response(200)

        mock_http_req.side_effect = [resp_429, resp_429, resp_429, resp_200]

        client = app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer test-token"},
        )

        assert resp.status_code == 200
        # Verify 4 resolve calls — keys 1,2,3 excluded progressively
        assert mock_proxy.resolve_api_key_for_scope.call_count == 4

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_no_failover_on_500(self, mock_get_proxy, mock_quota_cls, mock_http_req, app):
        """500 internal server error should NOT trigger failover — return error directly."""
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token()
        mock_proxy.resolve_api_key_for_scope.return_value = (
            "sk-1",
            "https://api.openai.com/v1",
            1,
            None,
            None,
        )
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()

        mock_http_req.return_value = _mock_upstream_response(
            500, b'{"error":{"message":"internal error"}}'
        )

        client = app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer test-token"},
        )

        # Should return 500 directly, no retry
        assert resp.status_code == 500
        # Only 1 resolve call — no retry
        assert mock_proxy.resolve_api_key_for_scope.call_count == 1

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_failover_on_503(self, mock_get_proxy, mock_quota_cls, mock_http_req, app):
        """First key returns 503, second key returns 200 → succeeds via failover.

        Issue #1995: 503 Service Unavailable is a transient gateway error.
        """
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token()
        mock_proxy.resolve_api_key_for_scope.side_effect = [
            ("sk-key1", "https://api.openai.com/v1", 1, None, None),
            ("sk-key2", "https://api.openai.com/v1", 2, None, None),
            None,
        ]
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()

        mock_http_req.side_effect = [
            _mock_upstream_response(503, b'{"error":{"message":"Service Unavailable"}}'),
            _mock_upstream_response(200),
        ]

        client = app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer test-token"},
        )

        assert resp.status_code == 200
        # Second resolve call should have key 1 excluded
        calls = mock_proxy.resolve_api_key_for_scope.call_args_list
        assert len(calls) == 2
        second_exclude = calls[1].kwargs.get("exclude_key_ids", set())
        assert 1 in second_exclude

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_failover_on_502(self, mock_get_proxy, mock_quota_cls, mock_http_req, app):
        """First key returns 502 Bad Gateway, second key returns 200 → succeeds.

        Issue #1995: 502 Bad Gateway is a transient gateway error.
        """
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token()
        mock_proxy.resolve_api_key_for_scope.side_effect = [
            ("sk-key1", "https://api.openai.com/v1", 1, None, None),
            ("sk-key2", "https://api.openai.com/v1", 2, None, None),
            None,
        ]
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()

        mock_http_req.side_effect = [
            _mock_upstream_response(502, b'{"error":{"message":"Bad Gateway"}}'),
            _mock_upstream_response(200),
        ]

        client = app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer test-token"},
        )

        assert resp.status_code == 200
        calls = mock_proxy.resolve_api_key_for_scope.call_args_list
        assert len(calls) == 2
        second_exclude = calls[1].kwargs.get("exclude_key_ids", set())
        assert 1 in second_exclude

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_failover_on_504(self, mock_get_proxy, mock_quota_cls, mock_http_req, app):
        """First key returns 504 Gateway Timeout, second key returns 200 → succeeds.

        Issue #1995: 504 Gateway Timeout is a transient gateway error.
        """
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token()
        mock_proxy.resolve_api_key_for_scope.side_effect = [
            ("sk-key1", "https://api.openai.com/v1", 1, None, None),
            ("sk-key2", "https://api.openai.com/v1", 2, None, None),
            None,
        ]
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()

        mock_http_req.side_effect = [
            _mock_upstream_response(504, b'{"error":{"message":"Gateway Timeout"}}'),
            _mock_upstream_response(200),
        ]

        client = app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer test-token"},
        )

        assert resp.status_code == 200
        calls = mock_proxy.resolve_api_key_for_scope.call_args_list
        assert len(calls) == 2
        second_exclude = calls[1].kwargs.get("exclude_key_ids", set())
        assert 1 in second_exclude

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_all_keys_exhausted_on_503(self, mock_get_proxy, mock_quota_cls, mock_http_req, app):
        """All keys fail with 503 → returns 502 with exhaustion message.

        Issue #1995: Verify all keys are tried before giving up on 503.
        """
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token()
        mock_proxy.resolve_api_key_for_scope.side_effect = [
            ("sk-1", "https://api.openai.com/v1", 1, None, None),
            ("sk-2", "https://api.openai.com/v1", 2, None, None),
            ("sk-3", "https://api.openai.com/v1", 3, None, None),
            None,
        ]
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()

        mock_http_req.return_value = _mock_upstream_response(
            503, b'{"error":{"message":"Service Unavailable"}}'
        )

        client = app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer test-token"},
        )

        assert resp.status_code == 502
        data = resp.get_json()
        assert "3 API key(s) failed" in data["error"]["message"]

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_no_keys_configured(self, mock_get_proxy, mock_quota_cls, mock_http_req, app):
        """No API keys configured → returns 500 config error."""
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token()
        mock_proxy.resolve_api_key_for_scope.return_value = None
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()

        client = app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer test-token"},
        )

        assert resp.status_code == 500
        data = resp.get_json()
        assert "No API key configured" in data["error"]["message"]

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_failover_on_400_model_not_available(
        self, mock_get_proxy, mock_quota_cls, mock_http_req, app
    ):
        """First key returns 400 'Model not available for this API key',
        second key returns 200 → succeeds via failover."""
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token()
        mock_proxy.resolve_api_key_for_scope.side_effect = [
            ("sk-key1", "https://mintcn.macaron.xin/v1", 21, None, None),
            ("sk-key2", "https://coding.dashscope.aliyuncs.com/v1", 18, None, None),
            None,
        ]
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()

        mock_http_req.side_effect = [
            _mock_upstream_response(
                400,
                b'{"error":{"message":"Model glm-5 is not available for this API key",'
                b'"type":"invalid_request_error"}}',
            ),
            _mock_upstream_response(200),
        ]

        client = app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={"model": "glm-5", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer test-token"},
        )

        assert resp.status_code == 200
        calls = mock_proxy.resolve_api_key_for_scope.call_args_list
        assert len(calls) == 2
        second_exclude = calls[1].kwargs.get("exclude_key_ids", set())
        assert 21 in second_exclude

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_no_failover_on_generic_400(self, mock_get_proxy, mock_quota_cls, mock_http_req, app):
        """A generic 400 (not 'Model not available') should NOT trigger failover."""
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token()
        mock_proxy.resolve_api_key_for_scope.return_value = (
            "sk-key1",
            "https://api.openai.com/v1",
            1,
            None,
            None,
        )
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()

        mock_http_req.return_value = _mock_upstream_response(
            400, b'{"error":{"message":"invalid request body","type":"invalid_request_error"}}'
        )

        client = app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer test-token"},
        )

        assert resp.status_code == 400
        assert mock_proxy.resolve_api_key_for_scope.call_count == 1

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_ha_pool_failover_is_limited_to_selected_model_keys(
        self, mock_get_proxy, mock_quota_cls, mock_http_req, app
    ):
        """Integrated-mode HA should only retry across keys that support the selected model."""
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = {
            **_mock_proxy_token(),
            "scope": "remote",
            "ha_candidate_keys": [
                {"key_id": 11, "priority": 200, "weight": 100},
                {"key_id": 22, "priority": 100, "weight": 100},
                {"key_id": 33, "priority": 50, "weight": 100},
            ],
            "ha_model_key_ids": {
                "model-a": [11, 33],
                "model-b": [22],
            },
        }
        mock_proxy.resolve_api_key_from_key_ids.side_effect = [
            ("sk-key1", "https://api.openai.com/v1", 11, None, None),
            ("sk-key3", "https://api.openai.com/v1", 33, None, None),
            None,
        ]
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()
        mock_http_req.side_effect = [
            _mock_upstream_response(429, b'{"error":{"message":"rate limited"}}'),
            _mock_upstream_response(200),
        ]

        client = app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={"model": "model-a", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer test-token"},
        )

        assert resp.status_code == 200
        first_call, second_call = mock_proxy.resolve_api_key_from_key_ids.call_args_list
        assert first_call.args[2] == [11, 33]
        assert second_call.args[2] == [11, 33]
        assert second_call.kwargs["exclude_key_ids"] == {11}
        mock_proxy.resolve_api_key_for_scope.assert_not_called()

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_ha_pool_exhaustion_terminates_when_resolver_ignores_exclude(
        self, mock_get_proxy, mock_quota_cls, mock_http_req, app
    ):
        """Issue #2466: a resolver that ignores exclude_key_ids and keeps handing
        back the same key must terminate the failover loop with 502, not retry
        the excluded key forever."""
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = {
            **_mock_proxy_token(),
            "scope": "remote",
            "ha_candidate_keys": [
                {"key_id": 11, "priority": 200, "weight": 100},
            ],
            "ha_model_key_ids": {
                "model-a": [11],
            },
        }
        # return_value (not side_effect): ignores exclude_key_ids, always
        # returns key 11 even after it was excluded by a 429.
        mock_proxy.resolve_api_key_from_key_ids.return_value = (
            "sk-key1",
            "https://api.openai.com/v1",
            11,
            None,
            None,
        )
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()
        # Two upstream 429s: the loop must stop after the second resolve (the
        # excluded key comes back), so a StopIteration here would surface as a
        # test failure rather than an infinite retry loop.
        mock_http_req.side_effect = [
            _mock_upstream_response(429, b'{"error":{"message":"rate limited"}}'),
            _mock_upstream_response(429, b'{"error":{"message":"rate limited"}}'),
        ]

        client = app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={"model": "model-a", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer test-token"},
        )

        assert resp.status_code == 502
        data = resp.get_json()
        assert data["error"]["type"] == "upstream_error"
        assert "1 API key(s) failed" in data["error"]["message"]
        # Exactly two resolves: first attempt fails and excludes key 11, the
        # second returns the excluded key and terminates as pool exhaustion.
        assert mock_proxy.resolve_api_key_from_key_ids.call_count == 2
        second_call = mock_proxy.resolve_api_key_from_key_ids.call_args_list[1]
        assert second_call.kwargs["exclude_key_ids"] == {11}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
