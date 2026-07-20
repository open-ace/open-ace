"""
Security checks for administrator-configured outbound HTTP(S) URLs.

The guard is intentionally conservative: only globally routable HTTP(S)
destinations are allowed by default. This prevents SSRF against loopback,
private networks, link-local metadata endpoints, and other non-public ranges.

The validation is enforced at *connect* time via :func:`safe_request`, which
pins the verified IP into the actual HTTP request so the system resolver cannot
rebind the destination to a private address between validation and the dial
(closing the DNS-rebinding TOCTOU window).
"""

from __future__ import annotations

import ipaddress
import logging
import os
import socket
import urllib.parse
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse, urlunparse

import requests
from requests.adapters import HTTPAdapter

logger = logging.getLogger(__name__)

Resolver = Callable[..., Iterable[tuple]]
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

BLOCKED_HOSTNAMES = {
    # IPv4 loopback
    "localhost",
    "localhost.localdomain",
    # IPv6 loopback
    "ip6-localhost",
    "ip6-loopback",
    # Broadcast
    "broadcasthost",
    # AWS/GCP/Azure metadata
    "metadata",
    "metadata.google.internal",
    "metadata.azure",
    # Vultr metadata
    "metadata.vultr",
    # Kubernetes/OpenShift/Docker internal DNS
    "kubernetes",
    "kubernetes.default",
    "openshift",
    "docker",
    # IP 字面量形式（辅助防御）
    "169.254.169.254",
}

# Default port whitelist for outbound HTTP(S) requests
# Covers standard HTTP/HTTPS, common alternative ports (Keycloak, GitLab), Authentik
_DEFAULT_ALLOWED_PORTS = {80, 443, 8080, 8443, 9000, 9443}


def _get_allowed_ports() -> set[int]:
    """Parse OPENACE_OUTBOUND_ALLOWED_PORTS or return defaults.

    Security consideration: empty value = defaults, non-empty = explicit list.
    Admin must explicitly add ports beyond defaults.
    """
    raw = os.environ.get("OPENACE_OUTBOUND_ALLOWED_PORTS", "").strip()
    if not raw:
        return _DEFAULT_ALLOWED_PORTS.copy()

    ports: set[int] = set()
    for p in raw.split(","):
        try:
            port = int(p.strip())
            if 1 <= port <= 65535:
                ports.add(port)
            else:
                logger.warning(
                    f"Invalid port '{p}' in OPENACE_OUTBOUND_ALLOWED_PORTS (out of range)"
                )
        except ValueError:
            logger.warning(f"Non-integer port '{p}' in OPENACE_OUTBOUND_ALLOWED_PORTS")
    return ports or {80, 443}  # Fallback to minimal safe set


# Cache for allowed ports
_ALLOWED_PORTS_CACHE: set[int] | None = None


def get_allowed_ports() -> set[int]:
    """Get allowed ports for outbound requests (cached)."""
    global _ALLOWED_PORTS_CACHE
    if _ALLOWED_PORTS_CACHE is None:
        _ALLOWED_PORTS_CACHE = _get_allowed_ports()
    return _ALLOWED_PORTS_CACHE


def _normalize_hostname(host: str) -> str:
    """Normalize hostname with security checks.

    Decodes percent-encoding once and checks for dangerous characters.

    Raises:
        ValueError: If hostname contains dangerous characters after decoding.
    """
    # 1. Percent-decode once
    try:
        decoded = urllib.parse.unquote(host, errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("Hostname contains invalid percent-encoding") from exc

    # 2. Security checks
    if "%" in decoded:
        raise ValueError("Hostname contains unexpected percent-encoding (possible double-encoding)")
    if "\x00" in decoded:
        raise ValueError("Hostname contains NULL character")

    # 3. Normalization
    normalized = decoded.rstrip(".").lower()

    return normalized


def _check_username_password_for_dangerous_chars(
    username: str | None, password: str | None
) -> str | None:
    """Check username/password for dangerous characters after decoding.

    Returns:
        Error message if dangerous characters found, None otherwise.
    """
    for name, value in [("username", username), ("password", password)]:
        if not value:
            continue
        # Decode percent-encoding
        decoded = urllib.parse.unquote(value, errors="strict")
        # Check for dangerous characters
        if "\x00" in decoded:
            return f"URL {name} contains NULL character"
        if "@" in decoded:
            return f"URL {name} contains '@' character (possible parser confusion)"
    return None


# Networks that must be rejected even though ``ipaddress.is_global`` returns
# ``True`` for them. These cover NAT64 encodings of private/metadata IPs
# (RFC 6052 well-known prefix), the full CGNAT range (RFC 6598, only part of
# which Python treats as private), benchmarking/documentation ranges
# (RFC 2544/5735/6815), the ``0.0.0.0/8`` "this network" block, and other
# ranges that ``is_global`` alone fails to catch.
_NON_PUBLIC_GLOBAL_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT (full range)
    ipaddress.ip_network("192.0.0.0/24"),  # IETF protocol assignments
    ipaddress.ip_network("192.0.2.0/24"),  # TEST-NET-1 documentation
    ipaddress.ip_network("198.18.0.0/15"),  # benchmarking
    ipaddress.ip_network("198.51.100.0/24"),  # TEST-NET-2 documentation
    ipaddress.ip_network("203.0.113.0/24"),  # TEST-NET-3 documentation
    ipaddress.ip_network("64:ff9b::/96"),  # NAT64 well-known prefix
    ipaddress.ip_network("2001:db8::/32"),  # documentation
)


