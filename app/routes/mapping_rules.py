"""
Open ACE - Tool Account Mapping Rules API

API endpoints for managing auto-mapping rules and viewing unmapped accounts.
"""

from flask import Blueprint, jsonify, request

from app.auth.decorators import admin_required
from app.repositories.tool_account_mapping_rule_repo import ToolAccountMappingRuleRepository
from app.repositories.user_tool_account_repo import UserToolAccountRepository
from app.services.tool_account_auto_mapping_service import ToolAccountAutoMappingService

mapping_rules_bp = Blueprint("mapping_rules_bp", __name__)


@mapping_rules_bp.route("/api/mapping-rules", methods=["GET"])
@admin_required
def get_all_rules():
    """Get all mapping rules."""
    repo = ToolAccountMappingRuleRepository()
    rules = repo.get_all()
    return jsonify([rule.to_dict() for rule in rules])


@mapping_rules_bp.route("/api/mapping-rules/user/<int:user_id>", methods=["GET"])
@admin_required
def get_user_rules(user_id: int):
    """Get mapping rules for a specific user."""
    repo = ToolAccountMappingRuleRepository()
    rules = repo.get_by_user_id(user_id)
    return jsonify([rule.to_dict() for rule in rules])


@mapping_rules_bp.route("/api/mapping-rules", methods=["POST"])
@admin_required
def create_rule():
    """Create a new mapping rule."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    user_id = data.get("user_id")
    pattern = data.get("pattern")
    if not user_id or not pattern:
        return jsonify({"error": "user_id and pattern are required"}), 400

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
    """Update a mapping rule."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    repo = ToolAccountMappingRuleRepository()
    rule = repo.update(
        id=id,
        user_id=data.get("user_id"),
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
    """Delete a mapping rule."""
    repo = ToolAccountMappingRuleRepository()
    success = repo.delete(id)
    if success:
        return jsonify({"success": True})
    else:
        return jsonify({"error": "Failed to delete rule"}), 500


@mapping_rules_bp.route("/api/mapping-rules/user/<int:user_id>/generate-default", methods=["POST"])
@admin_required
def generate_default_rules(user_id: int):
    """Generate default mapping rules for a user."""
    service = ToolAccountAutoMappingService()
    rules = service.create_default_rules_for_user(user_id)
    return jsonify([rule.to_dict() for rule in rules]), 201


@mapping_rules_bp.route("/api/mapping-stats", methods=["GET"])
@admin_required
def get_mapping_stats():
    """Get mapping statistics."""
    service = ToolAccountAutoMappingService()
    stats = service.get_mapping_stats()
    return jsonify(stats)


@mapping_rules_bp.route("/api/mapping-rules/auto-map", methods=["POST"])
@admin_required
def run_auto_mapping():
    """Run auto-mapping for all unmapped accounts."""
    data = request.get_json() or {}
    dry_run = data.get("dry_run", False)

    service = ToolAccountAutoMappingService()
    results, still_unmapped = service.run_auto_mapping(dry_run=dry_run)

    return jsonify({
        "mapped_count": len(results),
        "unmapped_count": len(still_unmapped),
        "mappings": [r.__dict__ for r in results],
        "dry_run": dry_run,
    })


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
        return jsonify({
            "matched": True,
            "user_id": result.user_id,
            "username": result.username,
            "matched_by": result.matched_by,
            "rule_id": result.rule_id,
        })
    else:
        return jsonify({"matched": False})


@mapping_rules_bp.route("/api/unmapped-accounts", methods=["GET"])
@admin_required
def get_unmapped_accounts():
    """Get list of unmapped tool accounts."""
    repo = UserToolAccountRepository()
    unmapped = repo.get_unmapped_tool_accounts()

    # Add inferred tool type
    service = ToolAccountAutoMappingService()
    for account in unmapped:
        account["inferred_tool_type"] = service._infer_tool_type(
            account.get("sender_name", "")
        )

    return jsonify(unmapped)


@mapping_rules_bp.route("/api/unmapped-accounts/<sender_name>/suggest-mapping", methods=["GET"])
@admin_required
def suggest_mapping(sender_name: str):
    """Get suggested mapping for an unmapped account."""
    service = ToolAccountAutoMappingService()
    result = service.auto_map_account(sender_name)

    if result:
        return jsonify({
            "suggested_user_id": result.user_id,
            "suggested_username": result.username,
            "matched_by": result.matched_by,
            "rule_id": result.rule_id,
        })
    else:
        return jsonify({"suggestion": None})


@mapping_rules_bp.route("/api/unmapped-accounts/<sender_name>/map", methods=["POST"])
@admin_required
def manual_map_account(sender_name: str):
    """Manually map an unmapped account to a user."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    user_id = data.get("user_id")
    if not user_id:
        return jsonify({"error": "user_id is required"}), 400

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
