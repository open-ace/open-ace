"""
Open ACE - Remote Workspace API Routes

API endpoints for remote workspace management including:
- Machine registration and management
- Remote session creation and control
- WebSocket agent communication
- LLM API proxy for remote CLI tools
- Usage reporting from remote agents
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import threading
import time
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from flask import Blueprint, Response, g, jsonify, request, stream_with_context

from app.auth.decorators import _extract_token, admin_required, enforce_password_change_requirement
from app.modules.governance.audit_logger import AuditAction, AuditLogger
from app.modules.workspace.agent_token import token_hash_prefix
from app.modules.workspace.api_key_proxy import get_api_key_proxy_service
from app.modules.workspace.llm_proxy_handler import handle_llm_proxy_request
from app.modules.workspace.remote_agent_manager import get_remote_agent_manager
from app.modules.workspace.remote_session_manager import get_remote_session_manager
from app.modules.workspace.session_access import _set_user_from_token, _set_user_from_webui_token
from app.modules.workspace.terminal_store import terminal_info_store
from app.repositories.database import adapt_sql

logger = logging.getLogger(__name__)

MAX_RAW_CONTENT_LENGTH = 100000
MAX_MESSAGE_LENGTH = 50000

# Module-level audit logger instance
audit_logger = AuditLogger()

# AUTH_FAILURE rate-limiting state: {token_hash_prefix: last_audit_timestamp}
_auth_failure_lock = threading.Lock()
_auth_failure_last_audit: dict[str, float] = {}
_AUTH_FAILURE_RATE_LIMIT_SECONDS = 300  # 5 minutes

_CLI_SETTINGS_TOOLS = ["claude-code", "qwen-code", "codex-cli", "zcode"]

remote_bp = Blueprint("remote", __name__)


@remote_bp.before_request
def load_user():
    """Load the current user from session token before each request.

    Most remote endpoints require authentication. Returns 401 if no valid
    session token is provided. WebSocket, agent, and LLM-proxy endpoints
    use their own auth (JWT tokens) and are exempted.

    Token / WebUI-token loading is shared with the run-timeline blueprint via
    ``session_access``; only the remote-specific exemption list and the
    terminal-status proxy-token special case live here.
    """
    # Skip auth for CORS preflight requests
    if request.method == "OPTIONS":
        return

    # Skip auth for endpoints that use their own authentication (JWT, API keys)
    # or are public (agent install/uninstall scripts, agent file downloads)
    _exact_exempt = {
        "/api/remote/agent/register",
        "/api/remote/agent/ws",
        "/api/remote/agent/message",
        # Uses the Remote Agent Bearer token, not a WebUI session token.
        "/api/remote/usage-report",
        "/api/remote/agent/install.sh",
        "/api/remote/agent/install.ps1",
        "/api/remote/agent/uninstall.sh",
        "/api/remote/agent/uninstall.ps1",
    }
    if request.path in _exact_exempt:
        return
    if request.path.startswith("/api/remote/llm-proxy"):
        return
    if request.endpoint == "remote.terminal_websocket":
        return
    # VSCode proxy and WebSocket endpoints use their own token authentication
    # (token in query string, validated against vscode_info_store)
    if request.path.startswith("/api/remote/vscode/") and (
        "/proxy/" in request.path or request.path.endswith("/ws")
    ):
        return
    # Agent file downloads are public (needed for agent installation)
    if request.path.startswith("/api/remote/agent/files/"):
        return

    # Shared session/Authorization-token loading.
    if _set_user_from_token():
        # Enforce the forced-password-change lockdown before letting the
        # request reach the ~40 before_request-only remote endpoints (only a
        # handful carry @admin_required/@machine_access_required, which re-run
        # their own checks). Mirrors auth_required/admin_required.
        password_change_response = enforce_password_change_requirement(getattr(g, "user", None))
        if password_change_response is not None:
            return password_change_response
        return None  # Authenticated

    # Special case: WebSocket proxy token for terminal status endpoint.
    # The WebSocket proxy process uses its own token (stored in
    # terminal_info_store) to fetch terminal info for connecting to the remote
    # terminal server. This sits between the session-token and WebUI-token
    # fallbacks, so it cannot be folded into the shared load_remote_user().
    if request.path.startswith("/api/remote/terminal/") and request.path.endswith("/status"):
        from app.modules.workspace.terminal_store import terminal_info_store

        # Extract terminal_id from path: /api/remote/terminal/{terminal_id}/status
        path_parts = request.path.split("/")
        if len(path_parts) >= 5:
            terminal_id = path_parts[4]
            machine_id = request.args.get("machine_id", "")
            token = _extract_token()
            if machine_id and token:
                info = terminal_info_store.get(machine_id, terminal_id)
                if info:
                    stored_token = info.get("token", "")
                    if stored_token and hmac.compare_digest(token, stored_token):
                        # Proxy token matched - set a minimal g.user for access check
                        g.user = {"role": "proxy", "id": None}
                        g.user_id = None
                        g.user_role = "proxy"
                        logger.info("Proxy token authenticated for terminal %s", terminal_id[:8])
                        return None  # Authenticated as proxy

    # Shared WebUI URL-token fallback (iframe requests from qwen-code-webui).
    if _set_user_from_webui_token():
        password_change_response = enforce_password_change_requirement(getattr(g, "user", None))
        if password_change_response is not None:
            return password_change_response
        return None

    return jsonify({"error": "Authentication required"}), 401


def _validate_agent_bearer(machine_id: str) -> tuple[str | None, tuple[Any, Any] | None]:
    """Validate the Bearer token in the Authorization header.

    Returns:
        (token_value, None) on success.
        (None, error_response) on failure.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None, (jsonify({"error": "Missing Bearer token"}), 401)

    token = auth_header[7:]  # Strip "Bearer " prefix
    if not token:
        return None, (jsonify({"error": "Empty Bearer token"}), 401)

    agent_mgr = get_remote_agent_manager()
    if not agent_mgr.validate_agent_token(token, machine_id):
        return None, (jsonify({"error": "Invalid or revoked Bearer token"}), 401)

    return token, None


def _check_legacy_fallback(machine_id: str) -> tuple[bool, tuple[Any, Any] | None]:
    """Check if a machine qualifies for legacy (no-Bearer) auth.

    Legacy machines that were registered before Bearer token enforcement
    can still authenticate without a Bearer token, but with an expiry
    deadline and a clear_legacy_mode transition on first Bearer use.

    Returns:
        (is_legacy, None) if legacy mode applies.
        (False, error_response) if not legacy and no Bearer provided.
        (False, None) if Bearer is present (caller should validate normally).
    """
    agent_mgr = get_remote_agent_manager()

    # If an Authorization header is present, clear legacy mode and use Bearer
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        # If this was a legacy machine, clear the flag
        if agent_mgr.is_legacy_machine(machine_id):
            agent_mgr.clear_legacy_mode(machine_id)
            logger.info("Legacy mode cleared for machine %s after Bearer auth", machine_id[:8])
        return False, None

    # No Bearer header — check if legacy mode is allowed
    if not agent_mgr.is_legacy_machine(machine_id):
        return False, (jsonify({"error": "Missing Bearer token"}), 401)

    # Legacy machine — check deadline (P2-1: 90-day expiry)
    machine = agent_mgr.get_machine(machine_id)
    if machine and machine.get("created_at"):
        try:
            from datetime import datetime, timezone

            created_at = machine["created_at"]
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at)
            if created_at.tzinfo is not None:
                created_at = created_at.replace(tzinfo=None)

            age_days = (datetime.now(timezone.utc).replace(tzinfo=None) - created_at).days
            deadline = agent_mgr.LEGACY_MODE_DEADLINE_DAYS
            if age_days > deadline:
                return False, (
                    jsonify({"error": "Legacy mode expired. Please re-register the agent."}),
                    401,
                )
        except Exception:
            pass  # If date parsing fails, allow legacy

    logger.warning(
        "Legacy auth (no Bearer) accepted for machine %s — deadline approaching",
        machine_id[:8],
    )
    return True, None


def _audit_auth_failure(token_or_prefix: str, reason: str, client_ip: str) -> None:
    """Record an AGENT_AUTH_FAILURE audit event with rate limiting.

    Only writes one audit log per token_hash_prefix per 5-minute window.
    """
    prefix = token_hash_prefix(token_or_prefix) if len(token_or_prefix) > 16 else token_or_prefix

    now = time.time()
    with _auth_failure_lock:
        last = _auth_failure_last_audit.get(prefix, 0)
        if now - last < _AUTH_FAILURE_RATE_LIMIT_SECONDS:
            return  # Rate-limited — skip audit write
        _auth_failure_last_audit[prefix] = now

    audit_logger.log(
        action=AuditAction.AGENT_AUTH_FAILURE.value,
        severity="warning",
        resource_type="agent_token",
        details={
            "token_hash_prefix": prefix,
            "reason": reason,
            "client_ip": client_ip,
        },
        success=False,
    )


def _audit_usage_report_failure(
    action: str,
    session_id: str,
    machine_id: str,
    client_ip: str,
    error_reason: str,
    details: dict | None = None,
    *,
    user_id: int | None = None,
    tenant_id: int | None = None,
    machine_verified: bool = False,
) -> None:
    """Record a usage report audit event for auth failure or binding mismatch.

    Args:
        action: AuditAction enum value (USAGE_REPORT_AUTH_FAILURE or USAGE_REPORT_BINDING_MISMATCH)
        session_id: The session_id from the request
        machine_id: The machine_id from the request
        client_ip: Client IP address
        error_reason: Reason for the failure
        details: Optional additional details to include
        user_id: User attributable to the authenticated machine, if known.
        tenant_id: Tenant attributable to the authenticated machine, if known.
        machine_verified: Whether machine_id names an authenticated database row.
    """
    if machine_verified and action == AuditAction.USAGE_REPORT_BINDING_MISMATCH.value:
        try:
            agent_mgr = get_remote_agent_manager()
            audit_key = f"usage-binding-audit:{machine_id}:{client_ip}"
            if not _check_usage_report_rate_limit(agent_mgr, audit_key, 5):
                return
        except Exception:
            logger.exception("Failed to apply usage binding audit rate limit")
            return

    audit_details = {
        "machine_id": machine_id,
        "client_ip": client_ip,
        "error_reason": error_reason,
    }
    if details:
        audit_details.update(details)

    audit_logger.log(
        action=action,
        severity="warning",
        resource_type="remote_machine" if machine_verified else "usage_report",
        resource_id=machine_id if machine_verified else None,
        details=audit_details,
        user_id=user_id,
        tenant_id=tenant_id,
        ip_address=client_ip,
        # A caller-controlled or cross-tenant session identifier must never be
        # persisted until the full machine/tenant/user binding succeeds.
        session_id=None,
        success=False,
        error_message=error_reason,
    )


def _validate_usage_report_binding(
    session_id: str, machine_id: str, client_ip: str
) -> tuple[dict | None, tuple[Any, Any] | None]:
    """Validate session-machine binding and tenant consistency for usage report.

    Args:
        session_id: The session_id from the request
        machine_id: The machine_id from the request
        client_ip: Client IP address for audit logging

    Returns:
        (session_data, None) on success.
        (None, error_response) on failure.
    """
    agent_mgr = get_remote_agent_manager()
    machine = agent_mgr.get_machine(machine_id)
    if not machine:
        _audit_usage_report_failure(
            AuditAction.USAGE_REPORT_BINDING_MISMATCH.value,
            session_id,
            machine_id,
            client_ip,
            "machine_not_found",
        )
        return None, (jsonify({"error": "Machine not found"}), 403)

    machine_tenant_id = machine.get("tenant_id")
    machine_owner_id = machine.get("created_by")

    # Query only inside the authenticated machine+tenant scope.  A missing,
    # cross-machine, and cross-tenant session all produce the same response and
    # audit shape, preventing session-existence and cross-tenant oracles.
    try:
        with agent_mgr.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                adapt_sql(
                    "SELECT session_id, remote_machine_id, tenant_id, user_id, status "
                    "FROM agent_sessions "
                    "WHERE session_id = ? AND remote_machine_id = ? AND tenant_id = ?"
                ),
                (session_id, machine_id, machine_tenant_id),
            )
            row = cursor.fetchone()
    except Exception as e:
        logger.error(f"Failed to query session {session_id[:8]}: {e}")
        return None, (jsonify({"error": "Internal error"}), 500)

    if not row:
        _audit_usage_report_failure(
            AuditAction.USAGE_REPORT_BINDING_MISMATCH.value,
            session_id,
            machine_id,
            client_ip,
            "binding_rejected",
            user_id=machine_owner_id,
            tenant_id=machine_tenant_id,
            machine_verified=True,
        )
        return None, (jsonify({"error": "Usage report binding rejected"}), 403)

    # Extract session data
    session_data = (
        dict(row)
        if isinstance(row, dict)
        else {
            "session_id": row[0],
            "remote_machine_id": row[1],
            "tenant_id": row[2],
            "user_id": row[3],
            "status": row[4],
        }
    )

    # A machine token is allowed to report only for a user assigned to that
    # machine. Registration always assigns created_by as an administrator; this
    # explicit lookup also supports sessions owned by delegated users.
    session_user_id = session_data.get("user_id")
    if not session_user_id or not agent_mgr.check_user_access(machine_id, session_user_id):
        _audit_usage_report_failure(
            AuditAction.USAGE_REPORT_BINDING_MISMATCH.value,
            session_id,
            machine_id,
            client_ip,
            "cross_user",
            user_id=machine_owner_id,
            tenant_id=machine_tenant_id,
            machine_verified=True,
        )
        return None, (jsonify({"error": "Usage report binding rejected"}), 403)

    return session_data, None


