"""
LLM Proxy URL Security Guard - Unified request wrapper with SSRF protection.

This module provides a single entry point for all LLM proxy outbound requests,
integrating allowlist checks, outbound URL validation, and IP pinning to prevent
SSRF attacks via administrator-configured base_url values.

Security Model:
1. Allowlist check first - hosts in OPENACE_LLM_PROXY_ALLOWED_HOSTS bypass public check
2. Public URL validation via outbound_url_guard.validate_public_http_url()
3. IP pinning via safe_request() to prevent DNS rebinding
4. Audit logging for blocked requests

Related: GitHub Issue #1894
"""

from __future__ import annotations

import ipaddress
import logging
import os
import threading
import time
from typing import Any
from urllib.parse import urlparse

import requests
from flask import jsonify

from app.utils.outbound_url_guard import (
    OutboundUrlBlockedError,
    safe_request,
    validate_public_http_url,
)

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────


def _get_guard_mode() -> str:
    """Get the current guard mode from environment variable.

    Returns:
        'log' for logging-only mode, 'enforce' for blocking mode (default).
    """
    mode = os.environ.get("OPENACE_LLM_PROXY_URL_GUARD_MODE", "enforce").strip().lower()
    return mode if mode in ("log", "enforce") else "enforce"


def _is_guard_enabled() -> bool:
    """Check if the guard is enabled (for emergency rollback)."""
    return os.environ.get("OPENACE_LLM_PROXY_URL_GUARD_ENABLED", "true").strip().lower() not in (
        "false",
        "0",
        "no",
    )


# ── Allowlist Management ──────────────────────────────────────────────────────

# Cache for allowlist (process-level, thread-safe)
_cached_allowlist: set[str] | None = None
_cache_expiry: float = 0
_cache_lock = threading.Lock()
CACHE_TTL_SECONDS = 60


def _normalize_host_for_allowlist(host: str) -> str:
    """Normalize a hostname for allowlist matching.

    - IPv6 addresses: compressed to canonical form
    - IPv4 addresses: normalized
    - Domain names: lowercase, trailing dot removed
    - Scope IDs (e.g., %eth0) are rejected

    Args:
        host: The hostname or IP address to normalize.

    Returns:
        Normalized hostname string.

    Raises:
        ValueError: If host contains a scope ID (not supported).
    """
    if not host:
        return ""

    host = host.strip()

    # Reject scope IDs (e.g., fe80::1%eth0)
    if "%" in host:
        raise ValueError(f"IPv6 scope ID not supported in allowlist: {host}")

    # Try to parse as IP address
    try:
        ip = ipaddress.ip_address(host)
        return str(ip)  # Returns canonical form (compressed for IPv6)
    except ValueError:
        pass

    # Domain name: lowercase, remove trailing dot
    return host.lower().rstrip(".")


def _parse_allowlist_from_env() -> set[str]:
    """Parse allowlist from environment variable.

    Format: OPENACE_LLM_PROXY_ALLOWED_HOSTS=host1,host2,host3

    Handles:
    - Empty entries (consecutive commas)
    - Whitespace around entries
    - Invalid entries (logged as warnings, skipped)

    Returns:
        Set of normalized hostnames/IPs.
    """
    raw = os.environ.get("OPENACE_LLM_PROXY_ALLOWED_HOSTS", "").strip()
    if not raw:
        return set()

    allowlist: set[str] = set()
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            normalized = _normalize_host_for_allowlist(entry)
            if normalized:
                allowlist.add(normalized)
        except ValueError as e:
            logger.warning("Invalid allowlist entry '%s': %s", entry, e)
        except Exception as e:
            logger.warning("Failed to normalize allowlist entry '%s': %s", entry, e)

    return allowlist


