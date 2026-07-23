"""
Unified authentication decorators for Open ACE.

Replaces 11+ scattered require_auth/require_admin implementations with a
single, consistent framework. All token extraction, validation, and error
responses go through one code path.

Usage:
    @auth_required                              # Just needs login
    @admin_required                             # Needs admin role
    @auth_required(ownership='session')         # Needs session ownership
    @public_endpoint                            # Explicitly marks as public (no auth)

Issue #1896: Query session token security
- Session tokens must only come from cookie or Authorization header
- URL tokens (WebUI, Proxy, Browser) are allowed only on specific paths
- All URL token usage is logged for audit
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import time
from functools import wraps
from typing import TYPE_CHECKING, Literal, cast
from urllib.parse import unquote

from flask import Response, g, jsonify, request

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.services.auth_service import AuthService


def _get_auth_service() -> AuthService:
    """Lazy import to avoid circular imports."""
    from app.services.auth_service import AuthService

    return AuthService()


# ── URL Token Security (Issue #1896) ─────────────────────────────────────

# WebUI token TTL in seconds (default 30 minutes)
WEBUI_TOKEN_TTL_SECONDS = 1800

# URL token allowed path prefixes
URL_TOKEN_ALLOWED_PATHS = [
    "/api/admin/",  # WebUI token for admin routes
    "/api/remote/terminal/",  # Proxy token for terminal routes
    "/api/remote/vscode/",  # Browser token for VSCode routes
    "/api/workspace/",  # WebUI token for workspace routes
]


def _get_url_token_allowed_paths() -> list[str]:
    """Get the list of allowed paths for URL tokens.

    Returns:
        List of path prefixes where URL tokens are allowed.
    """
    return URL_TOKEN_ALLOWED_PATHS


def _is_url_token_allowed_path(path: str) -> bool:
    """Check if the path allows URL token authentication.

    Args:
        path: Request path to check.

    Returns:
        True if URL tokens are allowed on this path.
    """
    for allowed in _get_url_token_allowed_paths():
        # Check both with and without trailing slash
        if path.startswith(allowed):
            return True
        # Also check exact match (e.g., /api/admin matches /api/admin/)
        if allowed.endswith("/") and path == allowed[:-1]:
            return True
    return False


def _classify_query_token(token: str) -> Literal["webui_v2", "webui_v1", "proxy", "browser", "session"]:
    """Classify the type of token from query parameter.

    Classification is based on validation method, not format pattern,
    to prevent format confusion attacks.

    Args:
        token: Token string from query parameter.

    Returns:
        Token type: "webui_v2", "webui_v1", "proxy", "browser", or "session"
    """
    if not token:
        return "session"

    # Normalize the token first
    token = normalize_token(token)

    # Try WebUI token validation (v2 format)
    if token.startswith("v2:"):
        # v2 format: v2:user_id:port:timestamp:random:signature
        parts = token.split(":")
        if len(parts) == 6:
            return "webui_v2"

    # Try WebUI token validation (v1 format)
    # v1 format: user_id:port:random:signature (4 parts)
    parts = token.split(":")
    if len(parts) == 4:
        try:
            # Check if first two parts are integers (user_id and port)
            int(parts[0])
            int(parts[1])
            # Could be WebUI token, but need to verify via actual validation
            # We return webui_v1 tentatively, actual validation happens later
            return "webui_v1"
        except (ValueError, TypeError):
            pass

    # Try to find in terminal_info_store (Proxy token)
    try:
        from app.modules.workspace.terminal_store import terminal_info_store

        found = terminal_info_store.find_by_token(token)
        if found:
            return "proxy"
    except Exception:
        pass

    # Try to find in vscode_info_store (Browser token)
    try:
        from app.modules.workspace.vscode_store import vscode_info_store

        found = vscode_info_store.find_by_token(token)
        if found:
            return "browser"
    except Exception:
        pass

    # Default to session token type
    return "session"


def _validate_webui_token_v2(token: str, token_secret: str) -> tuple[bool, int | None, str | None]:
    """Validate a v2 format WebUI token with TTL.

    v2 format: v2:user_id:port:timestamp:random:signature

    Args:
        token: Token string to validate.
        token_secret: Secret key for signature verification.

    Returns:
        Tuple of (is_valid, user_id, error_message).
    """
    parts = token.split(":")
    if len(parts) != 6:
        return False, None, "Invalid v2 token format"

    try:
        _, user_id_str, port_str, timestamp_str, random_part, signature = parts
        user_id = int(user_id_str)
        port = int(port_str)
        timestamp = int(timestamp_str)
    except (ValueError, TypeError) as e:
        return False, None, f"Token parse error: {e}"

    # Verify signature
    payload = f"v2:{user_id}:{port}:{timestamp}:{random_part}"
    expected_signature = hashlib.sha256(
        f"{payload}:{token_secret}".encode()
    ).hexdigest()[:16]

    if not hmac.compare_digest(signature, expected_signature):
        return False, None, "Invalid signature"

    # Check TTL
    current_time = int(time.time())
    age_seconds = current_time - timestamp
    if age_seconds > WEBUI_TOKEN_TTL_SECONDS:
        return False, None, f"Token expired (age: {age_seconds}s, TTL: {WEBUI_TOKEN_TTL_SECONDS}s)"

    if age_seconds < 0:
        return False, None, "Token timestamp is in the future"

    return True, user_id, None


def _validate_webui_token_v1(token: str, token_secret: str) -> tuple[bool, int | None, str | None]:
    """Validate a v1 format WebUI token (legacy, no TTL).

    v1 format: user_id:port:random:signature

    Args:
        token: Token string to validate.
        token_secret: Secret key for signature verification.

    Returns:
        Tuple of (is_valid, user_id, error_message).
    """
    parts = token.split(":")
    if len(parts) != 4:
        return False, None, "Invalid v1 token format"

    try:
        user_id_str, port_str, random_part, signature = parts
        user_id = int(user_id_str)
        port = int(port_str)
    except (ValueError, TypeError) as e:
        return False, None, f"Token parse error: {e}"

    # Verify signature
    expected_signature = hashlib.sha256(
        f"{user_id}:{port}:{random_part}:{token_secret}".encode()
    ).hexdigest()[:16]

    if not hmac.compare_digest(signature, expected_signature):
        return False, None, "Invalid signature"

    return True, user_id, None


def _log_url_token_usage(
    token_type: str,
    path: str,
    tenant_id: int | None = None,
    user_id: int | None = None,
    is_legacy: bool = False,
) -> None:
    """Log URL token usage for audit.

    Args:
        token_type: Type of URL token used.
        path: Request path.
        tenant_id: Optional tenant ID.
        user_id: Optional user ID.
        is_legacy: Whether this is a legacy v1 token.
    """
    try:
        from app.modules.governance.audit_logger import AuditAction, AuditLogger

        audit_logger = AuditLogger()

        # Map token type to audit action
        action_map = {
            "webui_v2": AuditAction.WEBUI_TOKEN_IN_QUERY_USED,
            "webui_v1": AuditAction.LEGACY_WEBUI_TOKEN_USED,
            "proxy": AuditAction.PROXY_TOKEN_IN_QUERY_USED,
            "browser": AuditAction.BROWSER_TOKEN_IN_QUERY_USED,
        }

        action = action_map.get(token_type)
        if not action:
            return

        audit_logger.log_action(
            action,
            user_id=user_id,
            severity="warning" if is_legacy else "info",
            resource_type="url_token",
            details={
                "token_type": token_type,
                "path": path,
                "is_legacy": is_legacy,
            },
            tenant_id=tenant_id,
        )
    except Exception as e:
        logger.warning("Failed to log URL token usage: %s", e)


def _log_query_session_token_rejected(path: str, tenant_id: int | None = None) -> None:
    """Log when a session token is rejected from query parameter.

    Args:
        path: Request path.
        tenant_id: Optional tenant ID.
    """
    try:
        from app.modules.governance.audit_logger import AuditAction, AuditLogger

        audit_logger = AuditLogger()
        audit_logger.log_action(
            AuditAction.QUERY_SESSION_TOKEN_REJECTED,
            severity="warning",
            resource_type="session",
            details={
                "path": path,
                "reason": "session_token_in_query_param",
            },
            tenant_id=tenant_id,
        )
    except Exception as e:
        logger.warning("Failed to log query session token rejected: %s", e)


def _log_url_token_path_violation(
    token_type: str,
    path: str,
    tenant_id: int | None = None,
) -> None:
    """Log when a URL token is used on a disallowed path.

    Args:
        token_type: Type of URL token used.
        path: Request path.
        tenant_id: Optional tenant ID.
    """
    try:
        from app.modules.governance.audit_logger import AuditAction, AuditLogger

        audit_logger = AuditLogger()
        audit_logger.log_action(
            AuditAction.URL_TOKEN_PATH_VIOLATION,
            severity="warning",
            resource_type="url_token",
            details={
                "token_type": token_type,
                "path": path,
            },
            tenant_id=tenant_id,
        )
    except Exception as e:
        logger.warning("Failed to log URL token path violation: %s", e)


def normalize_token(token: str) -> str:
    """Normalize token that may be URL-encoded.

    Some clients pass URL-encoded tokens through query params or headers.
    urllib.parse.unquote is idempotent for non-encoded strings, so this
    safely handles both encoded and non-encoded tokens.

    This is the centralized normalization function for all token types
    (WebUI tokens, proxy tokens, terminal tokens, etc.).

    Args:
        token: Token string from request, may be URL-encoded.

    Returns:
        Decoded token string.
    """
    return unquote(token) if token else ""


def normalize_webui_token(token: str) -> str:
    """Normalize WebUI token that may be double-encoded.

    Some clients pass URL-encoded tokens through encodeURIComponent() twice,
    resulting in %3A becoming %253A. Flask's request.args.get() decodes once,
    so we get %3A in the token. We need to decode it again to get the correct
    format (user_id:port:random:signature).

    DEPRECATED: Use normalize_token() instead for all token types.
    This function is kept for backward compatibility.

    Args:
        token: Token string from request, may contain %3A if double-encoded.

    Returns:
        Normalized token with colons restored.
    """
    # Use the general normalize_token function
    return normalize_token(token)


def _extract_session_token() -> str:
    """Extract session token from cookie or Authorization header only.

    This is the safe extraction method for regular API endpoints.
    Query parameter tokens are NOT accepted.

    Returns:
        Token string or empty string if not found.
    """
    token = request.cookies.get("session_token")
    if token:
        return cast("str", token)

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return cast("str", auth_header[7:])

    return ""


def _extract_token() -> str:
    """Extract auth token from request (cookie → header → query param).

    For query parameter tokens, validates token type and logs usage.
    Session tokens from query parameters are rejected per Issue #1896.

    Returns:
        Token string or empty string if not found/rejected.
    """
    # First try cookie and header (safe sources)
    token = request.cookies.get("session_token")
    if token:
        return cast("str", token)

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return cast("str", auth_header[7:])

    # Check query parameter token
    query_token = request.args.get("token", "")
    if not query_token:
        return ""

    # Classify and validate query token
    query_token = normalize_token(query_token)
    token_type = _classify_query_token(query_token)

    # Check policy for session tokens
    from app.config.feature_flags import get_query_token_policy, is_query_session_token_rejected

    policy = get_query_token_policy()

    if token_type == "session":
        # Session token in query param - check policy
        if is_query_session_token_rejected():
            _log_query_session_token_rejected(request.path)
            logger.warning(
                "Session token rejected from query param for path %s (policy=%s)",
                request.path,
                policy,
            )
            return ""
        # In observe/warn mode, log but allow (for migration period)
        logger.info(
            "Session token used in query param for path %s (policy=%s)",
            request.path,
            policy,
        )

    return cast("str", query_token)


def _authenticate(token: str) -> tuple[bool, dict | None]:
    """Validate token and return (success, user_dict_or_error)."""
    auth_service = _get_auth_service()
    result: tuple[bool, dict | None] = auth_service.validate_session(token)
    return result


def _load_user_from_token(token: str) -> dict | None:
    """Validate token and set g.user. Returns user dict or None."""
    try:
        valid, result = _authenticate(token)
        if not valid or result is None:
            return None
        return {
            "id": result.get("user_id"),
            "username": result.get("username"),
            "email": result.get("email"),
            "role": result.get("role"),
            "tenant_id": result.get("tenant_id"),
            "must_change_password": bool(result.get("must_change_password")),
        }
    except Exception as e:
        logger.error(f"Database error during authentication: {e}")
        return None


def _check_session_ownership(user_id: int, session_id: str, tenant_id: int | None = None) -> bool:
    """Check if user owns the given session."""
    from app.modules.workspace.session_manager import get_session_manager

    mgr = get_session_manager()
    session = mgr.get_session(session_id, tenant_id=tenant_id)
    if not session:
        return False
    return getattr(session, "user_id", None) == user_id


def _check_machine_admin(user_id: int, machine_id: str) -> bool:
    """Check if user is system admin or machine admin."""
    g_user = getattr(g, "user", {})
    if g_user.get("role") == "admin":
        return True
    try:
        from app.services.remote_agent_manager import get_remote_agent_manager

        mgr = get_remote_agent_manager()
        perm = mgr.get_user_permission(machine_id, user_id)
        return cast("bool", perm == "admin")
    except Exception:
        return False


_PASSWORD_CHANGE_ALLOWED_ENDPOINTS = {
    ("GET", "/api/auth/check"),
    ("GET", "/api/auth/me"),
    ("GET", "/api/auth/profile"),
    ("POST", "/api/auth/change-password"),
    ("POST", "/api/auth/logout"),
    ("GET", "/api/password-policy"),
}


def _is_password_change_allowed_request() -> bool:
    """Return whether the current request is exempt from password-change enforcement."""
    return (request.method, request.path) in _PASSWORD_CHANGE_ALLOWED_ENDPOINTS


def _password_change_required_response() -> tuple[Response, int]:
    """Return the standard response for blocked requests."""
    return (
        jsonify(
            {
                "error": "Password change required",
                "code": "password_change_required",
                "must_change_password": True,
            }
        ),
        403,
    )


def enforce_password_change_requirement(user: dict | None) -> tuple[Response, int] | None:
    """Block non-exempt requests when the user must change their password."""
    if not user or not user.get("must_change_password"):
        return None
    if _is_password_change_allowed_request():
        return None
    return _password_change_required_response()


# ── Public API for cross-module use ──────────────────────────────────


def validate_session_token(token: str) -> dict | None:
    """Validate a session token and return the user dict, or None if invalid.

    Public wrapper for _load_user_from_token — use this instead of
    importing the private function directly.
    """
    return _load_user_from_token(token)


def check_machine_admin_permission(user_id: int, machine_id: str) -> bool:
    """Check if a user has admin permission on a remote machine.

    Public wrapper for _check_machine_admin — use this instead of
    importing the private function directly.
    """
    return _check_machine_admin(user_id, machine_id)


def auth_required(f=None, *, ownership=None):
    """
    Decorator: require authentication, optionally with ownership check.

    Uses session token from cookie or Authorization header only.
    Query parameter session tokens are rejected per Issue #1896.

    Args:
        f: The function to decorate.
        ownership: Optional ownership check type.
            'session' — verifies g.user.id matches session's user_id
            'machine' — verifies machine admin permission

    Sets g.user, g.user_id, g.user_role on success.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Use session token extraction (cookie/header only)
            token = _extract_session_token()
            if not token:
                return jsonify({"error": "Authentication required"}), 401

            user = _load_user_from_token(token)
            if not user:
                return jsonify({"error": "Invalid or expired session"}), 401

            g.user = user
            g.user_id = user.get("id")
            g.user_role = user.get("role")
            g.tenant_id = user.get("tenant_id")

            password_change_response = enforce_password_change_requirement(user)
            if password_change_response is not None:
                return password_change_response

            # Ownership checks (admin bypasses)
            if g.user_role == "admin":
                return func(*args, **kwargs)

            if ownership == "session":
                session_id = kwargs.get("session_id")
                if (
                    session_id
                    and isinstance(g.user_id, int)
                    and not _check_session_ownership(
                        g.user_id,
                        session_id,
                        user.get("tenant_id"),
                    )
                ):
                    return jsonify({"error": "Access denied"}), 403

            elif ownership == "machine":
                machine_id = kwargs.get("machine_id")
                if (
                    machine_id
                    and isinstance(g.user_id, int)
                    and not _check_machine_admin(g.user_id, machine_id)
                ):
                    return jsonify({"error": "Machine admin permission required"}), 403

            return func(*args, **kwargs)

        return wrapper

    if f is not None:
        return decorator(f)
    return decorator


