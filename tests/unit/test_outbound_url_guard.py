"""Unit tests for outbound URL SSRF protection."""

import socket

import pytest

from app.utils.outbound_url_guard import (
    OutboundUrlBlockedError,
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
