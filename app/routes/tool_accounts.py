from typing import Any

"""
Open ACE - User Tool Accounts API Routes

API routes for managing user tool account mappings.

Issue #2761: Extended with mapping source/status support, conflict tracking,
and predeclared account management.
"""

import logging

from flask import Blueprint, g, jsonify, request

from app.auth.decorators import auth_required
from app.models.user_tool_account import TOOL_TYPES, get_tool_type_display
from app.repositories.user_repo import UserRepository
from app.repositories.user_tool_account_repo import UserToolAccountRepository

logger = logging.getLogger(__name__)

tool_accounts_bp = Blueprint("tool_accounts", __name__)
tool_account_repo = UserToolAccountRepository()
user_repo = UserRepository()


@tool_accounts_bp.before_request
@auth_required
def _require_auth():
    pass


@tool_accounts_bp.route("/tool-accounts", methods=["GET"])
def get_all_tool_accounts():
    """Get all tool account mappings."""
    mappings = tool_account_repo.get_all()

    # Group by user
    result: dict[int, dict[str, Any]] = {}
    for mapping in mappings:
        user_id = mapping.user_id
        if user_id not in result:
            user = user_repo.get_user_by_id(user_id)
            result[user_id] = {"user": user, "tool_accounts": []}
        result[user_id]["tool_accounts"].append(mapping.to_dict())

    return jsonify(result)


@tool_accounts_bp.route("/tool-accounts/user/<int:user_id>", methods=["GET"])
def get_user_tool_accounts(user_id: int):
    """Get tool accounts for a specific user."""
    mappings = tool_account_repo.get_by_user_id(user_id)

    # Format with display names
    result = []
    for mapping in mappings:
        data = mapping.to_dict()
        if mapping.tool_type:
            data["tool_type_display"] = get_tool_type_display(mapping.tool_type)
        result.append(data)

    return jsonify(result)


@tool_accounts_bp.route("/tool-accounts/unmapped", methods=["GET"])
def get_unmapped_tool_accounts():
    """Get sender_names that are not mapped to any user."""
    unmapped = tool_account_repo.get_unmapped_tool_accounts()

    # Issue #1829, F3: classify tool_type primarily from the structured
    # ``message_source`` resolved during import, instead of the brittle
    # ``-dingtalk`` substring (real DingTalk userids don't follow a -dingtalk
    # convention, so that heuristic rarely matched real data). The Feishu/Lark
    # ``ou_`` prefix and the openclaw-family tool-name tokens are kept as
    # fallbacks: ``ou_`` is Feishu's stable OpenAPI id convention, and
    # openclaw-family sub-tools (qwen/claude/openclaw) all share
    # message_source="openclaw", so the tool-name token is the only way to tell
    # them apart.
    source_to_tool_type = {
        "dingtalk": "dingtalk",
        "feishu": "feishu",
        "slack": "slack",
    }

    result = []
    for item in unmapped:
        sender_name = item.get("sender_name")
        message_source = item.get("message_source")

        tool_type = source_to_tool_type.get(message_source or "")
        # Feishu/Lark OpenAPI sender ids use the stable ou_ prefix; kept as a
        # fallback for rows whose message_source wasn't resolved.
        if not tool_type and sender_name and sender_name.startswith("ou_"):
            tool_type = "feishu"
        # openclaw-family sub-tools carry the tool name in sender_name (e.g.
        # "user-host-qwen"); message_source is "openclaw" and carries no
        # sub-tool signal, so the token is the only discriminator here.
        if not tool_type and sender_name:
            for token, ttype in (
                ("-qwen", "qwen"),
                ("-claude", "claude"),
                ("-openclaw", "openclaw"),
            ):
                if token in sender_name:
                    tool_type = ttype
                    break

        result.append(
            {
                "sender_name": sender_name,
                "tool_type": tool_type,
                "tool_type_display": get_tool_type_display(tool_type),
                "message_count": item.get("message_count"),
                "first_date": item.get("first_date"),
                "last_date": item.get("last_date"),
            }
        )

    return jsonify(result)


