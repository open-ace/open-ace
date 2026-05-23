"""
Open ACE - Remote Workspace API Routes

API endpoints for remote workspace management including:
- Machine registration and management
- Remote session creation and control
- WebSocket agent communication
- LLM API proxy for remote CLI tools
- Usage reporting from remote agents
"""

import hmac
import json
import logging
import os
import time
import uuid

from flask import Blueprint, Response, g, jsonify, request, stream_with_context

from app.auth.decorators import _extract_token, _load_user_from_token, admin_required
from app.modules.workspace.api_key_proxy import get_api_key_proxy_service
from app.modules.workspace.remote_agent_manager import get_remote_agent_manager
from app.modules.workspace.remote_session_manager import get_remote_session_manager
from app.modules.workspace.terminal_store import terminal_info_store

logger = logging.getLogger(__name__)

MAX_RAW_CONTENT_LENGTH = 100000
MAX_MESSAGE_LENGTH = 50000

remote_bp = Blueprint("remote", __name__)


@remote_bp.before_request
def load_user():
    """Load the current user from session token before each request.

    Most remote endpoints require authentication. Returns 401 if no valid
    session token is provided. WebSocket, agent, and LLM-proxy endpoints
    use their own auth (JWT tokens) and are exempted.
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
    # Agent file downloads are public (needed for agent installation)
    if request.path.startswith("/api/remote/agent/files/"):
        return

    token = _extract_token()

    if token:
        user = _load_user_from_token(token)
        if user:
            g.user = user
            g.user_id = user.get("id")
            g.user_role = user.get("role")
            return None  # Authenticated

    # Special case: WebSocket proxy token for terminal status endpoint
    # The WebSocket proxy process uses its own token (stored in terminal_info_store)
    # to fetch terminal info for connecting to the remote terminal server
    if request.path.startswith("/api/remote/terminal/") and request.path.endswith("/status"):
        from app.modules.workspace.terminal_store import terminal_info_store

        # Extract terminal_id from path: /api/remote/terminal/{terminal_id}/status
        path_parts = request.path.split("/")
        if len(path_parts) >= 5:
            terminal_id = path_parts[4]
            machine_id = request.args.get("machine_id", "")
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

    # Fallback: try WebUI token validation (for iframe requests from qwen-code-webui)
    url_token = request.args.get("token")
    if url_token:
        try:
            from app.services.webui_manager import WebUIManager

            webui_manager = WebUIManager()
            is_valid, user_id, error = webui_manager.validate_token(url_token)
            if is_valid and user_id:
                from app.repositories.user_repo import UserRepository

                user_repo = UserRepository()
                user_data = user_repo.get_user_by_id(user_id)
                if user_data:
                    g.user = {
                        "id": user_id,
                        "username": user_data.get("username"),
                        "email": user_data.get("email"),
                        "role": user_data.get("role"),
                    }
                    g.user_id = user_id
                    g.user_role = user_data.get("role")
                    return None
        except Exception as e:
            logger.warning("Failed to validate URL token: %s", e)

    return jsonify({"error": "Authentication required"}), 401


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
        }
    )


@remote_bp.route("/machines/<machine_id>", methods=["GET"])
def get_machine(machine_id):
    """Get details and status of a specific machine."""

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
@admin_required
def deregister_machine(machine_id):
    """Deregister a remote machine. Admin only."""

    agent_mgr = get_remote_agent_manager()
    success = agent_mgr.deregister_machine(machine_id)

    if success:
        return jsonify({"success": True, "message": "Machine deregistered"})
    return jsonify({"error": "Machine not found"}), 404


@remote_bp.route("/machines/<machine_id>/assign", methods=["POST"])
def assign_user(machine_id):
    """Assign a user to a machine. System admin or machine admin."""
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
@admin_required
def list_api_keys():
    """List all encrypted API keys (without revealing actual keys). Admin only."""

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
@admin_required
def store_api_key():
    """Store a new encrypted API key. Admin only."""

    data = request.get_json() or {}
    provider = data.get("provider")
    key_name = data.get("key_name")
    api_key = data.get("api_key")
    base_url = data.get("base_url")
    tenant_id = int(data.get("tenant_id", 1))
    cli_tools = data.get("cli_tools")  # JSON array: ["claude-code", "qwen-code"]
    cli_settings = data.get(
        "cli_settings"
    )  # JSON object: {"claude-code": {...}, "qwen-code": {...}}

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
        cli_tools=cli_tools,
        cli_settings=cli_settings,
    )

    if result.get("success"):
        return jsonify({"success": True, "key": result})
    return jsonify({"error": result.get("error", "Failed to store API key")}), 400


@remote_bp.route("/api-keys/<int:key_id>", methods=["PUT"])
@admin_required
def update_api_key(key_id):
    """Update an API key by ID. Admin only."""

    data = request.get_json() or {}
    key_name = data.get("key_name")
    base_url = data.get("base_url")
    cli_tools = data.get("cli_tools")
    cli_settings = data.get("cli_settings")
    is_active = data.get("is_active")
    if is_active is not None and not isinstance(is_active, bool):
        return jsonify({"error": "is_active must be a boolean"}), 400
    tenant_id = int(data.get("tenant_id", 1))

    api_proxy = get_api_key_proxy_service()
    success = api_proxy.update_api_key_by_id(
        key_id=key_id,
        tenant_id=tenant_id,
        key_name=key_name,
        base_url=base_url,
        cli_tools=cli_tools,
        cli_settings=cli_settings,
        is_active=is_active,
    )

    if success:
        return jsonify({"success": True, "message": "API key updated"})
    return jsonify({"error": "API key not found or update failed"}), 404


@remote_bp.route("/api-keys/<int:key_id>", methods=["DELETE"])
@admin_required
def delete_api_key(key_id):
    """Delete an API key by ID. Admin only."""

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
def create_remote_session():
    """Create a new remote session on a selected machine."""

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

    result, access_error = _check_session_access(session_id)
    if access_error:
        return access_error

    if result:
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

    session_mgr = get_remote_session_manager()
    success = session_mgr.abort_request(session_id)

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
        if result.get("error") == "hostname_conflict":
            return jsonify({"error": result["message"]}), 409
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

    # Debug: log all non-poll agent messages
    if msg_type not in ("poll", "heartbeat"):
        import sys

        status = data.get("status", "")
        stream = data.get("stream", "")
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

    if msg_type == "register":
        # Agent re-registering on reconnect
        agent_mgr.register_connection(machine_id, None)
        capabilities = data.get("capabilities", {})
        if capabilities:
            agent_mgr.update_capabilities(machine_id, capabilities)
        pending = agent_mgr.get_pending_commands(machine_id)
        return jsonify({"success": True, "type": "register_ack", "pending_commands": pending})

    elif msg_type == "heartbeat":
        status = data.get("status", "idle")
        active_sessions = data.get("active_sessions", 0)
        capabilities = data.get("capabilities", {})
        agent_mgr.process_heartbeat(machine_id, status, active_sessions, capabilities=capabilities)
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
        sync_session_mgr = get_remote_session_manager()._session_manager
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
                    context={
                        "workspace_type": "terminal",
                        "remote_machine_id": machine_id,
                    },
                )
            else:
                # Update model/project_path if missing on existing session
                updates = {}
                if model and not existing.model:
                    updates["model"] = model
                if project_path and not existing.project_path:
                    updates["project_path"] = project_path
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
                        metadata = {"message_source": message_source}
                        if msg_uuid:
                            metadata["uuid"] = msg_uuid
                        if content_blocks:
                            metadata["content_blocks"] = content_blocks
                        if input_tokens or output_tokens:
                            metadata["input_tokens"] = input_tokens
                            metadata["output_tokens"] = output_tokens

                        sync_session_mgr.add_message(
                            session_id=session_id,
                            role=role,
                            content=content[:MAX_MESSAGE_LENGTH],
                            tokens_used=tokens_used,
                            model=msg_model,
                            metadata=metadata,
                        )
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
                        with get_db_connection() as conn:
                            cursor = conn.cursor()
                            cursor.execute(
                                adapt_sql(
                                    """INSERT OR IGNORE INTO daily_messages
                                    (date, tool_name, host_name, message_id, role, content,
                                     full_entry, tokens_used, input_tokens, output_tokens,
                                     model, timestamp, message_source,
                                     conversation_id, agent_session_id, project_path)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
                                ),
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
                                    project_path or "",
                                ),
                            )
                            conn.commit()
                    except Exception as e:
                        logger.debug("Failed to insert daily_message: %s", e)
            except Exception as e:
                logger.debug("Failed to mirror messages: %s", e)

        except Exception as e:
            logger.error("Failed to process session_sync: %s", e)

        return jsonify({"success": True})

    else:
        return jsonify({"error": f"Unknown message type: {msg_type}"}), 400


