#!/usr/bin/env python3
"""
Open ACE - Remote Workspace API Routes

API endpoints for remote workspace management including:
- Machine registration and management
- Remote session creation and control
- WebSocket agent communication
- LLM API proxy for remote CLI tools
- Usage reporting from remote agents
"""

import json
import logging
import os
import time
import uuid

from flask import Blueprint, Response, g, jsonify, request, stream_with_context

from app.modules.workspace.api_key_proxy import get_api_key_proxy_service
from app.modules.workspace.remote_agent_manager import get_remote_agent_manager
from app.modules.workspace.remote_session_manager import (
    get_remote_session_manager,
)
from app.services.auth_service import AuthService

logger = logging.getLogger(__name__)

remote_bp = Blueprint("remote", __name__)
auth_service = AuthService()


@remote_bp.before_request
def load_user():
    """Load the current user from session token before each request."""
    # Skip auth for WebSocket upgrade and agent endpoints (they use JWT)
    if request.path.endswith("/agent/ws"):
        return
    if request.path.endswith("/llm-proxy"):
        return
    if request.path.endswith("/usage-report"):
        return
    if request.path.startswith("/remote/agent/install"):
        return

    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    if token:
        session = auth_service.get_session(token)
        if session:
            g.user = {
                "id": session.get("user_id"),
                "username": session.get("username"),
                "email": session.get("email"),
                "role": session.get("role"),
            }
        else:
            g.user = None
    else:
        url_token = request.args.get("token")
        if url_token:
            # First try as a session_token (for SSE / EventSource which can't send cookies)
            session = auth_service.get_session(url_token)
            if session:
                g.user = {
                    "id": session.get("user_id"),
                    "username": session.get("username"),
                    "email": session.get("email"),
                    "role": session.get("role"),
                }
            else:
                # Fall back to WebUI token validation
                try:
                    from app.services.webui_manager import WebUIManager

                    webui_manager = WebUIManager()
                    is_valid, user_id, error = webui_manager.validate_token(url_token)
                    if is_valid and user_id:
                        from app.repositories.user_repo import UserRepository

                        user_repo = UserRepository()
                        user = user_repo.get_user_by_id(user_id)
                        if user:
                            g.user = {
                                "id": user_id,
                                "username": user.get("username"),
                                "email": user.get("email"),
                                "role": user.get("role"),
                            }
                        else:
                            g.user = None
                    else:
                        g.user = None
                except Exception as e:
                    logger.warning(f"Failed to validate URL token: {e}")
                    g.user = None
        else:
            g.user = None


def _require_auth():
    """Require authentication for the current request."""
    if not hasattr(g, "user") or not g.user:
        return jsonify({"error": "Authentication required"}), 401
    return None


def _require_admin():
    """Require admin role for the current request."""
    auth_error = _require_auth()
    if auth_error:
        return auth_error
    if g.user.get("role") != "admin":
        return jsonify({"error": "Admin access required"}), 403
    return None


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


# ==================== Machine Management (Admin) ====================


@remote_bp.route("/machines/register", methods=["POST"])
def register_machine():
    """
    Generate a registration token for a new machine.
    Admin only - the token is used by the agent to authenticate registration.
    """
    auth_error = _require_admin()
    if auth_error:
        return auth_error

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
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    agent_mgr = get_remote_agent_manager()

    if g.user.get("role") == "admin":
        machines = agent_mgr.list_machines()
    else:
        machines = agent_mgr.list_machines(user_id=g.user["id"])

    return jsonify(
        {
            "success": True,
            "machines": machines,
        }
    )


@remote_bp.route("/machines/<machine_id>", methods=["GET"])
def get_machine(machine_id):
    """Get details and status of a specific machine."""
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    agent_mgr = get_remote_agent_manager()
    machine = agent_mgr.get_machine(machine_id)

    if not machine:
        return jsonify({"error": "Machine not found"}), 404

    # Check access for non-admin users
    if g.user.get("role") != "admin":
        if not agent_mgr.check_user_access(machine_id, g.user["id"]):
            return jsonify({"error": "Access denied"}), 403

    return jsonify(
        {
            "success": True,
            "machine": machine,
        }
    )


@remote_bp.route("/machines/<machine_id>", methods=["DELETE"])
def deregister_machine(machine_id):
    """Deregister a remote machine. Admin only."""
    auth_error = _require_admin()
    if auth_error:
        return auth_error

    agent_mgr = get_remote_agent_manager()
    success = agent_mgr.deregister_machine(machine_id)

    if success:
        return jsonify({"success": True, "message": "Machine deregistered"})
    return jsonify({"error": "Machine not found"}), 404


