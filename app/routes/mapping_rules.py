"""
Open ACE - Tool Account Mapping Rules API

API endpoints for managing auto-mapping rules and viewing unmapped accounts.

Issue #2180: Tenant isolation for mapping rules.
Rules are associated with users, and users belong to tenants.
Tenant admins can only manage rules for users in their tenant.
"""

import logging

from flask import Blueprint, g, jsonify, request

from app.auth.decorators import admin_required, resolve_tenant_scope
from app.repositories.tool_account_mapping_rule_repo import ToolAccountMappingRuleRepository
from app.repositories.user_tool_account_repo import UserToolAccountRepository
from app.repositories.user_repo import UserRepository
from app.services.tool_account_auto_mapping_service import ToolAccountAutoMappingService

logger = logging.getLogger(__name__)

mapping_rules_bp = Blueprint("mapping_rules_bp", __name__)
user_repo = UserRepository()


def _validate_user_in_tenant(user_id: int, tenant_id: int) -> bool:
    """
    Validate that a user belongs to the specified tenant.

    Issue #2180: Ensures tenant admin can only operate on users in their tenant.
    """
    user = user_repo.get_user_by_id(user_id)
    if not user:
        return False
    user_tenant_id = user.get("tenant_id")
    return user_tenant_id == tenant_id


def _get_tenant_scoped_user_ids(tenant_id: int) -> list[int]:
    """
    Get list of user IDs belonging to a tenant.

    Used for filtering rules by tenant.
    """
    users = user_repo.get_all_users(tenant_id=tenant_id)
    return [u["id"] for u in users]


@mapping_rules_bp.route("/api/mapping-rules", methods=["GET"])
@admin_required
def get_all_rules():
    """
    Get all mapping rules.

    Issue #2180: Tenant admin sees only rules for users in their tenant.
    Platform admin sees all rules.
    """
    user_role = g.user.get("role")
    user_tenant_id = g.user.get("tenant_id")

    repo = ToolAccountMappingRuleRepository()

    if user_role == "platform_admin":
        # Platform admin: can see all rules
        rules = repo.get_all()
    elif user_role == "tenant_admin":
        # Tenant admin: only see rules for users in their tenant
        if user_tenant_id is None:
            return jsonify({"error": "Tenant admin must have tenant_id"}), 403
        # Get rules filtered by tenant's users
        all_rules = repo.get_all()
        tenant_user_ids = set(_get_tenant_scoped_user_ids(user_tenant_id))
        rules = [r for r in all_rules if r.user_id in tenant_user_ids]
    elif user_role == "admin":
        # Legacy admin: check tenant_id if available
        if user_tenant_id is not None:
            all_rules = repo.get_all()
            tenant_user_ids = set(_get_tenant_scoped_user_ids(user_tenant_id))
            rules = [r for r in all_rules if r.user_id in tenant_user_ids]
        else:
            rules = repo.get_all()
    else:
        return jsonify({"error": "Permission denied"}), 403

    return jsonify([rule.to_dict() for rule in rules])


@mapping_rules_bp.route("/api/mapping-rules/user/<int:user_id>", methods=["GET"])
@admin_required
def get_user_rules(user_id: int):
    """
    Get mapping rules for a specific user.

    Issue #2180: Validate user belongs to caller's tenant.
    """
    user_role = g.user.get("role")
    user_tenant_id = g.user.get("tenant_id")

    # Validate user belongs to caller's tenant (for tenant admin)
    if user_role in ("tenant_admin", "admin"):
        if user_tenant_id is None:
            return jsonify({"error": "Tenant admin must have tenant_id"}), 403
        if not _validate_user_in_tenant(user_id, user_tenant_id):
            return jsonify({"error": "User not found"}), 404

    repo = ToolAccountMappingRuleRepository()
    rules = repo.get_by_user_id(user_id)
    return jsonify([rule.to_dict() for rule in rules])