# ==================== Terminal Management ====================


@remote_bp.route("/terminal/start", methods=["POST"])
def start_terminal():
    """Start a web terminal on a remote machine."""
    data = request.get_json() or {}
    machine_id = data.get("machine_id")
    work_dir = data.get("work_dir")

    if not machine_id:
        return jsonify({"error": "machine_id is required"}), 400

    # Check access
    agent_mgr = get_remote_agent_manager()
    if g.user.get("role") != "admin":
        if not agent_mgr.check_user_access(machine_id, g.user["id"]):
            return jsonify({"error": "Access denied"}), 403

    # Get machine info for title/hostname
    machine = agent_mgr.get_machine(machine_id)
    machine_name = machine.get("machine_name", machine_id[:8]) if machine else machine_id[:8]
    hostname = machine.get("hostname", machine_id[:8]) if machine else machine_id[:8]

    # Generate terminal ID and proxy tokens for multiple providers
    terminal_id = str(uuid.uuid4())

    # Create agent_sessions record for terminal
    from app.modules.workspace.session_manager import get_session_manager

    sm = get_session_manager()
    sm.create_session(
        session_id=terminal_id,
        tool_name="claude-code",
        user_id=g.user["id"],
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

    # Get machine's tenant_id for token generation
    tenant_id = machine.get("tenant_id", 1) if machine else 1

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
    backend_url = agent_mgr.get_backend_url()
    proxy_url = f"{backend_url}/api/remote/llm-proxy"
    logger.info("start_terminal: backend_url=%s, proxy_url=%s", backend_url, proxy_url)

    # Get CLI settings for both Claude Code and Qwen Code
    cli_settings = {}
    for tool_name in ["claude-code", "qwen-code"]:
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
def start_cli_terminal():
    """Start an SSH/local CLI-backed terminal session.

    Unlike the web terminal flow, this endpoint does not ask the remote agent to
    spawn a PTY. The caller is already inside an SSH/local shell on the remote
    machine, so the server only creates the Open ACE session and returns short
    lived proxy tokens for local CLI processes.
    """
    data = request.get_json() or {}
    machine_id = data.get("machine_id")
    work_dir = data.get("work_dir") or ""
    source = data.get("source") or "ssh_cli"

    if not machine_id:
        return jsonify({"error": "machine_id is required"}), 400

    agent_mgr = get_remote_agent_manager()
    if g.user.get("role") != "admin":
        if not agent_mgr.check_user_access(machine_id, g.user["id"]):
            return jsonify({"error": "Access denied"}), 403

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

    backend_url = agent_mgr.get_backend_url()
    proxy_url = f"{backend_url}/api/remote/llm-proxy"

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
                "tokens": {
                    "anthropic": anthropic_token,
                    "openai": openai_token,
                },
            },
        }
    )