@remote_bp.route("/machines/<machine_id>/assign", methods=["POST"])
def assign_user(machine_id):
    """Assign a user to a machine. System admin or machine admin."""
    auth_error = _require_auth()
    if auth_error:
        return auth_error
    admin_error = _require_machine_admin(machine_id)
    if admin_error:
        return admin_error

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
def revoke_user(machine_id, user_id):
    """Revoke a user's access to a machine. System admin or machine admin."""
    auth_error = _require_auth()
    if auth_error:
        return auth_error
    admin_error = _require_machine_admin(machine_id)
    if admin_error:
        return admin_error

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


# ==================== API Key Management (Admin) ====================


@remote_bp.route("/api-keys", methods=["GET"])
def list_api_keys():
    """List all encrypted API keys (without revealing actual keys). Admin only."""
    auth_error = _require_admin()
    if auth_error:
        return auth_error

    data = request.args
    tenant_id = int(data.get("tenant_id", 1))

    api_proxy = get_api_key_proxy_service()
    keys = api_proxy.list_api_keys(tenant_id)

    return jsonify(
        {
            "success": True,
            "keys": keys,
        }
    )


@remote_bp.route("/api-keys", methods=["POST"])
def store_api_key():
    """Store a new encrypted API key. Admin only."""
    auth_error = _require_admin()
    if auth_error:
        return auth_error

    data = request.get_json() or {}
    provider = data.get("provider")
    key_name = data.get("key_name")
    api_key = data.get("api_key")
    base_url = data.get("base_url")
    tenant_id = int(data.get("tenant_id", 1))

    if not provider or not key_name or not api_key:
        return jsonify({"error": "provider, key_name, and api_key are required"}), 400

    api_proxy = get_api_key_proxy_service()
    result = api_proxy.store_api_key(
        tenant_id=tenant_id,
        provider=provider,
        key_name=key_name,
        api_key=api_key,
        base_url=base_url,
        created_by=g.user["id"],
    )

    if result.get("success"):
        return jsonify({"success": True, "key": result})
    return jsonify({"error": result.get("error", "Failed to store API key")}), 400


@remote_bp.route("/api-keys/<int:key_id>", methods=["DELETE"])
def delete_api_key(key_id):
    """Delete an API key by ID. Admin only."""
    auth_error = _require_admin()
    if auth_error:
        return auth_error

    data = request.get_json() or {}
    tenant_id = int(data.get("tenant_id", 1))

    api_proxy = get_api_key_proxy_service()
    success = api_proxy.delete_api_key_by_id(key_id, tenant_id)

    if success:
        return jsonify({"success": True, "message": "API key deleted"})
    return jsonify({"error": "API key not found"}), 404


# ==================== Machine User Assignments ====================


@remote_bp.route("/machines/<machine_id>/users", methods=["GET"])
def get_machine_users(machine_id):
    """Get list of users assigned to a machine. System admin or machine admin."""
    auth_error = _require_auth()
    if auth_error:
        return auth_error
    admin_error = _require_machine_admin(machine_id)
    if admin_error:
        return admin_error

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
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    agent_mgr = get_remote_agent_manager()
    machines = agent_mgr.get_available_machines(g.user["id"])

    # Filter to only show online machines
    available = [m for m in machines if m.get("status") == "online"]

    return jsonify(
        {
            "success": True,
            "machines": available,
        }
    )


