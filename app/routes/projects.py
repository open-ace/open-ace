"""
Open ACE - Project Routes

API routes for project management operations.
"""

import logging
import os
import platform
import subprocess

from flask import Blueprint, g, jsonify, request

from app.auth.decorators import _extract_token, _load_user_from_token
from app.repositories.project_repo import ProjectRepository
from app.repositories.user_repo import UserRepository

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
                return None

    # Fallback: try WebUI token from query param
    url_token = request.args.get("token")
    if url_token:
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
                    return None

    return jsonify({"error": "Authentication required"}), 401


def run_as_user(system_account: str, command: list) -> subprocess.CompletedProcess:
    """Run a command as a specific user using sudo."""
    sudo_cmd = ["sudo", "-u", system_account] + command
    return subprocess.run(sudo_cmd, capture_output=True, text=True, timeout=30)


@projects_bp.route("/projects", methods=["GET"])
def api_get_projects():
    """Get projects accessible by current user."""
    user_id = g.user_id

    # Get user's projects
    projects = project_repo.get_user_projects(user_id)

    # Also include shared projects
    all_projects = project_repo.get_all_projects()
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
    system_account = g.user.get("system_account") if g.user else None
    data = request.get_json() or {}

    path = data.get("path")
    name = data.get("name")
    description = data.get("description")
    is_shared = data.get("is_shared", False)
    create_dir = data.get("create_dir", True)

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
    existing = project_repo.get_project_by_path(path)
    if existing:
        return jsonify({"error": "Project already exists", "project": existing.to_dict()}), 409

    # Create directory if requested and doesn't exist
    dir_created = False
    if create_dir:
        try:
            if system_account:
                # Use sudo to check and create directory as the user
                result = run_as_user(system_account, ["test", "-e", path])
                path_exists = result.returncode == 0

                if not path_exists:
                    # Create directory using sudo mkdir -p
                    result = run_as_user(system_account, ["mkdir", "-p", path])
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
                    result = run_as_user(system_account, ["test", "-d", path])
                    if result.returncode != 0:
                        return jsonify({"error": "Path exists but is not a directory"}), 400
            else:
                # Fallback to process user's permissions (for admin or no system_account)
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

    # Create project in database
    project_id = project_repo.create_project(
        path=path,
        name=name,
        description=description,
        created_by=user_id,
        is_shared=is_shared,
    )

    if project_id:
        project = project_repo.get_project_by_id(project_id)
        if project is None:
            return jsonify({"error": "Project not found"}), 404
        return (
            jsonify(
                {
                    "success": True,
                    "project": project.to_dict(),
                    "dir_created": dir_created,
                }
            ),
            201,
        )

    return jsonify({"error": "Failed to create project"}), 500


@projects_bp.route("/projects/<int:project_id>", methods=["GET"])
def api_get_project(project_id):
    """Get project details."""
    project = project_repo.get_project_by_id(project_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404

    # Check if user has access
    user_id = g.user_id
    user_project = project_repo.get_user_project(user_id, project_id)

    if not user_project and not project.is_shared:
        return jsonify({"error": "Access denied"}), 403

    # Get project stats
    stats = project_repo.get_project_stats(project_id)

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
    project = project_repo.get_project_by_id(project_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404

    # Only creator or admin can update
    user_id = g.user_id
    user_role = g.user.get("role")

    if project.created_by != user_id and user_role != "admin":
        return jsonify({"error": "Only project creator or admin can update"}), 403

    data = request.get_json() or {}
    name = data.get("name")
    description = data.get("description")
    is_shared = data.get("is_shared")

    success = project_repo.update_project(
        project_id=project_id,
        name=name,
        description=description,
        is_shared=is_shared,
    )

    if success:
        project = project_repo.get_project_by_id(project_id)
        if project is None:
            return jsonify({"error": "Project not found"}), 404
        return jsonify({"success": True, "project": project.to_dict()})

    return jsonify({"error": "Failed to update project"}), 500


@projects_bp.route("/projects/<int:project_id>", methods=["DELETE"])
def api_delete_project(project_id):
    """Delete a project (soft delete)."""
    project = project_repo.get_project_by_id(project_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404

    # Only creator or admin can delete
    user_id = g.user_id
    user_role = g.user.get("role")

    if project.created_by != user_id and user_role != "admin":
        return jsonify({"error": "Only project creator or admin can delete"}), 403

    # Soft delete
    success = project_repo.delete_project(project_id, soft_delete=True)

    if success:
        return jsonify({"success": True, "message": "Project deleted"})

    return jsonify({"error": "Failed to delete project"}), 500


@projects_bp.route("/projects/stats", methods=["GET"])
def api_get_all_project_stats():
    """Get statistics for all projects (admin only)."""
    if g.user.get("role") != "admin":
        return jsonify({"error": "Admin access required"}), 403

    stats = project_repo.get_all_project_stats()

    return jsonify(
        {
            "success": True,
            "stats": [s.to_dict() for s in stats],
        }
    )


@projects_bp.route("/projects/<int:project_id>/daily", methods=["GET"])
def api_get_project_daily_stats(project_id):
    """Get daily statistics for a project."""
    project = project_repo.get_project_by_id(project_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404

    # Check access
    user_id = g.user_id
    user_project = project_repo.get_user_project(user_id, project_id)

    if not user_project and not project.is_shared and g.user.get("role") != "admin":
        return jsonify({"error": "Access denied"}), 403

    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    stats = project_repo.get_project_daily_stats(
        project_id=project_id,
        start_date=start_date,
        end_date=end_date,
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
    project = project_repo.get_project_by_id(project_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404

    # Check access
    user_id = g.user_id
    user_project = project_repo.get_user_project(user_id, project_id)

    if not user_project and not project.is_shared and g.user.get("role") != "admin":
        return jsonify({"error": "Access denied"}), 403

    user_stats = project_repo.get_project_users(project_id)

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
