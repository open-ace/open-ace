"""
Open ACE - User Tool Accounts API Routes

API routes for managing user tool account mappings.

Issue #2759: Authorization fix for tool account management interfaces.
- All endpoints require admin role
- tenant_admin can only operate on users in their tenant
- platform_admin has global access with cross-tenant audit logging

Issue #2761: Extended with mapping source/status support, conflict tracking,
and predeclared account management.
"""

import logging
import uuid
from typing import Any

from flask import Blueprint, g, jsonify, request

from app.auth.decorators import admin_required
from app.auth.permissions import is_platform_admin_role
from app.auth.tool_account_auth import (
    get_mapping_and_validate_tenant,
    get_tenant_scoped_user_ids,
    validate_target_user_for_write,
    validate_user_in_tenant,
)
from app.models.user_tool_account import TOOL_TYPES, get_tool_type_display
from app.modules.governance.audit_logger import AuditAction, AuditLogger
from app.repositories.user_repo import UserRepository
from app.repositories.user_tool_account_repo import UserToolAccountRepository

logger = logging.getLogger(__name__)

tool_accounts_bp = Blueprint("tool_accounts", __name__)
tool_account_repo = UserToolAccountRepository()
user_repo = UserRepository()


@tool_accounts_bp.before_request
@admin_required
def _require_admin():
    """Require admin role for all tool account management endpoints.

    Issue #2759: Replace auth_required with admin_required.
    """
    pass


# =============================================================================
# Audit Logging Helpers
# =============================================================================


def _log_tool_account_action(
    action: AuditAction,
    target_user_id: int | None,
    tool_account: str | None,
    mapping_id: int | None = None,
    old_value: dict | None = None,
    new_value: dict | None = None,
    tenant_id: int | None = None,
) -> None:
    """
    Log tool account mapping action for audit.

    Issue #2759: Audit logging for all write operations.

    Args:
        action: The audit action type.
        target_user_id: The user ID being affected.
        tool_account: The tool account name.
        mapping_id: The mapping record ID.
        old_value: Previous value (for updates).
        new_value: New value (for creates/updates).
        tenant_id: Target tenant ID.
    """
    try:
        audit_logger = AuditLogger()
        actor_user_id = g.user.get("id")
        actor_tenant_id = g.user.get("tenant_id")

        details: dict[str, Any] = {
            "target_user_id": target_user_id,
            "tool_account": tool_account,
            "actor_tenant_id": actor_tenant_id,
        }

        if old_value is not None:
            details["old_value"] = old_value
        if new_value is not None:
            details["new_value"] = new_value

        # Mark cross-tenant operations
        if tenant_id is not None and actor_tenant_id != tenant_id:
            details["is_cross_tenant"] = True

        audit_logger.log_action(
            action,
            user_id=actor_user_id,
            severity="info",
            resource_type="tool_account_mapping",
            resource_id=str(mapping_id) if mapping_id else None,
            tenant_id=tenant_id,
            details=details,
        )
    except Exception as e:
        logger.warning("Failed to log tool account audit: %s", e)


# =============================================================================
# Query Endpoints
# =============================================================================


@tool_accounts_bp.route("/tool-accounts", methods=["GET"])
def get_all_tool_accounts():
    """
    Get all tool account mappings.

    Issue #2759: Tenant isolation for listing.
    - tenant_admin: only see mappings for users in their tenant
    - platform_admin: see all mappings
    """
    user_role = g.user.get("role")
    user_tenant_id = g.user.get("tenant_id")

    if user_role == "tenant_admin":
        # Tenant admin: only see mappings for users in their tenant
        if user_tenant_id is None:
            return jsonify({"error": "Tenant admin must have tenant_id"}), 403

        tenant_user_ids = set(get_tenant_scoped_user_ids(user_tenant_id))
        all_mappings = tool_account_repo.get_all()
        mappings = [m for m in all_mappings if m.user_id in tenant_user_ids]
    else:
        # Platform admin and legacy admin: can see all
        mappings = tool_account_repo.get_all()

    # Group by user
    result: dict[int, dict[str, Any]] = {}
    for mapping in mappings:
        uid = mapping.user_id
        if uid not in result:
            user = user_repo.get_user_by_id(uid)
            result[uid] = {"user": user, "tool_accounts": []}
        result[uid]["tool_accounts"].append(mapping.to_dict())

    return jsonify(result)


