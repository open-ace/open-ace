"""
LLM Proxy URL Validator

Provides SSRF protection for LLM proxy custom base_url configurations.
Implements allowlist mechanism, IP pinning, DNS rebinding protection,
and audit logging with sanitization.

Issue: #1894
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import socket
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from app.utils.outbound_url_guard import (
    IPAddress,
    _is_public_address,
    _parse_ip_address,
    _resolve_addresses,
    validate_public_http_url,
)

logger = logging.getLogger(__name__)

# DNS cache for allowlist entries (TTL 300s)
_DNS_CACHE: dict[str, tuple[tuple[IPAddress, ...], float]] = {}
_DNS_CACHE_TTL = 300.0


@dataclass(frozen=True)
class LlmProxyValidationResult:
    """Result for LLM proxy URL validation."""

    allowed: bool
    error: str | None = None
    resolved_ips: tuple[IPAddress, ...] = ()
    is_allowlist_match: bool = False


@dataclass(frozen=True)
class AllowlistValidationResult:
    """Result for allowlist entry validation."""

    valid: bool
    error: str | None = None
    expected_ips: tuple[IPAddress, ...] = ()
    expected_network: str | None = None


def get_allowed_hosts() -> dict[int, list[str]]:
    """Parse allowlist from environment variables.

    Returns:
        Dict mapping tenant_id to list of allowed hosts.
        tenant_id 0 represents global allowlist.
    """
    result: dict[int, list[str]] = {}

    # Global allowlist
    global_hosts = os.environ.get("OPENACE_LLM_PROXY_ALLOWED_HOSTS", "").strip()
    if global_hosts:
        result[0] = [h.strip() for h in global_hosts.split(",") if h.strip()]

    # Tenant-specific allowlists
    tenant_json = os.environ.get("OPENACE_LLM_PROXY_TENANT_ALLOWLISTS", "").strip()
    if tenant_json:
        try:
            tenant_data = json.loads(tenant_json)
            if isinstance(tenant_data, dict):
                for tenant_id_str, hosts in tenant_data.items():
                    try:
                        tenant_id = int(tenant_id_str)
                        if isinstance(hosts, list):
                            result[tenant_id] = [
                                h.strip() for h in hosts if isinstance(h, str) and h.strip()
                            ]
                    except ValueError:
                        logger.warning(
                            "Invalid tenant_id in OPENACE_LLM_PROXY_TENANT_ALLOWLISTS: %s",
                            tenant_id_str,
                        )
        except json.JSONDecodeError:
            logger.warning("Invalid JSON in OPENACE_LLM_PROXY_TENANT_ALLOWLISTS")

    return result


def get_cached_dns_result(host: str) -> tuple[IPAddress, ...] | None:
    """Get cached DNS result if still valid.

    Args:
        host: Hostname to check.

    Returns:
        Cached IP addresses or None if not cached/expired.
    """
    if host not in _DNS_CACHE:
        return None
    ips, timestamp = _DNS_CACHE[host]
    if time.time() - timestamp > _DNS_CACHE_TTL:
        del _DNS_CACHE[host]
        return None
    return ips


def set_cached_dns_result(host: str, ips: tuple[IPAddress, ...]) -> None:
    """Cache DNS resolution result.

    Args:
        host: Hostname that was resolved.
        ips: Resolved IP addresses.
    """
    _DNS_CACHE[host] = (ips, time.time())


def validate_allowlist_entry(
    host: str,
    *,
    resolver: Any = socket.getaddrinfo,
) -> AllowlistValidationResult:
    """Validate an allowlist entry for security.

    For IP entries: must be in expected private ranges.
    For hostname entries: DNS resolves must be in same /16 network.

    Args:
        host: The allowlist entry (IP or hostname).
        resolver: DNS resolver function.

    Returns:
        Validation result with expected IPs and network.
    """
    host = host.strip().lower()
    if not host:
        return AllowlistValidationResult(False, "Empty host")

    # Try parsing as IP first
    try:
        ip = _parse_ip_address(host)
        # Allowlist entries should be for private/internal use
        # Public IPs don't need allowlist (they pass SSRF check)
        if _is_public_address(ip):
            return AllowlistValidationResult(
                False,
                f"Public IP {ip} doesn't need allowlist",
            )
        return AllowlistValidationResult(True, expected_ips=(ip,), expected_network=str(ip))
    except ValueError:
        pass  # Not an IP, treat as hostname

    # Resolve hostname
    try:
        addresses = _resolve_addresses(host, 443, resolver)
    except OSError as exc:
        return AllowlistValidationResult(False, f"DNS resolution failed: {exc}")

    if not addresses:
        return AllowlistValidationResult(False, "No IP addresses resolved")

    # Check all IPs are in same /16 network segment
    networks: set[str] = set()
    for addr in addresses:
        if _is_public_address(addr):
            # Hostname resolved to public IP - doesn't need allowlist
            continue
        # Get /16 network for private IPs
        try:
            if isinstance(addr, ipaddress.IPv4Address):
                net = ipaddress.ip_network(f"{addr}/16", strict=False)
                networks.add(str(net.network_address))
            else:
                # IPv6: use /64
                net = ipaddress.ip_network(f"{addr}/64", strict=False)
                networks.add(str(net.network_address))
        except Exception:
            networks.add(str(addr))

    if not networks:
        return AllowlistValidationResult(
            False,
            "All resolved IPs are public (no need for allowlist)",
        )

    if len(networks) > 1:
        logger.warning(
            "Allowlist hostname %s resolves to multiple network segments: %s",
            host,
            list(networks),
        )
        # Take first network as expected
        expected_net = sorted(networks)[0]
    else:
        expected_net = list(networks)[0]

    return AllowlistValidationResult(
        True,
        expected_ips=tuple(addresses),
        expected_network=expected_net,
    )


def is_allowed_host(
    host: str,
    tenant_id: int,
    *,
    resolver: Any = socket.getaddrinfo,
) -> tuple[bool, AllowlistValidationResult | None]:
    """Check if host is in allowlist for tenant.

    Checks both tenant-specific and global allowlists.

    Args:
        host: Host to check.
        tenant_id: Tenant ID.
        resolver: DNS resolver function.

    Returns:
        Tuple of (is_allowed, validation_result).
    """
    allowed_hosts = get_allowed_hosts()

    # Normalize host
    try:
        normalized = host.strip().lower().rstrip(".")
    except Exception:
        return False, None

    # Check tenant-specific allowlist first
    tenant_hosts = allowed_hosts.get(tenant_id, [])
    if normalized in [h.lower().rstrip(".") for h in tenant_hosts]:
        result = validate_allowlist_entry(normalized, resolver=resolver)
        return result.valid, result

    # Check global allowlist
    global_hosts = allowed_hosts.get(0, [])
    if normalized in [h.lower().rstrip(".") for h in global_hosts]:
        result = validate_allowlist_entry(normalized, resolver=resolver)
        return result.valid, result

    return False, None


def hash_host_for_audit(host: str) -> str:
    """Hash host for audit logging (sanitization).

    Args:
        host: Original host value.

    Returns:
        SHA256 hash prefix (16 chars) for audit logging.
    """
    if not host:
        return ""
    return hashlib.sha256(host.encode()).hexdigest()[:16]


def sanitize_error_message(error: str) -> str:
    """Sanitize error message for client response.

    Removes sensitive URL details, keeping only generic error info.

    Args:
        error: Original error message.

    Returns:
        Sanitized error message safe for client.
    """
    # Remove specific IP addresses
    error_lower = error.lower()
    if "private address" in error_lower:
        return "Blocked outbound URL: host resolves to private address"
    if "localhost" in error_lower or "127.0.0.1" in error_lower:
        return "Blocked outbound URL: localhost not allowed"
    if "metadata" in error_lower or "169.254" in error_lower:
        return "Blocked outbound URL: metadata endpoint not allowed"
    if "non-public" in error_lower:
        return "Blocked outbound URL: non-public address blocked"
    if "port" in error_lower:
        return "Blocked outbound URL: port not allowed"
    if "credentials" in error_lower:
        return "Blocked outbound URL: credentials not allowed in URL"
    if "scheme" in error_lower or "http" in error_lower:
        return "Blocked outbound URL: invalid scheme"

    return "Blocked outbound URL: security policy violation"


def validate_llm_proxy_url(
    url: str,
    tenant_id: int,
    provider: str,
    *,
    resolver: Any = socket.getaddrinfo,
) -> LlmProxyValidationResult:
    """Validate LLM proxy URL with SSRF protection.

    Checks allowlist first, then standard SSRF validation.

    Args:
        url: URL to validate.
        tenant_id: Tenant ID for allowlist lookup.
        provider: LLM provider name.
        resolver: DNS resolver function.

    Returns:
        Validation result with resolved IPs.
    """
    if not url:
        return LlmProxyValidationResult(True)

    # Parse URL to get host
    try:
        parsed = urlparse(url)
    except Exception:
        return LlmProxyValidationResult(False, "Invalid URL format")

    host = parsed.hostname
    if not host:
        return LlmProxyValidationResult(False, "URL missing hostname")

    try:
        normalized_host = host.strip().lower().rstrip(".")
    except Exception:
        return LlmProxyValidationResult(False, "Invalid hostname")

    # Check if SSRF check is disabled (emergency mode)
    if os.environ.get("OPENACE_LLM_PROXY_DISABLE_SSRF_CHECK", "").lower() == "true":
        logger.warning("SSRF check disabled via environment variable")
        return LlmProxyValidationResult(True)

    # Check allowlist
    allowed, allowlist_result = is_allowed_host(normalized_host, tenant_id, resolver=resolver)
    if allowed and allowlist_result:
        # Check cached DNS result for consistency
        cached = get_cached_dns_result(normalized_host)
        if cached is None:
            # Resolve and cache
            try:
                addresses = _resolve_addresses(normalized_host, parsed.port or 443, resolver)
                set_cached_dns_result(normalized_host, tuple(addresses))
            except OSError:
                pass  # Allow on DNS failure, will be caught by safe_request

        return LlmProxyValidationResult(
            True,
            is_allowlist_match=True,
            resolved_ips=allowlist_result.expected_ips,
        )

    # Standard SSRF validation
    result = validate_public_http_url(url, resolver=resolver)
    if not result.allowed:
        return LlmProxyValidationResult(False, result.error)

    return LlmProxyValidationResult(True, resolved_ips=result.resolved_addresses)


def resolve_and_store_ips(
    url: str,
    *,
    resolver: Any = socket.getaddrinfo,
) -> tuple[str, tuple[IPAddress, ...]]:
    """Resolve URL IPs for storage (IP pinning).

    Args:
        url: URL to resolve.
        resolver: DNS resolver function.

    Returns:
        Tuple of (comma-separated IPs string, tuple of IPAddress).
    """
    if not url:
        return "", ()

    try:
        parsed = urlparse(url)
        host = parsed.hostname
        if not host:
            return "", ()

        # Try parsing as IP literal
        try:
            ip = _parse_ip_address(host)
            if _is_public_address(ip):
                return str(ip), (ip,)
            return "", ()  # Private IP, don't store
        except ValueError:
            pass

        # Resolve hostname
        try:
            addresses = _resolve_addresses(host, parsed.port or 443, resolver)
        except OSError:
            return "", ()

        # Filter to public IPs only
        public_ips = [addr for addr in addresses if _is_public_address(addr)]
        if not public_ips:
            return "", ()

        return ",".join(str(ip) for ip in public_ips), tuple(public_ips)

    except Exception:
        return "", ()


def validate_ip_against_stored(
    url: str,
    stored_ips: str,
    *,
    resolver: Any = socket.getaddrinfo,
) -> tuple[bool, str | None]:
    """Validate current DNS resolution against stored IPs.

    DNS rebinding protection: ensures IP hasn't changed since config.

    Args:
        url: URL to validate.
        stored_ips: Comma-separated IPs from config time.
        resolver: DNS resolver function.

    Returns:
        Tuple of (is_valid, error_message).
    """
    if not stored_ips:
        return True, None  # No stored IPs means private URL, skip check

    stored_set = {ip.strip() for ip in stored_ips.split(",") if ip.strip()}

    try:
        parsed = urlparse(url)
        host = parsed.hostname
        if not host:
            return False, "URL missing hostname"

        # Resolve current IPs
        try:
            current_addresses = _resolve_addresses(host, parsed.port or 443, resolver)
        except OSError as exc:
            return False, f"DNS resolution failed: {exc}"

        current_set = {str(addr) for addr in current_addresses}

        # Check if any current IP matches stored
        if not current_set.intersection(stored_set):
            return False, f"DNS rebinding detected: IPs changed from {stored_ips}"

        return True, None

    except Exception as exc:
        return False, str(exc)


def audit_blocked_url(
    tenant_id: int,
    provider: str,
    url: str,
    reason: str,
    user_id: int | None = None,
    username: str | None = None,
) -> None:
    """Log blocked URL to audit log with sanitization.

    Args:
        tenant_id: Tenant ID.
        provider: LLM provider.
        url: Blocked URL (will be sanitized).
        reason: Block reason.
        user_id: User ID if available.
        username: Username if available.
    """
    try:
        from app.modules.governance.audit_logger import AuditAction, AuditLogger

        parsed = urlparse(url) if url else None
        host = parsed.hostname if parsed else None

        audit_logger = AuditLogger()
        audit_logger.log_action(
            action=AuditAction.LLM_PROXY_URL_BLOCKED,
            user_id=user_id,
            username=username,
            resource_type="llm_proxy",
            severity="warning",
            details={
                "blocked_host_hash": hash_host_for_audit(host or ""),
                "blocked_scheme": parsed.scheme if parsed else None,
                "blocked_port": parsed.port if parsed else None,
                "blocked_reason": reason,
                "provider": provider,
            },
        )
    except Exception as exc:
        logger.error("Failed to log SSRF audit event: %s", exc)


# Prometheus metrics (lazy initialization)
_METRICS: dict[str, Any] = {}


def _get_metrics():
    """Lazily initialize Prometheus metrics."""
    if _METRICS:
        return _METRICS

    try:
        from prometheus_client import Counter, Histogram

        _METRICS["ssrf_blocked"] = Counter(
            "llm_proxy_ssrf_blocked_total",
            "Total SSRF blocked requests",
            ["tenant_id", "provider", "reason"],
        )
        _METRICS["allowlist_hits"] = Counter(
            "llm_proxy_allowlist_hits_total",
            "Total allowlist hits",
            ["tenant_id", "allowlist_type"],
        )
        _METRICS["ip_mismatch"] = Counter(
            "llm_proxy_ip_mismatch_total",
            "Total IP mismatch detections",
            ["tenant_id", "provider"],
        )
        _METRICS["dns_duration"] = Histogram(
            "llm_proxy_dns_lookup_duration_seconds",
            "DNS lookup duration",
        )
    except ImportError:
        pass  # Prometheus not available

    return _METRICS


def record_ssrf_blocked(tenant_id: int, provider: str, reason: str) -> None:
    """Record SSRF blocked metric."""
    metrics = _get_metrics()
    if "ssrf_blocked" in metrics:
        metrics["ssrf_blocked"].labels(
            tenant_id=str(tenant_id),
            provider=provider,
            reason=reason,
        ).inc()


def record_allowlist_hit(tenant_id: int, allowlist_type: str) -> None:
    """Record allowlist hit metric."""
    metrics = _get_metrics()
    if "allowlist_hits" in metrics:
        metrics["allowlist_hits"].labels(
            tenant_id=str(tenant_id),
            allowlist_type=allowlist_type,
        ).inc()


def record_ip_mismatch(tenant_id: int, provider: str) -> None:
    """Record IP mismatch metric."""
    metrics = _get_metrics()
    if "ip_mismatch" in metrics:
        metrics["ip_mismatch"].labels(
            tenant_id=str(tenant_id),
            provider=provider,
        ).inc()
