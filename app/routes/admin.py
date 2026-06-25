"""
Open ACE - Admin Routes

API routes for admin operations.
"""

import logging
import secrets
import string
from typing import cast

import bcrypt
from flask import Blueprint, g, jsonify, request

from app.auth.decorators import admin_required
from app.repositories.usage_repo import UsageRepository
from app.repositories.user_repo import UserRepository
from app.schemas.quota import validate_quota_update
from app.services.auth_service import get_security_settings_cached
from app.utils.validators import validate_email, validate_password, validate_username
from app.utils.workspace import ensure_system_user

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
    """Get all users, optionally filtered by tenant."""
    tenant_id = request.args.get("tenant_id", type=int)

    users = user_repo.get_all_users(tenant_id=tenant_id)

    # Remove password hashes
    for user in users:
        user.pop("password_hash", None)

    # Batch load tenant info to avoid N+1 queries
    tenant_ids = {user.get("tenant_id") for user in users if user.get("tenant_id")}
    if tenant_ids:
        from app.services.tenant_service import TenantService

        tenant_service = TenantService()
        tenants = tenant_service.list_tenants()
        tenant_map = {t.id: t.name for t in tenants if t.id in tenant_ids}
        for user in users:
            user["tenant_name"] = tenant_map.get(user.get("tenant_id"))

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
    tenant_id = data.get("tenant_id", 1)

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

    # Check tenant quota before creating user
    from app.services.tenant_service import TenantService

    tenant_service = TenantService()
    if not tenant_service.can_add_user(tenant_id):
        tenant = tenant_service.get_tenant(tenant_id)
        max_users = tenant.quota.max_users if tenant else 0
        return jsonify({"error": f"Tenant user quota exceeded (max: {max_users})"}), 400

    # Create user
    password_hash = hash_password(password)
    system_account = data.get("system_account")
    if system_account and not validate_username(system_account):
        return jsonify({"error": "Invalid system_account name"}), 400
    user_id = user_repo.create_user(
        username,
        email,
        password_hash,
        str(role),
        system_account=system_account,
        tenant_id=tenant_id,
    )

    if user_id:
        # Increment tenant user count (compensate if fails)
        if not tenant_service.increment_user_count(tenant_id):
            # Rollback: delete created user if tenant count update fails
            user_repo.delete_user(user_id)
            logger.error(f"Rollback: deleted user {user_id} due to tenant count update failure")
            return jsonify({"error": "Failed to update tenant user count"}), 500
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

    # Handle tenant_id change
    new_tenant_id = data.get("tenant_id")
    if new_tenant_id is not None:
        from app.services.tenant_service import TenantService

        tenant_service = TenantService()
        # Check if user exists and get current tenant
        current_user = user_repo.get_user_by_id(user_id)
        if current_user:
            current_tenant_id = current_user.get("tenant_id", 1)
            # If tenant is changing, check quota for new tenant
            if new_tenant_id != current_tenant_id:
                if not tenant_service.can_add_user(new_tenant_id):
                    tenant = tenant_service.get_tenant(new_tenant_id)
                    max_users = tenant.quota.max_users if tenant else 0
                    return (
                        jsonify({"error": f"Target tenant quota exceeded (max: {max_users})"}),
                        400,
                    )
                # Decrement old tenant count and increment new tenant count
                tenant_service.decrement_user_count(current_tenant_id)
                tenant_service.increment_user_count(new_tenant_id)

    success = user_repo.update_user(
        user_id=user_id,
        username=data.get("username"),
        email=data.get("email"),
        role=data.get("role"),
        is_active=data.get("is_active"),
        system_account=system_account,
        tenant_id=new_tenant_id,
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


@admin_bp.route("/admin/users/<int:user_id>/reset-password", methods=["POST"])
@admin_required
def api_reset_user_password(user_id):
    """Reset user password and generate a temporary password.

    The user must change the temporary password on next login.
    Returns the temporary password to the admin for secure delivery to the user.
    """
    # Get user
    user = user_repo.get_user_by_id(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    # Get security settings for password policy
    settings = get_security_settings_cached()
    min_length = 12  # Default to 12 for temporary passwords

    if settings:
        policy_min = settings.get("password_min_length", 8)
        # Use policy minimum if it's higher, but ensure at least 12 chars for security
        min_length = max(policy_min, 12)

    # Generate temporary password
    # Include uppercase, lowercase, digits, and special characters
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    temp_password = "".join(secrets.choice(chars) for _ in range(min_length))

    # Validate generated password meets policy
    is_valid, error_msg = validate_password(temp_password, policy_settings=settings)
    if not is_valid:
        # If validation fails (unlikely), regenerate with stronger requirements
        chars = string.ascii_uppercase + string.ascii_lowercase + string.digits + "!@#$%^&*"
        temp_password = "".join(secrets.choice(chars) for _ in range(16))

    # Update password
    password_hash = hash_password(temp_password)
    success = user_repo.update_password(user_id, password_hash)

    if not success:
        return jsonify({"error": "Failed to update password"}), 500

    # Set must_change_password flag to force password change on next login
    user_repo.set_must_change_password(user_id, True)

    logger.info(f"Password reset for user {user_id} by admin {g.user_id}")

    return jsonify(
        {
            "success": True,
            "temporary_password": temp_password,
            "message": "Password reset successful. User must change password on next login.",
        }
    )


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


# Token quotas are stored in M (millions) units
TOKEN_QUOTA_MULTIPLIER = 1_000_000


@admin_bp.route("/admin/quota/stats", methods=["GET"])
@admin_required
def api_quota_stats():
    """Get quota allocation statistics for reference."""
    from app.services.tenant_service import TenantService

    tenant_service = TenantService()

    # Get tenant info (default tenant_id=1 for single-tenant mode)
    tenant = tenant_service.get_tenant(1)
    if not tenant:
        return jsonify({"error": "Tenant not found"}), 404

    tenant_quota = tenant.quota

    # Calculate allocated quotas from all users
    users = user_repo.get_all_users()

    allocated = {
        "daily_token": 0,
        "monthly_token": 0,
        "daily_request": 0,
        "monthly_request": 0,
    }

    active_users = 0
    for user in users:
        if user.get("is_active", True):
            active_users += 1
            if user.get("daily_token_quota"):
                allocated["daily_token"] += user["daily_token_quota"]
            if user.get("monthly_token_quota"):
                allocated["monthly_token"] += user["monthly_token_quota"]
            if user.get("daily_request_quota"):
                allocated["daily_request"] += user["daily_request_quota"]
            if user.get("monthly_request_quota"):
                allocated["monthly_request"] += user["monthly_request_quota"]

    # Calculate remaining (token quotas stored in M units)
    remaining = {
        "daily_token": tenant_quota.daily_token_limit
        - allocated["daily_token"] * TOKEN_QUOTA_MULTIPLIER,
        "monthly_token": tenant_quota.monthly_token_limit
        - allocated["monthly_token"] * TOKEN_QUOTA_MULTIPLIER,
        "daily_request": tenant_quota.daily_request_limit - allocated["daily_request"],
        "monthly_request": tenant_quota.monthly_request_limit - allocated["monthly_request"],
    }

    # Calculate percentages
    def calc_percent(allocated_val: float, limit_val: int) -> float:
        if limit_val <= 0:
            return 0.0
        return round((allocated_val / limit_val) * 100, 1)

    percentages = {
        "daily_token": calc_percent(
            allocated["daily_token"] * TOKEN_QUOTA_MULTIPLIER, tenant_quota.daily_token_limit
        ),
        "monthly_token": calc_percent(
            allocated["monthly_token"] * TOKEN_QUOTA_MULTIPLIER, tenant_quota.monthly_token_limit
        ),
        "daily_request": calc_percent(allocated["daily_request"], tenant_quota.daily_request_limit),
        "monthly_request": calc_percent(
            allocated["monthly_request"], tenant_quota.monthly_request_limit
        ),
    }

    return jsonify(
        {
            "tenant_quota": {
                "daily_token_limit": tenant_quota.daily_token_limit,
                "monthly_token_limit": tenant_quota.monthly_token_limit,
                "daily_request_limit": tenant_quota.daily_request_limit,
                "monthly_request_limit": tenant_quota.monthly_request_limit,
                "max_users": tenant_quota.max_users,
            },
            "allocated": allocated,
            "remaining": remaining,
            "percentages": percentages,
            "user_count": {
                "total": len(users),
                "active": active_users,
                "max": tenant_quota.max_users,
            },
        }
    )
