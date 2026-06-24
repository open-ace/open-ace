"""Shared session-access check for remote session endpoints.

Centralises the owner / system-admin / machine-admin authorization so the
remote blueprint and the run-timeline blueprint share one implementation
instead of duplicating the ~15-line check. Returns ``(session_status, error)``
where ``error`` is a ``(response, status_code)`` tuple when access is denied,
or ``None`` when allowed.
"""

from __future__ import annotations

from typing import Any

from flask import g, jsonify

from app.modules.workspace.remote_agent_manager import get_remote_agent_manager
from app.modules.workspace.remote_session_manager import get_remote_session_manager


def check_session_access(
    session_id: str,
) -> tuple[dict[str, Any] | None, tuple | None]:
    """Check session access: owner, system admin, or machine admin."""
    session_mgr = get_remote_session_manager()
    status = session_mgr.get_session_status(session_id)
    if not status:
        return None, (jsonify({"error": "Session not found"}), 404)
    # System admin
    if g.user.get("role") == "admin":
        return status, None
    # Session owner
    session = session_mgr._session_manager.get_session(session_id)
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


__all__ = ["check_session_access"]
