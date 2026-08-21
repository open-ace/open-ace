"""Unit tests for outbound URL SSRF protection."""

import ipaddress
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


# ── Issue #2236: TLS SNI and Certificate Verification Tests ───────────────────────


@pytest.mark.security
def test_safe_request_retains_hostname_for_tls_sni():
    """Test that safe_request retains original URL hostname for TLS SNI (Issue #2236).

    This is critical for SSL certificate verification, as certificates are issued
    to domain names, not IP literals.
    """
    from app.utils.outbound_url_guard import safe_request

    # Mock the session.request to capture the URL
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_session.request.return_value = mock_response

    # Mock resolver to return a public IP
    def mock_resolver(host, port, type=socket.SOCK_STREAM):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]

    with patch("app.utils.outbound_url_guard.requests.Session") as mock_session_class:
        mock_session_class.return_value = mock_session

        # Call safe_request with a domain URL
        original_url = "https://api.deepseek.com/v1/chat/completions"
        safe_request("POST", original_url, resolver=mock_resolver)

        # Verify that the original URL was passed to session.request (not an IP literal)
        call_args = mock_session.request.call_args
        assert call_args[0][1] == original_url  # The URL argument
        assert "api.deepseek.com" in call_args[0][1]
        assert "8.8.8.8" not in call_args[0][1]


@pytest.mark.security
def test_ip_literal_url_skips_dns_resolution():
    """Test that IP literal URLs skip DNS resolution in _PinnedIPAdapter (Issue #2236).

    This is a performance optimization and security measure for IP literal URLs.
    """
    from app.utils.outbound_url_guard import _PinnedIPAdapter

    # Create an adapter
    adapter = _PinnedIPAdapter(allowed_ips=[], resolver=_resolver("8.8.8.8"))

    # Test with a public IP literal URL
    adapter._check_resolved_ip("https://8.8.8.8/test")  # Should not raise

    # Test with a private IP literal URL (should raise)
    with pytest.raises(OutboundUrlBlockedError, match="non-public IP"):
        adapter._check_resolved_ip("https://10.0.0.1/test")


@pytest.mark.security
def test_dns_rebinding_detection_at_connect_time():
    """Test that DNS rebinding is detected at connect time (Issue #2236).

    The adapter should detect if the hostname resolves to a different IP
    than the pre-verified IP.
    """
    from app.utils.outbound_url_guard import _PinnedIPAdapter

    # Create an adapter with pre-verified IPs
    adapter = _PinnedIPAdapter(
        allowed_ips=[ipaddress.ip_address("93.184.216.34")],
        resolver=_resolver("8.8.8.8"),  # Different IP
    )

    # The adapter should allow different public IPs (CDN scenario) but log a warning
    # This test verifies it doesn't raise an error for different public IPs
    adapter._check_resolved_ip("https://example.com/test")  # Should not raise


@pytest.mark.security
def test_dns_rebinding_to_private_ip_blocked():
    """Test that DNS rebinding to private IP is blocked (Issue #2236)."""
    from app.utils.outbound_url_guard import _PinnedIPAdapter

    # Create an adapter
    adapter = _PinnedIPAdapter(
        allowed_ips=[ipaddress.ip_address("93.184.216.34")],
        resolver=_resolver("10.0.0.1"),  # Private IP
    )

    # The adapter should raise an error for private IP resolution
    with pytest.raises(OutboundUrlBlockedError, match="DNS rebinding detected"):
        adapter._check_resolved_ip("https://example.com/test")


# ── Issue #2236: DeepSeek API Integration Tests ───────────────────────────────────


@pytest.mark.security
def test_deepseek_api_url_validation():
    """Test that DeepSeek API URL is validated correctly (Issue #2236)."""
    # Mock DNS to return a public IP for api.deepseek.com
    result = validate_public_http_url(
        "https://api.deepseek.com/v1/chat/completions",
        resolver=_resolver("104.18.25.175"),  # Example public IP
    )

    assert result.allowed
    assert result.resolved_addresses
    assert len(result.resolved_addresses) > 0
    # Verify the resolved IP is public
    for addr in result.resolved_addresses:
        assert _is_public_address_test(addr)


def _is_public_address_test(address):
    """Helper function to test if an address is public."""
    from app.utils.outbound_url_guard import _is_public_address

    return _is_public_address(address)


@pytest.mark.security
def test_deepseek_api_tls_sni_with_hostname():
    """Test that DeepSeek API requests use correct TLS SNI with hostname (Issue #2236).

    This is critical for certificate verification.
    """
    from app.utils.outbound_url_guard import safe_request

    import ipaddress

    # Mock the session.request to verify URL
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_session.request.return_value = mock_response

    # Mock resolver to return DeepSeek's IP
    def mock_resolver(host, port, type=socket.SOCK_STREAM):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("104.18.25.175", port))]

    with patch("app.utils.outbound_url_guard.requests.Session") as mock_session_class:
        mock_session_class.return_value = mock_session

        url = "https://api.deepseek.com/v1/chat/completions"
        safe_request("POST", url, resolver=mock_resolver)

        # Verify that the URL contains the hostname, not the IP
        call_args = mock_session.request.call_args
        requested_url = call_args[0][1]
        assert "api.deepseek.com" in requested_url
        assert "104.18.25.175" not in requested_url


# ── Issue #2236: CDN and Edge Case Tests ─────────────────────────────────────────


