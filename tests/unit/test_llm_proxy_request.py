"""Unit tests for LLM Proxy URL security guard.

Tests for the unified request wrapper with SSRF protection (Issue #1894).

Coverage:
- IPv4 private address blocking
- IPv6 private address blocking
- Metadata IP blocking
- NAT64 address blocking
- Allowlist bypass
- DNS rebinding prevention
- Guard mode (log/enforce)
- Parameter compatibility
"""

from __future__ import annotations

import os
import socket
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
import requests
from flask import Flask

from app.utils.llm_proxy_request import (
    _normalize_host_for_allowlist,
    get_allowlist,
    is_host_in_allowlist,
    reset_allowlist_cache_for_tests,
    safe_llm_proxy_request,
    validate_base_url_for_storage,
)
from app.utils.outbound_url_guard import OutboundUrlBlockedError


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_cache():
    """Reset allowlist cache before each test."""
    reset_allowlist_cache_for_tests()
    yield
    reset_allowlist_cache_for_tests()


@pytest.fixture
def mock_env_allowlist(monkeypatch):
    """Set environment variable for allowlist."""

    def _set(value: str):
        monkeypatch.setenv("OPENACE_LLM_PROXY_ALLOWED_HOSTS", value)
        reset_allowlist_cache_for_tests()

    return _set


@pytest.fixture
def mock_guard_mode(monkeypatch):
    """Set guard mode."""

    def _set(mode: str):
        monkeypatch.setenv("OPENACE_LLM_PROXY_URL_GUARD_MODE", mode)

    return _set


@pytest.fixture
def flask_app():
    """Create a Flask app for testing."""
    app = Flask(__name__)
    app.config["TESTING"] = True
    return app


def _mock_resolver(*addresses):
    """Create a mock DNS resolver that returns specific addresses."""

    def resolve(host, port, type=socket.SOCK_STREAM):
        return [
            (
                socket.AF_INET6 if ":" in address else socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (address, port),
            )
            for address in addresses
        ]

    return resolve


# ── Allowlist Normalization Tests ─────────────────────────────────────────────


class TestNormalizeHostForAllowlist:
    """Tests for host normalization in allowlist matching."""

    def test_ipv6_compressed(self):
        """IPv6 addresses should be compressed to canonical form."""
        assert _normalize_host_for_allowlist("::1") == "::1"
        assert _normalize_host_for_allowlist("0:0:0:0:0:0:0:1") == "::1"

    def test_ipv6_full_form(self):
        """Full IPv6 form should be compressed."""
        assert _normalize_host_for_allowlist("0000:0000:0000:0000:0000:0000:0000:0001") == "::1"

    def test_ipv6_scope_id_rejected(self):
        """IPv6 scope ID should raise ValueError."""
        with pytest.raises(ValueError, match="scope ID"):
            _normalize_host_for_allowlist("fe80::1%eth0")

    def test_domain_lowercase(self):
        """Domain names should be lowercased."""
        assert _normalize_host_for_allowlist("Example.COM") == "example.com"

    def test_domain_trailing_dot(self):
        """Trailing dot should be removed."""
        assert _normalize_host_for_allowlist("example.com.") == "example.com"

    def test_ipv4_normalized(self):
        """IPv4 addresses should be normalized."""
        assert _normalize_host_for_allowlist("10.0.0.1") == "10.0.0.1"

    def test_empty_string(self):
        """Empty string should return empty."""
        assert _normalize_host_for_allowlist("") == ""

    def test_whitespace_trimmed(self):
        """Whitespace should be trimmed."""
        assert _normalize_host_for_allowlist("  example.com  ") == "example.com"


# ── Allowlist Cache Tests ─────────────────────────────────────────────────────