@mapping_rules_bp.route("/api/mapping-rules", methods=["POST"])
@admin_required
def create_rule():
    """
    Create a new mapping rule.

    Issue #2180: Validate user_id belongs to caller's tenant.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    user_id = data.get("user_id")
    pattern = data.get("pattern")
    if not user_id or not pattern:
        return jsonify({"error": "user_id and pattern are required"}), 400

    user_role = g.user.get("role")
    user_tenant_id = g.user.get("tenant_id")

    # Validate user belongs to caller's tenant
    if user_role in ("tenant_admin", "admin"):
        if user_tenant_id is None:
            return jsonify({"error": "Tenant admin must have tenant_id"}), 403
        if not _validate_user_in_tenant(user_id, user_tenant_id):
            return jsonify({"error": "Cannot create rule for user in different tenant"}), 403

    repo = ToolAccountMappingRuleRepository()
    rule = repo.create(
        user_id=user_id,
        pattern=pattern,
        match_type=data.get("match_type", "exact"),
        tool_type=data.get("tool_type"),
        priority=data.get("priority", 0),
        is_auto=data.get("is_auto", True),
        is_active=data.get("is_active", True),
        description=data.get("description"),
    )

    if rule:
        return jsonify(rule.to_dict()), 201
    else:
        return jsonify({"error": "Failed to create rule"}), 500


@mapping_rules_bp.route("/api/mapping-rules/<int:id>", methods=["PUT"])
@admin_required
def update_rule(id: int):
    """
    Update a mapping rule.

    Issue #2180: Validate rule belongs to user in caller's tenant.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    user_role = g.user.get("role")
    user_tenant_id = g.user.get("tenant_id")

    repo = ToolAccountMappingRuleRepository()

    # Get existing rule and validate tenant
    existing_rule = repo.get_by_id(id)
    if not existing_rule:
        return jsonify({"error": "Rule not found"}), 404

    if user_role in ("tenant_admin", "admin"):
        if user_tenant_id is None:
            return jsonify({"error": "Tenant admin must have tenant_id"}), 403
        if not _validate_user_in_tenant(existing_rule.user_id, user_tenant_id):
            return jsonify({"error": "Rule not found"}), 404

    # If user_id is being changed, validate new user too
    new_user_id = data.get("user_id")
    if new_user_id and user_role in ("tenant_admin", "admin"):
        if not _validate_user_in_tenant(new_user_id, user_tenant_id):
            return jsonify({"error": "Cannot assign rule to user in different tenant"}), 403

    rule = repo.update(
        id=id,
        user_id=new_user_id,
        pattern=data.get("pattern"),
        match_type=data.get("match_type"),
        tool_type=data.get("tool_type"),
        priority=data.get("priority"),
        is_auto=data.get("is_auto"),
        is_active=data.get("is_active"),
        description=data.get("description"),
    )

    if rule:
        return jsonify(rule.to_dict())
    else:
        return jsonify({"error": "Rule not found or update failed"}), 404


@mapping_rules_bp.route("/api/mapping-rules/<int:id>", methods=["DELETE"])
@admin_required
def delete_rule(id: int):
    """
    Delete a mapping rule.

    Issue #2180: Validate rule belongs to user in caller's tenant.
    """
    user_role = g.user.get("role")
    user_tenant_id = g.user.get("tenant_id")

    repo = ToolAccountMappingRuleRepository()

    # Get existing rule and validate tenant
    existing_rule = repo.get_by_id(id)
    if not existing_rule:
        return jsonify({"error": "Rule not found"}), 404

    if user_role in ("tenant_admin", "admin"):
        if user_tenant_id is None:
            return jsonify({"error": "Tenant admin must have tenant_id"}), 403
        if not _validate_user_in_tenant(existing_rule.user_id, user_tenant_id):
            return jsonify({"error": "Rule not found"}), 404

    success = repo.delete(id)
    if success:
        return jsonify({"success": True})
    else:
        return jsonify({"error": "Failed to delete rule"}), 500


@mapping_rules_bp.route("/api/mapping-rules/user/<int:user_id>/generate-default", methods=["POST"])
@admin_required
def generate_default_rules(user_id: int):
    """
    Generate default mapping rules for a user.

    Issue #2180: Validate user belongs to caller's tenant.
    """
    user_role = g.user.get("role")
    user_tenant_id = g.user.get("tenant_id")

    # Validate user belongs to caller's tenant
    if user_role in ("tenant_admin", "admin"):
        if user_tenant_id is None:
            return jsonify({"error": "Tenant admin must have tenant_id"}), 403
        if not _validate_user_in_tenant(user_id, user_tenant_id):
            return jsonify({"error": "User not found"}), 404

    service = ToolAccountAutoMappingService()
    rules = service.create_default_rules_for_user(user_id)
    return jsonify([rule.to_dict() for rule in rules]), 201


@mapping_rules_bp.route("/api/mapping-stats", methods=["GET"])
@admin_required
def get_mapping_stats():
    """
    Get mapping statistics.

    Issue #2180: Tenant admin sees only their tenant's stats.
    """
    user_role = g.user.get("role")
    user_tenant_id = g.user.get("tenant_id")

    service = ToolAccountAutoMappingService()

    if user_role in ("tenant_admin", "admin") and user_tenant_id is not None:
        # Filter stats by tenant
        stats = service.get_mapping_stats(tenant_id=user_tenant_id)
    else:
        stats = service.get_mapping_stats()

    return jsonify(stats)


