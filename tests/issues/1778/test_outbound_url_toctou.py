"""Tests for the SSRF TOCTOU / DNS-rebinding protection (PR #1778).

These tests verify the outbound URL guard's defense-in-depth approach:

1. ``safe_request`` pre-resolves the hostname and verifies all IPs are public.
   The request URL retains the original hostname so TLS SNI and certificate
   verification work correctly (HTTPS certificates are issued to domain names,
   not IP literals).

2. ``_PinnedIPAdapter`` re-validates at connect time by resolving the hostname
   again and blocking any IP that is not public. This catches DNS rebinding
   attacks where the authoritative DNS server flips the A record between the
   pre-validation and the actual connection.

3. ``_is_public_address`` uses an explicit denylist (not just ``is_global``)
   to reject NAT64-encoded metadata, CGNAT, multicast, and other non-public
   ranges that ``is_global`` returns ``True`` for.
"""

import ipaddress
import socket

import pytest
import requests

from app.utils.outbound_url_guard import (
    OutboundUrlBlockedError,
    _is_public_address,
    _PinnedIPAdapter,
    safe_request,
    validate_public_http_url,
)


class _RebindingResolver:
    """getaddrinfo that flips: first call returns a PUBLIC ip,
    later calls return ``169.254.169.254`` (metadata)."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, host, *args, **kwargs):
        self.calls += 1
        ip = "93.184.216.34" if self.calls == 1 else "169.254.169.254"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]


# ── safe_request: URL retains hostname for TLS SNI ──────────────────────


def test_safe_request_retains_hostname_for_tls_sni(monkeypatch):
    """safe_request must retain the original hostname in the URL.

    The previous approach replaced the URL hostname with an IP literal, which
    broke TLS SNI (urllib3 derives SNI from the URL host, not the Host header).
    This caused SSL certificate verification failures for any HTTPS endpoint
    with a domain-issued certificate.
    """
    resolver = _RebindingResolver()
    captured = {}

    def fake_send(self, request, **kwargs):  # type: ignore[no-untyped-def]
        captured["url"] = request.url
        captured["headers"] = dict(request.headers)
        resp = requests.Response()
        resp.status_code = 200
        resp.url = request.url
        return resp

    monkeypatch.setattr(_PinnedIPAdapter, "send", fake_send)

    safe_request(
        "GET",
        "https://sso.evil.example/token",
        resolver=resolver,
        timeout=5,
    )

    outgoing = captured.get("url", "")
    # URL retains the original hostname (not IP literal) for TLS SNI
    assert "sso.evil.example" in outgoing, f"URL should retain hostname for SNI. url={outgoing!r}"
    assert "93.184.216.34" not in outgoing, f"URL should not contain IP literal. url={outgoing!r}"


# ── _PinnedIPAdapter._check_resolved_ip: connect-time re-validation ────


def test_adapter_blocks_rebinding_to_metadata():
    """Adapter must block when connect-time resolution returns a non-public IP."""
    adapter = _PinnedIPAdapter(
        allowed_ips={ipaddress.ip_address("93.184.216.34")},
        resolver=lambda host, *a, **kw: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))
        ],
    )
    with pytest.raises(OutboundUrlBlockedError, match="non-public IP"):
        adapter._check_resolved_ip("https://sso.evil.example/token")


def test_adapter_allows_matching_public_ip():
    """Adapter must allow when connect-time resolution matches pre-verified IPs."""
    adapter = _PinnedIPAdapter(
        allowed_ips={ipaddress.ip_address("93.184.216.34")},
        resolver=lambda host, *a, **kw: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ],
    )
    # Should not raise
    adapter._check_resolved_ip("https://sso.evil.example/token")


def test_adapter_allows_different_public_ip_with_warning(caplog):
    """Adapter should allow (with warning) different public IPs for CDN endpoints."""
    adapter = _PinnedIPAdapter(
        allowed_ips={ipaddress.ip_address("93.184.216.34")},
        resolver=lambda host, *a, **kw: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("104.16.123.96", 443))
        ],
    )
    with caplog.at_level("WARNING"):
        adapter._check_resolved_ip("https://sso.evil.example/token")
    assert "not in pre-verified set" in caplog.text


def test_adapter_validates_ip_literal_url():
    """Adapter must validate IP-literal URLs directly."""
    adapter = _PinnedIPAdapter(allowed_ips={ipaddress.ip_address("93.184.216.34")})
    # Public IP literal — should pass
    adapter._check_resolved_ip("https://93.184.216.34/token")
    # Non-public IP literal — should block
    with pytest.raises(OutboundUrlBlockedError, match="non-public IP"):
        adapter._check_resolved_ip("https://169.254.169.254/token")


def test_adapter_blocks_on_dns_failure():
    """Adapter must block when DNS resolution fails at connect time."""

    def failing_resolver(host, *args, **kwargs):
        raise OSError("DNS server down")

    adapter = _PinnedIPAdapter(
        allowed_ips={ipaddress.ip_address("93.184.216.34")},
        resolver=failing_resolver,
    )
    with pytest.raises(OutboundUrlBlockedError, match="DNS resolution failed"):
        adapter._check_resolved_ip("https://sso.evil.example/token")


def test_adapter_blocks_empty_resolution():
    """Adapter must block when DNS returns no IPs."""
    adapter = _PinnedIPAdapter(
        allowed_ips={ipaddress.ip_address("93.184.216.34")},
        resolver=lambda host, *a, **kw: [],
    )
    with pytest.raises(OutboundUrlBlockedError, match="did not resolve"):
        adapter._check_resolved_ip("https://sso.evil.example/token")


# ── safe_request: fails closed on non-public resolution ─────────────────


def test_safe_request_fails_closed_when_resolver_returns_metadata():
    """If the resolution returns a non-public IP, the request is refused."""

    def metadata_resolver(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))]

    sess = requests.Session()

    with pytest.raises(OutboundUrlBlockedError):
        safe_request(
            "GET",
            "https://sso.evil.example/token",
            session=sess,
            resolver=metadata_resolver,
            timeout=5,
        )


# ── safe_request: adapter unmount on shared sessions ───────────────────


def test_safe_request_restores_previous_adapter_on_shared_session(monkeypatch):
    """safe_request must restore the previous adapter, not replace it with a vanilla one."""
    from requests.adapters import HTTPAdapter

    def stable_resolver(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    def fake_send(self, request, **kwargs):
        resp = requests.Response()
        resp.status_code = 200
        return resp

    monkeypatch.setattr(_PinnedIPAdapter, "send", fake_send)

    sess = requests.Session()
    # Simulate a caller-provided custom adapter (e.g. with retry config)
    custom_adapter = HTTPAdapter(max_retries=3)
    sess.mount("https://", custom_adapter)

    safe_request(
        "GET",
        "https://sso.evil.example/token",
        session=sess,
        resolver=stable_resolver,
        timeout=5,
    )

    # The pinned adapter must have been removed; the original custom adapter
    # must be restored (not replaced with a vanilla HTTPAdapter).
    restored = sess.adapters.get("https://")
    assert restored is custom_adapter, f"Previous adapter should be restored, got {restored!r}"


def test_safe_request_unmounts_pinned_adapter_after_request(monkeypatch):
    """After safe_request, the session must not retain the _PinnedIPAdapter."""
    from requests.adapters import HTTPAdapter

    def stable_resolver(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    def fake_send(self, request, **kwargs):
        resp = requests.Response()
        resp.status_code = 200
        return resp

    monkeypatch.setattr(_PinnedIPAdapter, "send", fake_send)

    sess = requests.Session()

    safe_request(
        "GET",
        "https://sso.evil.example/token",
        session=sess,
        resolver=stable_resolver,
        timeout=5,
    )

    # The adapter must not be a _PinnedIPAdapter (it should be restored to the
    # default or a vanilla HTTPAdapter).
    current = sess.adapters.get("https://")
    assert not isinstance(
        current, _PinnedIPAdapter
    ), f"_PinnedIPAdapter leaked into shared session: {current!r}"


# ── _is_public_address denylist ─────────────────────────────────────────


@pytest.mark.parametrize(
    "addr",
    [
        "64:ff9b::169.254.169.254",  # NAT64-encoded AWS/GCP metadata
        "64:ff9b::7f00:1",  # NAT64-encoded loopback 127.0.0.1
        "224.0.0.1",  # multicast
        "233.252.1.1",  # multicast
        "0.0.0.1",  # 0.0.0.0/8 current network
        "198.18.0.1",  # benchmarking 198.18.0.0/15
        "192.0.2.1",  # TEST-NET-1 documentation
        "203.0.113.1",  # TEST-NET-3 documentation
        "2001:db8::1",  # documentation
    ],
)
def test_is_public_address_rejects_metadata_and_other_non_public(addr):
    """The predicate must use an explicit denylist, not ``is_global`` alone."""
    ip = ipaddress.ip_address(addr)
    assert not _is_public_address(ip), f"{addr} leaked through is_global-only predicate"
