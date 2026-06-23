"""
Open ACE - Admin Routes

API routes for admin operations.
"""

import logging
from typing import cast

import bcrypt
from flask import Blueprint, g, jsonify, request

from app.auth.decorators import admin_required
from app.repositories.usage_repo import UsageRepository
from app.repositories.user_repo import UserRepository
from app.schemas.quota import validate_quota_update
from app.services.auth_service import get_security_settings_cached
from app.utils.validators import validate_email, validate_password, validate_username
from app.utils.workspace import ensure_system_user, get_workspace_base_dir

logger = logging.getLogger(__name__)

admin_bp = Blueprint("admin", __name__)
user_repo = UserRepository()
usage_repo = UsageRepository()


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return cast("str", bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode())


@admin_bp.route("/admin/users", methods=["GET"])
@admin_required
def api_get_users():
    """Get all users."""
    users = user_repo.get_all_users()

    # Remove password hashes
    for user in users:
        user.pop("password_hash", None)

    return jsonify(users)


@admin_bp.route("/admin/users", methods=["POST"])
@admin_required
def api_create_user():
    """Create a new user."""
    data = request.get_json() or {}
    username: str = data.get("username", "")
    email: str = data.get("email", "")
    password: str = data.get("password", "")
    role = data.get("role", "user")

    # Validate inputs
    if not validate_username(username):
        return jsonify({"error": "Invalid username"}), 400

    if not validate_email(email):
        return jsonify({"error": "Invalid email"}), 400

    # Validate password with security policy
    settings = get_security_settings_cached()
    is_valid, error_msg = validate_password(password, policy_settings=settings)
    if not is_valid:
        return jsonify({"error": error_msg}), 400

    # Check if user exists
    if user_repo.get_user_by_username(username):
        return jsonify({"error": "Username already exists"}), 400

    if user_repo.get_user_by_email(email):
        return jsonify({"error": "Email already exists"}), 400

    # Create user
    password_hash = hash_password(password)
    system_account = data.get("system_account")
    if system_account and not validate_username(system_account):
        return jsonify({"error": "Invalid system_account name"}), 400
    user_id = user_repo.create_user(
        username, email, password_hash, str(role), system_account=system_account
    )

    if user_id:
        # Auto-create system user for workspace if system_account is provided
        if system_account:
            uid = data.get("system_uid")  # Optional: specific UID
            if ensure_system_user(system_account, uid=uid):
                logger.info(f"System user {system_account} ready for workspace")
            else:
                logger.warning(
                    f"Failed to create system user {system_account}, workspace may not work"
                )
        return jsonify({"success": True, "user_id": user_id}), 201

    return jsonify({"error": "Failed to create user"}), 500


@admin_bp.route("/admin/users/<int:user_id>", methods=["PUT"])
@admin_required
def api_update_user(user_id):
    """Update a user."""
    data = request.get_json() or {}

    # Auto-create system user if system_account is being set
    system_account = data.get("system_account")
    if system_account and not validate_username(system_account):
        return jsonify({"error": "Invalid system_account name"}), 400
    if system_account:
        uid = data.get("system_uid")
        ensure_system_user(system_account, uid=uid)

    success = user_repo.update_user(
        user_id=user_id,
        username=data.get("username"),
        email=data.get("email"),
        role=data.get("role"),
        is_active=data.get("is_active"),
        system_account=system_account,
    )

    if success:
        return jsonify({"success": True})

    return jsonify({"error": "Failed to update user"}), 500


@admin_bp.route("/admin/users/<int:user_id>", methods=["DELETE"])
@admin_required
def api_delete_user(user_id):
    """Delete a user."""
    # Prevent deleting yourself
    if g.user_id == user_id:
        return jsonify({"error": "Cannot delete yourself"}), 400

    success = user_repo.delete_user(user_id)

    if success:
        return jsonify({"success": True})

    return jsonify({"error": "Failed to delete user"}), 500


@admin_bp.route("/admin/users/<int:user_id>/password", methods=["PUT"])
@admin_required
def api_update_user_password(user_id):
    """Update a user's password."""
    data = request.get_json() or {}
    password: str = data.get("password", "")

    # Validate password with security policy
    settings = get_security_settings_cached()
    is_valid, error_msg = validate_password(password, policy_settings=settings)
    if not is_valid:
        return jsonify({"error": error_msg}), 400

    password_hash = hash_password(password)
    success = user_repo.update_password(user_id, password_hash)

    if success:
        return jsonify({"success": True})

    return jsonify({"error": "Failed to update password"}), 500


@admin_bp.route("/admin/users/<int:user_id>/quota", methods=["PUT"])
@admin_required
def api_update_user_quota(user_id):
    """Update a user's quota."""
    data = request.get_json() or {}

    # Validate quota values before updating
    is_valid, errors = validate_quota_update(
        daily_token_quota=data.get("daily_token_quota"),
        monthly_token_quota=data.get("monthly_token_quota"),
        daily_request_quota=data.get("daily_request_quota"),
        monthly_request_quota=data.get("monthly_request_quota"),
    )

    if not is_valid:
        # Return validation errors with i18n-friendly format
        error_messages = []
        for field, msg in errors.items():
            error_messages.append(f"{field}: {msg}")

        return (
            jsonify(
                {
                    "error": "Quota validation failed",
                    "details": errors,
                    "message": "; ".join(error_messages),
                }
            ),
            400,
        )

    # If validation passes, proceed with update
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
@admin_required
def api_quota_usage():
    """Get quota usage for all users."""
    from datetime import datetime

    users = user_repo.get_all_users()
    today = datetime.now().strftime("%Y-%m-%d")
    month_start = datetime.now().replace(day=1).strftime("%Y-%m-%d")

    for user in users:
        user.pop("password_hash", None)
        user_id: int = user["id"]
        system_account = user.get("system_account") or user.get("username", "")

        # Today's usage
        today_combined = usage_repo.get_combined_usage(
            user_id=user_id,
            system_account=system_account,
            start_date=today,
            end_date=today,
        )
        user["tokens_used_today"] = today_combined["tokens"]
        user["requests_today"] = today_combined["requests"]

        # Monthly usage
        monthly_combined = usage_repo.get_combined_usage(
            user_id=user_id,
            system_account=system_account,
            start_date=month_start,
            end_date=today,
        )
        user["tokens_used_month"] = monthly_combined["tokens"]
        user["requests_month"] = monthly_combined["requests"]

    return jsonify(users)