def _require_machine_admin(machine_id):
    """Check system admin or machine admin. Returns error or None."""
    if g.user.get("role") == "admin":
        return None
    mgr = get_remote_agent_manager()
    perm = mgr.get_user_permission(machine_id, g.user["id"])
    if perm != "admin":
        return jsonify({"error": "Machine admin permission required"}), 403
    return None


def _check_session_access(session_id):
    """Check session access: owner, system admin, or machine admin. Returns (session_status, error)."""
    from app.modules.workspace.session_access import check_session_access

    return check_session_access(session_id)


# ════════════════════════════════════════════
#  P2-1: Unified permission decorators
# ════════════════════════════════════════════


def _extract_machine_id():
    """Extract machine_id from request.

    Resolution order is path → query → body. Path wins when present so the
    machine the decorator authorizes is always the one the route actually
    operates on (routes read ``machine_id`` from ``view_args``). Letting a body
    field override the path would let a machine admin authorize against machine
    A while the route mutates machine B (cross-machine privilege escalation).
    """
    # URL path parameter is authoritative for routes like
    # /machines/<machine_id>/..., which already read it from the handler arg.
    machine_id = (request.view_args or {}).get("machine_id")
    # Fall back to query string, then JSON body (routes without a path arg).
    if not machine_id:
        machine_id = request.args.get("machine_id")
    if not machine_id:
        data = request.get_json(silent=True) or {}
        machine_id = data.get("machine_id")
    return machine_id


def _check_machine_access(machine_id):
    """Check if user has access to machine. Returns error or None."""
    if not machine_id:
        return jsonify({"error": "machine_id is required"}), 400
    if g.user.get("role") == "admin":
        return None
    mgr = get_remote_agent_manager()
    if not mgr.check_user_access(machine_id, g.user["id"]):
        return jsonify({"error": "Permission denied"}), 403
    return None


def machine_access_required(f):
    """Decorator: Check machine access permission (system admin or assigned user)."""
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        machine_id = _extract_machine_id()
        error = _check_machine_access(machine_id)
        if error:
            return error
        return f(*args, **kwargs)

    return decorated


def machine_admin_required(f):
    """Decorator: Check machine admin permission (system admin or machine admin)."""
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        machine_id = _extract_machine_id()
        error = _require_machine_admin(machine_id)
        if error:
            return error
        return f(*args, **kwargs)

    return decorated


# ==================== Machine Management (Admin) ====================


@remote_bp.route("/machines/register", methods=["POST"])
@admin_required
def register_machine():
    """
    Generate a registration token for a new machine.
    Admin only - the token is used by the agent to authenticate registration.
    """

    data = request.get_json() or {}
    agent_mgr = get_remote_agent_manager()

    # Get tenant_id from user or request
    tenant_id = data.get("tenant_id", 1)

    token = agent_mgr.create_registration_token(
        tenant_id=tenant_id,
        created_by=g.user["id"],
    )

    return jsonify(
        {
            "success": True,
            "registration_token": token,
            "message": "Use this token to register a remote agent. It is valid for one use.",
        }
    )


@remote_bp.route("/machines", methods=["GET"])
def list_machines():
    """List machines. Admin sees all, regular users see assigned machines."""

    agent_mgr = get_remote_agent_manager()

    if g.user.get("role") == "admin":
        machines = agent_mgr.list_machines()
    else:
        machines = agent_mgr.list_machines(user_id=g.user["id"])

    return jsonify(
        {
            "success": True,
            "machines": machines,
            "user_role": g.user.get("role"),  # P1-2: Explicit user role for frontend
        }
    )


@remote_bp.route("/machines/<machine_id>", methods=["GET"])
@machine_access_required
def get_machine(machine_id):
    """Get details and status of a specific machine."""
    # P2-1: Permission check moved to decorator @machine_access_required

    agent_mgr = get_remote_agent_manager()
    machine = agent_mgr.get_machine(machine_id)

    if not machine:
        return jsonify({"error": "Machine not found"}), 404

    return jsonify(
        {
            "success": True,
            "machine": machine,
        }
    )


@remote_bp.route("/machines/<machine_id>", methods=["DELETE"])
@admin_required
def deregister_machine(machine_id):
    """Deregister a remote machine. Admin only."""

    agent_mgr = get_remote_agent_manager()
    success = agent_mgr.deregister_machine(machine_id)

    if success:
        return jsonify({"success": True, "message": "Machine deregistered"})
    return jsonify({"error": "Machine not found"}), 404


@remote_bp.route("/machines/<machine_id>/assign", methods=["POST"])
@machine_admin_required
def assign_user(machine_id):
    """Assign a user to a machine. System admin or machine admin."""
    # P2-1: Permission check moved to decorator @machine_admin_required

    data = request.get_json() or {}
    user_id = data.get("user_id")
    permission = data.get("permission", "user")

    # Machine admins can only assign 'user' permission
    if g.user.get("role") != "admin":
        permission = "user"

    if not user_id:
        return jsonify({"error": "user_id is required"}), 400

    agent_mgr = get_remote_agent_manager()
    success = agent_mgr.assign_user(
        machine_id=machine_id,
        user_id=user_id,
        granted_by=g.user["id"],
        permission=permission,
    )

    if success:
        return jsonify({"success": True, "message": "User assigned to machine"})
    return jsonify({"error": "Failed to assign user"}), 400


@remote_bp.route("/machines/<machine_id>/assign/<int:user_id>", methods=["DELETE"])
@machine_admin_required
def revoke_user(machine_id, user_id):
    """Revoke a user's access to a machine. System admin or machine admin."""
    # P2-1: Permission check moved to decorator @machine_admin_required

    # Machine admins cannot revoke other admins
    if g.user.get("role") != "admin":
        mgr = get_remote_agent_manager()
        target_perm = mgr.get_user_permission(machine_id, user_id)
        if target_perm == "admin":
            return jsonify({"error": "Cannot revoke admin user"}), 403

    agent_mgr = get_remote_agent_manager()
    success = agent_mgr.revoke_user(machine_id, user_id)

    if success:
        return jsonify({"success": True, "message": "User access revoked"})
    return jsonify({"error": "Assignment not found"}), 404


@remote_bp.route("/machines/<machine_id>/token/rotate", methods=["POST"])
@admin_required
def rotate_machine_token(machine_id):
    """Rotate the agent token for a machine. System admin only.

    Revokes all existing tokens and issues a new one. If the existing
    tokens were already revoked (i.e., the machine was previously
    revoked and is being re-activated), this is logged as an unrevoke.
    """
    agent_mgr = get_remote_agent_manager()

    result = agent_mgr.rotate_agent_token(
        machine_id=machine_id,
        rotated_by=g.user["id"],
    )

    if result is None:
        return jsonify({"error": "Machine not found"}), 404

    new_token = result["new_token"]

    # AGENT_TOKEN_ROTATE audit event
    details = {
        "machine_id": machine_id,
        "rotated_by": g.user["id"],
    }
    if result.get("unrevoked"):
        details["unrevoke"] = True

    audit_logger.log_action(
        AuditAction.AGENT_TOKEN_ROTATE,
        user_id=g.user["id"],
        username=g.user.get("username"),
        severity="info",
        resource_type="remote_machine",
        resource_id=machine_id,
        details=details,
    )

    # Push rotate_token command to agent so it updates its local config.
    # send_command() only enqueues — check if agent is online to determine
    # whether the new token will be delivered immediately or needs manual update.
    agent_mgr.send_command(
        machine_id,
        {
            "command": "rotate_token",
            "new_token": new_token,
        },
    )
    is_online = agent_mgr.is_connected(machine_id)
    msg = (
        "Agent token rotated. The new token has been pushed to the agent."
        if is_online
        else "Agent token rotated. Agent is offline — save the new token and"
        " manually update the agent config."
    )

    return jsonify(
        {
            "success": True,
            "agent_token": new_token,
            "message": msg,
        }
    )


@remote_bp.route("/machines/<machine_id>/token/revoke", methods=["POST"])
@admin_required
def revoke_machine_token(machine_id):
    """Revoke all agent tokens for a machine. System admin only."""
    agent_mgr = get_remote_agent_manager()

    success = agent_mgr.revoke_agent_token(
        machine_id=machine_id,
        revoked_by=g.user["id"],
    )

    if success:
        # AGENT_TOKEN_REVOKE audit event
        audit_logger.log_action(
            AuditAction.AGENT_TOKEN_REVOKE,
            user_id=g.user["id"],
            username=g.user.get("username"),
            severity="warning",
            resource_type="remote_machine",
            resource_id=machine_id,
            details={
                "machine_id": machine_id,
                "revoked_by": g.user["id"],
            },
        )
        return jsonify({"success": True, "message": "Agent token revoked"})
    return jsonify({"error": "No active tokens found for this machine"}), 404


# ==================== Machine User Assignments ====================


@remote_bp.route("/machines/<machine_id>/users", methods=["GET"])
@machine_admin_required
def get_machine_users(machine_id):
    """Get list of users assigned to a machine. System admin or machine admin."""
    # P2-1: Permission check moved to decorator @machine_admin_required
    agent_mgr = get_remote_agent_manager()
    assignments = agent_mgr.get_machine_assignments(machine_id)

    return jsonify(
        {
            "success": True,
            "users": assignments,
        }
    )


# ==================== Session Management (User) ====================


@remote_bp.route("/machines/available", methods=["GET"])
def get_available_machines():
    """Get machines available to the current user."""

    agent_mgr = get_remote_agent_manager()
    machines = agent_mgr.get_available_machines(g.user["id"])

    # Filter out offline machines (show online/idle/busy)
    available = [m for m in machines if m.get("status") != "offline"]

    return jsonify(
        {
            "success": True,
            "machines": available,
        }
    )


@remote_bp.route("/sessions", methods=["POST"])
@machine_access_required
def create_remote_session():
    """Create a new remote session on a selected machine."""

    data = request.get_json() or {}
    machine_id = data.get("machine_id")
    project_path = data.get("project_path")
    model = data.get("model")
    cli_tool = data.get("cli_tool", "qwen-code-cli")
    title = data.get("title", "")
    permission_mode = data.get("permission_mode")
    ha_pool_token = data.get("ha_pool_token")

    if not machine_id:  # decorator already guards; narrows type for mypy
        return jsonify({"error": "machine_id is required"}), 400
    if not project_path:
        return jsonify({"error": "project_path is required"}), 400

    # P2-1: Permission check moved to decorator @machine_access_required
    session_mgr = get_remote_session_manager()
    result = session_mgr.create_remote_session(
        user_id=g.user["id"],
        machine_id=machine_id,
        project_path=project_path,
        model=model,
        cli_tool=cli_tool,
        title=title,
        permission_mode=permission_mode,
        ha_pool_token=ha_pool_token,
    )

    if result:
        return jsonify({"success": True, "session": result})
    return (
        jsonify(
            {"error": "Failed to create remote session. Check machine availability and access."}
        ),
        400,
    )


@remote_bp.route("/sessions/<session_id>", methods=["GET"])
def get_remote_session(session_id):
    """Get remote session status and output."""

    result, access_error = _check_session_access(session_id)
    if access_error:
        return access_error

    if result:
        # Return explicit error for ended sessions so frontend can handle properly
        status = result.get("status")
        if status in ("completed", "stopped", "error"):
            status_messages = {
                "completed": "Remote session has ended",
                "stopped": "Remote session has been stopped",
                "error": "Remote session encountered an error",
            }
            return jsonify(
                {
                    "success": False,
                    "session": result,
                    "error": status_messages.get(status, "Remote session has ended"),
                }
            )
        return jsonify({"success": True, "session": result})
    return jsonify({"error": "Session not found"}), 404


@remote_bp.route("/sessions/<session_id>/chat", methods=["POST"])
def send_remote_message(session_id):
    """Send a message to a remote session."""

    _, access_error = _check_session_access(session_id)
    if access_error:
        return access_error

    data = request.get_json() or {}
    content = data.get("content", "")
    permission_mode = data.get("permission_mode")

    if not content:
        return jsonify({"error": "content is required"}), 400

    session_mgr = get_remote_session_manager()

    # If permission_mode changed, send update command to agent
    if permission_mode:
        session_mgr.update_permission_mode(session_id, permission_mode)

    success = session_mgr.send_message(
        session_id=session_id,
        content=content,
        user_id=g.user["id"],
    )

    if success:
        return jsonify({"success": True})
    return (
        jsonify({"error": "Failed to send message. Session may not be active.", "reconnect": True}),
        400,
    )


