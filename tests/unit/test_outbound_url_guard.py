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
