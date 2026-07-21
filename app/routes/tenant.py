"""
Open ACE - Tenant Routes

API endpoints for multi-tenant management.
"""

import logging
from typing import cast

import bcrypt
from flask import Blueprint, jsonify, request

from app.auth.decorators import admin_required
from app.repositories.user_repo import UserRepository
from app.services.auth_service import get_security_settings_cached
from app.services.tenant_service import TenantService
from app.utils.validators import validate_email, validate_password, validate_username

logger = logging.getLogger(__name__)

# Create blueprint
tenant_bp = Blueprint("tenant", __name__, url_prefix="/api/tenants")

# Services
tenant_service = TenantService()
user_repo = UserRepository()


def _hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return cast("str", bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode())


@tenant_bp.route("", methods=["GET"])
@admin_required
def list_tenants():
    """List all tenants (admin only)."""

    # Get query parameters
    status = request.args.get("status")
    plan = request.args.get("plan")
    limit = request.args.get("limit", 100, type=int)
    offset = request.args.get("offset", 0, type=int)

    tenants = tenant_service.list_tenants(
        status=status, plan=plan, limit=min(limit, 1000), offset=offset
    )

    return jsonify(
        {
            "tenants": [t.to_dict() for t in tenants],
            "count": len(tenants),
        }
    )


@tenant_bp.route("/<int:tenant_id>", methods=["GET"])
@admin_required
def get_tenant(tenant_id: int):
    """Get tenant by ID (admin only)."""

    tenant = tenant_service.get_tenant(tenant_id)

    if not tenant:
        return jsonify({"error": "Tenant not found"}), 404

    return jsonify(tenant.to_dict())


@tenant_bp.route("/slug/<slug>", methods=["GET"])
@admin_required
def get_tenant_by_slug(slug: str):
    """Get tenant by slug (admin only)."""

    tenant = tenant_service.get_tenant_by_slug(slug)

    if not tenant:
        return jsonify({"error": "Tenant not found"}), 404

    return jsonify(tenant.to_dict())


@tenant_bp.route("", methods=["POST"])
@admin_required
def create_tenant():
    """Create a new tenant (admin only). Optionally create an admin user."""

    data = request.get_json()

    if not data:
        return jsonify({"error": "Request body required"}), 400

    name = data.get("name")
    if not name:
        return jsonify({"error": "Tenant name is required"}), 400

    slug = data.get("slug")

    tenant = tenant_service.create_tenant(
        name=name,
        slug=slug,
        plan=data.get("plan", "standard"),
        contact_email=data.get("contact_email", ""),
        contact_name=data.get("contact_name"),
        trial_days=data.get("trial_days"),
    )

    if not tenant:
        # Check if slug conflict is the cause
        if slug and tenant_service.get_tenant_by_slug(slug):
            return jsonify({"error": "Tenant slug already exists", "code": "SLUG_EXISTS"}), 409
        return jsonify({"error": "Failed to create tenant"}), 500

    # Optionally create admin user for the tenant
    admin_info = None
    admin_username = data.get("admin_username")
    admin_password = data.get("admin_password")
    admin_email = data.get("admin_email")

    # Guard: tenant.id must exist after creation
    assert tenant.id is not None  # Type guard for mypy
    tenant_id = tenant.id

    if admin_username and admin_password:
        # Validate admin username
        if not validate_username(admin_username):
            return jsonify({"error": "Invalid admin username"}), 400

        # Validate admin password
        settings = get_security_settings_cached()
        is_valid, error_msg = validate_password(admin_password, policy_settings=settings)
        if not is_valid:
            return jsonify({"error": f"Admin password invalid: {error_msg}"}), 400

        # Validate admin email if provided
        if admin_email and not validate_email(admin_email):
            return jsonify({"error": "Invalid admin email"}), 400

        # Check if username already exists
        if user_repo.get_user_by_username(admin_username):
            return jsonify({"error": "Admin username already exists"}), 400

        # Check if email already exists (if provided)
        if admin_email and user_repo.get_user_by_email(admin_email):
            return jsonify({"error": "Admin email already exists"}), 400

        # Create admin user
        password_hash = _hash_password(admin_password)
        admin_email_final = admin_email or f"{admin_username}@{slug or 'tenant'}.local"
        admin_user_id = user_repo.create_user(
            username=admin_username,
            email=admin_email_final,
            password_hash=password_hash,
            role="admin",
            is_active=True,
            tenant_id=tenant_id,
        )

        if admin_user_id:
            # Increment tenant user count
            tenant_service.increment_user_count(tenant_id)
            admin_info = {
                "user_id": admin_user_id,
                "username": admin_username,
                "email": admin_email_final,
                "role": "admin",
            }
            logger.info(f"Created admin user {admin_username} for tenant {tenant.name}")
        else:
            logger.warning(f"Failed to create admin user for tenant {tenant.name}")

    response = tenant.to_dict()
    if admin_info:
        response["admin_user"] = admin_info

    return jsonify(response), 201


