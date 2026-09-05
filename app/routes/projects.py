from __future__ import annotations

"""
Open ACE - Project Routes

API routes for project management operations.
"""

import logging
import os
import platform
import pwd
import subprocess
from datetime import datetime, timezone

import sqlalchemy as sa
from flask import Blueprint, g, jsonify, request

from app.auth.decorators import (
    _extract_token,
    _load_user_from_token,
    enforce_password_change_requirement,
    normalize_webui_token,
    require_tenant_scope,
    security_annotated,
)
from app.models.user import User
from app.repositories.project_repo import ProjectRepository
from app.repositories.user_repo import UserRepository
from app.services.permission_task_service import (
    PERMISSION_MAX_QUEUE_SIZE,
    PERMISSION_PRIORITY_AUTO_CREATE,
    PERMISSION_PRIORITY_MANUAL_FIX,
    PERMISSION_SYNC_THRESHOLD,
    get_permission_task_service,
)
from app.utils.request_context import get_current_tenant_id
from app.utils.validators import validate_project_name
from app.utils.workspace import (
    _is_docker_multi_user_mode,
    estimate_file_count_fast,
    setup_permissions_with_depth_limit,
)

logger = logging.getLogger(__name__)

projects_bp = Blueprint("projects", __name__)
project_repo = ProjectRepository()
user_repo = UserRepository()


@projects_bp.before_request
def _authenticate_user():
    """Authenticate via session token or WebUI token."""
    token = _extract_token()
    if token:
        user_data = _load_user_from_token(token)
        if user_data:
            user = user_repo.get_user_by_id(int(user_data.get("id", 0)))
            if user:
                g.user = user  # Store full user object for system_account access
                g.user_id = user.get("id")
                g.user_role = user.get("role")
                g.tenant_id = user.get("tenant_id")
                password_change_response = enforce_password_change_requirement(user)
                if password_change_response is not None:
                    return password_change_response
                return None

    # Fallback: try WebUI token from query param
    url_token = request.args.get("token")
    if url_token:
        # Handle double-encoded tokens from some clients
        url_token = normalize_webui_token(url_token)
        from app.services.webui_manager import get_webui_manager

        manager = get_webui_manager()
        if manager:
            valid, user_id, error = manager.validate_token(url_token)
            if valid and user_id:
                user = user_repo.get_user_by_id(user_id)
                if user:
                    g.user = user  # Store full user object for system_account access
                    g.user_id = user_id
                    g.user_role = user.get("role")
                    g.tenant_id = user.get("tenant_id")
                    password_change_response = enforce_password_change_requirement(user)
                    if password_change_response is not None:
                        return password_change_response
                    return None

    return jsonify({"error": "Authentication required"}), 401


@projects_bp.before_request
def _require_tenant_scope():
    """Fail closed for non-admins with no tenant (Issue #1775).

    Without this gate, ``get_current_tenant_id()`` returns ``None`` and the
    project repository treats it as a wildcard/global filter, leaking
    cross-tenant projects to a no-tenant non-admin. Admins keep global
    scope; tenant-scoped non-admins keep their tenant.

    The list endpoint ``GET /api/projects`` is exempted so a no-tenant
    non-admin still receives an empty list (rather than a 403) — this
    unblocks the qwen-code-webui new-session picker, which otherwise
    surfaces "Failed to fetch projects: FORBIDDEN". The handler itself
    returns an empty result without touching the repository when the
    tenant scope is missing, so no wildcard leak occurs (Issue #1859).
    """
    if request.endpoint == "projects.api_get_projects" and request.method == "GET":
        return None
    _, error = require_tenant_scope()
    if error is not None:
        return error


def get_effective_system_account(system_account: str | None) -> str | None:
    """Check if current user is already the target user.

    When NoNewPrivileges=true is set in systemd, sudo is blocked.
    If the process is already running as the target user, we can skip sudo.

    Returns None if current user matches target user, otherwise returns system_account.
    """
    if not system_account:
        return None

    current_user = pwd.getpwuid(os.getuid()).pw_name
    if current_user == system_account:
        return None

    return system_account


def run_as_user(system_account: str, command: list) -> subprocess.CompletedProcess:
    """Run a command as a specific user using sudo."""
    sudo_cmd = ["sudo", "-u", system_account] + command
    return subprocess.run(sudo_cmd, capture_output=True, text=True, timeout=30, cwd="/tmp")


