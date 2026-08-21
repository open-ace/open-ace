"""Unit tests for outbound URL SSRF protection."""

import os
import socket
from unittest.mock import MagicMock, patch

import pytest

from app.utils.outbound_url_guard import (
    OutboundUrlBlockedError,
    _get_proxies,
    assert_public_http_url,
    validate_public_http_url,
)


def _resolver(*addresses):
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


def test_allows_public_http_url_resolved_by_dns():
    result = validate_public_http_url(
        "https://login.example.com/oauth/token",
        resolver=_resolver("93.184.216.34"),
    )

    assert result.allowed


def test_allows_public_ip_address_without_dns_lookup():
    result = validate_public_http_url("https://8.8.8.8/oauth/token")

    assert result.allowed


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/admin",
        "http://127.0.0.1:8080/admin",
        "http://[::1]/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "ftp://example.com/file",
        "https://user:pass@example.com/token",
    ],
)
def test_rejects_non_public_and_unsafe_urls(url):
    result = validate_public_http_url(url)

    assert not result.allowed
    assert result.error


def test_rejects_dns_name_that_resolves_to_private_address():
    result = validate_public_http_url(
        "https://sso.example.com/token",
        resolver=_resolver("10.1.2.3"),
    )

    assert not result.allowed
    assert "10.1.2.3" in result.error


def test_rejects_dns_name_when_any_address_is_not_public():
    result = validate_public_http_url(
        "https://sso.example.com/token",
        resolver=_resolver("93.184.216.34", "192.168.1.10"),
    )

    assert not result.allowed
    assert "192.168.1.10" in result.error


def test_assert_public_http_url_raises_clear_exception():
    with pytest.raises(OutboundUrlBlockedError, match="non-public"):
        assert_public_http_url(
            "https://sso.example.com/token",
            resolver=_resolver("172.16.1.10"),
        )


# ── Port whitelist tests ─────────────────────────────────────────────────────


def test_rejects_non_standard_ports():
    """Test that non-whitelisted ports are rejected."""
    result = validate_public_http_url(
        "https://example.com:6379/admin",
        resolver=_resolver("93.184.216.34"),
    )

    assert not result.allowed
    assert "Port 6379 not in allowed ports" in result.error


def test_allows_ports_in_whitelist():
    """Test that whitelisted ports are allowed."""
    # Test default whitelist ports
    for port in [80, 443, 8080, 8443, 9000, 9443]:
        result = validate_public_http_url(
            f"https://example.com:{port}/oauth/token",
            resolver=_resolver("93.184.216.34"),
        )
        assert result.allowed, f"Port {port} should be allowed"


# ── Encoding normalization tests ─────────────────────────────────────────────


def test_rejects_percent_encoded_localhost():
    """Test that percent-encoded blocked hostnames are detected.

    Note: URL normalization decodes percent-encoding before checking BLOCKED_HOSTNAMES.
    The test verifies that decoding happens correctly.
    """
    # Test percent-encoded 'localhost' as the hostname itself
    # %6c%6f%63%61%6c%68%6f%73%74 = 'localhost' (percent-encoded)
    # After decoding, it should match 'localhost' in BLOCKED_HOSTNAMES
    result = validate_public_http_url(
        "http://%6c%6f%63%61%6c%68%6f%73%74/admin",
    )

    # Should be blocked because after decoding, it's 'localhost'
    assert not result.allowed
    assert result.error
    assert "blocked" in result.error.lower() or "localhost" in result.error.lower()


def test_rejects_trailing_dot_hostname():
    """Test that trailing dot in hostname is normalized and checked."""
    result = validate_public_http_url(
        "http://localhost./admin",
    )

    assert not result.allowed
    assert "localhost" in result.error.lower()


def test_rejects_extended_blocked_hostnames():
    """Test that extended BLOCKED_HOSTNAMES entries are rejected."""
    extended_hostnames = [
        "ip6-localhost",
        "ip6-loopback",
        "broadcasthost",
        "metadata.azure",
        "metadata.vultr",
        "kubernetes",
        "kubernetes.default",
        "openshift",
        "docker",
    ]

    for hostname in extended_hostnames:
        result = validate_public_http_url(f"http://{hostname}/admin")
        assert not result.allowed, f"Hostname '{hostname}' should be blocked"
        assert "blocked" in result.error.lower() or hostname in result.error.lower()


def test_rejects_null_in_username():
    """Test that NULL character in username is rejected."""
    result = validate_public_http_url(
        "http://user%00name:pass@example.com/admin",
        resolver=_resolver("93.184.216.34"),
    )

    assert not result.allowed
    assert "NULL" in result.error or "username" in result.error.lower()


