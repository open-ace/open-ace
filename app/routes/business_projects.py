"""
Business Projects API Routes

Issue #871: Predefined business projects for workspace categorization
"""

import logging

from flask import Blueprint, jsonify, request

from app.auth.decorators import admin_required
from app.models.business_project import BusinessProject, BusinessProjectMember
from app.repositories.business_project_repo import BusinessProjectRepository

logger = logging.getLogger(__name__)
business_projects_bp = Blueprint("business_projects", __name__)
repo = BusinessProjectRepository()


@business_projects_bp.route("/api/business-projects", methods=["GET"])
@admin_required
def list_business_projects():
    """List all business projects."""
    include_deleted = request.args.get("include_deleted", "false").lower() == "true"
    active_only = request.args.get("active_only", "true").lower() == "true"

    projects = repo.list_projects(include_deleted=include_deleted, active_only=active_only)
    result = [BusinessProject.from_dict(p).to_dict() for p in projects]
    return jsonify({"success": True, "projects": result})


@business_projects_bp.route("/api/business-projects/<int:project_id>", methods=["GET"])
@admin_required
def get_business_project(project_id: int):
    """Get a single business project."""
    project = repo.get_project(project_id)
    if not project:
        return jsonify({"error": "Business project not found"}), 404
    return jsonify({"success": True, "project": BusinessProject.from_dict(project).to_dict()})


@business_projects_bp.route("/api/business-projects", methods=["POST"])
@admin_required
def create_business_project():
    """Create a new business project."""
    data = request.get_json() or {}

    name = data.get("name", "").strip()
    code = data.get("code", "").strip()
    description = data.get("description")
    key_patterns = data.get("key_patterns", [])

    if not name or not code:
        return jsonify({"error": "Name and code are required"}), 400

    # Check for duplicate code
    existing = repo.get_project_by_code(code)
    if existing:
        return jsonify({"error": f"Business project with code '{code}' already exists"}), 400

    from flask import g
    created_by = g.user.get("id") if hasattr(g, "user") else None

    project = repo.create_project(
        name=name,
        code=code,
        description=description,
        key_patterns=key_patterns,
        created_by=created_by,
    )
    logger.info(f"Created business project '{name}' (code={code}) by user {created_by}")
    return jsonify({"success": True, "project": BusinessProject.from_dict(project).to_dict()})


@business_projects_bp.route("/api/business-projects/<int:project_id>", methods=["PUT"])
@admin_required
def update_business_project(project_id: int):
    """Update a business project."""
    data = request.get_json() or {}

    project = repo.get_project(project_id)
    if not project:
        return jsonify({"error": "Business project not found"}), 404

    name = data.get("name")
    code = data.get("code")
    description = data.get("description")
    key_patterns = data.get("key_patterns")
    is_active = data.get("is_active")

    # Check for duplicate code if changing
    if code and code != project.get("code"):
        existing = repo.get_project_by_code(code)
        if existing:
            return jsonify({"error": f"Business project with code '{code}' already exists"}), 400

    updated = repo.update_project(
        project_id=project_id,
        name=name,
        code=code,
        description=description,
        key_patterns=key_patterns,
        is_active=is_active,
    )
    logger.info(f"Updated business project {project_id} (name={updated.get('name')})")
    return jsonify({"success": True, "project": BusinessProject.from_dict(updated).to_dict()})


@business_projects_bp.route("/api/business-projects/<int:project_id>", methods=["DELETE"])
@admin_required
def delete_business_project(project_id: int):
    """Delete (soft delete) a business project."""
    project = repo.get_project(project_id)
    if not project:
        return jsonify({"error": "Business project not found"}), 404

    repo.delete_project(project_id)
    logger.info(f"Deleted business project {project_id} (name={project.get('name')})")
    return jsonify({"success": True})


@business_projects_bp.route("/api/business-projects/<int:project_id>/members", methods=["GET"])
@admin_required
def get_business_project_members(project_id: int):
    """Get members of a business project."""
    project = repo.get_project(project_id)
    if not project:
        return jsonify({"error": "Business project not found"}), 404

    members = repo.get_members(project_id)
    result = [BusinessProjectMember.from_dict(m).to_dict() for m in members]
    return jsonify({"success": True, "members": result})


@business_projects_bp.route("/api/business-projects/<int:project_id>/members", methods=["POST"])
@admin_required
def add_business_project_member(project_id: int):
    """Add a member to a business project."""
    project = repo.get_project(project_id)
    if not project:
        return jsonify({"error": "Business project not found"}), 404

    data = request.get_json() or {}
    user_id = data.get("user_id")

    if not user_id:
        return jsonify({"error": "user_id is required"}), 400

    member = repo.add_member(project_id, user_id)
    logger.info(
        f"Added member user_id={user_id} to business project {project_id} (name={project.get('name')})"
    )
    return jsonify({"success": True, "member": BusinessProjectMember.from_dict(member).to_dict()})


@business_projects_bp.route(
    "/api/business-projects/<int:project_id>/members/<int:member_id>", methods=["DELETE"]
)
@admin_required
def remove_business_project_member(project_id: int, member_id: int):
    """Remove a member from a business project."""
    project = repo.get_project(project_id)
    if not project:
        return jsonify({"error": "Business project not found"}), 404

    repo.remove_member(project_id, member_id)
    logger.info(
        f"Removed member {member_id} from business project {project_id} (name={project.get('name')})"
    )
    return jsonify({"success": True})


@business_projects_bp.route("/api/business-projects/<int:project_id>/stats", methods=["GET"])
@admin_required
def get_business_project_stats(project_id: int):
    """Get statistics for a business project."""
    project = repo.get_project(project_id)
    if not project:
        return jsonify({"error": "Business project not found"}), 404

    stats = repo.get_project_stats(project_id)
    if not stats:
        # Return empty stats if no workspaces assigned
        stats = {
            "business_project_id": project_id,
            "project_name": project.get("name"),
            "project_code": project.get("code"),
            "total_workspaces": 0,
            "total_tokens": 0,
            "total_requests": 0,
            "total_duration_seconds": 0,
            "first_access": None,
            "last_access": None,
        }
    return jsonify({"success": True, "stats": stats})