@remote_bp.route("/sessions/<session_id>/model", methods=["PUT"])
def update_remote_session_model(session_id):
    """Switch the model of an active remote session."""

    _, access_error = _check_session_access(session_id)
    if access_error:
        return access_error

    data = request.get_json() or {}
    model = data.get("model")
    if not model:
        return jsonify({"error": "model is required"}), 400

    session_mgr = get_remote_session_manager()
    success = session_mgr.update_model(session_id, model)

    if success:
        return jsonify({"success": True})
    return jsonify({"error": "Failed to update model"}), 400


@remote_bp.route("/sessions/<session_id>/abort", methods=["POST"])
def abort_remote_request(session_id):
    """Abort the current in-progress request without stopping the session."""

    _, access_error = _check_session_access(session_id)
    if access_error:
        return access_error

    payload = request.get_json(silent=True) or {}
    reason = payload.get("reason", "user")

    session_mgr = get_remote_session_manager()
    success = session_mgr.abort_request(session_id, reason=reason)

    if success:
        return jsonify({"success": True})
    return jsonify({"error": "Failed to abort request"}), 400


@remote_bp.route("/sessions/<session_id>/stop", methods=["POST"])
def stop_remote_session(session_id):
    """Stop a remote session."""

    _, access_error = _check_session_access(session_id)
    if access_error:
        return access_error

    session_mgr = get_remote_session_manager()
    success = session_mgr.stop_session(session_id)

    if success:
        return jsonify({"success": True})
    return jsonify({"error": "Failed to stop session"}), 400


@remote_bp.route("/sessions/<session_id>/pause", methods=["POST"])
def pause_remote_session(session_id):
    """Pause a remote session."""

    _, access_error = _check_session_access(session_id)
    if access_error:
        return access_error

    session_mgr = get_remote_session_manager()
    success = session_mgr.pause_session(session_id)

    if success:
        return jsonify({"success": True})
    return jsonify({"error": "Failed to pause session"}), 400


@remote_bp.route("/sessions/<session_id>/resume", methods=["POST"])
def resume_remote_session(session_id):
    """Resume a paused remote session."""

    _, access_error = _check_session_access(session_id)
    if access_error:
        return access_error

    session_mgr = get_remote_session_manager()
    success = session_mgr.resume_session(session_id)

    if success:
        return jsonify({"success": True})
    return jsonify({"error": "Failed to resume session"}), 400


@remote_bp.route("/sessions/<session_id>/permission", methods=["POST"])
def send_permission_response(session_id):
    """
    Send a permission response (approve/deny) from the frontend to the
    remote agent.

    Expected JSON body:
        {
            "request_id": "...",
            "behavior": "allow" | "deny",
            "tool_name": "run_shell_command",
            "message": "optional deny reason"
        }
    """

    session_info, access_error = _check_session_access(session_id)
    if access_error:
        return access_error

    data = request.get_json() or {}
    request_id = data.get("request_id")
    behavior = data.get("behavior", "deny")
    tool_name = data.get("tool_name", "")
    message = data.get("message")

    # Route through the session manager so the durable approval record and the
    # permission_answered event are persisted alongside the command, with the
    # operator identity (decided_by) captured from the auth state.
    session_mgr = get_remote_session_manager()
    session_mgr.respond_to_permission(
        session_id,
        request_id,
        behavior,
        tool_name,
        message,
        decided_by=g.user.get("id") if g.get("user") else None,
        decided_by_name=g.user.get("username") if g.get("user") else None,
    )

    return jsonify({"success": True})


@remote_bp.route("/sessions/<session_id>/interaction", methods=["POST"])
def send_interaction_response(session_id):
    """
    Send an interaction response from the frontend to the remote agent.

    This handles responses for interaction/requestUserInput and
    interaction/requestPermission server requests that were forwarded
    to the frontend for user decision.

    Expected JSON body:
        {
            "msg_id": "...",
            "response": {
                "action": "answer" | "decline",
                "response": "user input"  // for answer
            }
            // OR
            "response": {
                "decision": "allow" | "deny",
                "reason": "optional deny reason"
            }
        }
    """
    session_info, access_error = _check_session_access(session_id)
    if access_error:
        return access_error

    data = request.get_json() or {}
    msg_id = data.get("msg_id")
    response = data.get("response", {})

    if not msg_id:
        return jsonify({"success": False, "error": "Missing msg_id"}), 400

    session_mgr = get_remote_session_manager()
    success = session_mgr.respond_to_interaction(
        session_id,
        msg_id,
        response,
        decided_by=g.user.get("id") if g.get("user") else None,
        decided_by_name=g.user.get("username") if g.get("user") else None,
    )

    return jsonify({"success": success})


@remote_bp.route("/sessions/<session_id>/stream")
def stream_session_output(session_id):
    """SSE: real-time stream of remote session output, formatted as claude_json."""

    _, access_error = _check_session_access(session_id)
    if access_error:
        return access_error

    agent_mgr = get_remote_agent_manager()

    def generate():
        try:
            yield ": connected\n\n"
            # Resume from last delivered index (Issue #1511)
            # This prevents replaying all buffered output on SSE reconnect
            last_index = agent_mgr.get_last_delivered(session_id)
            idle_count = 0
            while True:
                new_output = agent_mgr.get_buffered_output(session_id, after_index=last_index)
                if new_output:
                    idle_count = 0
                    for entry in new_output:
                        data = entry.get("data", "").strip()
                        stream = entry.get("stream", "stdout")
                        if not data:
                            last_index += 1
                            continue
                        if stream == "stderr":
                            # Skip harmless Node.js warnings to avoid triggering
                            # the frontend's "reconnect" error state.
                            if any(
                                pat in data
                                for pat in (
                                    "trace-warnings",
                                    "ExperimentalWarning",
                                    "DeprecationWarning",
                                )
                            ):
                                last_index += 1
                                continue
                            # Forward genuine stderr as error events so the frontend
                            # can display CLI errors instead of silently hanging.
                            yield f"data: {json.dumps({'type': 'error', 'data': data})}\n\n"
                            last_index += 1
                            continue
                        if stream == "request_state":
                            try:
                                parsed = json.loads(data)
                                yield f"data: {json.dumps({'type': 'request_state', 'data': parsed})}\n\n"
                            except (json.JSONDecodeError, TypeError):
                                logger.warning(
                                    "Failed to parse request_state payload for session %s: %r",
                                    session_id[:8],
                                    data,
                                )
                            last_index += 1
                            continue
                        try:
                            parsed = json.loads(data)
                            if stream == "permission":
                                # Forward permission requests with a distinct type
                                yield f"data: {json.dumps({'type': 'permission_request', 'data': parsed})}\n\n"
                            else:
                                # Wrap as claude_json to match local streaming format
                                yield f"data: {json.dumps({'type': 'claude_json', 'data': parsed})}\n\n"
                            last_index += 1
                        except (json.JSONDecodeError, TypeError):
                            last_index += 1
                    # Update last_delivered after processing all entries in this batch
                    agent_mgr.set_last_delivered(session_id, last_index)
                else:
                    idle_count += 1
                    if (
                        idle_count >= 50
                    ):  # ~10 seconds (50 * 0.2s), aligned with KEEPALIVE_INTERVAL_MS
                        # Emit as a `data:` event (not an SSE comment line) so the
                        # browser fires onmessage and the frontend stall detector
                        # can reset on every keepalive. Comment lines (`:` prefix)
                        # are silently dropped by the browser and never reach JS.
                        yield f"data: {json.dumps({'type': 'keepalive'})}\n\n"
                        idle_count = 0

                # Check if session ended (in-memory, no DB query)
                if agent_mgr.is_session_ended(session_id):
                    break
                time.sleep(0.2)

            yield "data: [DONE]\n\n"
            # Session completed — clean up last_delivered (Issue #1511)
            agent_mgr.clear_last_delivered(session_id)
        except GeneratorExit:
            # Save delivered progress before disconnect (Issue #1511)
            # Without this, data sent during the current batch but not yet recorded
            # would be replayed on reconnect, causing duplicate messages.
            agent_mgr.set_last_delivered(session_id, last_index)
            logger.info(
                "Client disconnected during SSE for session %s, request continues in background",
                session_id[:8],
            )
            # Don't abort_request — CLI continues running, data accumulates in buffer
            # User can reconnect via SSE and receive buffered output

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ==================== Agent Installation ====================

AGENT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "remote-agent"
)


@remote_bp.route("/agent/install.sh", methods=["GET"])
def agent_install_script():
    """Serve the agent installation shell script (Linux/macOS)."""
    install_sh = os.path.join(AGENT_DIR, "install.sh")
    if not os.path.isfile(install_sh):
        return jsonify({"error": "install.sh not found"}), 404
    return Response(open(install_sh).read(), mimetype="text/x-shellscript")


@remote_bp.route("/agent/install.ps1", methods=["GET"])
def agent_install_script_windows():
    """Serve the agent installation PowerShell script (Windows)."""
    install_ps1 = os.path.join(AGENT_DIR, "install.ps1")
    if not os.path.isfile(install_ps1):
        return jsonify({"error": "install.ps1 not found"}), 404
    return Response(open(install_ps1).read(), mimetype="text/plain")


@remote_bp.route("/agent/uninstall.sh", methods=["GET"])
def agent_uninstall_script():
    """Serve the agent uninstallation shell script (Linux/macOS)."""
    uninstall_sh = os.path.join(AGENT_DIR, "uninstall.sh")
    if not os.path.isfile(uninstall_sh):
        return jsonify({"error": "uninstall.sh not found"}), 404
    return Response(open(uninstall_sh).read(), mimetype="text/x-shellscript")


@remote_bp.route("/agent/uninstall.ps1", methods=["GET"])
def agent_uninstall_script_windows():
    """Serve the agent uninstallation PowerShell script (Windows)."""
    uninstall_ps1 = os.path.join(AGENT_DIR, "uninstall.ps1")
    if not os.path.isfile(uninstall_ps1):
        return jsonify({"error": "uninstall.ps1 not found"}), 404
    return Response(open(uninstall_ps1).read(), mimetype="text/plain")


@remote_bp.route("/agent/files/<path:filename>", methods=["GET"])
def agent_files(filename):
    """Serve agent source files for download during installation."""
    filepath = os.path.join(AGENT_DIR, filename)
    # Security: prevent path traversal
    real_path = os.path.realpath(filepath)
    real_agent_dir = os.path.realpath(AGENT_DIR)
    if not real_path.startswith(real_agent_dir):
        return jsonify({"error": "Invalid path"}), 403
    if not os.path.isfile(real_path):
        return jsonify({"error": f"{filename} not found"}), 404
    return Response(open(real_path).read(), mimetype="text/plain")


# ==================== Agent Communication ====================


def validate_ip(ip_str: str) -> bool:
    """验证 IP 地址格式是否有效。"""
    if not isinstance(ip_str, str):
        return False
    try:
        ipaddress.ip_address(ip_str)
        return True
    except ValueError:
        return False


def get_client_ip_from_request() -> str:
    """从 HTTP Headers 或 remote_addr 获取客户端 IP（作为回退）。"""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return str(forwarded_for).split(",")[0].strip()

    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return str(real_ip).strip()

    return request.remote_addr or "127.0.0.1"


@remote_bp.route("/agent/register", methods=["POST"])
def agent_register():
    """
    Register a remote agent using a registration token.
    Called by the agent during initial setup.
    """
    data = request.get_json() or {}
    registration_token = data.get("registration_token")
    machine_id = data.get("machine_id", str(uuid.uuid4()))
    machine_name = data.get("machine_name")
    hostname = data.get("hostname")
    os_type = data.get("os_type")
    os_version = data.get("os_version")
    capabilities = data.get("capabilities")
    agent_version = data.get("agent_version")

    if not registration_token:
        return jsonify({"error": "registration_token is required"}), 400
    if not machine_name:
        return jsonify({"error": "machine_name is required"}), 400

    agent_mgr = get_remote_agent_manager()

    # 优先使用 Agent 上报的 IP，否则从请求获取
    agent_reported_ip = data.get("ip_address")
    if agent_reported_ip and agent_reported_ip != "127.0.0.1" and validate_ip(agent_reported_ip):
        ip_address = agent_reported_ip
    else:
        ip_address = get_client_ip_from_request()

    result = agent_mgr.register_machine(
        registration_token=registration_token,
        machine_id=machine_id,
        machine_name=machine_name,
        hostname=hostname,
        os_type=os_type,
        os_version=os_version,
        capabilities=capabilities,
        agent_version=agent_version,
        ip_address=ip_address,
    )

    if result:
        if result.get("error") == "hostname_conflict":
            return jsonify({"error": result["message"]}), 409
        # AGENT_REGISTER audit event
        audit_logger.log_action(
            AuditAction.AGENT_REGISTER,
            severity="info",
            resource_type="remote_machine",
            resource_id=machine_id,
            details={
                "machine_id": machine_id,
                "machine_name": machine_name,
                "hostname": hostname,
                "tenant_id": result.get("tenant_id"),
            },
            ip_address=ip_address,
        )
        return jsonify({"success": True, "machine": result})
    return jsonify({"error": "Invalid or expired registration token"}), 401