@remote_bp.route("/terminal/stop", methods=["POST"])
def stop_terminal():
    """Stop a web terminal on a remote machine."""
    data = request.get_json() or {}
    terminal_id = data.get("terminal_id")
    machine_id = data.get("machine_id")

    if not terminal_id or not machine_id:
        return jsonify({"error": "terminal_id and machine_id are required"}), 400

    # Check access
    agent_mgr = get_remote_agent_manager()
    if g.user.get("role") != "admin":
        if not agent_mgr.check_user_access(machine_id, g.user["id"]):
            return jsonify({"error": "Access denied"}), 403

    cmd = {
        "type": "command",
        "command": "stop_terminal",
        "terminal_id": terminal_id,
    }
    agent_mgr.send_command(machine_id, cmd)

    # Clean up local store
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
    backend_url = agent_mgr.get_backend_url()
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


# ==================== LLM Proxy ====================


@remote_bp.route("/llm-proxy", methods=["POST", "HEAD"])
@remote_bp.route("/llm-proxy/<path:path>", methods=["GET", "POST", "PUT", "DELETE", "HEAD"])
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
    # Extract proxy token from Authorization header or x-api-key (Claude Code)
    auth_header = request.headers.get("Authorization", "")
    proxy_token = auth_header.replace("Bearer ", "").strip()
    if not proxy_token:
        proxy_token = request.headers.get("x-api-key", "").strip()

    if not proxy_token:
        if request.method == "HEAD":
            return "", 401
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
        # If base_url already ends with /v1, strip the /v1 prefix from path
        if target_base.endswith("/v1"):
            target_base = target_base[:-3]  # Remove trailing /v1
    else:
        # Default provider URLs
        provider_urls = {
            "openai": "https://api.openai.com",
            "anthropic": "https://api.anthropic.com",
            "google": "https://generativelanguage.googleapis.com",
        }
        target_base = provider_urls.get(provider, "https://api.openai.com")

    if path:
        # For Anthropic provider, keep the full path including /v1 prefix
        # (z.ai proxy needs /v1/messages, not /messages)
        target_url = f"{target_base}/{path}"
    else:
        # Handle direct path in the request URL
        target_url = f"{target_base}{request.path.split('/llm-proxy')[-1]}"

    # Log model from request body
    try:
        _body_json = json.loads(request.get_data())
        _model = _body_json.get("model", "?")
    except Exception:
        _model = "?"
    logger.info(
        "LLM proxy: %s -> %s model=%s provider=%s", request.method, target_url, _model, provider
    )

    # Log response status for debugging
    _orig_target_url = target_url

    # ------------------------------------------------------------------
    # Responses API → Chat Completions conversion for non-OpenAI providers
    # ------------------------------------------------------------------
    # Codex CLI uses the OpenAI Responses API (POST /v1/responses) which
    # many third-party providers (dashscope, etc.) do not support. When
    # the target provider is not the real OpenAI and the request targets
    # /v1/responses, we convert the request body to Chat Completions
    # format and forward to /v1/chat/completions instead.
    _converted_from_responses = False
    # Check if the target provider actually supports the Responses API.
    # Only the real OpenAI API (api.openai.com) supports /v1/responses.
    # Third-party OpenAI-compatible providers (dashscope, etc.) typically
    # only support /v1/chat/completions, so we convert the request.
    _is_real_openai = "api.openai.com" in target_url
    if path and path.endswith("/responses") and not _is_real_openai:
        try:
            resp_body = json.loads(request.get_data())
            messages = []
            # Extract input: can be a string or array of content items
            input_data = resp_body.get("input", "")
            if isinstance(input_data, str):
                messages.append({"role": "user", "content": input_data})
            elif isinstance(input_data, list):
                # Build message list from input array
                for item in input_data:
                    if isinstance(item, dict):
                        role = item.get("role", "user")
                        content = item.get("content", "")
                        if isinstance(content, list):
                            # Multi-part content
                            content = " ".join(
                                p.get("text", "") for p in content if isinstance(p, dict)
                            )
                        # Map unsupported roles for non-OpenAI providers
                        if role == "developer":
                            role = "system"
                        messages.append({"role": role, "content": content or ""})

            # Add instructions as system message if present
            instructions = resp_body.get("instructions")
            if instructions:
                messages.insert(0, {"role": "system", "content": instructions})

            if not messages:
                messages.append({"role": "user", "content": ""})

            cc_body = {
                "model": resp_body.get("model", ""),
                "messages": messages,
                "stream": False,  # Non-streaming: we'll convert the response
            }
            if resp_body.get("max_output_tokens"):
                cc_body["max_tokens"] = resp_body["max_output_tokens"]
            if resp_body.get("temperature") is not None:
                cc_body["temperature"] = resp_body["temperature"]

            # Rewrite target URL to /v1/chat/completions
            target_url = target_url.replace("/responses", "/chat/completions")
            body = json.dumps(cc_body).encode("utf-8")
            _converted_from_responses = True
            logger.info(
                "LLM proxy: converted Responses API -> Chat Completions for %s",
                target_url,
            )
        except Exception as e:
            logger.warning("Failed to convert Responses API request: %s", e)

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
        if not _converted_from_responses:
            body = request.get_data()

        # Forward the request (bypass system proxy to avoid interference)
        resp = http_requests.request(
            method=request.method,
            url=target_url,
            headers=fwd_headers,
            data=body,
            stream=True,
            timeout=120,
            proxies={"http": None, "https": None},  # type: ignore[dict-item]
        )

        if resp.status_code >= 400:
            peek = (
                resp.content[:500]
                if not resp.headers.get("Content-Type", "").startswith("text/event-stream")
                else b""
            )
            logger.error(
                f"LLM proxy error {resp.status_code} from {_orig_target_url}: {peek.decode('utf-8', errors='replace')}"
            )

        # If we converted from Responses API, convert the Chat Completions
        # response back to Responses API format
        if _converted_from_responses and resp.status_code == 200:
            try:
                cc_resp = resp.json()
                # Build a minimal Responses API compatible response
                response_id = f"resp_{cc_resp.get('id', 'default')}"
                model = cc_resp.get("model", "")
                output_text = ""
                if cc_resp.get("choices"):
                    output_text = cc_resp["choices"][0].get("message", {}).get("content", "")
                usage = cc_resp.get("usage", {})

                # Return SSE stream in Responses API format
                import uuid as _uuid

                item_id = f"msg_{_uuid.uuid4().hex[:24]}"
                events: list[dict] = [
                    {
                        "type": "response.created",
                        "response": {
                            "id": response_id,
                            "object": "response",
                            "status": "in_progress",
                            "model": model,
                            "output": [],
                            "usage": None,
                        },
                    },
                    {
                        "type": "response.output_item.added",
                        "output_index": 0,
                        "item": {
                            "type": "message",
                            "id": item_id,
                            "status": "in_progress",
                            "role": "assistant",
                            "content": [],
                        },
                    },
                    {
                        "type": "response.content_part.added",
                        "output_index": 0,
                        "content_index": 0,
                        "part": {"type": "output_text", "text": ""},
                    },
                    {
                        "type": "response.output_text.delta",
                        "output_index": 0,
                        "content_index": 0,
                        "delta": output_text,
                    },
                    {
                        "type": "response.output_text.done",
                        "output_index": 0,
                        "content_index": 0,
                        "text": output_text,
                    },
                    {
                        "type": "response.content_part.done",
                        "output_index": 0,
                        "content_index": 0,
                        "part": {"type": "output_text", "text": output_text},
                    },
                    {
                        "type": "response.output_item.done",
                        "output_index": 0,
                        "item": {
                            "type": "message",
                            "id": item_id,
                            "status": "completed",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": output_text}],
                        },
                    },
                    {
                        "type": "response.completed",
                        "response": {
                            "id": response_id,
                            "object": "response",
                            "status": "completed",
                            "model": model,
                            "output": [
                                {
                                    "type": "message",
                                    "id": item_id,
                                    "status": "completed",
                                    "role": "assistant",
                                    "content": [{"type": "output_text", "text": output_text}],
                                }
                            ],
                            "usage": (
                                {
                                    "input_tokens": usage.get("prompt_tokens", 0),
                                    "output_tokens": usage.get("completion_tokens", 0),
                                    "total_tokens": usage.get("total_tokens", 0),
                                }
                                if usage
                                else None
                            ),
                        },
                    },
                ]

                def sse_stream():
                    for event in events:
                        yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"

                return Response(
                    sse_stream(),
                    status=200,
                    content_type="text/event-stream",
                )
            except Exception as e:
                logger.error("Failed to convert CC response to Responses format: %s", e)
                # Fall through to normal response handling

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

        logger.info(
            "Recording LLM usage: session_id=%s, input=%d, output=%d, provider=%s",
            session_id[:8],
            input_tokens,
            output_tokens,
            provider,
        )

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
                # Handle None values for token counts (new sessions)
                session.total_input_tokens = (session.total_input_tokens or 0) + input_tokens
                session.total_output_tokens = (session.total_output_tokens or 0) + output_tokens
                session.total_tokens = (session.total_tokens or 0) + input_tokens + output_tokens
                session.request_count = (
                    session.request_count or 0
                ) + 1  # Increment request count for each API call
                sm.update_session(session)
                logger.info(
                    "Updated session %s tokens: input=%d, output=%d, requests=%d",
                    session_id[:8],
                    session.total_input_tokens,
                    session.total_output_tokens,
                    session.request_count,
                )
            else:
                logger.warning(
                    "Session %s not found for usage recording",
                    session_id[:8],
                )

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
    """Browse the file system on a remote machine.

    Returns directory information for the specified path.
    If no path is specified, returns the machine's work_dir as the default.

    Note: Full directory browsing requires sending commands to the remote agent.
    This simplified implementation returns machine info with work_dir for basic usage.
    """

    # Check authentication - g.user may not be set if before_request auth failed
    # (e.g., invalid/expired token, missing session) - Issue #477
    if not hasattr(g, "user") or g.user is None:
        return jsonify({"error": "Unauthorized"}), 401

    agent_mgr = get_remote_agent_manager()

    # Check access
    if g.user.get("role") != "admin":
        if not agent_mgr.check_user_access(machine_id, g.user["id"]):
            return jsonify({"error": "Access denied"}), 403

    path = request.args.get("path")

    # Get machine info
    machine = agent_mgr.get_machine(machine_id)
    if not machine:
        return jsonify({"error": "Machine not found"}), 404

    # Determine the path to browse
    work_dir = machine.get("work_dir") or "/root/workspace"
    browse_path = path or work_dir

    # Return a simplified response compatible with frontend expectations
    # The frontend can use the work_dir as the default project path
    return jsonify(
        {
            "success": True,
            "result": {
                "path": browse_path,
                "name": browse_path.split("/")[-1] or "/",
                "directories": [],  # Full browsing requires remote agent command
                "parent": browse_path.rsplit("/", 1)[0] if "/" in browse_path else None,
                "homePath": work_dir,
                "canCreate": True,  # Assume user can create in work_dir
                "is_writable": True,
            },
            "machine": machine,
            "message": "Full directory browsing requires remote agent support. "
            "Use the machine's work_dir as the default project path.",
        }
    )