def get_allowlist() -> set[str]:
    """Get the current allowlist (cached, thread-safe).

    Uses a TTL-based cache with lock-protected refresh.
    Read operations are lock-free (Python reference assignment is atomic).

    Returns:
        Set of normalized allowed hosts.
    """
    global _cached_allowlist, _cache_expiry

    # Fast path: return cached value if still valid (no lock needed for read)
    if _cached_allowlist is not None and time.time() < _cache_expiry:
        return _cached_allowlist

    # Slow path: refresh cache with lock protection
    with _cache_lock:
        # Double-check after acquiring lock (another thread may have refreshed)
        if _cached_allowlist is not None and time.time() < _cache_expiry:
            return _cached_allowlist

        _cached_allowlist = _parse_allowlist_from_env()
        _cache_expiry = time.time() + CACHE_TTL_SECONDS

        logger.debug(
            "Refreshed LLM proxy allowlist: %d hosts, TTL=%ds",
            len(_cached_allowlist),
            CACHE_TTL_SECONDS,
        )
        return _cached_allowlist


def reset_allowlist_cache_for_tests() -> None:
    """Reset allowlist cache (for testing only)."""
    global _cached_allowlist, _cache_expiry
    with _cache_lock:
        _cached_allowlist = None
        _cache_expiry = 0


def is_host_in_allowlist(host: str) -> bool:
    """Check if a host is in the allowlist.

    Args:
        host: Hostname or IP to check.

    Returns:
        True if host is in allowlist, False otherwise.
    """
    if not host:
        return False

    try:
        normalized = _normalize_host_for_allowlist(host)
        return normalized in get_allowlist()
    except ValueError:
        return False


# ── URL Validation ─────────────────────────────────────────────────────────────


def _extract_host_from_url(url: str) -> str | None:
    """Extract hostname from a URL.

    Args:
        url: The URL to parse.

    Returns:
        Hostname or None if invalid.
    """
    try:
        parsed = urlparse(url)
        return parsed.hostname
    except Exception:
        return None


def _classify_rejection_reason(url: str, validation_result: Any) -> str:
    """Classify the rejection reason for audit logging.

    Args:
        url: The blocked URL.
        validation_result: The validation result from outbound_url_guard.

    Returns:
        One of: 'loopback', 'private_ip', 'metadata', 'link_local', 'not_in_allowlist', 'other'
    """
    host = _extract_host_from_url(url)
    if not host:
        return "invalid_url"

    error_msg = validation_result.error or ""

    if any(
        term in error_msg.lower()
        for term in ["loopback", "localhost", "127.", "::1", "0:0:0:0:0:0:0:1"]
    ):
        return "loopback"

    if any(term in error_msg.lower() for term in ["metadata", "169.254"]):
        return "metadata"

    if any(term in error_msg.lower() for term in ["link-local", "169.254"]):
        return "link_local"

    if any(
        term in error_msg.lower()
        for term in ["private", "10.", "172.", "192.168", "fc00", "fe80"]
    ):
        return "private_ip"

    return "other"


# ── Audit Logging ─────────────────────────────────────────────────────────────


def _log_audit_event(
    *,
    blocked_host: str,
    tenant_id: int,
    provider: str,
    user_id: int | None = None,
    api_key_id: int | None = None,
    reason: str,
    source: str = "request",
    guard_mode: str,
) -> None:
    """Log a URL block event for audit.

    Args:
        blocked_host: The blocked hostname/IP.
        tenant_id: Tenant ID.
        provider: Provider name.
        user_id: User ID (if available).
        api_key_id: API key ID (if available).
        reason: Rejection reason classification.
        source: 'request' or 'storage'.
        guard_mode: Current guard mode ('log' or 'enforce').
    """
    try:
        from app.modules.governance.audit_logger import AuditAction, AuditLogger

        audit_logger = AuditLogger()

        # Use the new action type
        action = getattr(AuditAction, "LLM_PROXY_URL_BLOCKED", None)
        if action is None:
            # Fallback if not yet added to enum
            action_value = "llm_proxy_url_blocked"
        else:
            action_value = action.value

        audit_logger.log(
            action=action_value,
            user_id=user_id,
            severity="warning" if guard_mode == "log" else "error",
            resource_type="llm_proxy_url",
            details={
                "blocked_host": blocked_host,
                "tenant_id": tenant_id,
                "provider": provider,
                "api_key_id": api_key_id,
                "reason": reason,
                "source": source,
                "guard_mode": guard_mode,
            },
            success=False,
            error_message=f"URL blocked by SSRF guard: {blocked_host}",
        )
    except Exception as e:
        logger.error("Failed to log audit event: %s", e)


