#!/usr/bin/env python3
"""
Open ACE - Tenant Routes

API endpoints for multi-tenant management.
"""

import logging

from flask import Blueprint, jsonify, request

from app.auth.decorators import admin_required
from app.services.tenant_service import TenantService

logger = logging.getLogger(__name__)

# Create blueprint
tenant_bp = Blueprint("tenant", __name__, url_prefix="/api/tenants")

# Services
tenant_service = TenantService()


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
    """Create a new tenant (admin only)."""

    data = request.get_json()

    if not data:
        return jsonify({"error": "Request body required"}), 400

    name = data.get("name")
    if not name:
        return jsonify({"error": "Tenant name is required"}), 400

    tenant = tenant_service.create_tenant(
        name=name,
        slug=data.get("slug"),
        plan=data.get("plan", "standard"),
        contact_email=data.get("contact_email", ""),
        contact_name=data.get("contact_name"),
        trial_days=data.get("trial_days"),
    )

    if not tenant:
        return jsonify({"error": "Failed to create tenant"}), 500

    return jsonify(tenant.to_dict()), 201


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

    tenant = tenant_service.get_tenant(tenant_id)
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
    return jsonify(tenant.to_dict())


@tenant_bp.route("/<int:tenant_id>/activate", methods=["POST"])
@admin_required
def activate_tenant(tenant_id: int):
    """Activate a suspended tenant (admin only)."""

    success = tenant_service.activate_tenant(tenant_id)

    if not success:
        return jsonify({"error": "Failed to activate tenant"}), 500

    tenant = tenant_service.get_tenant(tenant_id)
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
