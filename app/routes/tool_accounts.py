#!/usr/bin/env python3
"""
Open ACE - User Tool Accounts API Routes

API routes for managing user tool account mappings.
"""

import logging
from flask import Blueprint, jsonify, request

from app.repositories.user_tool_account_repo import UserToolAccountRepository
from app.repositories.user_repo import UserRepository
from app.models.user_tool_account import TOOL_TYPES, get_tool_type_display

logger = logging.getLogger(__name__)

tool_accounts_bp = Blueprint("tool_accounts", __name__)
tool_account_repo = UserToolAccountRepository()
user_repo = UserRepository()


@tool_accounts_bp.route("/tool-accounts", methods=["GET"])
def get_all_tool_accounts():
    """Get all tool account mappings."""
    mappings = tool_account_repo.get_all()
    
    # Group by user
    result = {}
    for mapping in mappings:
        user_id = mapping.user_id
        if user_id not in result:
            user = user_repo.get_user_by_id(user_id)
            result[user_id] = {
                "user": user,
                "tool_accounts": []
            }
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
    
    # Group by type pattern
    result = []
    for item in unmapped:
        sender_name = item.get("sender_name")
        
        # Try to identify tool type from sender_name
        tool_type = None
        if sender_name:
            if "-qwen" in sender_name:
                tool_type = "qwen"
            elif "-claude" in sender_name:
                tool_type = "claude"
            elif "-openclaw" in sender_name:
                tool_type = "openclaw"
            elif sender_name.startswith("ou_"):
                tool_type = "feishu"
        
        result.append({
            "sender_name": sender_name,
            "tool_type": tool_type,
            "tool_type_display": get_tool_type_display(tool_type),
            "message_count": item.get("message_count"),
            "first_date": item.get("first_date"),
            "last_date": item.get("last_date"),
        })
    
    return jsonify(result)


@tool_accounts_bp.route("/tool-accounts", methods=["POST"])
def create_tool_account():
    """Create a new tool account mapping."""
    data = request.get_json()
    
    if not data:
        return jsonify({"error": "No data provided"}), 400
    
    user_id = data.get("user_id")
    tool_account = data.get("tool_account")
    
    if not user_id or not tool_account:
        return jsonify({"error": "user_id and tool_account are required"}), 400
    
    # Check if user exists
    user = user_repo.get_user_by_id(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    
    # Check if tool_account already mapped
    existing = tool_account_repo.get_by_tool_account(tool_account)
    if existing:
        return jsonify({
            "error": "Tool account already mapped to another user",
            "existing_user_id": existing.user_id
        }), 400
    
    mapping = tool_account_repo.create(
        user_id=user_id,
        tool_account=tool_account,
        tool_type=data.get("tool_type"),
        description=data.get("description")
    )
    
    if mapping:
        # Update daily_messages user_id
        updated_count = tool_account_repo.update_daily_messages_user_id(
            tool_account, user_id
        )
        return jsonify({
            "mapping": mapping.to_dict(),
            "updated_messages": updated_count
        })
    
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
        description=data.get("description")
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
        user_id=user_id,
        tool_accounts=data.get("tool_accounts")
    )
    
    return jsonify({
        "created_count": len(mappings),
        "mappings": [m.to_dict() for m in mappings]
    })


@tool_accounts_bp.route("/tool-types", methods=["GET"])
def get_tool_types():
    """Get available tool types."""
    return jsonify([
        {"value": k, "display": v}
        for k, v in TOOL_TYPES.items()
    ])