# ── Error Response ─────────────────────────────────────────────────────────────


def _build_error_response() -> tuple[Any, int]:
    """Build a standardized error response for URL blocking.

    The response does NOT expose the blocked IP/host for security.

    Returns:
        Tuple of (Flask Response, status_code).
    """
    return (
        jsonify(
            {
                "error": {
                    "message": "Provider endpoint URL is not accessible",
                    "type": "proxy_url_blocked",
                    "code": "SSRF_BLOCKED",
                }
            }
        ),
        403,
    )


# ── Main Entry Point ──────────────────────────────────────────────────────────


def safe_llm_proxy_request(
    method: str,
    url: str,
    *,
    tenant_id: int,
    provider: str,
    user_id: int | None = None,
    api_key_id: int | None = None,
    session: requests.Session | None = None,
    source: str = "request",
    **kwargs: Any,
) -> requests.Response | tuple[Any, int]:
    """
    Unified wrapper for LLM proxy requests with SSRF protection.

    This function:
    1. Checks if the URL host is in the allowlist (bypasses public check)
    2. Validates the URL using outbound_url_guard for public accessibility
    3. Executes the request with IP pinning via safe_request()
    4. Logs blocked requests for audit

    Args:
        method: HTTP method (GET, POST, etc.).
        url: Target URL for the request.
        tenant_id: Tenant ID for audit logging.
        provider: Provider name (openai, anthropic, etc.).
        user_id: Optional user ID for audit logging.
        api_key_id: Optional API key ID for audit logging.
        session: Optional requests Session to use.
        source: 'request' for runtime checks, 'storage' for pre-storage validation.
        **kwargs: Additional arguments passed to requests (headers, data, timeout, etc.).

    Returns:
        On success: requests.Response object.
        On failure: tuple of (Flask Response, status_code) for error handling.

    Example:
        response = safe_llm_proxy_request(
            "POST",
            "https://api.openai.com/v1/chat/completions",
            tenant_id=1,
            provider="openai",
            headers={"Authorization": "Bearer key"},
            json={"model": "gpt-4", "messages": [...]}
        )
        if isinstance(response, tuple):
            return response  # Error response
        # Use response.json() etc.
    """
    # Emergency disable check
    if not _is_guard_enabled():
        logger.warning("LLM proxy URL guard is DISABLED, skipping validation")
        return safe_request(method, url, session=session, **kwargs)

    guard_mode = _get_guard_mode()

    # Extract host for allowlist check
    host = _extract_host_from_url(url)

    # Check allowlist first
    if host and is_host_in_allowlist(host):
        logger.debug("URL host '%s' is in allowlist, bypassing public check", host)
        # Still use safe_request for IP pinning to prevent DNS rebinding
        try:
            return safe_request(method, url, session=session, **kwargs)
        except OutboundUrlBlockedError as e:
            # Even allowlist hosts can fail if DNS rebinding is detected
            logger.warning("Allowlist host '%s' failed safe_request: %s", host, e)
            reason = "dns_rebinding"
            _log_audit_event(
                blocked_host=host,
                tenant_id=tenant_id,
                provider=provider,
                user_id=user_id,
                api_key_id=api_key_id,
                reason=reason,
                source=source,
                guard_mode=guard_mode,
            )
            if guard_mode == "log":
                logger.warning("LOG MODE: Would have blocked URL '%s' (reason: %s)", url, reason)
                # In log mode, still try to proceed
                import requests as http_requests

                return http_requests.request(method, url, **kwargs)
            return _build_error_response()

    # Validate URL for public accessibility
    validation_result = validate_public_http_url(url)

    if not validation_result.allowed:
        reason = _classify_rejection_reason(url, validation_result)

        # Log the block event
        _log_audit_event(
            blocked_host=host or "unknown",
            tenant_id=tenant_id,
            provider=provider,
            user_id=user_id,
            api_key_id=api_key_id,
            reason=reason,
            source=source,
            guard_mode=guard_mode,
        )

        if guard_mode == "log":
            logger.warning(
                "LOG MODE: Would have blocked URL '%s' (reason: %s, error: %s)",
                url,
                reason,
                validation_result.error,
            )
            # In log mode, proceed with the request anyway
            import requests as http_requests

            return http_requests.request(method, url, **kwargs)

        logger.warning(
            "Blocked LLM proxy request to non-public URL: %s (reason: %s)",
            host,
            reason,
        )
        return _build_error_response()

    # Execute with IP pinning
    try:
        return safe_request(method, url, session=session, **kwargs)
    except OutboundUrlBlockedError as e:
        # This can happen if DNS rebinding is detected at connect time
        logger.warning("URL blocked during safe_request: %s", e)
        reason = "dns_rebinding"
        _log_audit_event(
            blocked_host=host or "unknown",
            tenant_id=tenant_id,
            provider=provider,
            user_id=user_id,
            api_key_id=api_key_id,
            reason=reason,
            source=source,
            guard_mode=guard_mode,
        )

        if guard_mode == "log":
            import requests as http_requests

            return http_requests.request(method, url, **kwargs)

        return _build_error_response()