@dataclass(frozen=True)
class OutboundUrlValidationResult:
    """Result for an outbound URL security validation."""

    allowed: bool
    error: str | None = None
    # The public IP addresses that were verified for this URL. Populated only
    # when ``allowed`` is True. Callers SHOULD pin one of these IPs into the
    # actual request (see :func:`safe_request`) so the system resolver cannot
    # rebind the destination between validation and the dial.
    resolved_addresses: tuple[IPAddress, ...] = ()


class OutboundUrlBlockedError(ValueError):
    """Raised when an outbound URL fails SSRF protection."""


def validate_public_http_url(
    url: str,
    *,
    resolver: Resolver = socket.getaddrinfo,
) -> OutboundUrlValidationResult:
    """Validate that a URL points to a public HTTP(S) destination.

    Returns the verified public IP addresses so callers can pin them into the
    actual request. Using these pinned IPs (via :func:`safe_request`) is what
    closes the DNS-rebinding TOCTOU window — validation alone is advisory.
    """
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

    # Enhanced username/password check with decoding
    if parsed.username or parsed.password:
        # Check for dangerous characters after decoding
        error = _check_username_password_for_dangerous_chars(parsed.username, parsed.password)
        if error:
            return OutboundUrlValidationResult(False, error)
        return OutboundUrlValidationResult(False, "URL credentials are not allowed")

    # Normalize hostname with security checks
    try:
        host = _normalize_hostname(parsed.hostname)
    except ValueError as exc:
        return OutboundUrlValidationResult(False, str(exc))

    if host in BLOCKED_HOSTNAMES:
        return OutboundUrlValidationResult(False, f"Blocked non-public host: {host}")

    # Port validation
    allowed_ports = get_allowed_ports()
    if parsed.port is not None:
        # Explicit port must be in whitelist
        if parsed.port not in allowed_ports:
            return OutboundUrlValidationResult(
                False, f"Port {parsed.port} not in allowed ports: {sorted(allowed_ports)}"
            )
    # If port is None, it's inferred from scheme (80/443) and is allowed

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

    public_addresses = [addr for addr in addresses if _is_public_address(addr)]
    if len(public_addresses) != len(addresses):
        non_public = next(addr for addr in addresses if not _is_public_address(addr))
        return OutboundUrlValidationResult(
            False,
            f"Blocked non-public address: {non_public}",
        )

    return OutboundUrlValidationResult(True, resolved_addresses=tuple(public_addresses))


def assert_public_http_url(url: str, *, resolver: Resolver = socket.getaddrinfo) -> None:
    """Raise OutboundUrlBlockedError if a URL is not safe for outbound requests."""
    result = validate_public_http_url(url, resolver=resolver)
    if not result.allowed:
        raise OutboundUrlBlockedError(result.error or "URL is blocked by outbound policy")


def resolve_public_addresses(
    url: str, *, resolver: Resolver = socket.getaddrinfo
) -> tuple[str, tuple[IPAddress, ...], int | None, str]:
    """Resolve and validate ``url`` once, returning the parts needed to pin a request.

    Returns ``(original_host, public_ips, port, path_and_query)``. Raises
    :class:`OutboundUrlBlockedError` if the URL is unsafe. Callers use the
    returned IPs to build a pinned request URL (see :func:`safe_request`).
    """
    result = validate_public_http_url(url, resolver=resolver)
    if not result.allowed:
        raise OutboundUrlBlockedError(result.error or "URL is blocked by outbound policy")
    if not result.resolved_addresses:
        raise OutboundUrlBlockedError("URL validation did not yield a pinned address")
    parsed = urlparse(url)
    original_host = (parsed.hostname or "").rstrip(".").lower()
    path_and_query = urlunparse(("", "", parsed.path or "/", "", parsed.query, ""))
    return original_host, result.resolved_addresses, parsed.port, path_and_query