@remote_bp.route("/agent/ws", methods=["GET"])
def agent_websocket():
    """Deprecated. Use HTTP polling via /api/remote/agent/message instead."""
    return (
        jsonify(
            {
                "error": "WebSocket no longer supported",
                "endpoint": "/api/remote/agent/message",
            }
        ),
        410,
    )


@remote_bp.route("/agent/message", methods=["POST"])
def agent_message():
    """
    HTTP fallback for agent communication when WebSocket is not available.

    Agents POST messages here (heartbeat, output, status, usage).
    Server returns any pending commands in the response.
    """
    data = request.get_json() or {}
    msg_type = data.get("type")

    # Debug: log all non-poll agent messages (filter sensitive fields)
    if msg_type not in ("poll", "heartbeat"):
        import sys

        status = data.get("status", "")
        stream = data.get("stream", "")
        # For vscode_status, filter out sensitive fields before logging
        if msg_type == "vscode_status":
            debug_data = {k: v for k, v in data.items() if k not in ("cs_password", "token")}
            d = str(debug_data)[:200]
        else:
            d = (data.get("data") or "")[:200]
        print(
            f"AGENT-DEBUG type={msg_type} sid={(data.get('session_id') or '')[:8]} status={status} stream={stream} data={d}",
            file=sys.stderr,
            flush=True,
        )

    if not msg_type:
        return jsonify({"error": "type is required"}), 400

    agent_mgr = get_remote_agent_manager()
    machine_id = data.get("machine_id")

    if not machine_id:
        return jsonify({"error": "machine_id is required"}), 400

    # ===== Bearer token authentication =====
    # All message types except "register" require a valid Bearer token
    # (or legacy mode fallback for pre-existing machines).
    if msg_type != "register":
        # First, check if the machine exists
        machine = agent_mgr.get_machine(machine_id)
        if not machine:
            return jsonify({"error": "Unknown machine_id"}), 404

        # Check for legacy fallback (no Bearer header, machine in legacy_mode)
        is_legacy, legacy_error = _check_legacy_fallback(machine_id)
        if legacy_error:
            # Not legacy and no Bearer → try extracting for audit
            auth_header = request.headers.get("Authorization", "")
            client_ip = get_client_ip_from_request()
            if auth_header.startswith("Bearer ") and (
                msg_type != "usage_report" or _should_audit_usage_auth_failure(agent_mgr, client_ip)
            ):
                failed_token = auth_header[7:]
                _audit_auth_failure(failed_token, "invalid_or_revoked", client_ip)
            return legacy_error

        if not is_legacy:
            # Normal Bearer token validation
            bearer_token, bearer_error = _validate_agent_bearer(machine_id)
            if bearer_error:
                client_ip = get_client_ip_from_request()
                auth_header = request.headers.get("Authorization", "")
                if auth_header.startswith("Bearer ") and (
                    msg_type != "usage_report"
                    or _should_audit_usage_auth_failure(agent_mgr, client_ip)
                ):
                    _audit_auth_failure(
                        auth_header[7:],
                        bearer_error[0].get_json().get("error", "unknown"),
                        client_ip,
                    )
                return bearer_error

        # Check for offline→online reconnect (AGENT_RECONNECT audit)
        if machine.get("status") == "offline":
            g._did_reconnect = True
            g._previous_status = "offline"
        else:
            g._did_reconnect = False
    else:
        g._did_reconnect = False

    if msg_type == "register":
        # Validate machine exists in DB before allowing registration
        machine = agent_mgr.get_machine(machine_id)
        if not machine:
            return jsonify({"error": "Unknown machine_id"}), 404
        # Agent re-registering on reconnect
        agent_mgr.register_connection(machine_id, None)
        capabilities = data.get("capabilities", {})
        if capabilities:
            agent_mgr.update_capabilities(machine_id, capabilities)
        # Update IP address if agent reports a valid one (not 127.0.0.1)
        agent_reported_ip = data.get("ip_address")
        if (
            agent_reported_ip
            and agent_reported_ip != "127.0.0.1"
            and validate_ip(agent_reported_ip)
        ):
            agent_mgr.update_machine_ip(machine_id, agent_reported_ip)
        pending = agent_mgr.get_pending_commands(machine_id)
        return jsonify({"success": True, "type": "register_ack", "pending_commands": pending})

    elif msg_type == "heartbeat":
        status = data.get("status", "idle")
        active_sessions = data.get("active_sessions", 0)
        capabilities = data.get("capabilities", {})
        agent_mgr.process_heartbeat(machine_id, status, active_sessions, capabilities=capabilities)
        # AGENT_RECONNECT audit: if machine was offline, log the reconnection
        if getattr(g, "_did_reconnect", False):
            audit_logger.log_action(
                AuditAction.AGENT_RECONNECT,
                severity="info",
                resource_type="remote_machine",
                resource_id=machine_id,
                details={
                    "machine_id": machine_id,
                    "previous_status": getattr(g, "_previous_status", "unknown"),
                },
            )
        pending = agent_mgr.get_pending_commands(machine_id)
        return jsonify({"success": True, "type": "heartbeat_ack", "pending_commands": pending})

    elif msg_type == "poll":
        # Lightweight poll — no DB write, just return pending commands
        agent_mgr.ensure_agent_tracked(machine_id)
        pending = agent_mgr.get_pending_commands(machine_id)
        return jsonify({"success": True, "type": "poll_ack", "pending_commands": pending})

    elif msg_type == "session_output":
        session_id = data.get("session_id")
        output_data = data.get("data", "")
        stream = data.get("stream", "stdout")
        is_complete = data.get("is_complete", False)

        if stream == "stderr" and output_data:
            logger.info("Agent stderr [%s]: %s", (session_id or "")[:8], output_data[:200])

        if session_id:
            session_mgr = get_remote_session_manager()
            session_mgr.process_session_output(
                session_id=session_id,
                data=output_data,
                stream=stream,
                is_complete=is_complete,
            )

        return jsonify({"success": True})

    elif msg_type == "session_status":
        session_id = data.get("session_id")
        status = data.get("status")
        pid = data.get("pid")

        logger.info("Agent session_status [%s]: status=%s", (session_id or "")[:8], status)

        if session_id and status:
            session_mgr = get_remote_session_manager()
            session_mgr.process_session_status_update(
                session_id=session_id,
                status=status,
                pid=pid,
            )

        return jsonify({"success": True})

    elif msg_type == "usage_report":
        return _process_authenticated_usage_report(
            data,
            machine_id,
            get_client_ip_from_request(),
        )

    elif msg_type == "permission_request":
        session_id = data.get("session_id")
        control_request = data.get("control_request", {})

        if session_id:
            session_mgr = get_remote_session_manager()
            session_mgr.process_permission_request(
                session_id=session_id,
                control_request=control_request,
            )

        pending = agent_mgr.get_pending_commands(machine_id)
        return jsonify({"success": True, "pending_commands": pending})

    elif msg_type == "request_state":
        session_id = data.get("session_id")
        state = data.get("state")
        reason = data.get("reason", "user")
        message = data.get("message")

        if session_id and state:
            session_mgr = get_remote_session_manager()
            session_mgr.process_request_state(
                session_id=session_id,
                state=state,
                reason=reason,
                message=message,
            )

        pending = agent_mgr.get_pending_commands(machine_id)
        return jsonify({"success": True, "pending_commands": pending})

    elif msg_type == "terminal_status":
        # Agent reports terminal server status
        terminal_id = data.get("terminal_id", "")
        status = data.get("status", "")
        ws_url = data.get("ws_url", "")
        term_token = data.get("token", "")
        error = data.get("error", "")

        logger.info(
            "Terminal status [%s]: status=%s ws_url=%s",
            terminal_id[:8],
            status,
            ws_url,
        )

        # Store terminal info in agent manager for later retrieval
        agent_mgr.store_terminal_info(
            machine_id,
            terminal_id,
            {
                "status": status,
                "ws_url": ws_url,
                "token": term_token,
                "error": error,
            },
        )

        # Update agent_sessions status
        from app.modules.workspace.session_manager import get_session_manager

        sm = get_session_manager()
        if terminal_id and status:
            if status == "stopped":
                sm.complete_session(terminal_id)
                logger.info("Terminal session %s marked as completed", terminal_id[:8])
            elif status == "running":
                sm.update_session_fields(terminal_id, {"status": "active"})
            elif status == "error":
                sm.update_session_fields(terminal_id, {"status": "error"})
                logger.warning("Terminal session %s error: %s", terminal_id[:8], error)

        return jsonify({"success": True})

    elif msg_type == "browse_result":
        # Agent reports directory browse result
        request_id = data.get("request_id", "")
        success = data.get("success", False)
        result = data.get("result")
        error = data.get("error")

        if request_id:
            agent_mgr.store_browse_result(
                request_id,
                {
                    "success": success,
                    "result": result,
                    "error": error,
                },
            )
            logger.info("Stored browse result for request %s", request_id[:8])
        else:
            logger.warning("browse_result received without request_id")

        return jsonify({"success": True})

    elif msg_type == "git_result":
        # Agent reports git command result (git_status, git_diff, git_file)
        request_id = data.get("request_id", "")
        success = data.get("success", False)
        result = data.get("result")
        error = data.get("error")

        if request_id:
            agent_mgr.store_browse_result(
                request_id,
                {
                    "success": success,
                    "result": result,
                    "error": error,
                },
            )
            logger.info("Stored git result for request %s", request_id[:8])
        else:
            logger.warning("git_result received without request_id")

        return jsonify({"success": True})

    elif msg_type == "vscode_status":
        # Agent reports VSCode (code-server) status
        from app.modules.workspace.vscode_store import vscode_info_store

        vscode_id = data.get("vscode_id", "")
        status = data.get("status", "")
        http_url = data.get("http_url", "")
        vscode_token = data.get("token", "")
        cs_password = data.get("cs_password", "")  # code-server's own password
        error = data.get("error", "")

        machine_id_for_vs = data.get("machine_id", "")

        if not vscode_id:
            logger.warning("vscode_status received without vscode_id")
            return jsonify({"success": True})

        if status == "running" and http_url:
            import secrets as _secrets

            browser_token = _secrets.token_hex(32)
            vscode_info_store.put(
                machine_id_for_vs,
                vscode_id,
                {
                    "status": "running",
                    "original_http_url": http_url,
                    "original_token": vscode_token,
                    "cs_password": cs_password,  # Store code-server password for proxy
                    "token": browser_token,
                    "machine_id": machine_id_for_vs,
                    "project_path": data.get("project_path", ""),
                },
            )
            logger.info("VSCode %s running on %s", vscode_id[:8], http_url)
        elif status == "stopped":
            vscode_info_store.pop(machine_id_for_vs, vscode_id)
            logger.info("VSCode %s stopped", vscode_id[:8])
        elif status == "error":
            vscode_info_store.put(
                machine_id_for_vs,
                vscode_id,
                {"status": "error", "error": error, "machine_id": machine_id_for_vs},
            )
            logger.warning("VSCode %s error: %s", vscode_id[:8], error)
        elif status == "not_found":
            vscode_info_store.pop(machine_id_for_vs, vscode_id)

        return jsonify({"success": True})

    elif msg_type == "session_sync":
        # Agent reports Claude Code session data from web terminal
        claude_session_id = data.get("session_id", "")
        terminal_id = data.get("terminal_id", "")
        tool_name = data.get("tool_name", "claude-code")
        model = data.get("model")
        project_path = data.get("project_path")
        message_count = data.get("message_count", 0)
        total_input_tokens = data.get("total_input_tokens", 0)
        total_output_tokens = data.get("total_output_tokens", 0)
        messages = data.get("messages", [])
        message_source = data.get("source") or "web_terminal"

        # Resolve effective session_id: prefer terminal_id (shown in sidebar)
        # over claude_session_id (internal Claude Code JSONL UUID).
        # Also capture user_id from the terminal session (authenticated web UI)
        # to avoid a redundant get_session() call below.
        sync_session_mgr = get_remote_session_manager()._session_manager
        terminal_session = None
        if terminal_id:
            terminal_session = sync_session_mgr.get_session(terminal_id)
            if terminal_session:
                session_id = terminal_id
            else:
                session_id = claude_session_id
                logger.warning(
                    "session_sync: terminal_id=%s not found, " "fall back to claude_session_id=%s",
                    terminal_id[:8],
                    claude_session_id[:8],
                )
        else:
            session_id = claude_session_id

        logger.info(
            "Session sync [%s] terminal=[%s]: tool=%s msgs=%d tokens=%d/%d",
            claude_session_id[:8],
            terminal_id[:8] if terminal_id else "none",
            tool_name,
            message_count,
            total_input_tokens,
            total_output_tokens,
        )

        # Resolve user_id: terminal session (authenticated) > machine assignment
        sync_user_id = None
        if terminal_session and terminal_session.user_id:
            sync_user_id = terminal_session.user_id
        if not sync_user_id:
            # Verify machine has an active agent connection before trusting
            # machine_id from the unauthenticated POST body.
            # agent_mgr is already available from L871 (top of agent_message).
            if not agent_mgr.is_connected(machine_id):
                logger.warning(
                    "session_sync: machine %s is not connected, skipping user_id resolution",
                    machine_id[:8],
                )
            else:
                try:
                    from app.repositories.database import adapt_sql, get_db_connection

                    with get_db_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute(
                            adapt_sql(
                                "SELECT user_id FROM machine_assignments "
                                "WHERE machine_id = ? AND user_id IS NOT NULL "
                                "ORDER BY granted_at DESC LIMIT 1"
                            ),
                            (machine_id,),
                        )
                        row = cursor.fetchone()
                        if row:
                            sync_user_id = row["user_id"]
                except Exception as e:
                    logger.warning(
                        "Failed to resolve user_id from machine_assignments for machine=%s: %s",
                        machine_id[:8],
                        e,
                    )

        try:
            # Upsert the session record
            existing = sync_session_mgr.get_session(session_id)
            if not existing:
                sync_session_mgr.create_session(
                    session_id=session_id,
                    tool_name=tool_name,
                    project_path=project_path or "",
                    model=model,
                    host_name=machine_id[:8],
                    user_id=sync_user_id,
                    context={
                        "workspace_type": "terminal",
                        "remote_machine_id": machine_id,
                    },
                )
            else:
                # Update model/project_path/user_id if missing on existing session
                updates = {}
                if model and not existing.model:
                    updates["model"] = model
                if project_path and not existing.project_path:
                    updates["project_path"] = project_path
                if sync_user_id and not existing.user_id:
                    updates["user_id"] = sync_user_id
                if updates:
                    sync_session_mgr.update_session_fields(session_id, updates)

            # Fetch existing message uuids for dedup and mirror to daily_messages
            try:
                from app.repositories.database import adapt_sql, get_db_connection

                # Dedup: collect existing message uuids via lightweight query
                existing_uuids: set[str] = set()
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        adapt_sql("SELECT metadata FROM session_messages WHERE session_id = ?"),
                        (session_id,),
                    )
                    for row in cursor.fetchall():
                        meta_raw = row["metadata"]
                        if isinstance(meta_raw, str):
                            try:
                                meta = json.loads(meta_raw)
                            except (json.JSONDecodeError, TypeError):
                                continue
                        elif isinstance(meta_raw, dict):
                            meta = meta_raw
                        else:
                            continue
                        em_uuid = meta.get("uuid", "")
                        if em_uuid:
                            existing_uuids.add(em_uuid)

                # Mirror messages to daily_messages for ConversationHistory visibility
                synced_message_delta = 0
                for msg in messages:
                    role = msg.get("role", "")
                    content = msg.get("content", "")
                    timestamp = msg.get("timestamp")
                    msg_model = msg.get("model") or model
                    msg_uuid = msg.get("uuid", "")
                    content_blocks = msg.get("content_blocks")
                    usage = msg.get("usage", {})

                    if not content or len(content) > MAX_RAW_CONTENT_LENGTH:
                        continue

                    # Normalize the role so variant tool-result spellings
                    # (toolResult / tool_result) collapse to the canonical
                    # "tool" before being written to daily_messages. This path
                    # writes directly and bypasses message_repo.save_message.
                    from app.utils.roles import normalize_message_role

                    role = normalize_message_role(role)

                    input_tokens = usage.get("input_tokens", 0) if isinstance(usage, dict) else 0
                    output_tokens = usage.get("output_tokens", 0) if isinstance(usage, dict) else 0
                    tokens_used = input_tokens + output_tokens

                    # Use uuid for dedup if available, fallback to timestamp-based id
                    message_id = msg_uuid or f"{session_id}-{timestamp}"

                    # Skip if message already synced (dedup by uuid)
                    if msg_uuid and msg_uuid in existing_uuids:
                        continue

                    # 1. Write to session_messages (dual-write with daily_messages)
                    try:
                        metadata = {"source": message_source}
                        if msg_uuid:
                            metadata["uuid"] = msg_uuid
                        if content_blocks:
                            metadata["content_blocks"] = content_blocks
                        if input_tokens or output_tokens:
                            metadata["input_tokens"] = input_tokens
                            metadata["output_tokens"] = output_tokens

                        stored = sync_session_mgr.append_transcript_message(
                            session_id=session_id,
                            role=role,
                            content=content[:MAX_MESSAGE_LENGTH],
                            tokens_used=tokens_used,
                            model=msg_model,
                            metadata=metadata,
                            timestamp=timestamp,
                            source="remote_sync",
                            external_message_id=msg_uuid or message_id,
                        )
                        if getattr(stored, "_was_inserted", False):
                            synced_message_delta += 1
                    except Exception as e:
                        logger.debug("Failed to add session_message: %s", e)

                    # 2. Write to daily_messages with enriched fields
                    date_str = (timestamp or time.strftime("%Y-%m-%d"))[:10]
                    full_entry_json = json.dumps(
                        {
                            "session_id": session_id,
                            "role": role,
                            "content": content[:MAX_MESSAGE_LENGTH],
                            "content_blocks": content_blocks,
                        },
                        ensure_ascii=False,
                    )

                    try:
                        current_session = sync_session_mgr.get_session(session_id)
                        # Issue #1852: Get tenant_id from session or infer from user_id
                        session_tenant_id = getattr(current_session, "tenant_id", None)
                        if session_tenant_id is None:
                            session_user_id = getattr(current_session, "user_id", None)
                            if session_user_id:
                                # Infer tenant_id from user_id
                                from app.repositories.user_repo import UserRepository

                                user_row = UserRepository().get_user_by_id(session_user_id)
                                if user_row and user_row.get("tenant_id"):
                                    session_tenant_id = user_row["tenant_id"]

                        with get_db_connection() as conn:
                            cursor = conn.cursor()
                            from app.repositories.database import is_postgresql

                            if is_postgresql():
                                cursor.execute(
                                    """INSERT INTO daily_messages
                                    (date, tool_name, host_name, message_id, role, content,
                                     full_entry, tokens_used, input_tokens, output_tokens,
                                     model, timestamp, message_source,
                                     conversation_id, agent_session_id, user_id, project_path, tenant_id)
                                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                    ON CONFLICT (date, tool_name, message_id, host_name) DO NOTHING""",
                                    (
                                        date_str,
                                        tool_name,
                                        machine_id[:8],
                                        message_id,
                                        role,
                                        content[:MAX_MESSAGE_LENGTH],
                                        full_entry_json,
                                        tokens_used,
                                        input_tokens,
                                        output_tokens,
                                        msg_model,
                                        timestamp,
                                        message_source,
                                        session_id,
                                        session_id,
                                        getattr(current_session, "user_id", None),
                                        project_path or "",
                                        session_tenant_id,
                                    ),
                                )
                            else:
                                cursor.execute(
                                    """INSERT OR IGNORE INTO daily_messages
                                    (date, tool_name, host_name, message_id, role, content,
                                     full_entry, tokens_used, input_tokens, output_tokens,
                                     model, timestamp, message_source,
                                     conversation_id, agent_session_id, user_id, project_path, tenant_id)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                    (
                                        date_str,
                                        tool_name,
                                        machine_id[:8],
                                        message_id,
                                        role,
                                        content[:MAX_MESSAGE_LENGTH],
                                        full_entry_json,
                                        tokens_used,
                                        input_tokens,
                                        output_tokens,
                                        msg_model,
                                        timestamp,
                                        message_source,
                                        session_id,
                                        session_id,
                                        getattr(current_session, "user_id", None),
                                        project_path or "",
                                        session_tenant_id,
                                    ),
                                )
                            conn.commit()
                    except Exception as e:
                        logger.debug("Failed to insert daily_message: %s", e)

                # Apply the net message-count delta once after the loop. The
                # transcript writer keeps add_message side-effect-free
                # (count_usage=False), so the session owner must advance
                # message_count itself — otherwise remote-synced sessions
                # never reflect their imported messages (#1128).
                if synced_message_delta:
                    sync_session_mgr.increment_session_usage(
                        session_id, message_delta=synced_message_delta
                    )
            except Exception as e:
                logger.debug("Failed to mirror messages: %s", e)

        except Exception as e:
            logger.error("Failed to process session_sync: %s", e)

        return jsonify({"success": True})

    elif msg_type == "command_response":
        # Agent responds to a synchronous command request (Issue #669)
        request_id = data.get("request_id")
        result = data.get("result")

        # Forward to RemoteAgentManager to signal waiting coroutine
        agent_mgr.handle_command_response(data)

        return jsonify({"success": True})

    else:
        return jsonify({"error": f"Unknown message type: {msg_type}"}), 400


# ==================== Terminal Management ====================


@remote_bp.route("/terminal/start", methods=["POST"])
@machine_access_required
def start_terminal():
    """Start a web terminal on a remote machine."""
    # P2-1: Permission check moved to decorator @machine_access_required
    data = request.get_json() or {}
    machine_id = data.get("machine_id")
    work_dir = data.get("work_dir")

    if not machine_id:  # decorator already guards; narrows type for mypy
        return jsonify({"error": "machine_id is required"}), 400

    # Get machine info for title/hostname
    agent_mgr = get_remote_agent_manager()
    machine = agent_mgr.get_machine(machine_id)
    machine_name = machine.get("machine_name", machine_id[:8]) if machine else machine_id[:8]
    hostname = machine.get("hostname", machine_id[:8]) if machine else machine_id[:8]
    tenant_id = machine.get("tenant_id", 1) if machine else 1

    # Generate terminal ID and proxy tokens for multiple providers
    terminal_id = str(uuid.uuid4())

    # Create agent_sessions record for terminal
    from app.modules.workspace.session_manager import get_session_manager

    sm = get_session_manager()
    sm.create_session(
        session_id=terminal_id,
        tool_name="claude-code",
        user_id=g.user["id"],
        tenant_id=tenant_id,
        title=f"Terminal: {machine_name}",
        host_name=hostname,
        project_path=work_dir or "",
    )
    # Update workspace_type and remote_machine_id columns
    sm.update_session_fields(
        terminal_id,
        {
            "workspace_type": "terminal",
            "remote_machine_id": machine_id,
        },
    )
    logger.info(
        f"Created terminal session {terminal_id} for user {g.user['id']} on machine {machine_id}"
    )

    # Generate proxy tokens for LLM API auth through the terminal
    api_proxy = get_api_key_proxy_service()
    anthropic_token = api_proxy.generate_proxy_token(
        user_id=g.user["id"],
        session_id=terminal_id,
        tenant_id=tenant_id,
        provider="anthropic",
        session_type="terminal",
    )
    openai_token = api_proxy.generate_proxy_token(
        user_id=g.user["id"],
        session_id=terminal_id,
        tenant_id=tenant_id,
        provider="openai",
        session_type="terminal",
    )

    # Use external URL for LLM proxy (remote machine needs to access it)
    backend_url = agent_mgr.get_backend_url(request.host_url)
    proxy_url = f"{backend_url}/api/remote/llm-proxy"
    logger.info("start_terminal: backend_url=%s, proxy_url=%s", backend_url, proxy_url)

    # Get CLI settings for supported menu tools
    cli_settings = {}
    for tool_name in _CLI_SETTINGS_TOOLS:
        tool_settings = api_proxy.get_cli_settings_for_tool(tenant_id, tool_name)
        if tool_settings:
            cli_settings[tool_name] = tool_settings

    # Send start_terminal command to agent with tokens for both providers
    cmd = {
        "type": "command",
        "command": "start_terminal",
        "terminal_id": terminal_id,
        "proxy_url": proxy_url,
        "anthropic_token": anthropic_token,
        "openai_token": openai_token,
        "work_dir": work_dir or "",
        "cli_settings": cli_settings,
    }
    agent_mgr.send_command(machine_id, cmd)

    # Return immediately with pending status; frontend polls status endpoint
    return jsonify(
        {
            "success": True,
            "terminal": {
                "terminal_id": terminal_id,
                "machine_id": machine_id,
                "status": "pending",
            },
        }
    )


@remote_bp.route("/terminal/cli/start", methods=["POST"])
@machine_access_required
def start_cli_terminal():
    """Start an SSH/local CLI-backed terminal session.

    Unlike the web terminal flow, this endpoint does not ask the remote agent to
    spawn a PTY. The caller is already inside an SSH/local shell on the remote
    machine, so the server only creates the Open ACE session and returns short
    lived proxy tokens for local CLI processes.
    """
    # P2-1: Permission check moved to decorator @machine_access_required
    data = request.get_json() or {}
    machine_id = data.get("machine_id")
    work_dir = data.get("work_dir") or ""
    source = data.get("source") or "ssh_cli"

    if not machine_id:  # decorator already guards; narrows type for mypy
        return jsonify({"error": "machine_id is required"}), 400

    agent_mgr = get_remote_agent_manager()
    machine = agent_mgr.get_machine(machine_id)
    machine_name = machine.get("machine_name", machine_id[:8]) if machine else machine_id[:8]
    hostname = machine.get("hostname", machine_id[:8]) if machine else machine_id[:8]
    tenant_id = machine.get("tenant_id", 1) if machine else 1
    terminal_id = str(uuid.uuid4())

    from app.modules.workspace.session_manager import get_session_manager

    sm = get_session_manager()
    sm.create_session(
        session_id=terminal_id,
        tool_name="claude-code",
        user_id=g.user["id"],
        title=f"Terminal: {machine_name}",
        host_name=hostname,
        project_path=work_dir,
        context={
            "workspace_type": "terminal",
            "remote_machine_id": machine_id,
            "terminal_source": source,
        },
    )
    # create_session context is JSON metadata; these fields also need the
    # dedicated DB columns used by session listing and access checks.
    sm.update_session_fields(
        terminal_id,
        {
            "workspace_type": "terminal",
            "remote_machine_id": machine_id,
        },
    )

    api_proxy = get_api_key_proxy_service()
    anthropic_token = api_proxy.generate_proxy_token(
        user_id=g.user["id"],
        session_id=terminal_id,
        tenant_id=tenant_id,
        provider="anthropic",
        session_type="terminal",
    )
    openai_token = api_proxy.generate_proxy_token(
        user_id=g.user["id"],
        session_id=terminal_id,
        tenant_id=tenant_id,
        provider="openai",
        session_type="terminal",
    )

    backend_url = agent_mgr.get_backend_url(request.host_url)
    proxy_url = f"{backend_url}/api/remote/llm-proxy"

    cli_settings = {}
    for tool_name in _CLI_SETTINGS_TOOLS:
        tool_settings = api_proxy.get_cli_settings_for_tool(tenant_id, tool_name)
        if tool_settings:
            cli_settings[tool_name] = tool_settings

    logger.info(
        "Created CLI terminal session %s for user %s on machine %s",
        terminal_id[:8],
        g.user["id"],
        machine_id,
    )

    return jsonify(
        {
            "success": True,
            "terminal": {
                "session_id": terminal_id,
                "terminal_id": terminal_id,
                "machine_id": machine_id,
                "status": "running",
                "source": source,
                "proxy_url": proxy_url,
                "cli_settings": cli_settings,
                "tokens": {
                    "anthropic": anthropic_token,
                    "openai": openai_token,
                },
            },
        }
    )


@remote_bp.route("/terminal/stop", methods=["POST"])
@machine_access_required
def stop_terminal():
    """Stop a web terminal on a remote machine."""
    # P2-1: Permission check moved to decorator @machine_access_required
    data = request.get_json() or {}
    terminal_id = data.get("terminal_id")
    machine_id = data.get("machine_id")

    if not terminal_id:
        return jsonify({"error": "terminal_id is required"}), 400
    if not machine_id:  # decorator already guards; narrows type for mypy
        return jsonify({"error": "machine_id is required"}), 400

    agent_mgr = get_remote_agent_manager()
    cmd = {
        "type": "command",
        "command": "stop_terminal",
        "terminal_id": terminal_id,
    }
    agent_mgr.send_command(machine_id, cmd)

    # Clean up local store; TerminalInfoStore also closes active bridge connections.
    terminal_info_store.pop(machine_id, terminal_id)

    # Update session status to completed
    from app.modules.workspace.session_manager import get_session_manager

    sm = get_session_manager()
    sm.complete_session(terminal_id)
    logger.info("Completed terminal session %s", terminal_id)

    return jsonify({"success": True})


@remote_bp.route("/terminal/<terminal_id>/attach", methods=["POST"])
def attach_terminal(terminal_id):
    """Attach to existing terminal session (after browser refresh).

    Allows user to reconnect to the same terminal session without losing
    PTY state (e.g., Claude Code chat history).
    """
    data = request.get_json() or {}
    machine_id = data.get("machine_id")

    if not machine_id:
        return jsonify({"error": "machine_id is required"}), 400

    # Check access
    agent_mgr = get_remote_agent_manager()
    if g.user.get("role") != "admin":
        if not agent_mgr.check_user_access(machine_id, g.user["id"]):
            return jsonify({"error": "Access denied"}), 403

    # Get machine's tenant_id for token generation
    attach_machine = agent_mgr.get_machine(machine_id)
    attach_tenant_id = attach_machine.get("tenant_id", 1) if attach_machine else 1

    # Generate fresh API tokens for LLM proxy auth
    api_proxy = get_api_key_proxy_service()
    api_proxy.revoke_proxy_tokens_for_session(
        terminal_id,
        reason="terminal_tokens_rotated",
    )
    anthropic_token = api_proxy.generate_proxy_token(
        user_id=g.user["id"],
        session_id=terminal_id,
        tenant_id=attach_tenant_id,
        provider="anthropic",
        session_type="terminal",
    )
    openai_token = api_proxy.generate_proxy_token(
        user_id=g.user["id"],
        session_id=terminal_id,
        tenant_id=attach_tenant_id,
        provider="openai",
        session_type="terminal",
    )

    # Get proxy URL for LLM API
    backend_url = agent_mgr.get_backend_url(request.host_url)
    proxy_url = f"{backend_url}/api/remote/llm-proxy"

    # Send attach_terminal command to agent with fresh tokens
    cmd = {
        "type": "command",
        "command": "attach_terminal",
        "terminal_id": terminal_id,
        "machine_id": machine_id,
        "anthropic_token": anthropic_token,
        "openai_token": openai_token,
        "proxy_url": proxy_url,
    }
    agent_mgr.send_command(machine_id, cmd)

    logger.info(
        "Sent attach_terminal command for %s with fresh API tokens",
        terminal_id[:8],
    )

    # Return immediately with pending status; frontend polls status endpoint
    return jsonify(
        {
            "success": True,
            "terminal": {
                "terminal_id": terminal_id,
                "machine_id": machine_id,
                "status": "pending",
            },
        }
    )


@remote_bp.route("/terminal/<terminal_id>/status", methods=["GET"])
def get_terminal_status(terminal_id):
    """Get terminal status.

    Supports two authentication methods:
    1. Standard user session (via session_token cookie)
    2. WebSocket proxy token (for proxy processes to fetch remote terminal info)
    """
    machine_id = request.args.get("machine_id")
    if not machine_id:
        return jsonify({"error": "machine_id query parameter is required"}), 400

    agent_mgr = get_remote_agent_manager()
    proxy_token = request.cookies.get("session_token", "")
    logger.info(
        "get_terminal_status: terminal=%s, machine=%s, has_proxy_token=%s",
        terminal_id[:8],
        machine_id[:8],
        bool(proxy_token),
    )

    # Check if this is a WebSocket proxy token
    # The terminal info store contains the proxy token (stored when proxy was started)
    info = terminal_info_store.get(machine_id, terminal_id)
    if info:
        stored_token = info.get("token", "")
        logger.info("get_terminal_status: found info, has_stored_token=%s", bool(stored_token))
        # If the provided token matches the stored proxy token, allow access
        if proxy_token and stored_token and hmac.compare_digest(proxy_token, stored_token):
            logger.info("get_terminal_status: proxy token matched, returning info")
            return jsonify({"success": True, "terminal": info})

    # Standard user authentication
    if g.user.get("role") != "admin":
        if not agent_mgr.check_user_access(machine_id, g.user["id"]):
            return jsonify({"error": "Access denied"}), 403

    if info:
        return jsonify({"success": True, "terminal": info})
    return jsonify({"success": True, "terminal": {"status": "unknown"}})


@remote_bp.route("/terminal/<terminal_id>/ws")
def terminal_websocket(terminal_id):
    """Fallback for non-WebSocket requests to the terminal WS endpoint.

    Real WebSocket connections are intercepted by TerminalWebSocketMiddleware
    at the WSGI layer (see app/terminal_ws_middleware.py).  This route only
    fires when the request is a plain HTTP GET (no WebSocket upgrade).
    """
    return jsonify({"error": "WebSocket upgrade required"}), 400


# ==================== LLM Proxy ====================


@remote_bp.route("/llm-proxy", methods=["POST", "HEAD"])
@remote_bp.route("/llm-proxy/<path:path>", methods=["GET", "POST", "PUT", "DELETE", "HEAD"])
def llm_proxy(path=""):
    api_proxy = get_api_key_proxy_service()
    return handle_llm_proxy_request(scope="remote", api_proxy=api_proxy, path=path)


# ==================== Usage Report (from Agent) ====================

_USAGE_REPORT_RATE_LIMIT_WINDOW = 60  # seconds
_USAGE_REPORT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_USAGE_REPORT_LEGACY_DEADLINE_DEFAULT = "2026-08-04T00:00:00+00:00"

# Constants for input validation (Issue #1891)
_MAX_TOKENS_PER_REPORT = 10**9  # 1 billion tokens per report
_MAX_REQUESTS_PER_REPORT = 1000  # max requests per single report


def _check_usage_report_rate_limit(agent_mgr: Any, key: str, limit: int) -> bool:
    """Atomically enforce a shared fixed-window rate limit.

    The state lives in the application database, so gunicorn workers and
    multiple application instances observe the same counter.  This helper is
    called only after the authenticated machine/session binding is verified;
    unauthenticated callers cannot allocate keys or exhaust valid counters.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(seconds=_USAGE_REPORT_RATE_LIMIT_WINDOW)
    stale_cutoff = now - timedelta(days=1)

    with agent_mgr.db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            adapt_sql(
                """
                INSERT INTO usage_report_rate_limits
                    (rate_key, window_started_at, request_count, updated_at)
                VALUES (?, ?, 0, ?)
                ON CONFLICT(rate_key) DO NOTHING
                """
            ),
            (key, now, now),
        )
        cursor.execute(
            adapt_sql(
                """
                UPDATE usage_report_rate_limits
                SET request_count = CASE
                        WHEN window_started_at <= ? THEN 1
                        ELSE request_count + 1
                    END,
                    window_started_at = CASE
                        WHEN window_started_at <= ? THEN ?
                        ELSE window_started_at
                    END,
                    updated_at = ?
                WHERE rate_key = ?
                RETURNING request_count
                """
            ),
            (cutoff, cutoff, now, now, key),
        )
        row = cursor.fetchone()
        cursor.execute(
            adapt_sql("DELETE FROM usage_report_rate_limits WHERE updated_at < ?"),
            (stale_cutoff,),
        )
        conn.commit()

    count = row["request_count"] if isinstance(row, dict) else row[0]
    return bool(count <= limit)