@tenant_bp.route("/<int:tenant_id>", methods=["PUT"])
@admin_required
def update_tenant(tenant_id: int):
    """Update tenant (admin only)."""

    data = request.get_json()

    if not data:
        return jsonify({"error": "Request body required"}), 400

    # Filter allowed fields
    allowed_fields = {
        "name",
        "slug",
        "plan",
        "status",
        "contact_email",
        "contact_phone",
        "contact_name",
        "trial_ends_at",
        "subscription_ends_at",
    }

    updates = {k: v for k, v in data.items() if k in allowed_fields}

    if not updates:
        return jsonify({"error": "No valid fields to update"}), 400

    success = tenant_service.update_tenant(tenant_id, updates)

    if not success:
        return jsonify({"error": "Failed to update tenant"}), 500

    tenant = tenant_service.get_tenant(tenant_id)
    if tenant is None:
        return jsonify({"error": "Tenant not found"}), 404
    return jsonify(tenant.to_dict())


@tenant_bp.route("/<int:tenant_id>/quota", methods=["PUT"])
@admin_required
def update_tenant_quota(tenant_id: int):
    """Update tenant quota (admin only)."""

    data = request.get_json()

    if not data:
        return jsonify({"error": "Request body required"}), 400

    success = tenant_service.update_quota(tenant_id, data)

    if not success:
        return jsonify({"error": "Failed to update tenant quota"}), 500

    tenant = tenant_service.get_tenant(tenant_id)
    if tenant is None:
        return jsonify({"error": "Tenant not found"}), 404
    return jsonify(tenant.to_dict())


@tenant_bp.route("/<int:tenant_id>/settings", methods=["PUT"])
@admin_required
def update_tenant_settings(tenant_id: int):
    """Update tenant settings (admin only)."""

    data = request.get_json()

    if not data:
        return jsonify({"error": "Request body required"}), 400

    success = tenant_service.update_settings(tenant_id, data)

    if not success:
        return jsonify({"error": "Failed to update tenant settings"}), 500

    # Invalidate tenant config cache for sensitive keyword settings
    try:
        from app.modules.workspace.tenant_config_cache import invalidate_tenant_config_cache

        invalidate_tenant_config_cache(tenant_id)
    except ImportError:
        pass  # Cache module may not be available in all contexts

    tenant = tenant_service.get_tenant(tenant_id)
    if tenant is None:
        return jsonify({"error": "Tenant not found"}), 404
    return jsonify(tenant.to_dict())


@tenant_bp.route("/<int:tenant_id>/suspend", methods=["POST"])
@admin_required
def suspend_tenant(tenant_id: int):
    """Suspend a tenant (admin only)."""

    data = request.get_json() or {}
    reason = data.get("reason")

    success = tenant_service.suspend_tenant(tenant_id, reason)

    if not success:
        return jsonify({"error": "Failed to suspend tenant"}), 500

    tenant = tenant_service.get_tenant(tenant_id)
    if tenant is None:
        return jsonify({"error": "Tenant not found"}), 404
    return jsonify(tenant.to_dict())


@tenant_bp.route("/<int:tenant_id>/activate", methods=["POST"])
@admin_required
def activate_tenant(tenant_id: int):
    """Activate a suspended tenant (admin only)."""

    success = tenant_service.activate_tenant(tenant_id)

    if not success:
        return jsonify({"error": "Failed to activate tenant"}), 500

    tenant = tenant_service.get_tenant(tenant_id)
    if tenant is None:
        return jsonify({"error": "Tenant not found"}), 404
    return jsonify(tenant.to_dict())


@tenant_bp.route("/<int:tenant_id>", methods=["DELETE"])
@admin_required
def delete_tenant(tenant_id: int):
    """Delete a tenant (admin only)."""

    hard = request.args.get("hard", "false").lower() == "true"

    success = tenant_service.delete_tenant(tenant_id, hard=hard)

    if not success:
        return jsonify({"error": "Failed to delete tenant"}), 500

    return jsonify({"message": "Tenant deleted"})


@tenant_bp.route("/<int:tenant_id>/usage", methods=["GET"])
@admin_required
def get_tenant_usage(tenant_id: int):
    """Get tenant usage history (admin only)."""

    days = request.args.get("days", 30, type=int)

    usage = tenant_service.get_usage_history(tenant_id, days=days)

    return jsonify(
        {
            "tenant_id": tenant_id,
            "days": days,
            "usage": [u.to_dict() for u in usage],
        }
    )


@tenant_bp.route("/<int:tenant_id>/stats", methods=["GET"])
@admin_required
def get_tenant_stats(tenant_id: int):
    """Get tenant statistics (admin only)."""

    stats = tenant_service.get_tenant_stats(tenant_id)

    if not stats:
        return jsonify({"error": "Tenant not found"}), 404

    return jsonify(stats)


@tenant_bp.route("/<int:tenant_id>/check-quota", methods=["POST"])
@admin_required
def check_tenant_quota(tenant_id: int):
    """Check if tenant has quota available."""

    data = request.get_json() or {}

    result = tenant_service.check_quota(
        tenant_id,
        tokens=data.get("tokens", 0),
        requests=data.get("requests", 1),
    )

    return jsonify(result)


@tenant_bp.route("/plans", methods=["GET"])
@admin_required
def get_plan_quotas():
    """Get quota configurations for all plans."""
    quotas = tenant_service.get_plan_quotas()
    return jsonify(quotas)


def register_tenant_routes(app):
    """Register tenant routes with the Flask app."""
    app.register_blueprint(tenant_bp)