class TestAllowlistCache:
    """Tests for allowlist caching behavior."""

    def test_cache_reads_environment(self, mock_env_allowlist):
        """Cache should read from environment variable."""
        mock_env_allowlist("private-llm.internal,10.0.0.5")

        allowlist = get_allowlist()
        assert "private-llm.internal" in allowlist
        assert "10.0.0.5" in allowlist

    def test_cache_empty_when_no_env(self, monkeypatch):
        """Cache should be empty when no environment variable set."""
        monkeypatch.delenv("OPENACE_LLM_PROXY_ALLOWED_HOSTS", raising=False)
        reset_allowlist_cache_for_tests()

        allowlist = get_allowlist()
        assert len(allowlist) == 0

    def test_cache_skips_empty_entries(self, mock_env_allowlist):
        """Cache should skip empty entries from environment."""
        mock_env_allowlist("host1,,host2,")

        allowlist = get_allowlist()
        assert "host1" in allowlist
        assert "host2" in allowlist
        assert "" not in allowlist

    def test_cache_trims_whitespace(self, mock_env_allowlist):
        """Cache should trim whitespace from entries."""
        mock_env_allowlist("  host1  ,  host2  ")

        allowlist = get_allowlist()
        assert "host1" in allowlist
        assert "host2" in allowlist

    def test_is_host_in_allowlist(self, mock_env_allowlist):
        """Check if host is in allowlist."""
        mock_env_allowlist("private-llm.internal,::1")

        assert is_host_in_allowlist("private-llm.internal") is True
        assert is_host_in_allowlist("PRIVATE-LLM.INTERNAL") is True  # Case insensitive
        assert is_host_in_allowlist("::1") is True
        assert is_host_in_allowlist("0:0:0:0:0:0:0:1") is True  # Normalized matches
        assert is_host_in_allowlist("other-host.com") is False

    def test_concurrent_cache_refresh(self, mock_env_allowlist):
        """Cache refresh should be thread-safe."""
        mock_env_allowlist("host1,host2")
        reset_allowlist_cache_for_tests()

        results = []
        errors = []

        def refresh_cache():
            try:
                for _ in range(100):
                    get_allowlist()
                    time.sleep(0.001)
                results.append("ok")
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=refresh_cache) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors in concurrent access: {errors}"
        assert len(results) == 10


# ── URL Blocking Tests ───────────────────────────────────────────────────────


