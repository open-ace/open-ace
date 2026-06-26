"""
Project Categories API Routes

Issue #1278: API for project categorization management
"""

import logging

from flask import Blueprint, g, jsonify, request

from app.auth.decorators import _extract_token, _load_user_from_token
from app.repositories.project_category_repo import ProjectCategoryRepository
from app.repositories.user_repo import UserRepository

logger = logging.getLogger(__name__)

project_categories_bp = Blueprint("project_categories", __name__)
category_repo = ProjectCategoryRepository()
user_repo = UserRepository()


@project_categories_bp.before_request
def _authenticate_user():
    """Authenticate via session token."""
    token = _extract_token()
    if token:
        user_data = _load_user_from_token(token)
        if user_data:
            user = user_repo.get_user_by_id(int(user_data.get("id", 0)))
            if user:
                g.user = user
                g.user_id = user.get("id")
                return None
    return jsonify({"error": "Authentication required"}), 401


@project_categories_bp.route("/api/project-categories", methods=["GET"])
def list_categories():
    """List all project categories."""
    categories = category_repo.list_categories(active_only=False)
    return jsonify(
        {
            "success": True,
            "categories": [c.to_dict() for c in categories],
        }
    )


@project_categories_bp.route("/api/project-categories", methods=["POST"])
def create_category():
    """Create a new category (admin only)."""
    if g.user.get("role") != "admin":
        return jsonify({"error": "Admin access required"}), 403

    data = request.get_json() or {}
    name = data.get("name", "").strip()
    key_patterns = data.get("key_patterns", [])
    sort_order = data.get("sort_order", 0)

    if not name:
        return jsonify({"error": "Name is required"}), 400

    if not isinstance(key_patterns, list):
        return jsonify({"error": "key_patterns must be an array"}), 400

    category_id = category_repo.create_category(name, key_patterns, sort_order)
    if category_id:
        category = category_repo.get_category(category_id)
        logger.info(f"Created project category '{name}' (id={category_id})")
        return jsonify({"success": True, "category": category.to_dict()}), 201

    return jsonify({"error": "Failed to create category"}), 500


@project_categories_bp.route("/api/project-categories/<int:category_id>", methods=["PUT"])
def update_category(category_id):
    """Update a category (admin only)."""
    if g.user.get("role") != "admin":
        return jsonify({"error": "Admin access required"}), 403

    category = category_repo.get_category(category_id)
    if not category:
        return jsonify({"error": "Category not found"}), 404

    data = request.get_json() or {}

    key_patterns = data.get("key_patterns")
    if key_patterns is not None and not isinstance(key_patterns, list):
        return jsonify({"error": "key_patterns must be an array"}), 400

    success = category_repo.update_category(
        category_id,
        name=data.get("name"),
        key_patterns=key_patterns,
        sort_order=data.get("sort_order"),
        is_active=data.get("is_active"),
    )

    if success:
        category = category_repo.get_category(category_id)
        logger.info(f"Updated project category {category_id} (name={category.name})")
        return jsonify({"success": True, "category": category.to_dict()})

    return jsonify({"error": "Failed to update category"}), 500


@project_categories_bp.route("/api/project-categories/<int:category_id>", methods=["DELETE"])
def delete_category(category_id):
    """Delete a category (admin only)."""
    if g.user.get("role") != "admin":
        return jsonify({"error": "Admin access required"}), 403

    category = category_repo.get_category(category_id)
    if not category:
        return jsonify({"error": "Category not found"}), 404

    success = category_repo.delete_category(category_id)
    if success:
        logger.info(f"Deleted project category {category_id} (name={category.name})")
        return jsonify({"success": True})

    return jsonify({"error": "Failed to delete category"}), 500
