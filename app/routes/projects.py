#!/usr/bin/env python3
"""
Open ACE - Project Routes

API routes for project management operations.
"""

import logging
import os
import platform
from datetime import datetime

from flask import Blueprint, jsonify, request

from app.repositories.project_repo import ProjectRepository
from app.repositories.user_repo import UserRepository
from app.services.auth_service import AuthService

logger = logging.getLogger(__name__)

projects_bp = Blueprint("projects", __name__)
auth_service = AuthService()
project_repo = ProjectRepository()
user_repo = UserRepository()


def get_current_user():
    """Get current user from session token."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")
    if not token:
        return None, {"error": "Unauthorized"}, 401

    valid, session_or_error = auth_service.validate_session(token)
    if not valid:
        return None, session_or_error, 401

    user_id = session_or_error.get("user_id")
    user = user_repo.get_user_by_id(user_id)
    return user, None, 200


def get_webui_user():
    """Get user from webui token (for iframe integration)."""
    from app.services.webui_manager import get_webui_manager

    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        # Also check query parameter for token
        token = request.args.get("token")

    if not token:
        return None, {"error": "Unauthorized"}, 401

    manager = get_webui_manager()
    if not manager:
        return None, {"error": "WebUI manager not available"}, 500

    valid, user_id, error = manager.validate_token(token)
    if not valid:
        return None, {"error": error}, 401

    user = user_repo.get_user_by_id(user_id)
    return user, None, 200


@projects_bp.route("/projects", methods=["GET"])
def api_get_projects():
    """Get projects accessible by current user."""
    # Try webui token first (for iframe integration)
    user, error, code = get_webui_user()
    if not user:
        # Try regular session
        user, error, code = get_current_user()
        if not user:
            return jsonify(error), code

    user_id = user.get("id")

    # Get user's projects
    projects = project_repo.get_user_projects(user_id)

    # Also include shared projects
    all_projects = project_repo.get_all_projects()
    for p in all_projects:
        if p.is_shared and p.id not in [proj.id for proj in projects]:
            projects.append(p)

    return jsonify({
        "success": True,
        "projects": [p.to_dict() for p in projects],
    })


@projects_bp.route("/projects", methods=["POST"])
def api_create_project():
    """Create a new project."""
    # Try webui token first (for iframe integration)
    user, error, code = get_webui_user()
    if not user:
        # Try regular session
        user, error, code = get_current_user()
        if not user:
            return jsonify(error), code

    user_id = user.get("id")
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
            if not os.path.exists(path):
                os.makedirs(path, exist_ok=True)
                dir_created = True
                logger.info(f"Created project directory: {path}")
            elif not os.path.isdir(path):
                return jsonify({"error": "Path exists but is not a directory"}), 400
        except PermissionError:
            return jsonify({"error": "Permission denied to create directory"}), 403
        except Exception as e:
            logger.error(f"Error creating directory: {e}")
            return jsonify({"error": f"Failed to create directory: {str(e)}"}), 500

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
        return jsonify({
            "success": True,
            "project": project.to_dict(),
            "dir_created": dir_created,
        }), 201

    return jsonify({"error": "Failed to create project"}), 500


@projects_bp.route("/projects/<int:project_id>", methods=["GET"])
def api_get_project(project_id):
    """Get project details."""
    user, error, code = get_current_user()
    if not user:
        # Try webui token
        user, error, code = get_webui_user()
        if not user:
            return jsonify(error), code

    project = project_repo.get_project_by_id(project_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404

    # Check if user has access
    user_id = user.get("id")
    user_project = project_repo.get_user_project(user_id, project_id)

    if not user_project and not project.is_shared:
        return jsonify({"error": "Access denied"}), 403

    # Get project stats
    stats = project_repo.get_project_stats(project_id)

    return jsonify({
        "success": True,
        "project": project.to_dict(),
        "stats": stats.to_dict() if stats else None,
    })


@projects_bp.route("/projects/<int:project_id>", methods=["PUT"])
def api_update_project(project_id):
    """Update project information."""
    user, error, code = get_current_user()
    if not user:
        return jsonify(error), code

    project = project_repo.get_project_by_id(project_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404

    # Only creator or admin can update
    user_id = user.get("id")
    user_role = user.get("role")

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
        return jsonify({"success": True, "project": project.to_dict()})

    return jsonify({"error": "Failed to update project"}), 500


@projects_bp.route("/projects/<int:project_id>", methods=["DELETE"])
def api_delete_project(project_id):
    """Delete a project (soft delete)."""
    user, error, code = get_current_user()
    if not user:
        return jsonify(error), code

    project = project_repo.get_project_by_id(project_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404

    # Only creator or admin can delete
    user_id = user.get("id")
    user_role = user.get("role")

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
    user, error, code = get_current_user()
    if not user:
        return jsonify(error), code

    user_role = user.get("role")
    if user_role != "admin":
        return jsonify({"error": "Admin access required"}), 403

    stats = project_repo.get_all_project_stats()

    return jsonify({
        "success": True,
        "stats": [s.to_dict() for s in stats],
    })


@projects_bp.route("/projects/<int:project_id>/daily", methods=["GET"])
def api_get_project_daily_stats(project_id):
    """Get daily statistics for a project."""
    user, error, code = get_current_user()
    if not user:
        return jsonify(error), code

    project = project_repo.get_project_by_id(project_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404

    # Check access
    user_id = user.get("id")
    user_project = project_repo.get_user_project(user_id, project_id)

    if not user_project and not project.is_shared and user.get("role") != "admin":
        return jsonify({"error": "Access denied"}), 403

    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    stats = project_repo.get_project_daily_stats(
        project_id=project_id,
        start_date=start_date,
        end_date=end_date,
    )

    return jsonify({
        "success": True,
        "stats": [s.to_dict() for s in stats],
    })


@projects_bp.route("/projects/<int:project_id>/users", methods=["GET"])
def api_get_project_users(project_id):
    """Get users collaborating on a project."""
    user, error, code = get_current_user()
    if not user:
        return jsonify(error), code

    project = project_repo.get_project_by_id(project_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404

    # Check access
    user_id = user.get("id")
    user_project = project_repo.get_user_project(user_id, project_id)

    if not user_project and not project.is_shared and user.get("role") != "admin":
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

    return jsonify({
        "success": True,
        "users": result,
    })