def safe_request(
    method: str,
    url: str,
    *,
    session: requests.Session | None = None,
    resolver: Resolver = socket.getaddrinfo,
    **kwargs: Any,
) -> requests.Response:
    """Issue an HTTP request with the verified IP pinned at connect time.

    This collapses validate+connect into a single resolution: the hostname is
    resolved exactly once via ``resolver``, every returned IP is checked with
    :func:`_is_public_address`, and the request is sent to the verified IP
    literal while preserving the original hostname as the ``Host`` header (so
    TLS SNI / virtual-host routing still works). Because the request URL
    contains an IP literal, ``urllib3`` does not re-resolve via the system
    resolver, which closes the DNS-rebinding TOCTOU window.

    Fails closed (raises :class:`OutboundUrlBlockedError`) if no public IP can
    be pinned.
    """
    original_host, public_ips, port, path_and_query = resolve_public_addresses(
        url, resolver=resolver
    )

    scheme = urlparse(url).scheme
    pinned_ip = public_ips[0]
    pinned_host = f"[{pinned_ip}]" if ":" in str(pinned_ip) else str(pinned_ip)
    port_suffix = f":{port}" if port else ""
    pinned_url = f"{scheme}://{pinned_host}{port_suffix}{path_and_query}"

    headers = dict(kwargs.pop("headers", {}) or {})
    headers.setdefault("Host", original_host)
    # ``urllib3`` derives the TLS SNI/Host header from the URL host. Because we
    # pinned an IP literal we must set the original host explicitly so HTTPS
    # virtual hosting and SNI keep working.
    headers["Host"] = original_host

    own_session = False
    if session is None:
        session = requests.Session()
        own_session = True
    try:
        adapter = _PinnedIPAdapter(allowed_ips=public_ips)
        session.mount(f"{scheme}://", adapter)
        return session.request(method, pinned_url, headers=headers, **kwargs)
    finally:
        if own_session:
            session.close()


class _PinnedIPAdapter(HTTPAdapter):
    """HTTPAdapter that re-validates the connect-time IP against an allowlist.

    Defense in depth: even though ``safe_request`` builds a URL whose host is
    the pinned IP literal (so ``urllib3`` should not re-resolve), this adapter
    intercepts the connection pool and refuses any IP that is not on the
    verified allowlist or that fails the public-address predicate. This guards
    against proxy configuration or future urllib3 changes re-introducing a
    resolution step.
    """

    def __init__(self, *args: Any, allowed_ips: Iterable[IPAddress] = (), **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._allowed_ips = {str(ip) for ip in allowed_ips}

    def get_connection_with_tls_context(self, request, verify, proxies=None, cert=None):
        self._check_pinned_url(request.url)
        return super().get_connection_with_tls_context(  # type: ignore[call-arg]
            request, verify, proxies=proxies, cert=cert
        )

    def get_connection(self, url, proxies=None):
        self._check_pinned_url(url)
        return super().get_connection(url, proxies=proxies)

    def _check_pinned_url(self, url: str) -> None:
        parsed = urlparse(url)
        host = (parsed.hostname or "").strip("[]")
        if not host:
            raise OutboundUrlBlockedError("Pinned request URL has no host")
        try:
            ip = _parse_ip_address(host)
        except ValueError as exc:
            raise OutboundUrlBlockedError(
                f"Pinned request URL host is not an IP literal: {host!r}"
            ) from exc
        if str(ip) not in self._allowed_ips:
            raise OutboundUrlBlockedError(f"Pinned request would reach unverified IP {ip}")
        if not _is_public_address(ip):
            raise OutboundUrlBlockedError(f"Pinned request would reach non-public IP {ip}")


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


def _is_public_address(address: ipaddress._BaseAddress) -> bool:
    """Return whether ``address`` is safe for outbound HTTP.

    Uses an explicit denylist rather than relying on ``is_global`` alone,
    because ``is_global`` returns ``True`` for NAT64-encoded metadata
    (``64:ff9b::169.254.169.254``), NAT64 of loopback (``64:ff9b::7f00:1``),
    CGNAT outside Python's narrow private slice (``100.128.0.1``), and
    multicast.
    """
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        return False
    if any(address in network for network in _NON_PUBLIC_GLOBAL_NETWORKS):
        return False
    return bool(address.is_global)


# Public alias so callers (e.g. the alert webhook path) share the same hardened
# denylist predicate as the outbound guard without depending on a private name.
is_public_address = _is_public_address
