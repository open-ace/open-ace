"""Open ACE - API Key Management Routes

API endpoints for managing encrypted API keys stored in the database.
Used by both local and remote workspaces (governed by the scope field).

Issue #2327: 建立统一、fail-closed 的 API Key actor/target tenant 授权模型。
"""

import logging

from flask import Blueprint, g, jsonify, request

from app.auth.decorators import api_key_admin_required
from app.modules.workspace.api_key_proxy import (
    get_api_key_proxy_service,
    validate_cli_settings_payload,
)

logger = logging.getLogger(__name__)

api_keys_bp = Blueprint("api_keys", __name__)


@api_keys_bp.route("/api-keys", methods=["GET"])
@api_key_admin_required
def list_api_keys():
    """
    List all encrypted API keys (without revealing actual keys). Admin only.

    Issue #2327: 使用集中式 tenant scope 授权原语。
    - tenant_admin: 只能查询自己租户的 API Key
    - platform_admin: 必须显式指定 target tenant
    """

    # 从 Flask g 获取已验证的 ActorScope
    scope = g.actor_scope

    api_proxy = get_api_key_proxy_service()
    keys = api_proxy.list_api_keys(scope.target_tenant_id)

    return jsonify(
        {
            "success": True,
            "keys": keys,
        }
    )


@api_keys_bp.route("/api-keys", methods=["POST"])
@api_key_admin_required
def store_api_key():
    """
    Store a new encrypted API key. Admin only.

    Issue #2327: 使用集中式 tenant scope 授权原语。
    - tenant_admin: 只能在自己租户创建 API Key
    - platform_admin: 必须显式指定 target tenant
    """

    data = request.get_json() or {}
    provider = data.get("provider")
    key_name = data.get("key_name")
    api_key = data.get("api_key")
    base_url = data.get("base_url")
    cli_tools = data.get("cli_tools")
    cli_settings = data.get("cli_settings")
    scope_field = data.get("scope", "shared")
    priority = data.get("priority", 0)
    weight = data.get("weight", 100)

    if not provider or not key_name or not api_key:
        return jsonify({"error": "provider, key_name, and api_key are required"}), 400

    if scope_field not in ("local", "remote", "shared"):
        return jsonify({"error": "scope must be 'local', 'remote', or 'shared'"}), 400

    validation_error = validate_cli_settings_payload(cli_settings)
    if validation_error:
        return jsonify({"error": validation_error}), 400

    # 从 Flask g 获取已验证的 ActorScope
    scope = g.actor_scope

    api_proxy = get_api_key_proxy_service()
    result = api_proxy.store_api_key(
        tenant_id=scope.target_tenant_id,
        provider=provider,
        key_name=key_name,
        api_key=api_key,
        base_url=base_url,
        created_by=scope.user_id,
        cli_tools=cli_tools,
        cli_settings=cli_settings,
        scope=scope_field,
        priority=int(priority),
        weight=int(weight),
    )

    if result.get("success"):
        return jsonify({"success": True, "key": result})
    return jsonify({"error": result.get("error", "Failed to store API key")}), 400


@api_keys_bp.route("/api-keys/<int:key_id>", methods=["PUT"])
@api_key_admin_required
def update_api_key(key_id):
    """
    Update an API key by ID. Admin only.

    Issue #2327: 使用集中式 tenant scope 授权原语 + API Key 所有权验证。
    - tenant_admin: 只能更新自己租户的 API Key
    - platform_admin: 必须显式指定 target tenant
    - Repository 层强制验证 key_id + tenant_id 匹配
    """

    data = request.get_json() or {}
    key_name = data.get("key_name")
    base_url = data.get("base_url")
    cli_tools = data.get("cli_tools")
    cli_settings = data.get("cli_settings")
    is_active = data.get("is_active")
    if is_active is not None and not isinstance(is_active, bool):
        return jsonify({"error": "is_active must be a boolean"}), 400
    scope_field = data.get("scope")
    if scope_field is not None and scope_field not in ("local", "remote", "shared"):
        return jsonify({"error": "scope must be 'local', 'remote', or 'shared'"}), 400
    priority = data.get("priority")
    weight = data.get("weight")

    validation_error = validate_cli_settings_payload(cli_settings)
    if validation_error:
        return jsonify({"error": validation_error}), 400

    # 从 Flask g 获取已验证的 ActorScope
    scope = g.actor_scope

    api_proxy = get_api_key_proxy_service()

    # Issue #2327: Repository 层强制验证 key_id + tenant_id 匹配
    # 如果 API Key 不存在或不属于目标租户，返回 403 而非 404
    success = api_proxy.update_api_key_by_id(
        key_id=key_id,
        tenant_id=scope.target_tenant_id,
        key_name=key_name,
        base_url=base_url,
        cli_tools=cli_tools,
        cli_settings=cli_settings,
        is_active=is_active,
        scope=scope_field,
        priority=int(priority) if priority is not None else None,
        weight=int(weight) if weight is not None else None,
    )

    if success:
        return jsonify({"success": True, "message": "API key updated"})
    # Issue #2327: 返回 403 而非 404，避免信息泄露
    return jsonify({"error": "API key not found or access denied"}), 403


@api_keys_bp.route("/api-keys/<int:key_id>", methods=["DELETE"])
@api_key_admin_required
def delete_api_key(key_id):
    """
    Delete an API key by ID. Admin only.

    Issue #2327: 使用集中式 tenant scope 授权原语 + API Key 所有权验证。
    - tenant_admin: 只能删除自己租户的 API Key
    - platform_admin: 必须显式指定 target tenant
    - Repository 层强制验证 key_id + tenant_id 匹配
    """

    # 从 Flask g 获取已验证的 ActorScope
    scope = g.actor_scope

    api_proxy = get_api_key_proxy_service()

    # Issue #2327: Repository 层强制验证 key_id + tenant_id 匹配
    # 如果 API Key 不存在或不属于目标租户，返回 403 而非 404
    success = api_proxy.delete_api_key_by_id(key_id, scope.target_tenant_id)

    if success:
        return jsonify({"success": True, "message": "API key deleted"})
    # Issue #2327: 返回 403 而非 404，避免信息泄露
    return jsonify({"error": "API key not found or access denied"}), 403