@tool_accounts_bp.route("/tool-accounts/user/<int:user_id>", methods=["GET"])
def get_user_tool_accounts(user_id: int):
    """
    Get tool accounts for a specific user.

    Issue #2759: Tenant isolation for user-specific query.
    - tenant_admin: validate user belongs to their tenant
    - platform_admin: no validation
    """
    user_role = g.user.get("role")
    user_tenant_id = g.user.get("tenant_id")

    # Validate user belongs to caller's tenant (tenant admin only)
    if user_role == "tenant_admin":
        if user_tenant_id is None:
            return jsonify({"error": "Tenant admin must have tenant_id"}), 403
        if not validate_user_in_tenant(user_id, user_tenant_id):
            return jsonify({"error": "User not found"}), 404

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
    """
    Get sender_names that are not mapped to any user.

    Issue #2759: Tenant isolation for unmapped accounts.
    - tenant_admin: only see unmapped accounts for their tenant
    - platform_admin: see all unmapped accounts
    """
    user_role = g.user.get("role")
    user_tenant_id = g.user.get("tenant_id")

    if user_role == "tenant_admin":
        if user_tenant_id is None:
            return jsonify({"error": "Tenant admin must have tenant_id"}), 403
        unmapped = tool_account_repo.get_unmapped_tool_accounts(tenant_id=user_tenant_id)
    else:
        unmapped = tool_account_repo.get_unmapped_tool_accounts()

    # Issue #1829, F3: classify tool_type primarily from message_source
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
        if not tool_type and sender_name and sender_name.startswith("ou_"):
            tool_type = "feishu"
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


@tool_accounts_bp.route("/tool-types", methods=["GET"])
def get_tool_types():
    """
    Get available tool types.

    Issue #2759: Require admin role (protected by before_request).
    """
    return jsonify([{"value": k, "display": v} for k, v in TOOL_TYPES.items()])


# =============================================================================
# Write Endpoints
# =============================================================================