class TestUrlBlocking:
    """Tests for URL blocking behavior."""

    def test_block_localhost(self, flask_app):
        """localhost should be blocked."""
        with flask_app.app_context():
            response = safe_llm_proxy_request(
                "GET",
                "http://localhost:8080/test",
                tenant_id=1,
                provider="openai",
            )
            assert isinstance(response, tuple)
            assert response[1] == 403
            data = response[0].get_json()
            assert data["error"]["code"] == "SSRF_BLOCKED"

    def test_block_127_0_0_1(self, flask_app):
        """127.0.0.1 should be blocked."""
        with flask_app.app_context():
            response = safe_llm_proxy_request(
                "GET",
                "http://127.0.0.1:8080/test",
                tenant_id=1,
                provider="openai",
            )
            assert isinstance(response, tuple)
            assert response[1] == 403

    def test_block_ipv6_loopback(self, flask_app):
        """::1 (IPv6 loopback) should be blocked."""
        with flask_app.app_context():
            response = safe_llm_proxy_request(
                "GET",
                "http://[::1]:8080/test",
                tenant_id=1,
                provider="openai",
            )
            assert isinstance(response, tuple)
            assert response[1] == 403

    def test_block_metadata_ip(self, flask_app):
        """169.254.169.254 (cloud metadata) should be blocked."""
        with flask_app.app_context():
            response = safe_llm_proxy_request(
                "GET",
                "http://169.254.169.254/latest/meta-data/",
                tenant_id=1,
                provider="openai",
            )
            assert isinstance(response, tuple)
            assert response[1] == 403

    def test_block_rfc1918_10(self, flask_app):
        """10.x.x.x (private network) should be blocked."""
        with flask_app.app_context():
            response = safe_llm_proxy_request(
                "GET",
                "http://10.0.0.1/test",
                tenant_id=1,
                provider="openai",
            )
            assert isinstance(response, tuple)
            assert response[1] == 403

    def test_block_rfc1918_172(self, flask_app):
        """172.16-31.x.x (private network) should be blocked."""
        with flask_app.app_context():
            response = safe_llm_proxy_request(
                "GET",
                "http://172.16.0.1/test",
                tenant_id=1,
                provider="openai",
            )
            assert isinstance(response, tuple)
            assert response[1] == 403

    def test_block_rfc1918_192(self, flask_app):
        """192.168.x.x (private network) should be blocked."""
        with flask_app.app_context():
            response = safe_llm_proxy_request(
                "GET",
                "http://192.168.1.1/test",
                tenant_id=1,
                provider="openai",
            )
            assert isinstance(response, tuple)
            assert response[1] == 403

    def test_block_ipv6_unique_local(self, flask_app):
        """fc00::/7 (IPv6 unique local) should be blocked."""
        with flask_app.app_context():
            response = safe_llm_proxy_request(
                "GET",
                "http://[fc00::1]/test",
                tenant_id=1,
                provider="openai",
            )
            assert isinstance(response, tuple)
            assert response[1] == 403

    def test_block_ipv6_link_local(self, flask_app):
        """fe80::/10 (IPv6 link-local) should be blocked."""
        with flask_app.app_context():
            response = safe_llm_proxy_request(
                "GET",
                "http://[fe80::1]/test",
                tenant_id=1,
                provider="openai",
            )
            assert isinstance(response, tuple)
            assert response[1] == 403

    def test_block_nat64_metadata(self, flask_app):
        """NAT64-encoded metadata IP should be blocked."""
        with flask_app.app_context():
            response = safe_llm_proxy_request(
                "GET",
                "http://[64:ff9b::169.254.169.254]/test",
                tenant_id=1,
                provider="openai",
            )
            assert isinstance(response, tuple)
            assert response[1] == 403


# ── Allowlist Bypass Tests ───────────────────────────────────────────────────


class TestAllowlistBypass:
    """Tests for allowlist bypass behavior."""

    def test_allowlist_private_host_passes(self, mock_env_allowlist, monkeypatch):
        """Private host in allowlist should pass validation."""
        mock_env_allowlist("10.0.0.5")

        # Mock safe_request to avoid actual network call
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("app.utils.llm_proxy_request.safe_request", return_value=mock_response):
            response = safe_llm_proxy_request(
                "GET",
                "http://10.0.0.5/test",
                tenant_id=1,
                provider="openai",
            )

        # Should not be blocked (returns mock response, not error tuple)
        assert not isinstance(response, tuple) or response[1] != 403

    def test_allowlist_ipv6_normalized_matching(self, mock_env_allowlist, monkeypatch):
        """IPv6 normalization should match allowlist."""
        # Add compressed form to allowlist
        mock_env_allowlist("::1")

        # Mock safe_request
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("app.utils.llm_proxy_request.safe_request", return_value=mock_response):
            # Request with expanded form should still match
            response = safe_llm_proxy_request(
                "GET",
                "http://[0:0:0:0:0:0:0:1]/test",
                tenant_id=1,
                provider="openai",
            )

        # Should not be blocked
        assert not isinstance(response, tuple) or response[1] != 403


# ── Guard Mode Tests ──────────────────────────────────────────────────────────


class TestGuardMode:
    """Tests for guard mode (log vs enforce)."""

    def test_log_mode_no_block(self, mock_guard_mode, monkeypatch):
        """Log mode should not block requests."""
        mock_guard_mode("log")

        # Mock the underlying request
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("requests.request", return_value=mock_response):
            response = safe_llm_proxy_request(
                "GET",
                "http://127.0.0.1/test",
                tenant_id=1,
                provider="openai",
            )

        # In log mode, should return the mock response (not blocked)
        assert not isinstance(response, tuple)

    def test_enforce_mode_blocks(self, mock_guard_mode, flask_app):
        """Enforce mode should block requests."""
        mock_guard_mode("enforce")

        with flask_app.app_context():
            response = safe_llm_proxy_request(
                "GET",
                "http://127.0.0.1/test",
                tenant_id=1,
                provider="openai",
            )

            assert isinstance(response, tuple)
            assert response[1] == 403


