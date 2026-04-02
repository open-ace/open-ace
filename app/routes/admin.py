#!/usr/bin/env python3
"""
Open ACE - Admin Routes

API routes for admin operations.
"""

import bcrypt

from flask import Blueprint, jsonify, request

from app.repositories.usage_repo import UsageRepository
from app.repositories.user_repo import UserRepository
from app.services.auth_service import AuthService
from app.utils.validators import validate_email, validate_password, validate_username

admin_bp = Blueprint("admin", __name__)
auth_service = AuthService()
user_repo = UserRepository()
usage_repo = UsageRepository()


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def require_admin(token: str):
    """Require admin role and return session data."""
    is_admin, session_or_error = auth_service.require_admin(token)
    return is_admin, session_or_error


@admin_bp.route("/admin/users", methods=["GET"])
def api_get_users():
    """Get all users."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    is_admin, session_or_error = require_admin(token)
    if not is_admin:
        return jsonify(session_or_error), (
            403 if "Admin" in session_or_error.get("error", "") else 401
        )

    users = user_repo.get_all_users()

    # Remove password hashes
    for user in users:
        user.pop("password_hash", None)

    return jsonify(users)


@admin_bp.route("/admin/users", methods=["POST"])
def api_create_user():
    """Create a new user."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    is_admin, session_or_error = require_admin(token)
    if not is_admin:
        return jsonify(session_or_error), (
            403 if "Admin" in session_or_error.get("error", "") else 401
        )

    data = request.get_json() or {}
    username = data.get("username")
    email = data.get("email")
    password = data.get("password")
    role = data.get("role", "user")

    # Validate inputs
    if not validate_username(username):
        return jsonify({"error": "Invalid username"}), 400

    if not validate_email(email):
        return jsonify({"error": "Invalid email"}), 400

    is_valid, error_msg = validate_password(password)
    if not is_valid:
        return jsonify({"error": error_msg}), 400

    # Check if user exists
    if user_repo.get_user_by_username(username):
        return jsonify({"error": "Username already exists"}), 400

    if user_repo.get_user_by_email(email):
        return jsonify({"error": "Email already exists"}), 400

    # Create user
    password_hash = hash_password(password)
    user_id = user_repo.create_user(username, email, password_hash, role)

    if user_id:
        return jsonify({"success": True, "user_id": user_id}), 201

    return jsonify({"error": "Failed to create user"}), 500


@admin_bp.route("/admin/users/<int:user_id>", methods=["PUT"])
def api_update_user(user_id):
    """Update a user."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    is_admin, session_or_error = require_admin(token)
    if not is_admin:
        return jsonify(session_or_error), (
            403 if "Admin" in session_or_error.get("error", "") else 401
        )

    data = request.get_json() or {}

    success = user_repo.update_user(
        user_id=user_id,
        username=data.get("username"),
        email=data.get("email"),
        role=data.get("role"),
        is_active=data.get("is_active"),
        linux_account=data.get("linux_account"),
    )

    if success:
        return jsonify({"success": True})

    return jsonify({"error": "Failed to update user"}), 500


@admin_bp.route("/admin/users/<int:user_id>", methods=["DELETE"])
def api_delete_user(user_id):
    """Delete a user."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    is_admin, session_or_error = require_admin(token)
    if not is_admin:
        return jsonify(session_or_error), (
            403 if "Admin" in session_or_error.get("error", "") else 401
        )

    # Prevent deleting yourself
    if session_or_error.get("user_id") == user_id:
        return jsonify({"error": "Cannot delete yourself"}), 400

    success = user_repo.delete_user(user_id)

    if success:
        return jsonify({"success": True})

    return jsonify({"error": "Failed to delete user"}), 500


@admin_bp.route("/admin/users/<int:user_id>/password", methods=["PUT"])
def api_update_user_password(user_id):
    """Update a user's password."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    is_admin, session_or_error = require_admin(token)
    if not is_admin:
        return jsonify(session_or_error), (
            403 if "Admin" in session_or_error.get("error", "") else 401
        )

    data = request.get_json() or {}
    password = data.get("password")

    is_valid, error_msg = validate_password(password)
    if not is_valid:
        return jsonify({"error": error_msg}), 400

    password_hash = hash_password(password)
    success = user_repo.update_password(user_id, password_hash)

    if success:
        return jsonify({"success": True})

    return jsonify({"error": "Failed to update password"}), 500


@admin_bp.route("/admin/users/<int:user_id>/quota", methods=["PUT"])
def api_update_user_quota(user_id):
    """Update a user's quota."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    is_admin, session_or_error = require_admin(token)
    if not is_admin:
        return jsonify(session_or_error), (
            403 if "Admin" in session_or_error.get("error", "") else 401
        )

    data = request.get_json() or {}

    success = user_repo.update_user_quota(
        user_id=user_id,
        daily_token_quota=data.get("daily_token_quota"),
        monthly_token_quota=data.get("monthly_token_quota"),
        daily_request_quota=data.get("daily_request_quota"),
        monthly_request_quota=data.get("monthly_request_quota"),
    )

    if success:
        return jsonify({"success": True})

    return jsonify({"error": "Failed to update quota"}), 500


@admin_bp.route("/admin/quota/usage", methods=["GET"])
def api_quota_usage():
    """Get quota usage for all users."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    is_admin, session_or_error = require_admin(token)
    if not is_admin:
        return jsonify(session_or_error), (
            403 if "Admin" in session_or_error.get("error", "") else 401
        )

    users = user_repo.get_all_users()

    # Remove sensitive data
    for user in users:
        user.pop("password_hash", None)

    return jsonify(users)
