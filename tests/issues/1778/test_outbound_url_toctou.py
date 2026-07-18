"""TDD red tests for the SSRF TOCTOU / DNS-rebinding group (PR #1778).

These tests prove the two High-severity findings on the outbound URL guard:

1. TOCTOU / DNS-rebinding: ``validate_public_http_url`` resolves the hostname
   once and checks the IPs, but callers then hand the *original* URL back to
   ``requests``/``urllib3``, which performs its OWN independent
   ``socket.getaddrinfo`` at connect time. Between the two resolutions an
   attacker-controlled authoritative DNS server can flip the A record
   (public -> loopback / metadata). The guard is advisory, not enforced.

2. ``_is_public_address`` predicate only checks ``address.is_global`` which
   returns ``True`` for NAT64-encoded metadata
   (``64:ff9b::169.254.169.254``), NAT64 of loopback (``64:ff9b::7f00:1``),
   CGNAT outside Python's narrow private slice (``100.128.0.1``), and
   multicast. A host resolving to any of these passes the guard outright,
   even without rebinding.

Both assertions FAIL against current main and PASS after the IP-pinning +
denylist fix.
"""

import ipaddress
import socket

import pytest
import requests

from app.utils.outbound_url_guard import _is_public_address, safe_request, validate_public_http_url


class _RebindingResolver:
    """getaddrinfo that flips: first call(s) from the guard return a PUBLIC ip,
    later call(s) from the HTTP client return ``169.254.169.254`` (metadata)."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, host, *args, **kwargs):
        self.calls += 1
        # First resolution (the guard): public IP -> passes _is_public_address.
        # Subsequent resolutions (the connect-time pin re-check): metadata.
        ip = "93.184.216.34" if self.calls == 1 else "169.254.169.254"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]


def test_safe_request_pins_verified_ip_and_never_rebinds(monkeypatch):
    """``safe_request`` must pin the validated IP into the dial.

    Before the fix: the guard passed (public) but ``requests`` was given the
    raw hostname and re-resolved via the system resolver at connect time, so an
    attacker who controlled DNS could flip the record to ``169.254.169.254``.

    After the fix: ``safe_request`` resolves ONCE (via its ``resolver`` kwarg),
    pins the verified public IP literal into the outgoing URL, and sets the
    original hostname as the ``Host`` header so TLS SNI / virtual hosting keeps
    working. Because the URL host is now an IP literal, ``urllib3`` does not
    re-resolve — the rebinding window is closed. This test exercises exactly
    that: the resolver is set up to rebind to metadata on its second call, but
    only the first (public) call is ever made.
    """
    resolver = _RebindingResolver()

    captured = {}

    # Patch the pinned adapter's ``send`` so we capture the prepared request
    # without performing a real network dial.
    from app.utils.outbound_url_guard import _PinnedIPAdapter

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

    # Only ONE resolution happened — the rebinding second call was never made.
    assert resolver.calls == 1, f"safe_request must resolve once; made {resolver.calls} calls"

    outgoing = captured.get("url", "")
    # The verified public IP is pinned into the URL...
    assert (
        "93.184.216.34" in outgoing
    ), f"safe_request did not pin the verified public IP. url={outgoing!r}"
    # ...and the metadata IP the attacker wanted to rebind to never appears.
    assert (
        "169.254.169.254" not in outgoing
    ), f"TOCTOU: request would reach metadata IP. url={outgoing!r}"
    # The original hostname is preserved as Host for SNI / virtual hosting.
    host_header = captured["headers"].get("Host")
    assert (
        host_header == "sso.evil.example"
    ), f"Host header not preserved when pinning IP: {host_header!r}"


def test_safe_request_fails_closed_when_resolver_returns_metadata():
    """If the single resolution returns a non-public IP, the request is refused.

    With the pin in place there is no second chance: a metadata answer means
    delivery is skipped, not dialed. (``pytest.importorskip`` guard for the
    blocklist import below is unnecessary — this module already imports the
    guard.)
    """
    from app.utils.outbound_url_guard import OutboundUrlBlockedError

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
    """The predicate must use an explicit denylist, not ``is_global`` alone.

    All of these return ``is_global == True`` today and slip past the guard.
    """
    ip = ipaddress.ip_address(addr)
    assert not _is_public_address(ip), f"{addr} leaked through is_global-only predicate"