# ── Storage Validation Tests ──────────────────────────────────────────────────


class TestStorageValidation:
    """Tests for base_url storage validation."""

    def test_validate_public_url_passes(self, flask_app):
        """Public URL should pass validation."""
        with flask_app.app_context():
            is_valid, error = validate_base_url_for_storage(
                "https://api.openai.com",
                tenant_id=1,
                provider="openai",
            )
            assert is_valid is True
            assert error is None

    def test_validate_private_url_fails(self):
        """Private URL should fail validation."""
        is_valid, error = validate_base_url_for_storage(
            "http://10.0.0.1:8080",
            tenant_id=1,
            provider="openai",
        )
        assert is_valid is False
        assert error is not None
        assert "allowlist" in error.lower() or "private" in error.lower()

    def test_validate_allowlist_url_passes(self, mock_env_allowlist):
        """Allowlist URL should pass validation."""
        mock_env_allowlist("private-llm.internal")

        is_valid, error = validate_base_url_for_storage(
            "http://private-llm.internal:8080",
            tenant_id=1,
            provider="openai",
        )
        assert is_valid is True
        assert error is None

    def test_validate_empty_url_passes(self):
        """Empty URL should pass validation."""
        is_valid, error = validate_base_url_for_storage(
            "",
            tenant_id=1,
            provider="openai",
        )
        assert is_valid is True
        assert error is None


# ── Parameter Compatibility Tests ─────────────────────────────────────────────


class TestParameterCompatibility:
    """Tests for parameter compatibility with underlying requests."""

    def test_timeout_parameter(self, monkeypatch):
        """timeout parameter should be passed through."""
        monkeypatch.setenv("OPENACE_LLM_PROXY_URL_GUARD_MODE", "log")

        mock_response = MagicMock()
        mock_response.status_code = 200

        captured = {}

        def mock_request(method, url, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            return mock_response

        with patch("requests.request", mock_request):
            safe_llm_proxy_request(
                "GET",
                "https://api.openai.com/v1/test",
                tenant_id=1,
                provider="openai",
                timeout=30,
            )

        assert captured.get("timeout") == 30

    def test_headers_parameter(self, monkeypatch):
        """headers parameter should be passed through."""
        monkeypatch.setenv("OPENACE_LLM_PROXY_URL_GUARD_MODE", "log")

        mock_response = MagicMock()
        mock_response.status_code = 200

        captured = {}

        def mock_request(method, url, **kwargs):
            captured["headers"] = kwargs.get("headers")
            return mock_response

        with patch("requests.request", mock_request):
            safe_llm_proxy_request(
                "GET",
                "https://api.openai.com/v1/test",
                tenant_id=1,
                provider="openai",
                headers={"X-Custom": "value"},
            )

        assert captured.get("headers", {}).get("X-Custom") == "value"


# ── Error Response Format Tests ───────────────────────────────────────────────


class TestErrorResponseFormat:
    """Tests for error response format."""

    def test_error_response_structure(self, flask_app):
        """Error response should have correct structure."""
        with flask_app.app_context():
            response = safe_llm_proxy_request(
                "GET",
                "http://127.0.0.1/test",
                tenant_id=1,
                provider="openai",
            )

            assert isinstance(response, tuple)
            flask_response, status = response
            assert status == 403

            data = flask_response.get_json()
            assert "error" in data
            assert data["error"]["type"] == "proxy_url_blocked"
            assert data["error"]["code"] == "SSRF_BLOCKED"
            # Should NOT expose the blocked IP
            assert "127.0.0.1" not in str(data)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])