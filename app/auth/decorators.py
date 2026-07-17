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
from functools import wraps
from typing import TYPE_CHECKING, cast

from flask import Response, g, jsonify, request

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.services.auth_service import AuthService


def _get_auth_service() -> AuthService:
    """Lazy import to avoid circular imports."""
    from app.services.auth_service import AuthService

    return AuthService()


def _extract_token() -> str:
    """Extract auth token from request (cookie → header → query param)."""
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
    if request.method == "OPTIONS":
        return True
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
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            token = _extract_token()
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
    1. Session token (from cookie, header, or query param)
    2. WebUI token (fallback from query param for iframe requests)

    Sets g.user, g.user_id, g.user_role on success.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # First try session token authentication
            token = _extract_token()
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