def test_rejects_at_in_username():
    """Test that @ symbol in username is rejected."""
    result = validate_public_http_url(
        "http://user%40name:pass@example.com/admin",
        resolver=_resolver("93.184.216.34"),
    )

    assert not result.allowed
    assert "@" in result.error or "username" in result.error.lower()


def test_rejects_double_encoded_hostname():
    """Test that double percent-encoding is detected and rejected."""
    # %2525 = '%' after first decode, which should trigger error
    result = validate_public_http_url(
        "http://exam%2525ple.com/admin",
    )

    assert not result.allowed
    assert "percent" in result.error.lower()


# ── Proxy configuration tests (Issue #2237) ─────────────────────────────────────


def test_get_proxies_returns_none_when_no_env_vars():
    """Test that _get_proxies returns None when no proxy env vars are set."""
    with patch.dict(os.environ, {}, clear=True):
        # Remove all proxy-related env vars
        for key in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]:
            os.environ.pop(key, None)
        result = _get_proxies()
        assert result is None


def test_get_proxies_returns_proxies_from_uppercase_env():
    """Test that _get_proxies reads HTTP_PROXY and HTTPS_PROXY env vars."""
    with patch.dict(
        os.environ,
        {
            "HTTP_PROXY": "http://proxy.example.com:8080",
            "HTTPS_PROXY": "http://proxy.example.com:8080",
        },
        clear=True,
    ):
        result = _get_proxies()
        assert result == {
            "http": "http://proxy.example.com:8080",
            "https": "http://proxy.example.com:8080",
        }


def test_get_proxies_returns_proxies_from_lowercase_env():
    """Test that _get_proxies reads http_proxy and https_proxy env vars."""
    with patch.dict(
        os.environ,
        {
            "http_proxy": "http://proxy.example.com:8080",
            "https_proxy": "http://proxy.example.com:8080",
        },
        clear=True,
    ):
        result = _get_proxies()
        assert result == {
            "http": "http://proxy.example.com:8080",
            "https": "http://proxy.example.com:8080",
        }


def test_get_proxies_prioritizes_uppercase_env():
    """Test that uppercase env vars take precedence over lowercase."""
    with patch.dict(
        os.environ,
        {
            "HTTP_PROXY": "http://upper.example.com:8080",
            "http_proxy": "http://lower.example.com:8080",
        },
        clear=True,
    ):
        result = _get_proxies()
        assert result == {"http": "http://upper.example.com:8080"}


def test_get_proxies_returns_partial_proxies():
    """Test that _get_proxies works when only one proxy is configured."""
    with patch.dict(os.environ, {"HTTPS_PROXY": "http://proxy.example.com:8080"}, clear=True):
        result = _get_proxies()
        assert result == {"https": "http://proxy.example.com:8080"}


def test_safe_request_uses_env_proxies(monkeypatch):
    """Test that safe_request uses proxy from environment variables."""
    import requests

    from app.utils.outbound_url_guard import safe_request

    # Mock the session.request to capture proxies argument
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_session.request.return_value = mock_response

    # Set proxy env vars
    monkeypatch.setenv("HTTP_PROXY", "http://test-proxy.example.com:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://test-proxy.example.com:8080")

    # Mock resolver to return a public IP
    def mock_resolver(host, port, type=socket.SOCK_STREAM):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]

    with patch("app.utils.outbound_url_guard.requests.Session") as mock_session_class:
        mock_session_class.return_value = mock_session
        # Call safe_request
        safe_request("GET", "https://example.com/test", resolver=mock_resolver)

        # Verify that proxies from env were used
        call_args = mock_session.request.call_args
        assert "proxies" in call_args[1]
        assert call_args[1]["proxies"] == {
            "http": "http://test-proxy.example.com:8080",
            "https": "http://test-proxy.example.com:8080",
        }


def test_safe_request_disables_proxy_when_no_env_vars(monkeypatch):
    """Test that safe_request disables proxy when no env vars are set."""
    import requests

    from app.utils.outbound_url_guard import safe_request

    # Mock the session.request to capture proxies argument
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_session.request.return_value = mock_response

    # Clear all proxy env vars
    for key in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]:
        monkeypatch.delenv(key, raising=False)

    # Mock resolver to return a public IP
    def mock_resolver(host, port, type=socket.SOCK_STREAM):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]

    with patch("app.utils.outbound_url_guard.requests.Session") as mock_session_class:
        mock_session_class.return_value = mock_session
        # Call safe_request
        safe_request("GET", "https://example.com/test", resolver=mock_resolver)

        # Verify that proxies were disabled
        call_args = mock_session.request.call_args
        assert "proxies" in call_args[1]
        assert call_args[1]["proxies"] == {"http": None, "https": None}