@tool_accounts_bp.route("/tool-accounts", methods=["POST"])
def create_tool_account():
    """Create a new tool account mapping.

    Issue #2761: Added is_predeclared parameter for predeclaring accounts.
    Response now includes mapping_source, mapping_status, and warnings.
    """
    data = request.get_json()

    if not data:
        return jsonify({"error": "No data provided"}), 400

    user_id = data.get("user_id")
    tool_account = data.get("tool_account")

    if not user_id or not tool_account:
        return jsonify({"error": "user_id and tool_account are required"}), 400

    # Validate tool_account format
    tool_account = tool_account.strip()
    if not tool_account:
        return jsonify({"error": "tool_account cannot be empty or whitespace"}), 400

    # Check if user exists
    user = user_repo.get_user_by_id(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    # Check if tool_account already mapped
    existing = tool_account_repo.get_by_tool_account(tool_account)
    if existing:
        return (
            jsonify(
                {
                    "error": "Tool account already mapped to another user",
                    "existing_user_id": existing.user_id,
                }
            ),
            400,
        )

    # Determine mapping source and status
    is_predeclared = data.get("is_predeclared", False)
    if is_predeclared:
        mapping_source = "predeclared"
        mapping_status = "pending"
    else:
        mapping_source = "manual"
        mapping_status = "active"

    # Get current user as creator
    created_by = None
    if hasattr(g, "user") and g.user:
        created_by = g.user.get("id")

    mapping = tool_account_repo.create(
        user_id=user_id,
        tool_account=tool_account,
        tool_type=data.get("tool_type"),
        description=data.get("description"),
        mapping_source=mapping_source,
        mapping_status=mapping_status,
        created_by=created_by,
        tenant_id=user.tenant_id,
    )

    if mapping:
        # Update daily_messages user_id (only for active mappings)
        updated_count = 0
        if mapping_status == "active":
            updated_count = tool_account_repo.update_daily_messages_user_id(tool_account, user_id)

        response = {
            "mapping": mapping.to_dict(),
            "updated_messages": updated_count,
            "mapping_source": mapping_source,
            "mapping_status": mapping_status,
            "warnings": [],
        }

        # Add warning for predeclared accounts
        if is_predeclared:
            response["warnings"].append(
                "This is a predeclared account. It will become active when matching data is detected."
            )

        return jsonify(response)

    return jsonify({"error": "Failed to create mapping"}), 500


@tool_accounts_bp.route("/tool-accounts/<int:id>", methods=["PUT"])
def update_tool_account(id: int):
    """Update a tool account mapping."""
    data = request.get_json()

    if not data:
        return jsonify({"error": "No data provided"}), 400

    mapping = tool_account_repo.update(
        id=id,
        user_id=data.get("user_id"),
        tool_account=data.get("tool_account"),
        tool_type=data.get("tool_type"),
        description=data.get("description"),
    )

    if mapping:
        return jsonify(mapping.to_dict())

    return jsonify({"error": "Failed to update mapping"}), 500


@tool_accounts_bp.route("/tool-accounts/<int:id>", methods=["DELETE"])
def delete_tool_account(id: int):
    """Delete a tool account mapping."""
    success = tool_account_repo.delete(id)

    if success:
        return jsonify({"status": "success"})

    return jsonify({"error": "Failed to delete mapping"}), 500


@tool_accounts_bp.route("/tool-accounts/user/<int:user_id>/batch", methods=["POST"])
def batch_create_user_tool_accounts(user_id: int):
    """Batch create tool account mappings for a user."""
    data = request.get_json()

    if not data or not data.get("tool_accounts"):
        return jsonify({"error": "tool_accounts list is required"}), 400

    # Check if user exists
    user = user_repo.get_user_by_id(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    mappings = tool_account_repo.batch_create_for_user(
        user_id=user_id, tool_accounts=data.get("tool_accounts")
    )

    return jsonify({"created_count": len(mappings), "mappings": [m.to_dict() for m in mappings]})


@tool_accounts_bp.route("/tool-types", methods=["GET"])
def get_tool_types():
    """Get available tool types."""
    return jsonify([{"value": k, "display": v} for k, v in TOOL_TYPES.items()])


# =========================================================================
# Issue #2761: New endpoints for conflict and status management
# =========================================================================


@tool_accounts_bp.route("/tool-accounts/conflicts", methods=["GET"])
def get_conflicts():
    """Get all unresolved conflict mappings.

    Issue #2761: Returns mappings with conflict status for admin review.
    """
    # Get current user's tenant
    tenant_id = None
    if hasattr(g, "user") and g.user:
        tenant_id = g.user.get("tenant_id")

    conflicts = tool_account_repo.get_by_status("conflict_type", tenant_id)
    conflicts.extend(tool_account_repo.get_by_status("conflict_owner", tenant_id))
    conflicts.extend(tool_account_repo.get_by_status("conflict_tenant", tenant_id))

    result = []
    for mapping in conflicts:
        data = mapping.to_dict()
        if mapping.tool_type:
            data["tool_type_display"] = get_tool_type_display(mapping.tool_type)
        result.append(data)

    return jsonify(result)


@tool_accounts_bp.route("/tool-accounts/pending", methods=["GET"])
def get_pending_mappings():
    """Get all pending (predeclared) mappings.

    Issue #2761: Returns mappings waiting for data to activate.
    """
    tenant_id = None
    if hasattr(g, "user") and g.user:
        tenant_id = g.user.get("tenant_id")

    pending = tool_account_repo.get_by_status("pending", tenant_id)

    result = []
    for mapping in pending:
        data = mapping.to_dict()
        if mapping.tool_type:
            data["tool_type_display"] = get_tool_type_display(mapping.tool_type)
        result.append(data)

    return jsonify(result)


@tool_accounts_bp.route("/tool-accounts/stale", methods=["GET"])
def get_stale_mappings():
    """Get all stale mappings.

    Issue #2761: Returns mappings with no recent activity.
    """
    tenant_id = None
    if hasattr(g, "user") and g.user:
        tenant_id = g.user.get("tenant_id")

    stale = tool_account_repo.get_by_status("stale", tenant_id)

    result = []
    for mapping in stale:
        data = mapping.to_dict()
        if mapping.tool_type:
            data["tool_type_display"] = get_tool_type_display(mapping.tool_type)
        result.append(data)

    return jsonify(result)


@tool_accounts_bp.route("/tool-accounts/<int:id>/resolve-conflict", methods=["POST"])
def resolve_conflict(id: int):
    """Resolve a conflict mapping.

    Issue #2761: Admin can confirm or reject a conflict mapping.

    Request body:
    {
        "action": "confirm" | "reject",
        "new_status": "active" | "pending"
    }
    """
    mapping = tool_account_repo.get_by_id(id)
    if not mapping:
        return jsonify({"error": "Mapping not found"}), 404

    # Check if mapping is in conflict state
    if not mapping.mapping_status or not mapping.mapping_status.startswith("conflict"):
        return jsonify({"error": "Mapping is not in conflict state"}), 400

    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    action = data.get("action")
    if action not in ("confirm", "reject"):
        return jsonify({"error": "action must be 'confirm' or 'reject'"}), 400

    if action == "confirm":
        new_status = data.get("new_status", "active")
        if new_status not in ("active", "pending"):
            return jsonify({"error": "new_status must be 'active' or 'pending'"}), 400
    else:
        # Reject: delete the mapping
        success = tool_account_repo.delete(id)
        if success:
            return jsonify({"status": "rejected", "message": "Conflict mapping rejected and deleted"})
        return jsonify({"error": "Failed to delete mapping"}), 500

    # Update status with optimistic lock
    updated = tool_account_repo.update_status_with_version(id, new_status, mapping.version)

    if updated:
        return jsonify({
            "status": "resolved",
            "mapping": updated.to_dict(),
            "new_status": new_status,
        })

    return jsonify({"error": "Failed to resolve conflict - version mismatch"}), 409


@tool_accounts_bp.route("/tool-accounts/<int:id>/touch-activity", methods=["POST"])
def touch_mapping_activity(id: int):
    """Update last_activity_at timestamp.

    Issue #2761: Called when account is seen in incoming data.
    """
    mapping = tool_account_repo.get_by_id(id)
    if not mapping:
        return jsonify({"error": "Mapping not found"}), 404

    success = tool_account_repo.touch_activity(id)

    if success:
        return jsonify({"status": "success"})

    return jsonify({"error": "Failed to update activity"}), 500


@tool_accounts_bp.route("/tool-accounts/status-summary", methods=["GET"])
def get_status_summary():
    """Get summary counts by mapping status.

    Issue #2761: Dashboard endpoint for monitoring mapping health.
    """
    tenant_id = None
    if hasattr(g, "user") and g.user:
        tenant_id = g.user.get("tenant_id")

    summary = {
        "pending": len(tool_account_repo.get_by_status("pending", tenant_id)),
        "active": len(tool_account_repo.get_by_status("active", tenant_id)),
        "stale": len(tool_account_repo.get_by_status("stale", tenant_id)),
        "conflict_type": len(tool_account_repo.get_by_status("conflict_type", tenant_id)),
        "conflict_owner": len(tool_account_repo.get_by_status("conflict_owner", tenant_id)),
        "conflict_tenant": len(tool_account_repo.get_by_status("conflict_tenant", tenant_id)),
    }

    return jsonify(summary)
