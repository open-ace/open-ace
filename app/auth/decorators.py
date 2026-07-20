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
"""

from __future__ import annotations

import logging
import os
from functools import wraps
from typing import TYPE_CHECKING, cast
from urllib.parse import unquote

from flask import Response, g, jsonify, request

logger = logging.getLogger(__name__)

# Configuration: Allow query session token for emergency rollback
# Set ALLOW_QUERY_SESSION_TOKEN=true to temporarily allow session tokens from query params
ALLOW_QUERY_SESSION_TOKEN = os.environ.get("ALLOW_QUERY_SESSION_TOKEN", "false").lower() == "true"

if TYPE_CHECKING:
    from app.services.auth_service import AuthService


def _get_auth_service() -> AuthService:
    """Lazy import to avoid circular imports."""
    from app.services.auth_service import AuthService

    return AuthService()


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
    """Extract session token from request (cookie → header only).

    This function ONLY extracts session tokens from secure sources
    (cookie and Authorization header). Query parameter tokens are NOT
    accepted for session authentication to prevent credential leakage
    through URLs.

    Returns:
        Session token string, or empty string if not found.
    """
    token = request.cookies.get("session_token")
    if token:
        return cast("str", token)

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return cast("str", auth_header[7:])

    # Emergency rollback: allow query param if explicitly enabled
    if ALLOW_QUERY_SESSION_TOKEN:
        return cast("str", request.args.get("token", ""))

    return ""


def _extract_url_token() -> str:
    """Extract URL token from request query parameter.

    This function extracts tokens from query parameters, which should
    ONLY be used for short-lived, scoped tokens like WebUI tokens
    or proxy tokens (not regular session tokens).

    Returns:
        URL token string, or empty string if not found.
    """
    return cast("str", request.args.get("token", ""))


def _looks_like_webui_token(token: str) -> bool:
    """Check if a token looks like a WebUI token format.

    WebUI tokens have format: user_id:port:random:signature
    - 4 parts separated by colons
    - user_id: positive integer
    - port: integer in range 1024-65535
    - random: 32-character hex string
    - signature: 16-character hex string

    This is a format check only; actual validation is done by
    WebUIManager.validate_token() which verifies the signature.

    Args:
        token: Token string to check.

    Returns:
        True if token matches WebUI token format, False otherwise.
    """
    if not token:
        return False

    parts = token.split(":")
    if len(parts) != 4:
        return False

    try:
        user_id = int(parts[0])
        port = int(parts[1])
        random_part = parts[2]
        signature = parts[3]

        return (
            user_id > 0
            and 1024 <= port <= 65535
            and len(random_part) == 32
            and len(signature) == 16
            and all(c in "0123456789abcdef" for c in random_part.lower())
            and all(c in "0123456789abcdef" for c in signature.lower())
        )
    except (ValueError, TypeError):
        return False


def _extract_token() -> str:
    """Extract auth token from request (cookie → header → query param).

    DEPRECATED: This function is kept for backward compatibility but should
    not be used for new code. Use _extract_session_token() or _extract_url_token()
    instead depending on the authentication context.

    For session authentication, use _extract_session_token().
    For WebUI/proxy token authentication, use _extract_url_token() with
    appropriate type checking.
    """
    token = request.cookies.get("session_token")
    if token:
        return cast("str", token)

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return cast("str", auth_header[7:])

    return cast("str", request.args.get("token", ""))


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

    Args:
        f: The function to decorate.
        ownership: Optional ownership check type.
            'session' — verifies g.user.id matches session's user_id
            'machine' — verifies machine admin permission

    Sets g.user, g.user_id, g.user_role on success.

    Note: Session tokens must come from cookie or Authorization header,
    not from query parameters (to prevent credential leakage through URLs).
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Use session token extraction (cookie/header only, not query param)
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

    Supports two authentication methods:
    1. Session token (from cookie or Authorization header only, NOT query param)
    2. WebUI token (from query param for iframe requests, with format check)

    Sets g.user, g.user_id, g.user_role on success.

    Note: Session tokens from query parameters are NOT accepted to prevent
    credential leakage through URLs. Only WebUI tokens (which are short-lived
    and scoped) are accepted from query parameters.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # First try session token authentication (cookie/header only)
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

            # Fallback: try WebUI token from query param (with format check)
            # This supports iframe requests from WebUI where session token is not available
            url_token = _extract_url_token()
            if url_token and _looks_like_webui_token(url_token):
                # Handle double-encoded tokens from some clients
                url_token = normalize_webui_token(url_token)
                from app.services.webui_manager import get_webui_manager

                manager = get_webui_manager()
                if manager:
                    valid, user_id, error = manager.validate_token(url_token)
                    if valid and user_id:
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


# ── Token extraction helpers for external modules ──────────────────────────────


def extract_session_token_for_auth() -> str:
    """Public wrapper for _extract_session_token().

    Use this function when you need to extract session tokens for
    authentication purposes. This function only extracts from cookie
    and Authorization header (NOT query params) to prevent credential
    leakage through URLs.

    Returns:
        Session token string, or empty string if not found.
    """
    return _extract_session_token()


def extract_url_token_for_auth() -> str:
    """Public wrapper for _extract_url_token().

    Use this function when you need to extract URL tokens (WebUI tokens,
    proxy tokens) for authentication purposes. This function only extracts
    from query parameters.

    Returns:
        URL token string, or empty string if not found.
    """
    return _extract_url_token()


def is_webui_token_format(token: str) -> bool:
    """Public wrapper for _looks_like_webui_token().

    Use this function to check if a token matches the WebUI token format
    before attempting validation.

    Args:
        token: Token string to check.

    Returns:
        True if token matches WebUI token format, False otherwise.
    """
    return _looks_like_webui_token(token)