def _should_audit_usage_auth_failure(agent_mgr: Any, client_ip: str) -> bool:
    """Bound usage-auth audit writes across workers without changing the 401."""
    try:
        return _check_usage_report_rate_limit(agent_mgr, f"usage-auth-audit:{client_ip}", 5)
    except Exception:
        logger.exception("Failed to apply usage auth audit rate limit")
        return False


def _legacy_usage_report_deadline() -> datetime:
    """Return the explicit rollout deadline for report_id-less Agents."""
    raw = os.environ.get(
        "OPENACE_USAGE_REPORT_LEGACY_DEADLINE",
        _USAGE_REPORT_LEGACY_DEADLINE_DEFAULT,
    )
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        logger.error("Invalid OPENACE_USAGE_REPORT_LEGACY_DEADLINE=%r; disabling fallback", raw)
        return datetime.min.replace(tzinfo=timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _normalize_usage_report_id(data: dict) -> tuple[dict, bool, str | None]:
    """Validate report_id or apply the short, explicitly bounded rollout fallback."""
    report_id = data.get("report_id")
    if isinstance(report_id, str) and _USAGE_REPORT_ID_RE.fullmatch(report_id):
        return data, False, None
    if report_id is not None:
        return data, False, "A valid report_id is required"

    deadline = _legacy_usage_report_deadline()
    if datetime.now(timezone.utc) >= deadline:
        return data, False, "report_id is required; the legacy migration window has expired"

    normalized = dict(data)
    normalized["report_id"] = f"legacy-{uuid.uuid4()}"
    logger.warning(
        "Accepted report_id-less usage payload during migration window ending %s",
        deadline.isoformat(),
    )
    return normalized, True, None


def _usage_report_payload_hash(data: dict) -> str:
    """Return a stable digest for replay-conflict detection."""
    canonical = {
        "machine_id": data.get("machine_id"),
        "session_id": data.get("session_id"),
        "tokens": data.get("tokens", {}),
        "requests": data.get("requests", 1),
    }
    raw = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _claim_usage_report(
    agent_mgr: Any,
    *,
    report_id: str,
    session_id: str,
    machine_id: str,
    user_id: int,
    tenant_id: int,
    payload_hash: str,
) -> str:
    """Claim a report ID and return claimed, duplicate, conflict, or processing."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with agent_mgr.db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            adapt_sql(
                """
                INSERT INTO usage_report_receipts
                    (report_id, session_id, machine_id, user_id, tenant_id,
                     payload_hash, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'processing', ?, ?)
                ON CONFLICT(report_id) DO NOTHING
                """
            ),
            (
                report_id,
                session_id,
                machine_id,
                user_id,
                tenant_id,
                payload_hash,
                now,
                now,
            ),
        )
        inserted = cursor.rowcount > 0
        if inserted:
            conn.commit()
            return "claimed"

        cursor.execute(
            adapt_sql(
                "SELECT session_id, machine_id, payload_hash, status "
                "FROM usage_report_receipts WHERE report_id = ?"
            ),
            (report_id,),
        )
        row = cursor.fetchone()
        if not row:
            conn.rollback()
            return "processing"

        existing = dict(row) if not isinstance(row, dict) else row
        same_report = (
            existing.get("session_id") == session_id
            and existing.get("machine_id") == machine_id
            and hmac.compare_digest(existing.get("payload_hash") or "", payload_hash)
        )
        if not same_report:
            conn.rollback()
            return "conflict"
        if existing.get("status") == "completed":
            conn.rollback()
            return "duplicate"
        if existing.get("status") == "failed":
            cursor.execute(
                adapt_sql(
                    "UPDATE usage_report_receipts SET status = 'processing', updated_at = ? "
                    "WHERE report_id = ? AND status = 'failed'"
                ),
                (now, report_id),
            )
            conn.commit()
            return "claimed" if cursor.rowcount > 0 else "processing"
        conn.rollback()
        return "processing"


def _finish_usage_report(agent_mgr: Any, report_id: str, status: str) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with agent_mgr.db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            adapt_sql(
                "UPDATE usage_report_receipts SET status = ?, updated_at = ?, "
                "processed_at = CASE WHEN ? = 'completed' THEN ? ELSE processed_at END "
                "WHERE report_id = ?"
            ),
            (status, now, status, now, report_id),
        )
        conn.commit()


def _validate_usage_report_input(tokens: dict, requests: int) -> tuple[str | None, str | None]:
    """Validate input data for usage report.

    Args:
        tokens: Token counts dict with 'input' and 'output' keys
        requests: Number of requests

    Returns:
        (None, None) if valid, (error_message, error_reason) if invalid
    """
    # Validate tokens is a dict
    if not isinstance(tokens, dict):
        return "tokens must be an object", "invalid_tokens_type"

    # Validate input tokens
    input_tokens = tokens.get("input", 0)
    if not isinstance(input_tokens, int) or isinstance(input_tokens, bool):
        return "tokens.input must be an integer", "invalid_input_tokens_type"
    if input_tokens < 0:
        return "tokens.input must be non-negative", "negative_input_tokens"

    # Validate output tokens
    output_tokens = tokens.get("output", 0)
    if not isinstance(output_tokens, int) or isinstance(output_tokens, bool):
        return "tokens.output must be an integer", "invalid_output_tokens_type"
    if output_tokens < 0:
        return "tokens.output must be non-negative", "negative_output_tokens"

    # Validate total tokens limit
    total_tokens = input_tokens + output_tokens
    if total_tokens > _MAX_TOKENS_PER_REPORT:
        return f"total tokens exceeds maximum ({_MAX_TOKENS_PER_REPORT})", "tokens_exceeds_limit"

    # Validate requests
    if not isinstance(requests, int) or isinstance(requests, bool):
        return "requests must be an integer", "invalid_requests_type"
    if requests < 0:
        return "requests must be non-negative", "negative_requests"
    if requests > _MAX_REQUESTS_PER_REPORT:
        return f"requests exceeds maximum ({_MAX_REQUESTS_PER_REPORT})", "requests_exceeds_limit"

    return None, None


def _process_authenticated_usage_report(data: dict, machine_id: str, client_ip: str):
    """Validate and apply one usage delta after machine-token authentication."""
    agent_mgr = get_remote_agent_manager()
    # A valid machine token is known at this boundary.  Apply machine/IP limits
    # before parsing or binding the caller-controlled session identifier so
    # invalid-binding traffic cannot amplify database or audit writes.
    for key, limit, label in (
        (f"machine:{machine_id}", 60, "this machine"),
        (f"ip:{client_ip}", 120, "this client"),
    ):
        if not _check_usage_report_rate_limit(agent_mgr, key, limit):
            return jsonify({"error": f"Rate limit exceeded for {label}"}), 429

    data, legacy_report_id, report_id_error = _normalize_usage_report_id(data)
    session_id = data.get("session_id")
    report_id = data.get("report_id")
    tokens = data.get("tokens", {})
    requests_count = data.get("requests", 1)

    if not session_id:
        return jsonify({"error": "session_id is required"}), 400
    if report_id_error:
        return jsonify({"error": report_id_error}), 400
    assert isinstance(report_id, str)

    input_error, _ = _validate_usage_report_input(tokens, requests_count)
    if input_error:
        return jsonify({"error": input_error}), 400

    session_data, binding_error = _validate_usage_report_binding(session_id, machine_id, client_ip)
    if binding_error:
        return binding_error
    assert session_data is not None

    if not _check_usage_report_rate_limit(agent_mgr, f"session:{session_id}", 10):
        return jsonify({"error": "Rate limit exceeded for this session"}), 429

    user_id = int(session_data["user_id"])
    tenant_id = int(session_data["tenant_id"])
    payload_hash = _usage_report_payload_hash(data)
    claim = _claim_usage_report(
        agent_mgr,
        report_id=report_id,
        session_id=session_id,
        machine_id=machine_id,
        user_id=user_id,
        tenant_id=tenant_id,
        payload_hash=payload_hash,
    )
    if claim == "duplicate":
        return jsonify({"success": True, "duplicate": True, "report_id": report_id})
    if claim == "processing":
        return jsonify({"error": "Usage report is already being processed"}), 409
    if claim == "conflict":
        _audit_usage_report_failure(
            AuditAction.USAGE_REPORT_BINDING_MISMATCH.value,
            session_id,
            machine_id,
            client_ip,
            "report_id_replay_conflict",
            {"report_id": report_id},
            user_id=user_id,
            tenant_id=tenant_id,
            machine_verified=True,
        )
        return jsonify({"error": "report_id was already used for different content"}), 409

    try:
        session_mgr = get_remote_session_manager()
        session_mgr.process_usage_report(
            session_id=session_id,
            tokens=tokens,
            requests=requests_count,
        )
    except Exception:
        _finish_usage_report(agent_mgr, report_id, "failed")
        logger.exception("Failed to process usage report %s", report_id)
        return jsonify({"error": "Failed to process usage report"}), 500

    _finish_usage_report(agent_mgr, report_id, "completed")
    audit_logger.log(
        action=AuditAction.USAGE_REPORT_ACCEPTED.value,
        user_id=user_id,
        tenant_id=tenant_id,
        severity="info",
        resource_type="usage_report",
        resource_id=report_id,
        session_id=session_id,
        ip_address=client_ip,
        details={
            "machine_id": machine_id,
            "session_id": session_id,
            "requests": requests_count,
            "total_tokens": tokens.get("input", 0) + tokens.get("output", 0),
            "legacy_report_id_generated": legacy_report_id,
            "idempotency_protected": not legacy_report_id,
            "legacy_deadline": (
                _legacy_usage_report_deadline().isoformat() if legacy_report_id else None
            ),
        },
        success=True,
    )
    return jsonify(
        {
            "success": True,
            "duplicate": False,
            "report_id": report_id,
            "legacy_report_id_generated": legacy_report_id,
        }
    )


@remote_bp.route("/usage-report", methods=["POST"])
def usage_report():
    """Receive an authenticated, bound, idempotent Agent usage report."""
    client_ip = get_client_ip_from_request()
    data = request.get_json() or {}
    machine_id = data.get("machine_id")
    if not machine_id:
        return jsonify({"error": "machine_id is required"}), 400
    agent_mgr = get_remote_agent_manager()
    machine = agent_mgr.get_machine(machine_id)
    if not machine:
        if _should_audit_usage_auth_failure(agent_mgr, client_ip):
            _audit_usage_report_failure(
                AuditAction.USAGE_REPORT_AUTH_FAILURE.value,
                data.get("session_id") or "",
                machine_id,
                client_ip,
                "unknown_machine",
            )
        return jsonify({"error": "Unknown machine"}), 401

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        _, bearer_error = _validate_agent_bearer(machine_id)
        if bearer_error:
            token = auth_header[7:]
            if _should_audit_usage_auth_failure(agent_mgr, client_ip):
                _audit_usage_report_failure(
                    AuditAction.USAGE_REPORT_AUTH_FAILURE.value,
                    data.get("session_id") or "",
                    machine_id,
                    client_ip,
                    "invalid_token",
                    {"token_hash_prefix": token_hash_prefix(token)},
                    user_id=machine.get("created_by"),
                    tenant_id=machine.get("tenant_id"),
                    machine_verified=True,
                )
            return bearer_error
        if agent_mgr.is_legacy_machine(machine_id):
            agent_mgr.clear_legacy_mode(machine_id)
            logger.info("Legacy mode cleared for machine %s after Bearer auth", machine_id[:8])
    else:
        _, legacy_error = _check_legacy_fallback(machine_id)
        if legacy_error:
            if _should_audit_usage_auth_failure(agent_mgr, client_ip):
                _audit_usage_report_failure(
                    AuditAction.USAGE_REPORT_AUTH_FAILURE.value,
                    data.get("session_id") or "",
                    machine_id,
                    client_ip,
                    "missing_token_or_legacy_expired",
                    user_id=machine.get("created_by"),
                    tenant_id=machine.get("tenant_id"),
                    machine_verified=True,
                )
            return legacy_error
        logger.warning(
            "Usage report accepted for legacy machine %s (no Bearer token)",
            machine_id[:8],
        )
    return _process_authenticated_usage_report(data, machine_id, client_ip)


# ==================== Machine File Browse ====================


@remote_bp.route("/machines/<machine_id>/browse", methods=["GET"])
@machine_access_required
def browse_remote_directory(machine_id):
    """Browse the file system on a remote machine.

    Returns directory information for the specified path.
    If no path is specified, returns the machine's work_dir as the default.
    """
    # P2-1: Permission check moved to decorator @machine_access_required

    agent_mgr = get_remote_agent_manager()
    path = request.args.get("path")

    # Get machine info
    machine = agent_mgr.get_machine(machine_id)
    if not machine:
        return jsonify({"error": "Machine not found"}), 404

    # Determine the path to browse
    work_dir = machine.get("work_dir") or "/root/workspace"
    browse_path = path or work_dir

    # Check if agent is online (accept "online", "idle", and "busy" as active states)
    # "busy" means the machine has active sessions but is still connected
    if machine.get("status") not in ("online", "idle", "busy"):
        # Agent offline - return fallback response
        return jsonify(
            {
                "success": False,
                "error": "Machine is offline. Directory browsing requires an active connection.",
                "result": {
                    "path": browse_path,
                    "name": browse_path.split("/")[-1] or "/",
                    "directories": [],
                    "parent": browse_path.rsplit("/", 1)[0] if "/" in browse_path else None,
                    "homePath": work_dir,
                    "is_writable": False,
                },
                "machine": machine,
            }
        )

    # Generate unique request ID for this browse request
    request_id = str(uuid.uuid4())

    # Send browse_directory command to agent
    command = {
        "type": "command",
        "command": "browse_directory",
        "request_id": request_id,
        "path": browse_path,
    }

    # Check agent connection for synchronous commands — fail fast if offline
    if not agent_mgr.is_agent_connected(machine_id):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Agent is not connected",
                }
            ),
            503,
        )

    agent_mgr.send_command(machine_id, command)

    # Wait for agent response (with timeout)
    result = agent_mgr.get_browse_result(request_id, timeout=15.0)

    if result is None:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Timeout waiting for agent response. Please try again.",
                }
            ),
            504,
        )

    # Return the result from agent
    return jsonify(
        {
            "success": result.get("success", False),
            "result": result.get("result"),
            "error": result.get("error"),
            "machine": machine,
        }
    )


@remote_bp.route("/machines/<machine_id>/create-directory", methods=["POST"])
def create_remote_directory(machine_id):
    """Create a directory on a remote machine.

    Expects JSON body with 'path' (full path of the directory to create).
    """
    if not hasattr(g, "user") or g.user is None:
        return jsonify({"error": "Unauthorized"}), 401

    agent_mgr = get_remote_agent_manager()

    if g.user.get("role") != "admin":
        if not agent_mgr.check_user_access(machine_id, g.user["id"]):
            return jsonify({"error": "Access denied"}), 403

    data = request.get_json() or {}
    dir_path = data.get("path", "")

    if not dir_path:
        return jsonify({"success": False, "error": "Path is required"}), 400

    if len(dir_path) > 4096:
        return jsonify({"success": False, "error": "Path too long"}), 400

    machine = agent_mgr.get_machine(machine_id)
    if not machine:
        return jsonify({"error": "Machine not found"}), 404

    if machine.get("status") not in ("online", "idle"):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Machine is offline. Directory creation requires an active connection.",
                }
            ),
            503,
        )

    request_id = str(uuid.uuid4())

    command = {
        "type": "command",
        "command": "create_directory",
        "request_id": request_id,
        "path": dir_path,
    }

    # Check agent connection for synchronous commands — fail fast if offline
    if not agent_mgr.is_agent_connected(machine_id):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Agent is not connected",
                }
            ),
            503,
        )

    agent_mgr.send_command(machine_id, command)

    result = agent_mgr.get_browse_result(request_id, timeout=15.0)

    if result is None:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Timeout waiting for agent response. Please try again.",
                }
            ),
            504,
        )

    return jsonify(
        {
            "success": result.get("success", False),
            "result": result.get("result"),
            "error": result.get("error"),
        }
    )


# ── Remote Git endpoints ────────────────────────────────────────────


def _dispatch_remote_git_command(machine_id, command, required_params):
    """Common auth check, agent lookup, command dispatch, and wait for git commands.

    Args:
        machine_id: The remote machine ID.
        command: The git command to send (e.g. "git_status", "git_diff", "git_file").
        required_params: List of query param names that must be present and non-empty.

    Returns:
        Flask JSON response tuple.
    """
    if not hasattr(g, "user") or g.user is None:
        return jsonify({"error": "Unauthorized"}), 401

    agent_mgr = get_remote_agent_manager()

    if g.user.get("role") != "admin":
        if not agent_mgr.check_user_access(machine_id, g.user["id"]):
            return jsonify({"error": "Access denied"}), 403

    # Validate required query params
    missing = [name for name in required_params if not request.args.get(name)]
    if missing:
        names = " and ".join(missing)
        plural = "s" if len(missing) > 1 else ""
        return (
            jsonify(
                {
                    "success": False,
                    "error": f"{names} parameter{plural} required",
                }
            ),
            400,
        )

    # Map query param names to command payload names
    # query "path" → command "project_path"
    query_to_command = {"path": "project_path"}
    payload = {}
    for name in required_params:
        key = query_to_command.get(name, name)
        payload[key] = request.args.get(name)

    machine = agent_mgr.get_machine(machine_id)
    if not machine:
        return jsonify({"error": "Machine not found"}), 404

    if not agent_mgr.is_agent_connected(machine_id):
        return jsonify({"success": False, "error": "Agent is not connected"}), 503

    request_id = str(uuid.uuid4())
    agent_mgr.send_command(
        machine_id,
        {
            "type": "command",
            "command": command,
            "request_id": request_id,
            **payload,
        },
    )

    result = agent_mgr.get_browse_result(request_id, timeout=15.0)

    if result is None:
        return (
            jsonify({"success": False, "error": "Timeout waiting for agent response"}),
            504,
        )

    return jsonify(
        {
            "success": result.get("success", False),
            "result": result.get("result"),
            "error": result.get("error"),
        }
    )


@remote_bp.route("/machines/<machine_id>/git/status", methods=["GET"])
def remote_git_status(machine_id):
    """Get git status on a remote machine.

    Query params: path (project path on the remote machine)
    """
    return _dispatch_remote_git_command(machine_id, "git_status", ["path"])


@remote_bp.route("/machines/<machine_id>/git/diff", methods=["GET"])
def remote_git_diff(machine_id):
    """Get git diff for a specific file on a remote machine.

    Query params: path (project path), file (relative file path)
    """
    return _dispatch_remote_git_command(machine_id, "git_diff", ["path", "file"])


@remote_bp.route("/machines/<machine_id>/git/file", methods=["GET"])
def remote_git_file(machine_id):
    """Read a file from a remote machine.

    Query params: path (project path), file (relative file path)
    """
    return _dispatch_remote_git_command(machine_id, "git_file", ["path", "file"])


# ── Remote VSCode (code-server) endpoints ───────────────────────────


@remote_bp.route("/vscode/start", methods=["POST"])
@machine_access_required
def remote_vscode_start():
    """Start a code-server instance on a remote machine."""
    # P2-1: Permission check moved to decorator @machine_access_required
    agent_mgr = get_remote_agent_manager()
    data = request.get_json() or {}
    machine_id = data.get("machine_id", "")
    project_path = data.get("project_path", "")

    if not project_path:
        return jsonify({"success": False, "error": "project_path is required"}), 400

    if not agent_mgr.is_agent_connected(machine_id):
        return jsonify({"success": False, "error": "Agent is not connected"}), 503

    vscode_id = str(uuid.uuid4())
    agent_mgr.send_command(
        machine_id,
        {
            "type": "command",
            "command": "start_vscode",
            "vscode_id": vscode_id,
            "project_path": project_path,
        },
    )

    return jsonify({"success": True, "vscode_id": vscode_id, "status": "pending"})


@remote_bp.route("/vscode/stop", methods=["POST"])
@machine_access_required
def remote_vscode_stop():
    """Stop a code-server instance on a remote machine."""
    # P2-1: Permission check moved to decorator @machine_access_required
    agent_mgr = get_remote_agent_manager()
    data = request.get_json() or {}
    vscode_id = data.get("vscode_id", "")
    machine_id = data.get("machine_id", "")

    if not vscode_id:
        return jsonify({"success": False, "error": "vscode_id is required"}), 400

    agent_mgr.send_command(
        machine_id,
        {
            "type": "command",
            "command": "stop_vscode",
            "vscode_id": vscode_id,
        },
    )

    # Clean up local store
    from app.modules.workspace.vscode_store import vscode_info_store

    vscode_info_store.pop(machine_id, vscode_id)

    return jsonify({"success": True})


@remote_bp.route("/vscode/<vscode_id>/status", methods=["GET"])
def remote_vscode_status(vscode_id):
    """Get the status of a code-server instance."""
    if not hasattr(g, "user") or g.user is None:
        return jsonify({"error": "Unauthorized"}), 401

    agent_mgr = get_remote_agent_manager()

    from app.modules.workspace.vscode_store import vscode_info_store

    found = vscode_info_store.find_by_vscode_id(vscode_id)
    if not found:
        return jsonify({"success": True, "status": "unknown"})

    machine_id, info = found

    if g.user.get("role") != "admin":
        if not agent_mgr.check_user_access(machine_id, g.user["id"]):
            return jsonify({"error": "Access denied"}), 403

    status = info.get("status", "unknown")
    response = {"success": True, "status": status}

    if status == "running":
        proxy_path = f"/api/remote/vscode/{vscode_id}/proxy/"
        proxy_url = f"{request.host_url.rstrip('/')}{proxy_path}"
        browser_token = info.get("token", "")
        # NOTE: Token is passed in the query string so that the iframe src URL
        # is absolute to Open ACE rather than relative to qwen-code-webui's
        # iframe origin. The token is generated with secrets.token_hex(32)
        # (256 bits of entropy) and is scoped to a single VSCode session.
        response["url"] = f"{proxy_url}?token={browser_token}"
    elif status == "error":
        response["error"] = info.get("error", "")

    return jsonify(response)


@remote_bp.route("/vscode/<vscode_id>/attach", methods=["POST"])
def remote_vscode_attach(vscode_id):
    """Re-attach to an existing code-server instance."""
    if not hasattr(g, "user") or g.user is None:
        return jsonify({"error": "Unauthorized"}), 401

    agent_mgr = get_remote_agent_manager()
    data = request.get_json() or {}
    machine_id = data.get("machine_id", "")

    if not machine_id:
        return jsonify({"success": False, "error": "machine_id is required"}), 400

    if g.user.get("role") != "admin":
        if not agent_mgr.check_user_access(machine_id, g.user["id"]):
            return jsonify({"error": "Access denied"}), 403

    agent_mgr.send_command(
        machine_id,
        {
            "type": "command",
            "command": "attach_vscode",
            "vscode_id": vscode_id,
        },
    )

    return jsonify({"success": True})


@remote_bp.route(
    "/vscode/<vscode_id>/proxy/<path:path>",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
)
@remote_bp.route(
    "/vscode/<vscode_id>/proxy/",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
)
def remote_vscode_proxy(vscode_id, path=""):
    """HTTP reverse proxy to remote code-server.

    Authentication is via the token in query string, validated against
    vscode_info_store. No session_token cookie required (needed for iframe access).

    For subsequent requests (static assets, API calls), the token can also be
    provided via a vscode_token cookie, which is set on the first successful
    request with a token in the query string.

    For nested iframe scenarios where cookies don't work, we also allow
    requests without token if the session is known to be running.
    """
    import hmac as _hmac

    from app.modules.workspace.vscode_proxy import build_target_url, proxy_request_streaming
    from app.modules.workspace.vscode_store import vscode_info_store

    found = vscode_info_store.find_by_vscode_id(vscode_id)
    if not found:
        return jsonify({"error": "VSCode session not found"}), 404

    machine_id, info = found

    # Get stored token
    stored_token = info.get("token", "")

    if not stored_token:
        return jsonify({"error": "VSCode session has no token"}), 500

    # Validate token from query string or cookie
    # Query string token takes precedence (used for initial iframe load)
    token = request.args.get("token", "")

    # If no token in query string, try cookie (for static assets, API calls)
    if not token:
        token = request.cookies.get(f"vscode_token_{vscode_id}", "")

    # For nested iframe scenarios (cookies blocked by SameSite), allow requests
    # without explicit token if the session is running. The proxy URL path
    # itself provides authentication (only valid vscode_id can be accessed).
    # This is a capability URL design - the security relies solely on:
    # 1. vscode_id is a UUID4 (~122 bits of randomness), hard to guess
    # 2. The URL is only visible to the user who started the session
    # 3. The session is scoped to a specific machine and project
    # Note: In this fallback case, we use stored_token for HMAC validation
    # but the caller does NOT prove they hold it. The real access control
    # is the vscode_id in the URL path (capability URL semantics).
    if not token and info.get("status") == "running":
        # Use stored token for internal validation (not sent to browser)
        token = stored_token

    if not token:
        return jsonify({"error": "Invalid or missing token"}), 403

    if not _hmac.compare_digest(token, stored_token):
        return jsonify({"error": "Invalid token"}), 403

    if info.get("status") != "running":
        return jsonify({"error": "VSCode session is not running"}), 503

    original_http_url = info.get("original_http_url", "")
    if not original_http_url:
        return jsonify({"error": "Remote URL not available"}), 500

    # Build target URL
    target_url = build_target_url(original_http_url, path)

    # Preserve query params (except token)
    params = dict(request.args)
    params.pop("token", None)

    # Collect request headers
    headers = {k: v for k, v in request.headers if k.lower() != "host"}

    # Add code-server password auth if available
    # code-server uses HTTP Basic Auth with empty username and password
    cs_password = info.get("cs_password", "")
    if cs_password:
        import base64 as _b64

        # Format: base64(":password") = base64(password) with colon prefix
        auth_value = _b64.b64encode(f":{cs_password}".encode()).decode()
        headers["Authorization"] = f"Basic {auth_value}"

    # Get request body
    body = request.get_data()

    # Proxy the request (streaming for efficient handling of large assets)
    status_code, resp_headers, content_gen = proxy_request_streaming(
        method=request.method,
        target_url=target_url,
        headers=headers,
        body=body,
        params=params if params else None,
    )

    # Build Flask streaming response
    response = Response(
        stream_with_context(content_gen),
        status=status_code,
    )
    for k, v in resp_headers.items():
        if k.lower() not in ("content-length", "content-encoding", "transfer-encoding"):
            response.headers[k] = v

    # Handle 302 redirect: preserve token in redirect URL
    # code-server redirects to ./?folder=xxx, but this loses the token param
    # We need to add the token back to the redirect Location
    if status_code == 302 and request.args.get("token"):
        location = resp_headers.get("Location", "")
        if location and "token=" not in location:
            # Handle relative paths properly using urljoin
            # If location is relative (e.g., "./?folder=xxx"), resolve it against current URL
            if (
                location.startswith("./")
                or location.startswith("/")
                or not location.startswith("http")
            ):
                location = urllib.parse.urljoin(request.url, location)
            # Add token to redirect URL
            separator = "?" if "?" not in location else "&"
            response.headers["Location"] = f"{location}{separator}token={token}"

    # Set cookie on first request with query string token
    # This allows subsequent static asset requests to be authenticated via cookie
    # Note: Set on any successful response (including 302 redirect), not just 200
    # Note: SameSite=Lax works for same-site iframe, but not nested cross-site iframe
    if request.args.get("token") and status_code < 400:
        cookie_name = f"vscode_token_{vscode_id}"
        cookie_value = token
        cookie_path = f"/api/remote/vscode/{vscode_id}/proxy/"
        # Add Secure flag if request is HTTPS (for production security)
        secure_flag = "; Secure" if request.is_secure else ""
        # Directly set Set-Cookie header (set_cookie may not work with streaming responses)
        response.headers["Set-Cookie"] = (
            f"{cookie_name}={cookie_value}; "
            f"Path={cookie_path}; "
            f"Max-Age={24 * 3600}; "
            f"HttpOnly; SameSite=Lax{secure_flag}"
        )

    return response


@remote_bp.route("/vscode/<vscode_id>/ws")
def remote_vscode_ws(vscode_id):
    """Fallback for non-WebSocket requests to the VSCode WS endpoint.

    Real WebSocket connections are intercepted by RemoteWSHandler
    at the WSGI layer (see app/remote_ws_handler.py).
    """
    return jsonify({"error": "WebSocket upgrade required"}), 400
