"""Integration tests for LLM Proxy URL security guard.

Tests the full flow with Flask app context and HA failover scenarios (Issue #1894).

Coverage:
- API key base_url blocking
- HA failover with different base_url values
- Gateway environment variable blocking
- Allowlist private host passthrough
- Storage pre-validation
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from app.modules.workspace.api_key_proxy import APIKeyProxyService
from app.utils.llm_proxy_request import reset_allowlist_cache_for_tests


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_cache():
    """Reset allowlist cache before each test."""
    reset_allowlist_cache_for_tests()
    yield
    reset_allowlist_cache_for_tests()


@pytest.fixture
def flask_app():
    """Create a Flask app for testing."""
    app = Flask(__name__)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def remote_app():
    """Flask app with remote blueprint."""
    from app.routes.remote import remote_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(remote_bp, url_prefix="/api/remote")
    return app


def _mock_proxy_token(**overrides):
    """Build a mock proxy token payload."""
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


# ── API Key Base URL Blocking Tests ───────────────────────────────────────────


class TestApiKeyBaseUrlBlocking:
    """Tests for API key base_url blocking."""

    @patch("requests.request")
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_private_base_url_blocked(
        self, mock_get_proxy, mock_quota_cls, mock_http, remote_app
    ):
        """Private base_url should be blocked at request time."""
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token(scope="remote")
        mock_proxy.resolve_api_key_for_scope.return_value = (
            "sk-key",
            "http://10.0.0.1:8080/v1",  # Private IP
            1,
            None,
        )
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()

        client = remote_app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer tok"},
        )

        assert resp.status_code == 403
        data = resp.get_json()
        assert data["error"]["code"] == "SSRF_BLOCKED"

    @patch("requests.request")
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_metadata_base_url_blocked(
        self, mock_get_proxy, mock_quota_cls, mock_http, remote_app
    ):
        """Metadata IP base_url should be blocked."""
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token(scope="remote")
        mock_proxy.resolve_api_key_for_scope.return_value = (
            "sk-key",
            "http://169.254.169.254",  # Metadata IP
            1,
            None,
        )
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()

        client = remote_app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={"model": "gpt-4", "messages": []},
            headers={"Authorization": "Bearer tok"},
        )

        assert resp.status_code == 403
        data = resp.get_json()
        assert data["error"]["code"] == "SSRF_BLOCKED"

    @patch("requests.request")
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_localhost_base_url_blocked(
        self, mock_get_proxy, mock_quota_cls, mock_http, remote_app
    ):
        """localhost base_url should be blocked."""
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token(scope="remote")
        mock_proxy.resolve_api_key_for_scope.return_value = (
            "sk-key",
            "http://localhost:8080",
            1,
            None,
        )
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()

        client = remote_app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={"model": "gpt-4", "messages": []},
            headers={"Authorization": "Bearer tok"},
        )

        assert resp.status_code == 403


# ── HA Failover Tests ──────────────────────────────────────────────────────────


class TestHAFailover:
    """Tests for HA failover with different base_url values."""

    @patch("requests.request")
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_ha_failover_first_key_public_second_private_blocked(
        self, mock_get_proxy, mock_quota_cls, mock_http, remote_app
    ):
        """HA failover: second key with private URL should be blocked."""
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token(
            scope="remote",
            ha_candidate_keys=[{"key_id": 1}, {"key_id": 2}],
            ha_model_key_ids={"gpt-4": [1, 2]},
        )
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()

        # First call returns public URL (key_id=1), fails with 401
        # Second call returns private URL (key_id=2), should be blocked
        call_count = 0

        def mock_resolve(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            exclude_key_ids = kwargs.get("exclude_key_ids", set())
            if 1 not in exclude_key_ids:
                return ("sk-key-1", "https://api.openai.com/v1", 1, None)
            elif 2 not in exclude_key_ids:
                return ("sk-key-2", "http://10.0.0.1:8080/v1", 2, None)  # Private
            return None

        mock_proxy.resolve_api_key_from_key_ids.side_effect = mock_resolve

        # First request to public URL returns 401 (triggers failover)
        mock_http.side_effect = [
            _mock_upstream_response(401, b'{"error": "unauthorized"}'),  # First attempt
        ]

        client = remote_app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={"model": "gpt-4", "messages": []},
            headers={"Authorization": "Bearer tok"},
        )

        # Should be blocked due to private URL in second key
        assert resp.status_code == 403
        data = resp.get_json()
        assert data["error"]["code"] == "SSRF_BLOCKED"

    @patch("requests.request")
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_ha_failover_all_private_urls_blocked(
        self, mock_get_proxy, mock_quota_cls, mock_http, remote_app
    ):
        """HA failover: all keys with private URLs should be blocked."""
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token(
            scope="remote",
            ha_candidate_keys=[{"key_id": 1}, {"key_id": 2}],
            ha_model_key_ids={"gpt-4": [1, 2]},
        )
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()

        # Both keys have private URLs
        def mock_resolve(*args, **kwargs):
            exclude_key_ids = kwargs.get("exclude_key_ids", set())
            if 1 not in exclude_key_ids:
                return ("sk-key-1", "http://10.0.0.1/v1", 1, None)
            elif 2 not in exclude_key_ids:
                return ("sk-key-2", "http://192.168.1.1/v1", 2, None)
            return None

        mock_proxy.resolve_api_key_from_key_ids.side_effect = mock_resolve

        client = remote_app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={"model": "gpt-4", "messages": []},
            headers={"Authorization": "Bearer tok"},
        )

        # Should be blocked on first key
        assert resp.status_code == 403


# ── Allowlist Bypass Tests ─────────────────────────────────────────────────────


class TestAllowlistBypassIntegration:
    """Tests for allowlist bypass in integration."""

    @patch("requests.request")
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_allowlist_private_host_passes(
        self, mock_get_proxy, mock_quota_cls, mock_http, remote_app, monkeypatch
    ):
        """Private host in allowlist should pass validation."""
        # Set allowlist
        monkeypatch.setenv("OPENACE_LLM_PROXY_ALLOWED_HOSTS", "private-llm.internal")
        reset_allowlist_cache_for_tests()

        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token(scope="remote")
        mock_proxy.resolve_api_key_for_scope.return_value = (
            "sk-key",
            "http://private-llm.internal:8080/v1",
            1,
            None,
        )
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()

        # Mock successful upstream response
        body = b'{"choices": [{"message": {"content": "hello"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}'
        mock_http.return_value = _mock_upstream_response(200, body)

        client = remote_app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer tok"},
        )

        # Should succeed
        assert resp.status_code == 200


# ── Storage Pre-validation Tests ─────────────────────────────────────────────────


class TestStoragePreValidation:
    """Tests for storage pre-validation."""

    def test_store_private_url_rejected(self):
        """Storing private URL should be rejected."""
        from app.utils.llm_proxy_request import validate_base_url_for_storage

        is_valid, error = validate_base_url_for_storage(
            "http://10.0.0.1:8080",
            tenant_id=1,
            provider="openai",
        )

        assert is_valid is False
        assert error is not None

    def test_store_public_url_accepted(self):
        """Storing public URL should be accepted."""
        from app.utils.llm_proxy_request import validate_base_url_for_storage

        is_valid, error = validate_base_url_for_storage(
            "https://api.openai.com",
            tenant_id=1,
            provider="openai",
        )

        assert is_valid is True
        assert error is None


# ── Error Message Security Tests ────────────────────────────────────────────────


class TestErrorMessageSecurity:
    """Tests for error message security."""

    @patch("requests.request")
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_error_does_not_expose_blocked_ip(
        self, mock_get_proxy, mock_quota_cls, mock_http, remote_app
    ):
        """Error response should not expose the blocked IP."""
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token(scope="remote")
        mock_proxy.resolve_api_key_for_scope.return_value = (
            "sk-key",
            "http://10.20.30.40:8080",  # Specific private IP
            1,
            None,
        )
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()

        client = remote_app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={"model": "gpt-4", "messages": []},
            headers={"Authorization": "Bearer tok"},
        )

        data = resp.get_json()
        response_str = json.dumps(data)

        # Should NOT contain the specific IP
        assert "10.20.30.40" not in response_str

        # Should have generic error message
        assert data["error"]["message"] == "Provider endpoint URL is not accessible"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])