@projects_bp.route("/projects", methods=["GET"])
def api_get_projects():
    """Get projects accessible by current user."""
    user_id = g.user_id
    tenant_id = get_current_tenant_id()

    # Non-admin without a tenant has no accessible projects — return an
    # empty list instead of falling through to the repository with
    # ``tenant_id=None`` (which is treated as a wildcard and would leak
    # cross-tenant data). The before_request gate exempts this route from
    # the 403 so the qwen-code-webui project picker still renders.
    # Admins keep their global scope when their own tenant_id is unset.
    # (Issue #1859)
    if tenant_id is None:
        user = getattr(g, "user", None) or {}
        if not User.is_admin_role(user.get("role")):
            return jsonify({"success": True, "projects": []})

    # Get user's projects
    projects = project_repo.get_user_projects(user_id, tenant_id=tenant_id)

    # Also include shared projects
    all_projects = project_repo.get_all_projects(tenant_id=tenant_id)
    for p in all_projects:
        if p.is_shared and p.id not in [proj.id for proj in projects]:
            projects.append(p)

    return jsonify(
        {
            "success": True,
            "projects": [p.to_dict() for p in projects],
        }
    )


@projects_bp.route("/projects", methods=["POST"])
def api_create_project():
    """Create a new project."""
    user_id = g.user_id
    tenant_id = get_current_tenant_id()
    system_account = g.user.get("system_account") if g.user else None
    data = request.get_json() or {}

    path = data.get("path")
    name = data.get("name")
    description = data.get("description")
    is_shared = data.get("is_shared", False)
    create_dir = data.get("create_dir", True)

    # Issue #2897: Validate project name to prevent XSS and path injection
    if name:
        is_valid, error_msg = validate_project_name(name)
        if not is_valid:
            return jsonify({"error": error_msg}), 400

    # Validate path
    if not path:
        return jsonify({"error": "Path is required"}), 400

    path = os.path.abspath(path)

    # Check if path is absolute
    if not os.path.isabs(path):
        return jsonify({"error": "Path must be absolute"}), 400

    # Check path format based on platform
    system = platform.system()
    if system == "Windows":
        if not (len(path) >= 2 and path[1] == ":"):
            return jsonify({"error": "Invalid Windows path format"}), 400
    else:
        if not path.startswith("/"):
            return jsonify({"error": "Path must start with /"}), 400

    # Check for path traversal
    if ".." in path:
        return jsonify({"error": "Path traversal not allowed"}), 400

    # Check if project already exists
    existing = project_repo.get_project_by_path(path, tenant_id=tenant_id)
    if existing:
        return jsonify({"error": "Project already exists", "project": existing.to_dict()}), 409

    # Create directory if requested and doesn't exist
    dir_created = False
    if create_dir:
        try:
            effective_system_account = get_effective_system_account(system_account)
            if effective_system_account:
                # Use sudo to check and create directory as the user
                result = run_as_user(effective_system_account, ["test", "-e", path])
                path_exists = result.returncode == 0

                if not path_exists:
                    # Create directory using sudo mkdir -p
                    result = run_as_user(effective_system_account, ["mkdir", "-p", path])
                    if result.returncode != 0:
                        logger.error(
                            f"Failed to create directory as {system_account}: {result.stderr}"
                        )
                        return (
                            jsonify(
                                {"error": f"Permission denied to create directory: {result.stderr}"}
                            ),
                            403,
                        )
                    dir_created = True
                    logger.info(f"Created project directory as {system_account}: {path}")
                else:
                    # Check if it's a directory
                    result = run_as_user(effective_system_account, ["test", "-d", path])
                    if result.returncode != 0:
                        return jsonify({"error": "Path exists but is not a directory"}), 400
            else:
                # Already running as target user or no system_account, use direct permissions
                if not os.path.exists(path):
                    os.makedirs(path, exist_ok=True)
                    dir_created = True
                    logger.info(f"Created project directory: {path}")
                elif not os.path.isdir(path):
                    return jsonify({"error": "Path exists but is not a directory"}), 400
        except PermissionError:
            return jsonify({"error": "Permission denied to create directory"}), 403
        except subprocess.TimeoutExpired:
            return jsonify({"error": "Timeout creating directory"}), 500
        except Exception as e:
            logger.error(f"Error creating directory: {e}")
            return jsonify({"error": "Failed to create directory"}), 500

    # Issue #2730 + #2746: Set shared project permissions for Docker multi-user mode
    # Note: Permission setup will happen after project creation

    # Create project in database
    project_id = project_repo.create_project(
        path=path,
        name=name,
        description=description,
        created_by=user_id,
        is_shared=is_shared,
        tenant_id=tenant_id,
    )

    if project_id:
        # Issue #2730 + #2746: Set shared project permissions
        permission_warning = None
        if is_shared and _is_docker_multi_user_mode():
            # Estimate file count to determine sync vs async
            file_count = estimate_file_count_fast(path)

            if file_count < PERMISSION_SYNC_THRESHOLD:
                # Small project: use optimized synchronous setup
                success, error_msg, files_processed = setup_permissions_with_depth_limit(
                    path,
                    depth_limit=None,  # No depth limit for small projects
                    timeout=60,
                    user_id=user_id,  # Issue #2745: Pass user_id for audit log
                    project_id=project_id,  # Issue #2745: Pass project_id for audit log
                )
                if not success:
                    logger.error(f"Failed to setup shared permissions: {error_msg}")
                    # Update project status to failed
                    from app import db  # type: ignore[attr-defined]  # type: ignore[attr-defined]

                    db.execute(
                        sa.text("""
                            UPDATE projects
                            SET permission_status = 'failed'
                            WHERE id = :project_id
                        """),
                        {"project_id": project_id},
                    )
                    db.commit()
                    permission_warning = f"Permission setup failed: {error_msg}"
            else:
                # Large project: submit async task
                from app import db  # type: ignore[attr-defined]  # type: ignore[attr-defined]

                service = get_permission_task_service()
                success, error_msg, task_info = service.submit_task(
                    db.session,
                    project_id=project_id,
                    user_id=user_id,
                    path=path,
                    priority=PERMISSION_PRIORITY_AUTO_CREATE,
                )

                if success and task_info:
                    logger.info(f"Submitted async permission task {task_info['task_id']}")

        project = project_repo.get_project_by_id(project_id, tenant_id=tenant_id)
        if project is None:
            return jsonify({"error": "Project not found"}), 404

        response = {
            "success": True,
            "project": project.to_dict(),
            "dir_created": dir_created,
        }

        if permission_warning:
            response["permission_warning"] = permission_warning

        return jsonify(response), 201

    return jsonify({"error": "Failed to create project"}), 500


