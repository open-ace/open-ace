"""Shared session-access and remote-user loading helpers.

Centralises two things the remote and run-timeline blueprints otherwise duplicate:

1. ``check_session_access`` — the owner / system-admin / machine-admin
   authorization. Returns ``(session_status, error)`` where ``error`` is a
   ``(response, status_code)`` tuple when access is denied, or ``None`` when
   allowed.
2. ``load_remote_user`` (+ ``_set_user_from_token`` /
   ``_set_user_from_webui_token``) — loading the current user from a session
   token or the WebUI URL token into ``flask.g``. Sharing one implementation
   means the two blueprints' auth cannot drift apart.
"""

from __future__ import annotations

import logging
from typing import Any

from flask import g, jsonify, request

from app.auth.decorators import (
    _extract_token,
    _load_user_from_token,
    enforce_password_change_requirement,
)
from app.modules.workspace.remote_agent_manager import get_remote_agent_manager
from app.modules.workspace.remote_session_manager import get_remote_session_manager

logger = logging.getLogger(__name__)


def check_session_access(
    session_id: str,
) -> tuple[dict[str, Any] | None, tuple | None]:
    """Check session access: owner, system admin, or machine admin."""
    session_mgr = get_remote_session_manager()
    status = session_mgr.get_session_status(session_id)
    if not status:
        return None, (jsonify({"error": "Session not found"}), 404)
    # System admins intentionally have global session visibility for operations.
    if g.user.get("role") == "admin":
        return status, None
    # Session owner
    current_tenant_id = g.user.get("tenant_id")
    # Fail closed: a non-admin with no tenant cannot be tenant-scoped. Previously
    # a null tenant_id silently skipped the cross-tenant check and fell through to
    # the (untenant-scoped) machine-admin branch, leaking cross-tenant sessions.
    if current_tenant_id in (None, ""):
        return None, (jsonify({"error": "Access denied"}), 403)
    session = session_mgr._session_manager.get_session(session_id, tenant_id=current_tenant_id)
    if session and current_tenant_id != session.tenant_id:
        return None, (jsonify({"error": "Access denied"}), 403)
    if session and session.user_id == g.user["id"]:
        return status, None
    # Machine admin
    mid = status.get("machine_id")
    if mid:
        mgr = get_remote_agent_manager()
        perm = mgr.get_user_permission(mid, g.user["id"])
        if perm == "admin":
            return status, None
    return None, (jsonify({"error": "Access denied"}), 403)


# ── shared remote-user loading ────────────────────────────────────────────


def _apply_user(user: dict[str, Any]) -> None:
    """Populate flask.g from a user dict."""
    g.user = user
    g.user_id = user.get("id")
    g.user_role = user.get("role")
    g.tenant_id = user.get("tenant_id")


def _set_user_from_token() -> bool:
    """Load g.user from the session/Authorization token. Returns True on success."""
    token = _extract_token()
    if not token:
        return False
    user = _load_user_from_token(token)
    if not user:
        return False
    _apply_user(user)
    return True


def _set_user_from_webui_token() -> bool:
    """Load g.user from the WebUI URL token (iframe requests). Returns True/False."""
    url_token = request.args.get("token")
    if not url_token:
        return False
    # Handle double-encoded tokens from some clients
    from app.auth.decorators import normalize_webui_token

    url_token = normalize_webui_token(url_token)
    try:
        from app.services.webui_manager import WebUIManager

        ok, user_id, _ = WebUIManager().validate_token(url_token)
        if ok and user_id:
            from app.repositories.user_repo import UserRepository

            user_data = UserRepository().get_user_by_id(user_id)
            if user_data:
                _apply_user(
                    {
                        "id": user_id,
                        "username": user_data.get("username"),
                        "email": user_data.get("email"),
                        "role": user_data.get("role"),
                        "tenant_id": user_data.get("tenant_id"),
                        "must_change_password": bool(user_data.get("must_change_password")),
                    }
                )
                return True
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("remote user load: webui token validation failed: %s", e)
    return False


def load_remote_user() -> tuple | None:
    """Authenticate a remote-API request: session token, then WebUI URL token.

    Sets ``flask.g`` on success and returns ``None``; returns a ``(response,
    401)`` tuple when no valid credential is present. Shared by the remote and
    run-timeline blueprints so their auth stays identical. CORS preflight
    (``OPTIONS``) is allowed through unauthenticated.
    """
    if request.method == "OPTIONS":
        return None
    if _set_user_from_token() or _set_user_from_webui_token():
        # Enforce the forced-password-change lockdown here (the shared
        # chokepoint for remote_bp and run_timeline_bp) so a user flagged
        # must_change_password=True cannot reach the ~40 before_request-only
        # remote endpoints or either run-timeline endpoint. Mirrors the gate
        # used in auth_required/admin_required. The proxy-token path sets
        # g.user without must_change_password, which the gate treats as falsy
        # (allow), so proxy auth is unaffected.
        password_change_response = enforce_password_change_requirement(getattr(g, "user", None))
        if password_change_response is not None:
            return password_change_response
        return None
    return jsonify({"error": "Authentication required"}), 401


__all__ = ["check_session_access", "load_remote_user"]