@tool_accounts_bp.route("/tool-accounts", methods=["POST"])
def create_tool_account():
    """
    Create a new tool account mapping.

    Issue #2759: Authorization for creation.
    - tenant_admin: validate target user belongs to their tenant
    - platform_admin: no validation

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

    user_role = g.user.get("role")
    user_tenant_id = g.user.get("tenant_id")

    # Validate target user for tenant_admin
    if user_role == "tenant_admin":
        if user_tenant_id is None:
            return jsonify({"error": "Tenant admin must have tenant_id"}), 403

        target_user, error = validate_target_user_for_write(user_id, user_tenant_id)
        if error:
            return jsonify({"error": error}), 403
    else:
        # Platform admin: just check user exists
        target_user = user_repo.get_user_by_id(user_id)
        if not target_user:
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

        # Audit logging
        _log_tool_account_action(
            action=AuditAction.TOOL_ACCOUNT_MAPPING_CREATE,
            target_user_id=user_id,
            tool_account=tool_account,
            mapping_id=mapping.id,
            new_value=mapping.to_dict(),
            tenant_id=target_user.get("tenant_id") if target_user else None,
        )

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
    """
    Update a tool account mapping.

    Issue #2759: Resource-level authorization for update.
    - Validate mapping exists
    - tenant_admin: validate mapping's user belongs to their tenant
    - If changing user_id, validate new user too
    """
    data = request.get_json()

    if not data:
        return jsonify({"error": "No data provided"}), 400

    user_role = g.user.get("role")
    user_tenant_id = g.user.get("tenant_id")

    # Get and validate mapping for tenant_admin
    if user_role == "tenant_admin":
        if user_tenant_id is None:
            return jsonify({"error": "Tenant admin must have tenant_id"}), 403

        mapping, owner_user, error = get_mapping_and_validate_tenant(id, user_tenant_id)
        if error:
            return jsonify({"error": "Mapping not found"}), 404
        if mapping is None:
            return jsonify({"error": "Mapping not found"}), 404
    else:
        # Platform admin: just check mapping exists
        mapping = tool_account_repo.get_by_id(id)
        if not mapping:
            return jsonify({"error": "Mapping not found"}), 404
        owner_user = user_repo.get_user_by_id(mapping.user_id)

    # Save old value for audit
    old_value = mapping.to_dict()

    # If user_id is being changed, validate new user too (tenant admin only)
    new_user_id = data.get("user_id")
    if new_user_id and new_user_id != mapping.user_id:
        if user_role == "tenant_admin":
            new_target_user, error = validate_target_user_for_write(new_user_id, user_tenant_id)
            if error:
                return jsonify({"error": error}), 403
        else:
            new_target_user = user_repo.get_user_by_id(new_user_id)
            if not new_target_user:
                return jsonify({"error": "New user not found"}), 404

    mapping = tool_account_repo.update(
        id=id,
        user_id=new_user_id,
        tool_account=data.get("tool_account"),
        tool_type=data.get("tool_type"),
        description=data.get("description"),
    )

    if mapping:
        # Audit logging
        _log_tool_account_action(
            action=AuditAction.TOOL_ACCOUNT_MAPPING_UPDATE,
            target_user_id=mapping.user_id,
            tool_account=mapping.tool_account,
            mapping_id=mapping.id,
            old_value=old_value,
            new_value=mapping.to_dict(),
            tenant_id=owner_user.get("tenant_id") if owner_user else None,
        )

        return jsonify(mapping.to_dict())

    return jsonify({"error": "Failed to update mapping"}), 500


@tool_accounts_bp.route("/tool-accounts/<int:id>", methods=["DELETE"])
def delete_tool_account(id: int):
    """
    Delete a tool account mapping.

    Issue #2759: Resource-level authorization for delete.
    - Validate mapping exists
    - tenant_admin: validate mapping's user belongs to their tenant
    """
    user_role = g.user.get("role")
    user_tenant_id = g.user.get("tenant_id")

    # Get and validate mapping for tenant_admin
    if user_role == "tenant_admin":
        if user_tenant_id is None:
            return jsonify({"error": "Tenant admin must have tenant_id"}), 403

        mapping, owner_user, error = get_mapping_and_validate_tenant(id, user_tenant_id)
        if error:
            return jsonify({"error": "Mapping not found"}), 404
        if mapping is None:
            return jsonify({"error": "Mapping not found"}), 404
    else:
        # Platform admin: just check mapping exists
        mapping = tool_account_repo.get_by_id(id)
        if not mapping:
            return jsonify({"error": "Mapping not found"}), 404
        owner_user = user_repo.get_user_by_id(mapping.user_id)

    # Save old value for audit
    old_value = mapping.to_dict()

    success = tool_account_repo.delete(id)

    if success:
        # Audit logging
        _log_tool_account_action(
            action=AuditAction.TOOL_ACCOUNT_MAPPING_DELETE,
            target_user_id=old_value.get("user_id"),
            tool_account=old_value.get("tool_account"),
            mapping_id=id,
            old_value=old_value,
            tenant_id=owner_user.get("tenant_id") if owner_user else None,
        )

        return jsonify({"status": "success"})

    return jsonify({"error": "Failed to delete mapping"}), 500


@tool_accounts_bp.route("/tool-accounts/user/<int:user_id>/batch", methods=["POST"])
def batch_create_user_tool_accounts(user_id: int):
    """
    Batch create tool account mappings for a user.

    Issue #2759: Authorization + transaction for batch operations.
    - Validate target user exists
    - tenant_admin: validate target user belongs to their tenant
    - Process each item with same validation as single create
    - Return detailed success/failure results
    """
    data = request.get_json()

    if not data or not data.get("tool_accounts"):
        return jsonify({"error": "tool_accounts list is required"}), 400

    user_role = g.user.get("role")
    user_tenant_id = g.user.get("tenant_id")

    # Validate target user for tenant_admin
    if user_role == "tenant_admin":
        if user_tenant_id is None:
            return jsonify({"error": "Tenant admin must have tenant_id"}), 403

        target_user, error = validate_target_user_for_write(user_id, user_tenant_id)
        if error:
            return jsonify({"error": error}), 403
    else:
        # Platform admin: just check user exists
        target_user = user_repo.get_user_by_id(user_id)
        if not target_user:
            return jsonify({"error": "User not found"}), 404

    tool_accounts = data.get("tool_accounts", [])
    created = []
    failed = []

    for account in tool_accounts:
        tool_account_name = account.get("tool_account")
        if not tool_account_name:
            failed.append({"tool_account": None, "error": "tool_account is required"})
            continue

        # Validate tool_account format
        tool_account_name = tool_account_name.strip()
        if not tool_account_name:
            failed.append({"tool_account": None, "error": "tool_account cannot be empty"})
            continue

        # Check if already mapped
        existing = tool_account_repo.get_by_tool_account(tool_account_name)
        if existing:
            failed.append(
                {
                    "tool_account": tool_account_name,
                    "error": "Already mapped to another user",
                    "existing_user_id": existing.user_id,
                }
            )
            continue

        # Determine mapping source and status
        is_predeclared = account.get("is_predeclared", False)
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

        # Create mapping
        mapping = tool_account_repo.create(
            user_id=user_id,
            tool_account=tool_account_name,
            tool_type=account.get("tool_type"),
            description=account.get("description"),
            mapping_source=mapping_source,
            mapping_status=mapping_status,
            created_by=created_by,
            tenant_id=target_user.tenant_id if target_user else None,
        )

        if mapping:
            # Update daily_messages (only for active mappings)
            if mapping_status == "active":
                tool_account_repo.update_daily_messages_user_id(tool_account_name, user_id)
            created.append(mapping.to_dict())
        else:
            failed.append({"tool_account": tool_account_name, "error": "Failed to create"})

    # Audit logging (batch)
    if created:
        _log_tool_account_action(
            action=AuditAction.TOOL_ACCOUNT_MAPPING_BATCH,
            target_user_id=user_id,
            tool_account=None,
            mapping_id=None,
            new_value={
                "created_count": len(created),
                "failed_count": len(failed),
                "created": created,
            },
            tenant_id=target_user.get("tenant_id") if target_user else None,
        )

    return jsonify(
        {
            "created_count": len(created),
            "failed_count": len(failed),
            "created": created,
            "failed": failed,
        }
    )


# =============================================================================
# Issue #2761: New endpoints for conflict and status management
# =============================================================================


@tool_accounts_bp.route("/tool-accounts/conflicts", methods=["GET"])
def get_conflicts():
    """Get all unresolved conflict mappings.

    Issue #2761: Returns mappings with conflict status for admin review.
    """
    user_role = g.user.get("role")
    user_tenant_id = g.user.get("tenant_id")

    if user_role == "tenant_admin":
        if user_tenant_id is None:
            return jsonify({"error": "Tenant admin must have tenant_id"}), 403

    # Get current user's tenant
    tenant_id = None
    if user_role == "tenant_admin":
        tenant_id = user_tenant_id

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
    user_role = g.user.get("role")
    user_tenant_id = g.user.get("tenant_id")

    if user_role == "tenant_admin":
        if user_tenant_id is None:
            return jsonify({"error": "Tenant admin must have tenant_id"}), 403

    tenant_id = None
    if user_role == "tenant_admin":
        tenant_id = user_tenant_id

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
    user_role = g.user.get("role")
    user_tenant_id = g.user.get("tenant_id")

    if user_role == "tenant_admin":
        if user_tenant_id is None:
            return jsonify({"error": "Tenant admin must have tenant_id"}), 403

    tenant_id = None
    if user_role == "tenant_admin":
        tenant_id = user_tenant_id

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
    Issue #2759: Tenant isolation for conflict resolution.

    Request body:
    {
        "action": "confirm" | "reject",
        "new_status": "active" | "pending"
    }
    """
    user_role = g.user.get("role")
    user_tenant_id = g.user.get("tenant_id")

    # Get mapping
    mapping = tool_account_repo.get_by_id(id)
    if not mapping:
        return jsonify({"error": "Mapping not found"}), 404

    # Check if mapping is in conflict state
    if not mapping.mapping_status or not mapping.mapping_status.startswith("conflict"):
        return jsonify({"error": "Mapping is not in conflict state"}), 400

    # Tenant isolation for tenant_admin
    if user_role == "tenant_admin":
        if user_tenant_id is None:
            return jsonify({"error": "Tenant admin must have tenant_id"}), 403

        # Get the owner user to check tenant
        owner_user = user_repo.get_user_by_id(mapping.user_id)
        if not owner_user or owner_user.tenant_id != user_tenant_id:
            return jsonify({"error": "Mapping not found"}), 404

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
            return jsonify(
                {"status": "rejected", "message": "Conflict mapping rejected and deleted"}
            )
        return jsonify({"error": "Failed to delete mapping"}), 500

    # Update status with optimistic lock
    updated = tool_account_repo.update_status_with_version(id, new_status, mapping.version)

    if updated:
        return jsonify(
            {
                "status": "resolved",
                "mapping": updated.to_dict(),
                "new_status": new_status,
            }
        )

    return jsonify({"error": "Failed to resolve conflict - version mismatch"}), 409