@projects_bp.route("/projects/<int:project_id>", methods=["GET"])
@security_annotated(reason="Ownership via get_user_project + is_shared flag check")
def api_get_project(project_id):
    """Get project details."""
    tenant_id = get_current_tenant_id()
    project = project_repo.get_project_by_id(project_id, tenant_id=tenant_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404

    # Check if user has access
    user_id = g.user_id
    user_project = project_repo.get_user_project(user_id, project_id, tenant_id=tenant_id)

    if not user_project and not project.is_shared:
        return jsonify({"error": "Access denied"}), 403

    # Get project stats
    stats = project_repo.get_project_stats(project_id, tenant_id=tenant_id)

    return jsonify(
        {
            "success": True,
            "project": project.to_dict(),
            "stats": stats.to_dict() if stats else None,
        }
    )


@projects_bp.route("/projects/<int:project_id>", methods=["PUT"])
def api_update_project(project_id):
    """Update project information."""
    tenant_id = get_current_tenant_id()
    project = project_repo.get_project_by_id(project_id, tenant_id=tenant_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404

    # Only creator or admin can update
    user_id = g.user_id
    user_role = g.user.get("role")

    if project.created_by != user_id and not User.is_admin_role(user_role):
        return jsonify({"error": "Only project creator or admin can update"}), 403

    data = request.get_json() or {}
    name = data.get("name")
    description = data.get("description")
    is_shared = data.get("is_shared")

    # Issue #2897: Validate project name to prevent XSS and path injection
    if name is not None:
        is_valid, error_msg = validate_project_name(name)
        if not is_valid:
            return jsonify({"error": error_msg}), 400

    # Issue #2730 + #2746: Set permissions when is_shared changes from False to True
    if is_shared is True and not project.is_shared:
        if _is_docker_multi_user_mode():
            # Estimate file count
            file_count = estimate_file_count_fast(project.path)

            if file_count < PERMISSION_SYNC_THRESHOLD:
                # Small project: synchronous setup
                success, error_msg, files_processed = setup_permissions_with_depth_limit(
                    project.path,
                    depth_limit=None,
                    timeout=60,
                    user_id=user_id,  # Issue #2745: Pass user_id for audit log
                    project_id=project_id,  # Issue #2745: Pass project_id for audit log
                )
                if not success:
                    logger.error(f"Failed to setup shared permissions: {error_msg}")
                    return (
                        jsonify({"error": f"Failed to setup shared permissions: {error_msg}"}),
                        500,
                    )
            else:
                # Large project: submit async task
                from app import db  # type: ignore[attr-defined]  # type: ignore[attr-defined]

                service = get_permission_task_service()
                success, error_msg, task_info = service.submit_task(
                    db.session,
                    project_id=project_id,
                    user_id=user_id,
                    path=project.path,
                    priority=PERMISSION_PRIORITY_AUTO_CREATE,
                )

                if not success:
                    return jsonify({"error": error_msg}), 503

    success = project_repo.update_project(
        project_id=project_id,
        name=name,
        description=description,
        is_shared=is_shared,
        tenant_id=tenant_id,
    )

    if success:
        project = project_repo.get_project_by_id(project_id, tenant_id=tenant_id)
        if project is None:
            return jsonify({"error": "Project not found"}), 404
        return jsonify({"success": True, "project": project.to_dict()})

    return jsonify({"error": "Failed to update project"}), 500


@projects_bp.route("/projects/<int:project_id>", methods=["DELETE"])
def api_delete_project(project_id):
    """Delete a project (soft delete)."""
    tenant_id = get_current_tenant_id()
    project = project_repo.get_project_by_id(project_id, tenant_id=tenant_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404

    # Only creator or admin can delete
    user_id = g.user_id
    user_role = g.user.get("role")

    if project.created_by != user_id and not User.is_admin_role(user_role):
        return jsonify({"error": "Only project creator or admin can delete"}), 403

    # Soft delete
    success = project_repo.delete_project(project_id, soft_delete=True, tenant_id=tenant_id)

    if success:
        return jsonify({"success": True, "message": "Project deleted"})

    return jsonify({"error": "Failed to delete project"}), 500


@projects_bp.route("/projects/stats", methods=["GET"])
def api_get_all_project_stats():
    """Get statistics for all projects (admin only)."""
    if not User.is_admin_role(g.user.get("role")):
        return jsonify({"error": "Admin access required"}), 403

    stats = project_repo.get_all_project_stats(tenant_id=get_current_tenant_id())

    return jsonify(
        {
            "success": True,
            "stats": [s.to_dict() for s in stats],
        }
    )


@projects_bp.route("/projects/<int:project_id>/daily", methods=["GET"])
@security_annotated(reason="Ownership via get_user_project + is_shared + admin check")
def api_get_project_daily_stats(project_id):
    """Get daily statistics for a project."""
    tenant_id = get_current_tenant_id()
    project = project_repo.get_project_by_id(project_id, tenant_id=tenant_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404

    # Check access
    user_id = g.user_id
    user_project = project_repo.get_user_project(user_id, project_id, tenant_id=tenant_id)

    if not user_project and not project.is_shared and not User.is_admin_role(g.user.get("role")):
        return jsonify({"error": "Access denied"}), 403

    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    stats = project_repo.get_project_daily_stats(
        project_id=project_id,
        start_date=start_date,
        end_date=end_date,
        tenant_id=tenant_id,
    )

    return jsonify(
        {
            "success": True,
            "stats": [s.to_dict() for s in stats],
        }
    )


@projects_bp.route("/projects/<int:project_id>/users", methods=["GET"])
def api_get_project_users(project_id):
    """Get users collaborating on a project."""
    tenant_id = get_current_tenant_id()
    project = project_repo.get_project_by_id(project_id, tenant_id=tenant_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404

    # Check access
    user_id = g.user_id
    user_project = project_repo.get_user_project(user_id, project_id, tenant_id=tenant_id)

    if not user_project and not project.is_shared and not User.is_admin_role(g.user.get("role")):
        return jsonify({"error": "Access denied"}), 403

    user_stats = project_repo.get_project_users(project_id, tenant_id=tenant_id)

    # Add username to each user stat
    result = []
    for us in user_stats:
        us_dict = us.to_dict()
        user_info = user_repo.get_user_by_id(us.user_id)
        if user_info:
            us_dict["username"] = user_info.get("username")
        result.append(us_dict)

    return jsonify(
        {
            "success": True,
            "users": result,
        }
    )


# ============================================================================
# Project User Management API (Issue #3275)
# ============================================================================


@projects_bp.route("/projects/<int:project_id>/users", methods=["POST"])
def api_add_project_user(project_id):
    """Add a user to a shared project.

    Issue #3275: Allows project creator or admin to add a visible user.

    Request body:
        - user_id: int - User ID to add

    Returns:
        JSON response with success status or error message.
    """
    tenant_id = get_current_tenant_id()
    project = project_repo.get_project_by_id(project_id, tenant_id=tenant_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404

    # Permission check: only creator or admin can manage users
    user_id = g.user_id
    user_role = g.user.get("role")

    if project.created_by != user_id and not User.is_admin_role(user_role):
        return jsonify({"error": "Only project creator or admin can manage users"}), 403

    # Parse request
    data = request.get_json() or {}
    target_user_id = data.get("user_id")

    if not target_user_id:
        return jsonify({"error": "user_id is required"}), 400

    # Get target user
    target_user = user_repo.get_user_by_id(target_user_id)
    if not target_user:
        return jsonify({"error": "User not found"}), 404

    # Tenant isolation: can only add users from the same tenant
    target_tenant_id = target_user.get("tenant_id")
    project_tenant_id = project.tenant_id

    if target_tenant_id != project_tenant_id:
        return jsonify({"error": "Cannot add user from different tenant"}), 403

    # Check if running in Docker multi-user mode
    if not _is_docker_multi_user_mode():
        return jsonify({"error": "User management only available in Docker multi-user mode"}), 400

    # Get target user's system_account
    target_system_account = target_user.get("system_account")
    if not target_system_account:
        return jsonify({"error": "User has no system account, cannot manage file permissions"}), 400

    # Add user to shared group (file system permission)
    from app.utils.workspace import add_user_to_shared_group

    if not add_user_to_shared_group(target_system_account):
        return jsonify({"error": "Failed to add user to shared group"}), 500

    # Add user to project in database
    project_repo.add_user_project(target_user_id, project_id)

    # Record audit log
    _log_project_user_audit(
        action="project_user_add",
        user_id=user_id,
        project_id=project_id,
        target_user_id=target_user_id,
        tenant_id=tenant_id,
    )

    logger.info(f"User {target_user_id} added to project {project_id} by {user_id}")

    return jsonify(
        {
            "success": True,
            "message": "User added successfully",
            "user_id": target_user_id,
        }
    )


@projects_bp.route("/projects/<int:project_id>/users/<int:target_user_id>", methods=["DELETE"])
def api_remove_project_user(project_id, target_user_id):
    """Remove a user from a shared project.

    Issue #3275: Allows project creator or admin to remove a visible user.

    Returns:
        JSON response with success status or error message.
        If user has active sessions, includes active_sessions count.
    """
    tenant_id = get_current_tenant_id()
    project = project_repo.get_project_by_id(project_id, tenant_id=tenant_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404

    # Permission check: only creator or admin can manage users
    user_id = g.user_id
    user_role = g.user.get("role")

    if project.created_by != user_id and not User.is_admin_role(user_role):
        return jsonify({"error": "Only project creator or admin can manage users"}), 403

    # Creator protection: cannot remove project creator
    if project.created_by == target_user_id:
        return jsonify({"error": "Cannot remove project creator"}), 403

    # Get target user
    target_user = user_repo.get_user_by_id(target_user_id)
    if not target_user:
        return jsonify({"error": "User not found"}), 404

    # Check if running in Docker multi-user mode
    if not _is_docker_multi_user_mode():
        return jsonify({"error": "User management only available in Docker multi-user mode"}), 400

    # Check for active sessions
    from app.utils.workspace import get_user_project_active_sessions

    active_sessions = get_user_project_active_sessions(target_user_id, project_id)

    # Get target user's system_account
    target_system_account = target_user.get("system_account")

    # Remove user from project in database
    if not project_repo.remove_user_project(target_user_id, project_id, tenant_id=tenant_id):
        return jsonify({"error": "Failed to remove user from project"}), 500

    # Remove user from shared group (file system permission)
    if target_system_account:
        from app.utils.workspace import remove_user_from_shared_group

        if not remove_user_from_shared_group(target_system_account):
            logger.warning(f"Failed to remove {target_system_account} from shared group")

    # Record audit log
    _log_project_user_audit(
        action="project_user_remove",
        user_id=user_id,
        project_id=project_id,
        target_user_id=target_user_id,
        tenant_id=tenant_id,
    )

    logger.info(f"User {target_user_id} removed from project {project_id} by {user_id}")

    response = {
        "success": True,
        "message": "User removed successfully",
        "user_id": target_user_id,
    }

    # Include active sessions count if > 0
    if active_sessions > 0:
        response["active_sessions"] = active_sessions
        response["warning"] = f"User has {active_sessions} active session(s)"

    return jsonify(response)


@projects_bp.route("/projects/<int:project_id>/users", methods=["PUT"])
def api_batch_update_project_users(project_id):
    """Batch update visible users for a shared project.

    Issue #3275: Allows project creator or admin to batch update user list.
    Maximum 50 users per batch.

    Request body:
        - user_ids: list[int] - List of target user IDs

    Returns:
        JSON response with added/removed/existing user lists.
    """
    tenant_id = get_current_tenant_id()
    project = project_repo.get_project_by_id(project_id, tenant_id=tenant_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404

    # Permission check: only creator or admin can manage users
    user_id = g.user_id
    user_role = g.user.get("role")

    if project.created_by != user_id and not User.is_admin_role(user_role):
        return jsonify({"error": "Only project creator or admin can manage users"}), 403

    # Parse request
    data = request.get_json() or {}
    target_user_ids = data.get("user_ids", [])

    # Validate batch size
    if len(target_user_ids) > 50:
        return jsonify({"error": "Maximum 50 users per batch operation"}), 400

    # Check if running in Docker multi-user mode
    if not _is_docker_multi_user_mode():
        return jsonify({"error": "User management only available in Docker multi-user mode"}), 400

    # Track group operations for rollback
    added_accounts = []
    operation_errors = []

    # Get current users
    current_users = project_repo.get_project_users(project_id, tenant_id=tenant_id)
    current_user_ids = {u.user_id for u in current_users}
    target_user_id_set = set(target_user_ids)

    # Calculate differences
    to_add = target_user_id_set - current_user_ids
    to_remove = current_user_ids - target_user_id_set

    # Creator protection: cannot remove project creator
    if project.created_by in to_remove:
        to_remove.discard(project.created_by)
        operation_errors.append("Cannot remove project creator")

    # Add users
    from app.utils.workspace import add_user_to_shared_group

    for target_user_id in to_add:
        target_user = user_repo.get_user_by_id(target_user_id)
        if not target_user:
            operation_errors.append(f"User {target_user_id} not found")
            continue

        # Tenant isolation
        target_tenant_id = target_user.get("tenant_id")
        if target_tenant_id != project.tenant_id:
            operation_errors.append(f"User {target_user_id} is from different tenant")
            continue

        # Get system_account
        target_system_account = target_user.get("system_account")
        if not target_system_account:
            operation_errors.append(f"User {target_user_id} has no system account")
            continue

        # Add to shared group
        if not add_user_to_shared_group(target_system_account):
            operation_errors.append(f"Failed to add user {target_user_id} to shared group")
            # Rollback previous additions
            for account in added_accounts:
                from app.utils.workspace import remove_user_from_shared_group

                remove_user_from_shared_group(account)
            return jsonify({"error": f"Failed to add user {target_user_id} to shared group"}), 500

        added_accounts.append(target_system_account)

        # Add to database
        project_repo.add_user_project(target_user_id, project_id)

    # Remove users
    for target_user_id in to_remove:
        target_user = user_repo.get_user_by_id(target_user_id)
        if target_user:
            target_system_account = target_user.get("system_account")
            if target_system_account:
                from app.utils.workspace import remove_user_from_shared_group

                if not remove_user_from_shared_group(target_system_account):
                    logger.warning(f"Failed to remove {target_system_account} from shared group")

        # Remove from database
        project_repo.remove_user_project(target_user_id, project_id, tenant_id=tenant_id)

    # Record audit log
    _log_project_user_audit(
        action="project_user_batch_update",
        user_id=user_id,
        project_id=project_id,
        target_user_id=None,
        tenant_id=tenant_id,
        details={
            "user_ids": target_user_ids,
            "added": list(to_add),
            "removed": list(to_remove),
            "errors": operation_errors if operation_errors else None,
        },
    )

    logger.info(
        f"Batch update for project {project_id}: added {len(to_add)}, removed {len(to_remove)} by {user_id}"
    )

    response = {
        "success": True,
        "message": "Users updated successfully",
        "added": list(to_add),
        "removed": list(to_remove),
        "existing": list(target_user_id_set & current_user_ids),
    }

    if operation_errors:
        response["errors"] = operation_errors

    return jsonify(response)


def _log_project_user_audit(
    action: str,
    user_id: int,
    project_id: int,
    target_user_id: int | None,
    tenant_id: int | None,
    details: dict | None = None,
) -> None:
    """Record audit log for project user management."""
    try:
        from app.modules.governance.audit_logger import AuditLogger

        audit_logger = AuditLogger()

        log_details = details or {}
        if target_user_id is not None:
            log_details["target_user_id"] = target_user_id

        audit_logger.log_action(
            action=action,
            user_id=user_id,
            resource_type="project",
            resource_id=str(project_id),
            details=log_details,
            tenant_id=tenant_id,
        )

    except Exception as e:
        logger.error(f"Failed to record project user audit log: {e}")


@projects_bp.route("/projects/<int:project_id>/fix-permissions", methods=["POST"])
def api_fix_project_permissions(project_id):
    """Fix shared project directory permissions.

    Issue #2730 + #2746: Allows manually fixing permissions for shared projects
    with optional async mode and depth limit.

    Request body (optional):
        - async: bool - Force async mode (default: based on file count)
        - depth_limit: int - Maximum recursion depth (default: 10)
        - force_restart: bool - Force restart even if checkpoint exists

    Returns:
        JSON response with success status, task info, or error message.
    """
    tenant_id = get_current_tenant_id()
    project = project_repo.get_project_by_id(project_id, tenant_id=tenant_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404

    # Only shared projects can have permissions fixed
    if not project.is_shared:
        return jsonify({"error": "Project is not shared"}), 400

    # Permission check: only creator or admin can fix permissions
    user_id = g.user_id
    user_role = g.user.get("role")
    if project.created_by != user_id and not User.is_admin_role(user_role):
        return jsonify({"error": "Only project creator or admin can fix permissions"}), 403

    # Check if running in Docker multi-user mode
    if not _is_docker_multi_user_mode():
        return jsonify({"error": "Permission fix is only available in Docker multi-user mode"}), 400

    # Parse request parameters
    data = request.get_json() or {}
    force_async = data.get("async", False)
    depth_limit = data.get("depth_limit")
    force_restart = data.get("force_restart", False)  # noqa: F841

    # Validate depth_limit parameter
    if depth_limit is not None:
        if not isinstance(depth_limit, int) or depth_limit < 1 or depth_limit > 50:
            return jsonify({"error": "depth_limit must be an integer between 1 and 50"}), 400

    # Estimate file count
    file_count = estimate_file_count_fast(project.path)

    # Determine execution mode
    use_async = force_async or file_count >= PERMISSION_SYNC_THRESHOLD

    if use_async:
        # Async mode: submit background task
        from app import db  # type: ignore[attr-defined]  # type: ignore[attr-defined]

        service = get_permission_task_service()

        # Check queue saturation
        is_saturated, queue_length = service.check_queue_saturation(db.session)
        if is_saturated:
            return (
                jsonify(
                    {
                        "error": f"Task queue is full ({queue_length}/{PERMISSION_MAX_QUEUE_SIZE}). Please retry later.",
                    }
                ),
                503,
            )

        # Submit task
        success, error_msg, task_info = service.submit_task(
            db.session,
            project_id=project_id,
            user_id=user_id,
            path=project.path,
            priority=PERMISSION_PRIORITY_MANUAL_FIX,  # Higher priority for manual fix
            depth_limit=depth_limit,
        )

        if success:
            assert task_info is not None  # Type guard for mypy
            response = {
                "success": True,
                "task_id": task_info["task_id"],
                "status": "pending",
                "estimated_files": file_count,
                "queue_position": task_info["queue_position"],
            }
            return jsonify(response), 202  # Accepted
        else:
            return jsonify({"error": error_msg}), 503

    else:
        # Sync mode: execute immediately
        success, error_msg, files_processed = setup_permissions_with_depth_limit(
            project.path,
            depth_limit=depth_limit,
            timeout=60,
            user_id=user_id,  # Issue #2745: Pass user_id for audit log
            project_id=project_id,  # Issue #2745: Pass project_id for audit log
        )

        if success:
            return jsonify(
                {
                    "success": True,
                    "message": "Permissions fixed successfully",
                    "files_processed": files_processed,
                }
            )
        else:
            return jsonify({"error": error_msg}), 500


# ============================================================================
# Permission Task Management Endpoints (Issue #2746)
# ============================================================================


@projects_bp.route("/permission-tasks/<task_id>", methods=["GET"])
@security_annotated(reason="Task initiator or project owner/admin check")
def api_get_permission_task_status(task_id):
    """Get permission task status.

    Issue #2746: Query status of async permission setup tasks.

    Returns:
        JSON response with task status information.
    """
    from app import db  # type: ignore[attr-defined]  # type: ignore[attr-defined]

    tenant_id = get_current_tenant_id()
    service = get_permission_task_service()
    task_status = service.get_task_status(db.session, task_id)

    if not task_status:
        return jsonify({"error": "Task not found"}), 404

    # Permission check: only task initiator or project creator/admin can view
    user_id = g.user_id
    user_role = g.user.get("role")

    # Get project to check ownership (with tenant isolation)
    project = project_repo.get_project_by_id(task_status["project_id"], tenant_id=tenant_id)
    if not project:
        return jsonify({"error": "Associated project not found"}), 404

    # Check if user has permission
    if (
        task_status["user_id"] != user_id
        and project.created_by != user_id
        and not User.is_admin_role(user_role)
    ):
        return jsonify({"error": "Access denied"}), 403

    # Calculate estimated completion time
    estimated_completion = None
    if (
        task_status["status"] == "running"
        and task_status["total_files"] > 0
        and task_status["progress"] > 0
    ):
        # Simple estimation based on current progress
        from datetime import timedelta

        elapsed_seconds = 60  # Assume 60 seconds for progress
        remaining_progress = 100 - task_status["progress"]
        remaining_seconds = (elapsed_seconds / task_status["progress"]) * remaining_progress
        estimated_completion = (
            datetime.now(timezone.utc) + timedelta(seconds=remaining_seconds)
        ).isoformat()

    response = {
        "task_id": task_status["task_id"],
        "project_id": task_status["project_id"],
        "status": task_status["status"],
        "priority": task_status["priority"],
        "progress": task_status["progress"],
        "files_processed": task_status["files_processed"],
        "total_files": task_status["total_files"],
        "depth_limit": task_status["depth_limit"],
        "created_at": task_status["created_at"],
        "started_at": task_status["started_at"],
        "completed_at": task_status["completed_at"],
        "error_message": task_status["error_message"],
    }

    if estimated_completion:
        response["estimated_completion"] = estimated_completion

    return jsonify(response)


@projects_bp.route("/permission-tasks/<task_id>", methods=["DELETE"])
@security_annotated(reason="Task initiator or project owner/admin check")
def api_cancel_permission_task(task_id):
    """Cancel a permission task.

    Issue #2746: Cancel pending or running permission setup tasks.

    Returns:
        JSON response with success status or error message.
    """
    from app import db  # type: ignore[attr-defined]  # type: ignore[attr-defined]

    tenant_id = get_current_tenant_id()
    service = get_permission_task_service()

    # Get task status first for permission check
    task_status = service.get_task_status(db.session, task_id)
    if not task_status:
        return jsonify({"error": "Task not found"}), 404

    # Permission check
    user_id = g.user_id
    user_role = g.user.get("role")

    # Get project to check ownership (with tenant isolation)
    project = project_repo.get_project_by_id(task_status["project_id"], tenant_id=tenant_id)
    if not project:
        return jsonify({"error": "Associated project not found"}), 404

    # Check if user has permission
    if (
        task_status["user_id"] != user_id
        and project.created_by != user_id
        and not User.is_admin_role(user_role)
    ):
        return jsonify({"error": "Access denied"}), 403

    # Cancel the task
    success, error_msg = service.cancel_task(db.session, task_id)

    if success:
        return jsonify({"success": True, "message": "Task cancelled"})
    else:
        return jsonify({"error": error_msg}), 400
