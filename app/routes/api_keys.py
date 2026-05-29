"""Open ACE - API Key Management Routes

API endpoints for managing encrypted API keys stored in the database.
Used by both local and remote workspaces (governed by the scope field).
"""

import logging

from flask import Blueprint, g, jsonify, request

from app.auth.decorators import admin_required
from app.modules.workspace.api_key_proxy import (
    get_api_key_proxy_service,
    validate_cli_settings_payload,
)

logger = logging.getLogger(__name__)

api_keys_bp = Blueprint("api_keys", __name__)


@api_keys_bp.route("/api-keys", methods=["GET"])
@admin_required
def list_api_keys():
    """List all encrypted API keys (without revealing actual keys). Admin only."""

    data = request.args
    tenant_id = int(data.get("tenant_id", 1))

    api_proxy = get_api_key_proxy_service()
    keys = api_proxy.list_api_keys(tenant_id)

    return jsonify(
        {
            "success": True,
            "keys": keys,
        }
    )


@api_keys_bp.route("/api-keys", methods=["POST"])
@admin_required
def store_api_key():
    """Store a new encrypted API key. Admin only."""

    data = request.get_json() or {}
    provider = data.get("provider")
    key_name = data.get("key_name")
    api_key = data.get("api_key")
    base_url = data.get("base_url")
    tenant_id = int(data.get("tenant_id", 1))
    cli_tools = data.get("cli_tools")
    cli_settings = data.get("cli_settings")
    scope = data.get("scope", "shared")
    priority = data.get("priority", 0)
    weight = data.get("weight", 100)

    if not provider or not key_name or not api_key:
        return jsonify({"error": "provider, key_name, and api_key are required"}), 400

    if scope not in ("local", "remote", "shared"):
        return jsonify({"error": "scope must be 'local', 'remote', or 'shared'"}), 400

    validation_error = validate_cli_settings_payload(cli_settings)
    if validation_error:
        return jsonify({"error": validation_error}), 400

    api_proxy = get_api_key_proxy_service()
    result = api_proxy.store_api_key(
        tenant_id=tenant_id,
        provider=provider,
        key_name=key_name,
        api_key=api_key,
        base_url=base_url,
        created_by=g.user["id"],
        cli_tools=cli_tools,
        cli_settings=cli_settings,
        scope=scope,
        priority=int(priority),
        weight=int(weight),
    )

    if result.get("success"):
        return jsonify({"success": True, "key": result})
    return jsonify({"error": result.get("error", "Failed to store API key")}), 400


@api_keys_bp.route("/api-keys/<int:key_id>", methods=["PUT"])
@admin_required
def update_api_key(key_id):
    """Update an API key by ID. Admin only."""

    data = request.get_json() or {}
    key_name = data.get("key_name")
    base_url = data.get("base_url")
    cli_tools = data.get("cli_tools")
    cli_settings = data.get("cli_settings")
    is_active = data.get("is_active")
    if is_active is not None and not isinstance(is_active, bool):
        return jsonify({"error": "is_active must be a boolean"}), 400
    scope = data.get("scope")
    if scope is not None and scope not in ("local", "remote", "shared"):
        return jsonify({"error": "scope must be 'local', 'remote', or 'shared'"}), 400
    priority = data.get("priority")
    weight = data.get("weight")
    tenant_id = int(data.get("tenant_id", 1))

    validation_error = validate_cli_settings_payload(cli_settings)
    if validation_error:
        return jsonify({"error": validation_error}), 400

    api_proxy = get_api_key_proxy_service()
    success = api_proxy.update_api_key_by_id(
        key_id=key_id,
        tenant_id=tenant_id,
        key_name=key_name,
        base_url=base_url,
        cli_tools=cli_tools,
        cli_settings=cli_settings,
        is_active=is_active,
        scope=scope,
        priority=int(priority) if priority is not None else None,
        weight=int(weight) if weight is not None else None,
    )

    if success:
        return jsonify({"success": True, "message": "API key updated"})
    return jsonify({"error": "API key not found or update failed"}), 404


@api_keys_bp.route("/api-keys/<int:key_id>", methods=["DELETE"])
@admin_required
def delete_api_key(key_id):
    """Delete an API key by ID. Admin only."""

    data = request.get_json() or {}
    tenant_id = int(data.get("tenant_id", 1))

    api_proxy = get_api_key_proxy_service()
    success = api_proxy.delete_api_key_by_id(key_id, tenant_id)

    if success:
        return jsonify({"success": True, "message": "API key deleted"})
    return jsonify({"error": "API key not found"}), 404
