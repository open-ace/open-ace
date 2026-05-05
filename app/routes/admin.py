"""
Open ACE - Admin Routes

API routes for admin operations.
"""

import logging
import os
import subprocess
from typing import Optional, cast

import bcrypt
from flask import Blueprint, g, jsonify, request

from app.auth.decorators import admin_required
from app.repositories.usage_repo import UsageRepository
from app.repositories.user_repo import UserRepository
from app.utils.validators import validate_email, validate_password, validate_username

logger = logging.getLogger(__name__)

admin_bp = Blueprint("admin", __name__)
user_repo = UserRepository()
usage_repo = UsageRepository()


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return cast("str", bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode())


def get_workspace_base_dir() -> str:
    """Get the workspace base directory. Configurable via WORKSPACE_BASE_DIR env var."""
    return os.environ.get("WORKSPACE_BASE_DIR", "/home")


def ensure_system_user(system_account: str, uid: Optional[int] = None) -> bool:
    """
    Ensure a system user exists for workspace operations.
    Creates the OS user, workspace directory, and .qwen directory.

    Args:
        system_account: Username for the system account.
        uid: Optional specific UID. If None, system auto-assigns.

    Returns:
        True if user exists or was created successfully.
    """
    base_dir = get_workspace_base_dir()

    # Check if user already exists
    result = subprocess.run(["id", system_account], capture_output=True, text=True)
    if result.returncode == 0:
        logger.info(f"System user {system_account} already exists")
        # Still ensure workspace directories exist
        _ensure_workspace_dirs(system_account, base_dir)
        return True

    # Build useradd command
    cmd = ["useradd", "-m", "-s", "/bin/bash"]
    if uid is not None:
        cmd.extend(["-u", str(uid)])
    cmd.append(system_account)

    logger.info(f"Creating system user: {system_account}" + (f" (UID: {uid})" if uid else ""))
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        logger.error(f"Failed to create system user {system_account}: {result.stderr}")
        return False

    logger.info(f"System user {system_account} created successfully")
    _ensure_workspace_dirs(system_account, base_dir)
    return True


def _ensure_workspace_dirs(system_account: str, base_dir: str):
    """Ensure workspace directories exist with correct ownership."""
    workspace_dir = f"{base_dir}/{system_account}"
    qwen_dir = f"{workspace_dir}/.qwen"

    for directory in [workspace_dir, qwen_dir]:
        if not os.path.exists(directory):
            try:
                os.makedirs(directory, mode=0o755, exist_ok=True)
            except PermissionError:
                logger.warning(f"Cannot create {directory} (permission denied)")
                continue

    # Set ownership
    try:
        uid_result = subprocess.run(["id", "-u", system_account], capture_output=True, text=True)
        gid_result = subprocess.run(["id", "-g", system_account], capture_output=True, text=True)
        if uid_result.returncode == 0 and gid_result.returncode == 0:
            uid = int(uid_result.stdout.strip())
            gid = int(gid_result.stdout.strip())
            for directory in [workspace_dir, qwen_dir]:
                try:
                    os.chown(directory, uid, gid)
                except PermissionError:
                    logger.warning(f"Cannot chown {directory} to {uid}:{gid}")
    except Exception as e:
        logger.warning(f"Error setting ownership for {system_account}: {e}")


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

    is_valid, error_msg = validate_password(password)
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
    users = user_repo.get_all_users()

    # Remove sensitive data
    for user in users:
        user.pop("password_hash", None)

    return jsonify(users)