def validate_base_url_for_storage(
    base_url: str,
    *,
    tenant_id: int,
    provider: str,
    user_id: int | None = None,
) -> tuple[bool, str | None]:
    """
    Validate a base_url before storing it in the API key configuration.

    This is a pre-check that runs when an admin saves an API key.
    The URL will still be validated at request time to prevent DNS rebinding.

    Args:
        base_url: The base URL to validate.
        tenant_id: Tenant ID for context.
        provider: Provider name.
        user_id: Optional user ID.

    Returns:
        Tuple of (is_valid, error_message).
        - (True, None) if valid or in allowlist.
        - (False, "error message") if invalid.
    """
    if not base_url:
        return True, None

    # Check allowlist
    host = _extract_host_from_url(base_url)
    if host and is_host_in_allowlist(host):
        return True, None

    # Validate
    result = validate_public_http_url(base_url)
    if result.allowed:
        return True, None

    # Determine user-friendly error message
    error_msg = result.error or "URL validation failed"

    # Classify for better user guidance
    reason = _classify_rejection_reason(base_url, result)

    if reason == "loopback":
        user_message = (
            "This URL points to a loopback address. "
            "If you need to use a local endpoint, add it to the allowlist."
        )
    elif reason == "private_ip":
        user_message = (
            "This URL points to a private network address. "
            "If you need to use a private model endpoint, "
            "add the host to the allowlist (OPENACE_LLM_PROXY_ALLOWED_HOSTS)."
        )
    elif reason == "metadata":
        user_message = "This URL points to a cloud metadata endpoint and cannot be used."
    elif reason == "link_local":
        user_message = "This URL points to a link-local address and cannot be used."
    else:
        user_message = f"The URL is not accessible: {error_msg}"

    # Log for audit
    _log_audit_event(
        blocked_host=host or "unknown",
        tenant_id=tenant_id,
        provider=provider,
        user_id=user_id,
        reason=reason,
        source="storage",
        guard_mode=_get_guard_mode(),
    )

    return False, user_message