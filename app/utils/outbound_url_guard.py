"""
Security checks for administrator-configured outbound HTTP(S) URLs.

The guard is intentionally conservative: only globally routable HTTP(S)
destinations are allowed by default. This prevents SSRF against loopback,
private networks, link-local metadata endpoints, and other non-public ranges.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Callable, Iterable, Union
from urllib.parse import urlparse

Resolver = Callable[..., Iterable[tuple]]
IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]

BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "metadata",
    "metadata.google.internal",
}


@dataclass(frozen=True)
class OutboundUrlValidationResult:
    """Result for an outbound URL security validation."""

    allowed: bool
    error: str | None = None


class OutboundUrlBlockedError(ValueError):
    """Raised when an outbound URL fails SSRF protection."""


def validate_public_http_url(
    url: str,
    *,
    resolver: Resolver = socket.getaddrinfo,
) -> OutboundUrlValidationResult:
    """Validate that a URL points to a public HTTP(S) destination."""
    if not url:
        return OutboundUrlValidationResult(False, "URL is empty")

    try:
        parsed = urlparse(url)
    except Exception:
        return OutboundUrlValidationResult(False, "URL is invalid")

    if parsed.scheme not in {"http", "https"}:
        return OutboundUrlValidationResult(False, "Only http and https URLs are allowed")

    if not parsed.hostname:
        return OutboundUrlValidationResult(False, "URL host is required")

    if parsed.username or parsed.password:
        return OutboundUrlValidationResult(False, "URL credentials are not allowed")

    host = parsed.hostname.rstrip(".").lower()
    if host in BLOCKED_HOSTNAMES:
        return OutboundUrlValidationResult(False, f"Blocked non-public host: {host}")

    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return OutboundUrlValidationResult(False, "URL host is invalid")

    try:
        addresses = _resolve_addresses(ascii_host, parsed.port, resolver)
    except OSError as exc:
        return OutboundUrlValidationResult(False, f"Host could not be resolved: {exc}")
    except ValueError as exc:
        return OutboundUrlValidationResult(False, str(exc))

    if not addresses:
        return OutboundUrlValidationResult(False, "Host did not resolve to an IP address")

    for address in addresses:
        if not _is_public_address(address):
            return OutboundUrlValidationResult(
                False,
                f"Blocked non-public address: {address}",
            )

    return OutboundUrlValidationResult(True)


def assert_public_http_url(url: str, *, resolver: Resolver = socket.getaddrinfo) -> None:
    """Raise OutboundUrlBlockedError if a URL is not safe for outbound requests."""
    result = validate_public_http_url(url, resolver=resolver)
    if not result.allowed:
        raise OutboundUrlBlockedError(result.error or "URL is blocked by outbound policy")


def _resolve_addresses(host: str, port: int | None, resolver: Resolver) -> set[IPAddress]:
    try:
        return {_parse_ip_address(host)}
    except ValueError:
        pass

    resolved: set[IPAddress] = set()
    for info in resolver(host, port or 443, type=socket.SOCK_STREAM):
        sockaddr = info[4]
        if not sockaddr:
            continue
        resolved.add(_parse_ip_address(str(sockaddr[0])))
    return resolved


def _parse_ip_address(value: str) -> IPAddress:
    return ipaddress.ip_address(value.split("%", 1)[0])


def _is_public_address(address: IPAddress) -> bool:
    return bool(address.is_global)