@remote_bp.route("/sessions", methods=["POST"])
def create_remote_session():
    """Create a new remote session on a selected machine."""
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    data = request.get_json() or {}
    machine_id = data.get("machine_id")
    project_path = data.get("project_path")
    model = data.get("model")
    cli_tool = data.get("cli_tool", "qwen-code-cli")
    title = data.get("title", "")
    permission_mode = data.get("permission_mode")

    if not machine_id:
        return jsonify({"error": "machine_id is required"}), 400
    if not project_path:
        return jsonify({"error": "project_path is required"}), 400

    session_mgr = get_remote_session_manager()
    result = session_mgr.create_remote_session(
        user_id=g.user["id"],
        machine_id=machine_id,
        project_path=project_path,
        model=model,
        cli_tool=cli_tool,
        title=title,
        permission_mode=permission_mode,
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
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    result, access_error = _check_session_access(session_id)
    if access_error:
        return access_error

    if result:
        return jsonify({"success": True, "session": result})
    return jsonify({"error": "Session not found"}), 404


@remote_bp.route("/sessions/<session_id>/chat", methods=["POST"])
def send_remote_message(session_id):
    """Send a message to a remote session."""
    auth_error = _require_auth()
    if auth_error:
        return auth_error

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
    auth_error = _require_auth()
    if auth_error:
        return auth_error

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
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    _, access_error = _check_session_access(session_id)
    if access_error:
        return access_error

    session_mgr = get_remote_session_manager()
    success = session_mgr.abort_request(session_id)

    if success:
        return jsonify({"success": True})
    return jsonify({"error": "Failed to abort request"}), 400


@remote_bp.route("/sessions/<session_id>/stop", methods=["POST"])
def stop_remote_session(session_id):
    """Stop a remote session."""
    auth_error = _require_auth()
    if auth_error:
        return auth_error

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
    auth_error = _require_auth()
    if auth_error:
        return auth_error

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
    auth_error = _require_auth()
    if auth_error:
        return auth_error

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
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    session_info, access_error = _check_session_access(session_id)
    if access_error:
        return access_error

    data = request.get_json() or {}
    request_id = data.get("request_id")
    behavior = data.get("behavior", "deny")
    tool_name = data.get("tool_name", "")
    message = data.get("message")

    # Queue the permission_response command for the agent
    agent_mgr = get_remote_agent_manager()
    cmd = {
        "type": "command",
        "command": "permission_response",
        "session_id": session_id,
        "behavior": behavior,
        "tool_name": tool_name,
    }
    if request_id:
        cmd["request_id"] = request_id
    if message:
        cmd["message"] = message

    agent_mgr.send_command(session_info.get("machine_id", ""), cmd)

    return jsonify({"success": True})


@remote_bp.route("/sessions/<session_id>/stream")
def stream_session_output(session_id):
    """SSE: real-time stream of remote session output, formatted as claude_json."""
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    _, access_error = _check_session_access(session_id)
    if access_error:
        return access_error

    agent_mgr = get_remote_agent_manager()

    def generate():
        try:
            yield ": connected\n\n"
            last_index = 0
            idle_count = 0
            while True:
                new_output = agent_mgr.get_buffered_output(session_id, after_index=last_index)
                if new_output:
                    idle_count = 0
                    for entry in new_output:
                        data = entry.get("data", "").strip()
                        stream = entry.get("stream", "stdout")
                        if not data or stream == "stderr":
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
                        except (json.JSONDecodeError, TypeError):
                            pass
                        last_index += 1
                else:
                    idle_count += 1
                    if idle_count >= 150:  # ~30 seconds (150 * 0.2s)
                        yield ": keepalive\n\n"
                        idle_count = 0

                # Check if session ended (in-memory, no DB query)
                if agent_mgr.is_session_ended(session_id):
                    break
                time.sleep(0.2)

            yield "data: [DONE]\n\n"
        except GeneratorExit:
            logger.info(
                "Client disconnected during SSE for session %s, aborting request",
                session_id[:8],
            )
            try:
                session_mgr = get_remote_session_manager()
                session_mgr.abort_request(session_id)
            except Exception:
                pass

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
    result = agent_mgr.register_machine(
        registration_token=registration_token,
        machine_id=machine_id,
        machine_name=machine_name,
        hostname=hostname,
        os_type=os_type,
        os_version=os_version,
        capabilities=capabilities,
        agent_version=agent_version,
        ip_address=request.remote_addr,
    )

    if result:
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

    if not msg_type:
        return jsonify({"error": "type is required"}), 400

    agent_mgr = get_remote_agent_manager()
    machine_id = data.get("machine_id")

    if not machine_id:
        return jsonify({"error": "machine_id is required"}), 400

    if msg_type == "register":
        # Agent re-registering on reconnect
        agent_mgr.register_connection(machine_id, None)
        data.get("capabilities", {})
        pending = agent_mgr.get_pending_commands(machine_id)
        return jsonify({"success": True, "type": "register_ack", "pending_commands": pending})

    elif msg_type == "heartbeat":
        status = data.get("status", "idle")
        active_sessions = data.get("active_sessions", 0)
        agent_mgr.process_heartbeat(machine_id, status, active_sessions)
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

        if session_id and status:
            session_mgr = get_remote_session_manager()
            session_mgr.process_session_status_update(
                session_id=session_id,
                status=status,
                pid=pid,
            )

        return jsonify({"success": True})

    elif msg_type == "usage_report":
        session_id = data.get("session_id")
        tokens = data.get("tokens", {})
        requests_count = data.get("requests", 1)

        if session_id:
            session_mgr = get_remote_session_manager()
            session_mgr.process_usage_report(
                session_id=session_id,
                tokens=tokens,
                requests=requests_count,
            )

        return jsonify({"success": True})

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

    else:
        return jsonify({"error": f"Unknown message type: {msg_type}"}), 400


# ==================== LLM Proxy ====================


@remote_bp.route("/llm-proxy", methods=["POST"])
@remote_bp.route("/llm-proxy/<path:path>", methods=["GET", "POST", "PUT", "DELETE"])
def llm_proxy(path=""):
    """
    Transparent LLM API proxy for remote CLI tools.

    The remote CLI sends standard LLM API requests here with a proxy token
    in the Authorization header. The server:
    1. Validates the proxy token
    2. Looks up the real API key
    3. Checks quota
    4. Forwards to the real LLM provider
    5. Streams the response back
    6. Records token usage
    """
    # Extract proxy token from Authorization header
    auth_header = request.headers.get("Authorization", "")
    proxy_token = auth_header.replace("Bearer ", "").strip()

    if not proxy_token:
        return (
            jsonify({"error": {"message": "Missing authorization token", "type": "auth_error"}}),
            401,
        )

    # Validate proxy token
    api_proxy = get_api_key_proxy_service()
    token_payload = api_proxy.validate_proxy_token(proxy_token)

    if not token_payload:
        return (
            jsonify({"error": {"message": "Invalid or expired proxy token", "type": "auth_error"}}),
            401,
        )

    user_id = token_payload["user_id"]
    tenant_id = token_payload["tenant_id"]
    provider = token_payload["provider"]
    session_id = token_payload["session_id"]

    # Check quota
    try:
        from app.modules.governance.quota_manager import QuotaManager

        quota_mgr = QuotaManager()
        quota_result = quota_mgr.check_quota(user_id)
        if not quota_result["allowed"]:
            return (
                jsonify(
                    {
                        "error": {
                            "message": f"Quota exceeded: {quota_result['reason']}",
                            "type": "quota_exceeded",
                        }
                    }
                ),
                429,
            )
    except Exception as e:
        logger.error(f"Quota check failed, denying request for safety: {e}")
        return (
            jsonify(
                {
                    "error": {
                        "message": "Quota check unavailable - request denied for safety",
                        "type": "quota_check_error",
                    }
                }
            ),
            429,
        )

    # Resolve real API key
    key_result = api_proxy.resolve_api_key(tenant_id, provider)
    if not key_result:
        return (
            jsonify(
                {
                    "error": {
                        "message": f"No API key configured for provider '{provider}'",
                        "type": "config_error",
                    }
                }
            ),
            500,
        )

    api_key, base_url = key_result

    # Determine target URL
    if base_url:
        target_base = base_url.rstrip("/")
    else:
        # Default provider URLs
        provider_urls = {
            "openai": "https://api.openai.com",
            "anthropic": "https://api.anthropic.com",
            "google": "https://generativelanguage.googleapis.com",
        }
        target_base = provider_urls.get(provider, "https://api.openai.com")

    if path:
        # Avoid double version prefix (e.g., base_url=.../v1 + path=v1/...)
        path_parts = path.split("/", 1)
        if len(path_parts) > 1 and target_base.endswith("/" + path_parts[0]):
            target_url = f"{target_base}/{path_parts[1]}"
        else:
            target_url = f"{target_base}/{path}"
    else:
        # Handle direct path in the request URL
        target_url = f"{target_base}{request.path.split('/llm-proxy')[-1]}"

    # Forward the request
    try:
        import requests as http_requests

        # Build forwarded headers
        fwd_headers = {}
        for key, value in request.headers:
            if key.lower() in ("content-type", "accept", "user-agent"):
                fwd_headers[key] = value

        # Set the real API key
        if provider == "anthropic":
            fwd_headers["x-api-key"] = api_key
            fwd_headers["anthropic-version"] = "2023-06-01"
        else:
            fwd_headers["Authorization"] = f"Bearer {api_key}"

        # Check if this is a streaming request
        body = request.get_data()

        # Forward the request (bypass system proxy to avoid interference)
        resp = http_requests.request(
            method=request.method,
            url=target_url,
            headers=fwd_headers,
            data=body,
            stream=True,
            timeout=120,
            proxies={"http": None, "https": None},
        )

        # Handle streaming response
        content_type = resp.headers.get("Content-Type", "")

        def generate():
            total_content = b""
            for chunk in resp.iter_content(chunk_size=4096):
                total_content += chunk
                yield chunk

            # After streaming completes, try to extract token usage
            try:
                _record_llm_usage(total_content, session_id, user_id, provider, content_type)
            except Exception as e:
                logger.error(f"Failed to record LLM usage: {e}")

        # Build response headers
        response_headers = {}
        for key, value in resp.headers.items():
            if key.lower() in ("content-type", "x-request-id", "openai-organization"):
                response_headers[key] = value

        if "text/event-stream" in content_type:
            return Response(
                stream_with_context(generate()),
                status=resp.status_code,
                headers=response_headers,
                content_type=content_type,
            )
        else:
            # Non-streaming response
            content = resp.content
            try:
                _record_llm_usage(content, session_id, user_id, provider, content_type)
            except Exception as e:
                logger.error(f"Failed to record LLM usage: {e}")

            return Response(
                content,
                status=resp.status_code,
                headers=response_headers,
                content_type=content_type,
            )

    except Exception as e:
        logger.error(f"LLM proxy error: {e}")
        return (
            jsonify(
                {
                    "error": {
                        "message": f"Proxy error: {str(e)}",
                        "type": "proxy_error",
                    }
                }
            ),
            502,
        )


def _record_llm_usage(
    content: bytes, session_id: str, user_id: int, provider: str, content_type: str
) -> None:
    """Extract and record token usage from LLM response."""
    try:
        if b"usage" not in content:
            return

        usage = None

        # Try parsing as a single JSON object (non-streaming response)
        try:
            data = json.loads(content)
            usage = data.get("usage", {})
        except json.JSONDecodeError:
            # Streaming SSE response — scan each line for usage data
            for line in content.split(b"\n"):
                line = line.strip()
                if not line or not line.startswith(b"data:"):
                    continue
                payload = line[len(b"data:") :].strip()
                if payload == b"[DONE]":
                    continue
                try:
                    chunk = json.loads(payload)
                    if "usage" in chunk:
                        usage = chunk["usage"]
                        break
                except (json.JSONDecodeError, ValueError):
                    continue

        if not usage or not isinstance(usage, dict):
            return

        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)

        if input_tokens or output_tokens:
            from app.modules.governance.quota_manager import QuotaManager

            quota_mgr = QuotaManager()
            quota_mgr.record_usage(
                user_id=user_id,
                tokens=input_tokens + output_tokens,
                requests=1,
            )

            # Update session token counts
            from app.modules.workspace.session_manager import get_session_manager

            sm = get_session_manager()
            session = sm.get_session(session_id)
            if session:
                session.total_input_tokens += input_tokens
                session.total_output_tokens += output_tokens
                session.total_tokens += input_tokens + output_tokens
                sm.update_session(session)

            # Refresh user_daily_stats so quota checks see up-to-date data
            try:
                from app.repositories.daily_stats_repo import DailyStatsRepository

                daily_stats_repo = DailyStatsRepository()
                daily_stats_repo.refresh_stats()
            except Exception:
                pass
    except Exception:
        pass