@mapping_rules_bp.route("/api/mapping-rules/auto-map", methods=["POST"])
@admin_required
def run_auto_mapping():
    """
    Run auto-mapping for all unmapped accounts.

    Issue #2180: Tenant admin can only run for their tenant.
    """
    data = request.get_json() or {}
    dry_run = data.get("dry_run", False)

    user_role = g.user.get("role")
    user_tenant_id = g.user.get("tenant_id")

    service = ToolAccountAutoMappingService()

    if user_role in ("tenant_admin", "admin") and user_tenant_id is not None:
        # Only auto-map for tenant's users
        results, still_unmapped = service.run_auto_mapping(
            dry_run=dry_run, tenant_id=user_tenant_id
        )
    else:
        results, still_unmapped = service.run_auto_mapping(dry_run=dry_run)

    return jsonify(
        {
            "mapped_count": len(results),
            "unmapped_count": len(still_unmapped),
            "mappings": [r.__dict__ for r in results],
            "dry_run": dry_run,
        }
    )


@mapping_rules_bp.route("/api/mapping-rules/test-match", methods=["POST"])
@admin_required
def test_match():
    """Test if a tool_account matches any rules."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    tool_account = data.get("tool_account")
    if not tool_account:
        return jsonify({"error": "tool_account is required"}), 400

    service = ToolAccountAutoMappingService()
    result = service.auto_map_account(tool_account, data.get("tool_type"))

    if result:
        return jsonify(
            {
                "matched": True,
                "user_id": result.user_id,
                "username": result.username,
                "matched_by": result.matched_by,
                "rule_id": result.rule_id,
            }
        )
    else:
        return jsonify({"matched": False})


@mapping_rules_bp.route("/api/unmapped-accounts", methods=["GET"])
@admin_required
def get_unmapped_accounts():
    """
    Get list of unmapped tool accounts.

    Issue #2180: Tenant admin sees only their tenant's unmapped accounts.
    """
    user_role = g.user.get("role")
    user_tenant_id = g.user.get("tenant_id")

    repo = UserToolAccountRepository()

    if user_role in ("tenant_admin", "admin") and user_tenant_id is not None:
        unmapped = repo.get_unmapped_tool_accounts(tenant_id=user_tenant_id)
    else:
        unmapped = repo.get_unmapped_tool_accounts()

    # Add inferred tool type
    service = ToolAccountAutoMappingService()
    for account in unmapped:
        account["inferred_tool_type"] = service._infer_tool_type(account.get("sender_name", ""))

    return jsonify(unmapped)


@mapping_rules_bp.route("/api/unmapped-accounts/<sender_name>/suggest-mapping", methods=["GET"])
@admin_required
def suggest_mapping(sender_name: str):
    """Get suggested mapping for an unmapped account."""
    service = ToolAccountAutoMappingService()
    result = service.auto_map_account(sender_name)

    if result:
        return jsonify(
            {
                "suggested_user_id": result.user_id,
                "suggested_username": result.username,
                "matched_by": result.matched_by,
                "rule_id": result.rule_id,
            }
        )
    else:
        return jsonify({"suggestion": None})


@mapping_rules_bp.route("/api/unmapped-accounts/<sender_name>/map", methods=["POST"])
@admin_required
def manual_map_account(sender_name: str):
    """
    Manually map an unmapped account to a user.

    Issue #2180: Validate user belongs to caller's tenant.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    user_id = data.get("user_id")
    if not user_id:
        return jsonify({"error": "user_id is required"}), 400

    user_role = g.user.get("role")
    user_tenant_id = g.user.get("tenant_id")

    # Validate user belongs to caller's tenant
    if user_role in ("tenant_admin", "admin"):
        if user_tenant_id is None:
            return jsonify({"error": "Tenant admin must have tenant_id"}), 403
        if not _validate_user_in_tenant(user_id, user_tenant_id):
            return jsonify({"error": "Cannot map to user in different tenant"}), 403

    repo = UserToolAccountRepository()
    mapping = repo.create(
        user_id=user_id,
        tool_account=sender_name,
        tool_type=data.get("tool_type"),
        description=data.get("description", "Manual mapping"),
    )

    if mapping:
        # Update daily_messages user_id
        repo.update_daily_messages_user_id(sender_name, user_id)
        return jsonify(mapping.to_dict()), 201
    else:
        return jsonify({"error": "Failed to create mapping"}), 500