@tool_accounts_bp.route("/tool-accounts/<int:id>/touch-activity", methods=["POST"])
def touch_mapping_activity(id: int):
    """Update last_activity_at timestamp.

    Issue #2761: Called when account is seen in incoming data.
    """
    user_role = g.user.get("role")
    user_tenant_id = g.user.get("tenant_id")

    mapping = tool_account_repo.get_by_id(id)
    if not mapping:
        return jsonify({"error": "Mapping not found"}), 404

    # Tenant isolation for tenant_admin
    if user_role == "tenant_admin":
        if user_tenant_id is None:
            return jsonify({"error": "Tenant admin must have tenant_id"}), 403

        # Get the owner user to check tenant
        owner_user = user_repo.get_user_by_id(mapping.user_id)
        if not owner_user or owner_user.tenant_id != user_tenant_id:
            return jsonify({"error": "Mapping not found"}), 404

    success = tool_account_repo.touch_activity(id)

    if success:
        return jsonify({"status": "success"})

    return jsonify({"error": "Failed to update activity"}), 500


@tool_accounts_bp.route("/tool-accounts/status-summary", methods=["GET"])
def get_status_summary():
    """Get summary counts by mapping status.

    Issue #2761: Dashboard endpoint for monitoring mapping health.
    Issue #2759: Tenant isolation for tenant_admin.
    """
    user_role = g.user.get("role")
    user_tenant_id = g.user.get("tenant_id")

    if user_role == "tenant_admin":
        if user_tenant_id is None:
            return jsonify({"error": "Tenant admin must have tenant_id"}), 403

    tenant_id = None
    if user_role == "tenant_admin":
        tenant_id = user_tenant_id

    summary = {
        "pending": len(tool_account_repo.get_by_status("pending", tenant_id)),
        "active": len(tool_account_repo.get_by_status("active", tenant_id)),
        "stale": len(tool_account_repo.get_by_status("stale", tenant_id)),
        "conflict_type": len(tool_account_repo.get_by_status("conflict_type", tenant_id)),
        "conflict_owner": len(tool_account_repo.get_by_status("conflict_owner", tenant_id)),
        "conflict_tenant": len(tool_account_repo.get_by_status("conflict_tenant", tenant_id)),
    }

    return jsonify(summary)