@pytest.mark.security
def test_cdn_ip_rotation_allows_different_public_ips():
    """Test that CDN IP rotation allows different public IPs (Issue #2236).

    CDN-fronted endpoints may return different public IPs between DNS resolutions.
    The adapter should allow this (with a warning) since HTTPS certificate
    verification mitigates rebinding to a different public host.
    """
    from app.utils.outbound_url_guard import _PinnedIPAdapter

    import ipaddress

    # Create an adapter with pre-verified IP
    adapter = _PinnedIPAdapter(
        allowed_ips=[ipaddress.ip_address("104.18.25.175")],
        resolver=_resolver("104.18.26.175"),  # Different public IP
    )

    # Should not raise an error (CDN scenario)
    adapter._check_resolved_ip("https://api.deepseek.com/v1/chat")


@pytest.mark.security
def test_dns_resolution_failure_handling():
    """Test that DNS resolution failures are handled gracefully (Issue #2236)."""
    from app.utils.outbound_url_guard import _PinnedIPAdapter

    # Create a resolver that raises an error
    def failing_resolver(host, port, type=socket.SOCK_STREAM):
        raise OSError("DNS resolution failed")

    adapter = _PinnedIPAdapter(allowed_ips=[], resolver=failing_resolver)

    # Should raise OutboundUrlBlockedError with clear message
    with pytest.raises(OutboundUrlBlockedError, match="DNS resolution failed"):
        adapter._check_resolved_ip("https://example.com/test")


@pytest.mark.security
def test_dns_resolution_timeout_handling():
    """Test that DNS resolution timeouts are handled gracefully (Issue #2236)."""
    from app.utils.outbound_url_guard import _PinnedIPAdapter

    # Create a resolver that raises a timeout error
    def timeout_resolver(host, port, type=socket.SOCK_STREAM):
        raise OSError("DNS resolution timeout")

    adapter = _PinnedIPAdapter(allowed_ips=[], resolver=timeout_resolver)

    # Should raise OutboundUrlBlockedError with clear message
    with pytest.raises(OutboundUrlBlockedError, match="DNS resolution failed"):
        adapter._check_resolved_ip("https://example.com/test")


@pytest.mark.security
def test_adapter_unmount_from_shared_session():
    """Test that adapter is unmounted from shared sessions (Issue #2236).

    This prevents adapter leakage into subsequent requests on shared sessions.
    """
    from app.utils.outbound_url_guard import safe_request

    import requests

    # Create a shared session with a custom adapter
    shared_session = requests.Session()
    custom_adapter = MagicMock()
    shared_session.mount("https://", custom_adapter)

    # Mock resolver
    def mock_resolver(host, port, type=socket.SOCK_STREAM):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]

    # Mock session.request
    with patch.object(shared_session, "request") as mock_request:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_request.return_value = mock_response

        # Call safe_request with the shared session
        safe_request(
            "GET", "https://example.com/test", session=shared_session, resolver=mock_resolver
        )

        # Verify that the custom adapter is restored
        assert shared_session.adapters.get("https://") == custom_adapter


@pytest.mark.security
def test_safe_request_blocks_private_network_ssr():
    """Test that safe_request blocks requests to private networks (Issue #2236)."""
    from app.utils.outbound_url_guard import safe_request

    # Mock resolver to return a private IP
    def private_resolver(host, port, type=socket.SOCK_STREAM):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", port))]

    # Should raise OutboundUrlBlockedError
    with pytest.raises(OutboundUrlBlockedError, match="non-public"):
        safe_request("GET", "https://example.com/test", resolver=private_resolver)


@pytest.mark.security
def test_safe_request_blocks_loopback():
    """Test that safe_request blocks requests to loopback addresses (Issue #2236)."""
    from app.utils.outbound_url_guard import safe_request

    # Mock resolver to return a loopback IP
    def loopback_resolver(host, port, type=socket.SOCK_STREAM):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    # Should raise OutboundUrlBlockedError
    with pytest.raises(OutboundUrlBlockedError, match="non-public"):
        safe_request("GET", "https://example.com/test", resolver=loopback_resolver)


@pytest.mark.security
def test_safe_request_blocks_metadata_endpoint():
    """Test that safe_request blocks requests to metadata endpoints (Issue #2236)."""
    from app.utils.outbound_url_guard import safe_request

    # Mock resolver to return metadata IP
    def metadata_resolver(host, port, type=socket.SOCK_STREAM):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port))]

    # Should raise OutboundUrlBlockedError
    with pytest.raises(OutboundUrlBlockedError, match="non-public"):
        safe_request("GET", "https://metadata.google.internal/test", resolver=metadata_resolver)


# ── Issue #2236: Performance Tests ───────────────────────────────────────────────


def test_ip_literal_url_validation():
    """Test that IP literal URLs are validated directly without DNS resolution (Issue #2236).

    This is a performance optimization. IP literal URLs should be validated
    directly without calling the DNS resolver.
    """
    from app.utils.outbound_url_guard import _PinnedIPAdapter

    # Create a resolver that should never be called
    def tracking_resolver(host, port, type=socket.SOCK_STREAM):
        raise AssertionError("Resolver should not be called for IP literal URLs")

    # Create an adapter with the tracking resolver
    adapter = _PinnedIPAdapter(allowed_ips=[], resolver=tracking_resolver)

    # Test with a public IP literal URL
    # Should succeed without calling the resolver
    adapter._check_resolved_ip("https://8.8.8.8/test")  # Should not raise

    # Test with a private IP literal URL (should raise, but still not call resolver)
    with pytest.raises(OutboundUrlBlockedError, match="non-public IP"):
        adapter._check_resolved_ip("https://10.0.0.1/test")