# ==================== Usage Report (from Agent) ====================


@remote_bp.route("/usage-report", methods=["POST"])
def usage_report():
    """Receive usage report from a remote agent."""
    data = request.get_json() or {}
    session_id = data.get("session_id")
    tokens = data.get("tokens", {})
    requests = data.get("requests", 1)
    data.get("machine_id")

    if not session_id:
        return jsonify({"error": "session_id is required"}), 400

    session_mgr = get_remote_session_manager()
    session_mgr.process_usage_report(
        session_id=session_id,
        tokens=tokens,
        requests=requests,
    )

    return jsonify({"success": True})


# ==================== Machine File Browse ====================


@remote_bp.route("/machines/<machine_id>/browse", methods=["GET"])
def browse_remote_directory(machine_id):
    """Browse the file system on a remote machine."""
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    agent_mgr = get_remote_agent_manager()

    # Check access
    if g.user.get("role") != "admin":
        if not agent_mgr.check_user_access(machine_id, g.user["id"]):
            return jsonify({"error": "Access denied"}), 403

    request.args.get("path")

    # Send browse command to agent and wait for response
    # For now, return machine info with work_dir
    machine = agent_mgr.get_machine(machine_id)
    if not machine:
        return jsonify({"error": "Machine not found"}), 404

    return jsonify(
        {
            "success": True,
            "machine": machine,
            "message": "File browsing requires WebSocket connection for real-time response",
        }
    )