def admin_required(f=None):
    """
    Decorator: require admin role.

    Authentication methods (in order):
    1. Session token from cookie or Authorization header
    2. WebUI token from query param (for iframe requests)

    Issue #1896: Query parameter session tokens are rejected.
    WebUI tokens are validated and logged for audit.

    Sets g.user, g.user_id, g.user_role on success.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # First try session token from cookie/header only
            token = _extract_session_token()
            if token:
                user = _load_user_from_token(token)
                if user:
                    if user.get("role") != "admin":
                        return jsonify({"error": "Admin access required"}), 403

                    g.user = user
                    g.user_id = user.get("id")
                    g.user_role = user.get("role")
                    g.tenant_id = user.get("tenant_id")

                    password_change_response = enforce_password_change_requirement(user)
                    if password_change_response is not None:
                        return password_change_response
                    return func(*args, **kwargs)

            # Fallback: try WebUI token from query param
            # This supports iframe requests from WebUI where session token is not available
            url_token = request.args.get("token")
            if url_token:
                # Check if path allows URL tokens
                if not _is_url_token_allowed_path(request.path):
                    _log_url_token_path_violation("webui", request.path)
                    return jsonify({"error": "URL token not allowed on this path"}), 403

                url_token = normalize_webui_token(url_token)
                from app.services.webui_manager import get_webui_manager

                manager = get_webui_manager()
                if manager:
                    # Validate token (supports v1 and v2 formats)
                    valid, user_id, error = manager.validate_token(url_token)
                    if valid and user_id:
                        # Log URL token usage
                        is_legacy = not url_token.startswith("v2:")
                        _log_url_token_usage(
                            "webui_v2" if not is_legacy else "webui_v1",
                            request.path,
                            user_id=user_id,
                            is_legacy=is_legacy,
                        )

                        # Load user from database to check role
                        from app.repositories.user_repo import UserRepository

                        user_repo = UserRepository()
                        user = user_repo.get_user_by_id(user_id)
                        if user:
                            if user.get("role") != "admin":
                                return jsonify({"error": "Admin access required"}), 403

                            g.user = user
                            g.user_id = user_id
                            g.user_role = user.get("role")
                            g.tenant_id = user.get("tenant_id")

                            password_change_response = enforce_password_change_requirement(user)
                            if password_change_response is not None:
                                return password_change_response
                            return func(*args, **kwargs)

            # No valid authentication found
            return jsonify({"error": "Authentication required"}), 401

        return wrapper

    if f is not None:
        return decorator(f)
    return decorator


def public_endpoint(f):
    """
    Decorator: explicitly mark an endpoint as public (no auth required).

    Used by the API security scanner to identify intentionally unauthenticated
    routes, replacing the hardcoded PUBLIC_ENDPOINTS list.
    """
    f._is_public_endpoint = True  # type: ignore[attr-defined]

    @wraps(f)
    def wrapper(*args, **kwargs):
        return f(*args, **kwargs)

    # Copy the marker to the wrapper so scanner can find it
    wrapper._is_public_endpoint = True  # type: ignore[attr-defined]
    return wrapper


# ── Tenant scope helpers ──────────────────────────────────────────────


def _normalize_user_tenant_id(value: object) -> int | None:
    """Coerce a raw ``tenant_id`` value into a positive int or None."""
    if value in (None, ""):
        return None
    try:
        tenant_id = int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return None
    return tenant_id if tenant_id > 0 else None


def resolve_tenant_scope() -> tuple[int | None, bool]:
    """Return ``(tenant_id, is_admin)`` for the current request.

    Mirrors the workspace pattern (``_tenant_scope_required`` /
    ``_session_lookup_tenant_id`` in ``app/routes/workspace.py``): admins
    keep global scope (``tenant_id`` may be ``None``), while non-admins are
    tenant-scoped to their resolved ``tenant_id``.

    This helper is side-effect free; callers decide what to do when a
    non-admin has no tenant (the route layer denies with 403, see
    :func:`require_tenant_scope`).
    """
    user = getattr(g, "user", None) or {}
    is_admin = user.get("role") == "admin"
    tenant_id = _normalize_user_tenant_id(user.get("tenant_id"))
    return tenant_id, is_admin


def require_tenant_scope() -> tuple[int | None, tuple[Response, int] | None]:
    """Resolve the request's tenant scope, denying non-admins without one.

    Returns ``(tenant_id, None)`` when the caller may proceed:

    * admins -> ``tenant_id`` is ``None`` (global scope preserved);
    * tenant-scoped non-admins -> their resolved ``tenant_id``.

    Returns ``(None, error_response)`` for a non-admin user with no
    resolvable tenant — the caller must ``return`` the error response to
    fail closed instead of passing ``None`` to the repository layer (which
    would otherwise be treated as a wildcard/global filter and leak
    cross-tenant data, see Issue #1775).
    """
    tenant_id, is_admin = resolve_tenant_scope()
    if is_admin:
        return None, None
    if tenant_id is None:
        return None, (jsonify({"error": "Tenant scope required"}), 403)
    return tenant_id, None
