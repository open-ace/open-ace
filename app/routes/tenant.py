"""
Open ACE - Tenant Routes

API endpoints for multi-tenant management.

Issue #2179: 租户管理员权限模型
- 路由层传入 ActorContext 到 Service 层

Issue #2790: 租户设置修改审计日志
- update_tenant_settings 记录 SYSTEM_CONFIG_CHANGE 审计
"""

import logging
from typing import Any, cast

import bcrypt
from flask import Blueprint, g, jsonify, request

from app.auth.decorators import (
    auth_required,
    platform_admin_required,
    same_tenant_or_platform_admin,
)
from app.core.actor_context import ActorContext
from app.modules.governance.audit_logger import AuditAction, AuditLogger
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
audit_logger = AuditLogger()


def _hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return cast("str", bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode())


def _get_client_info() -> dict[str, str | None]:
    """Issue #2790: 获取客户端信息用于审计。"""
    return {
        "ip_address": request.remote_addr,
        "user_agent": request.headers.get("User-Agent"),
    }


def _log_tenant_settings_audit(
    actor: ActorContext,
    tenant_id: int,
    changed_fields: list[str],
    old_values: dict,
    new_values: dict,
    success: bool = True,
    error: str | None = None,
    error_type: str | None = None,
) -> None:
    """Issue #2790: 租户设置修改审计日志封装。

    Args:
        actor: 操作者上下文
        tenant_id: 目标租户 ID
        changed_fields: 变更的字段列表
        old_values: 变更前的值（脱敏后）
        new_values: 变更后的值（脱敏后）
        success: 是否成功
        error: 错误消息
        error_type: 错误类型
    """
    client_info = _get_client_info()
    username = None
    if hasattr(g, "user") and isinstance(g.user, dict):
        username = g.user.get("username")

    # 审计详情
    details: dict[str, Any] = {
        "action": "update",
        "actor_scope": actor.role,
    }
    if changed_fields:
        details["changed_fields"] = changed_fields
    if old_values:
        details["old_values"] = old_values
    if new_values:
        details["new_values"] = new_values
    if error:
        details["error"] = error
    if error_type:
        details["error_type"] = error_type

    try:
        audit_logger.log_action(
            action=AuditAction.SYSTEM_CONFIG_CHANGE,
            user_id=actor.user_id,
            username=username,
            resource_type="tenant_settings",
            resource_id=str(tenant_id),
            tenant_id=tenant_id,  # Issue #2790: 使用目标租户 ID，便于按租户过滤
            details=details,
            success=success,
            error_message=error if not success else None,
            **client_info,
        )
    except Exception as e:
        # Issue #2790: 审计写入失败不阻塞业务，记录错误日志
        logger.error(
            f"审计日志写入失败 (tenant_settings): tenant_id={tenant_id}, error={e}",
            exc_info=True,
        )


@tenant_bp.route("", methods=["GET"])
@platform_admin_required
def list_tenants():
    """List all tenants (platform admin only).

    Issue #2179: Only platform admins can list all tenants.
    """

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
@platform_admin_required
def get_tenant(tenant_id: int):
    """Get tenant by ID (platform admin only).

    Issue #2179: Only platform admins can view any tenant.
    """

    tenant = tenant_service.get_tenant(tenant_id)

    if not tenant:
        return jsonify({"error": "Tenant not found"}), 404

    return jsonify(tenant.to_dict())


@tenant_bp.route("/slug/<slug>", methods=["GET"])
@platform_admin_required
def get_tenant_by_slug(slug: str):
    """Get tenant by slug (platform admin only).

    Issue #2179: Only platform admins can view any tenant by slug.
    """

    tenant = tenant_service.get_tenant_by_slug(slug)

    if not tenant:
        return jsonify({"error": "Tenant not found"}), 404

    return jsonify(tenant.to_dict())


@tenant_bp.route("", methods=["POST"])
@platform_admin_required
def create_tenant():
    """Create a new tenant (platform admin only). Optionally create an admin user.

    Issue #2179: Only platform admins can create new tenants.
    """

    # Issue #2179: 创建 ActorContext 传入 Service 层
    try:
        actor = ActorContext.from_flask_g()
    except ValueError as e:
        return jsonify({"error": f"Authentication context error: {e}"}), 401

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
        actor=actor,  # Issue #2179: 传入 actor
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
        # Issue #2755: Explicitly exclude soft-deleted users
        if user_repo.get_user_by_username(admin_username, include_deleted=False):
            return jsonify({"error": "Admin username already exists"}), 400

        # Check if email already exists (if provided)
        # Issue #2755: Explicitly exclude soft-deleted users
        if admin_email and user_repo.get_user_by_email(admin_email, include_deleted=False):
            return jsonify({"error": "Admin email already exists"}), 400

        # Create admin user
        password_hash = _hash_password(admin_password)
        admin_email_final = admin_email or f"{admin_username}@{slug or 'tenant'}.local"
        # Issue #2179: Use tenant_admin role instead of legacy admin
        admin_user_id = user_repo.create_user(
            username=admin_username,
            email=admin_email_final,
            password_hash=password_hash,
            role="tenant_admin",
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
                "role": "tenant_admin",
            }
            logger.info(f"Created admin user {admin_username} for tenant {tenant.name}")
        else:
            logger.warning(f"Failed to create admin user for tenant {tenant.name}")

    response = tenant.to_dict()
    if admin_info:
        response["admin_user"] = admin_info

    return jsonify(response), 201


@tenant_bp.route("/<int:tenant_id>", methods=["PUT"])
@platform_admin_required
def update_tenant(tenant_id: int):
    """Update tenant (platform admin only).

    Issue #2179: Only platform admins can update any tenant.
    """

    # Issue #2179: 创建 ActorContext 传入 Service 层
    try:
        actor = ActorContext.from_flask_g()
    except ValueError as e:
        return jsonify({"error": f"Authentication context error: {e}"}), 401

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

    try:
        success = tenant_service.update_tenant(
            tenant_id, updates, actor=actor
        )  # Issue #2179: 传入 actor
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403

    if not success:
        return jsonify({"error": "Failed to update tenant"}), 500

    tenant = tenant_service.get_tenant(tenant_id)
    if tenant is None:
        return jsonify({"error": "Tenant not found"}), 404
    return jsonify(tenant.to_dict())


@tenant_bp.route("/<int:tenant_id>/quota", methods=["PUT"])
@platform_admin_required
def update_tenant_quota(tenant_id: int):
    """Update tenant quota (platform admin only).

    Issue #2179: Only platform admins can modify tenant quota.
    """

    # Issue #2179: 创建 ActorContext 传入 Service 层
    try:
        actor = ActorContext.from_flask_g()
    except ValueError as e:
        return jsonify({"error": f"Authentication context error: {e}"}), 401

    data = request.get_json()

    if not data:
        return jsonify({"error": "Request body required"}), 400

    try:
        success = tenant_service.update_quota(
            tenant_id, data, actor=actor
        )  # Issue #2179: 传入 actor
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403

    if not success:
        return jsonify({"error": "Failed to update tenant quota"}), 500

    tenant = tenant_service.get_tenant(tenant_id)
    if tenant is None:
        return jsonify({"error": "Tenant not found"}), 404
    return jsonify(tenant.to_dict())


@tenant_bp.route("/<int:tenant_id>/settings", methods=["PUT"])
@same_tenant_or_platform_admin
def update_tenant_settings(tenant_id: int):
    """Update tenant settings (same tenant or platform admin).

    Issue #2179: Tenant admins can modify their own tenant's settings.
    Issue #2790: 记录审计日志。
    """

    # Issue #2179: 创建 ActorContext 传入 Service 层
    try:
        actor = ActorContext.from_flask_g()
    except ValueError as e:
        return jsonify({"error": f"Authentication context error: {e}"}), 401

    data = request.get_json()

    if not data:
        return jsonify({"error": "Request body required"}), 400

    # Issue #2790: Service 层返回 UpdateSettingsResult
    result = tenant_service.update_settings(tenant_id, data, actor=actor)

    # Issue #2790: 记录审计日志
    _log_tenant_settings_audit(
        actor=actor,
        tenant_id=tenant_id,
        changed_fields=result.changed_fields,
        old_values=result.old_values,
        new_values=result.new_values,
        success=result.success,
        error=result.error,
        error_type=result.error_type,
    )

    # 处理失败情况
    if not result.success:
        if result.error_type == "permission":
            return jsonify({"error": result.error}), 403
        elif result.error_type == "not_found":
            return jsonify({"error": result.error}), 404
        elif result.error_type == "validation":
            return jsonify({"error": result.error}), 400
        else:
            return jsonify({"error": result.error or "Failed to update tenant settings"}), 500

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
@platform_admin_required
def suspend_tenant(tenant_id: int):
    """Suspend a tenant (platform admin only).

    Issue #2179: Only platform admins can suspend tenants.
    """

    # Issue #2179: 创建 ActorContext 传入 Service 层
    try:
        actor = ActorContext.from_flask_g()
    except ValueError as e:
        return jsonify({"error": f"Authentication context error: {e}"}), 401

    data = request.get_json() or {}
    reason = data.get("reason")

    try:
        success = tenant_service.suspend_tenant(
            tenant_id, reason, actor=actor
        )  # Issue #2179: 传入 actor
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403

    if not success:
        return jsonify({"error": "Failed to suspend tenant"}), 500

    tenant = tenant_service.get_tenant(tenant_id)
    if tenant is None:
        return jsonify({"error": "Tenant not found"}), 404
    return jsonify(tenant.to_dict())


@tenant_bp.route("/<int:tenant_id>/activate", methods=["POST"])
@platform_admin_required
def activate_tenant(tenant_id: int):
    """Activate a suspended tenant (platform admin only).

    Issue #2179: Only platform admins can activate tenants.
    """

    # Issue #2179: 创建 ActorContext 传入 Service 层
    try:
        actor = ActorContext.from_flask_g()
    except ValueError as e:
        return jsonify({"error": f"Authentication context error: {e}"}), 401

    try:
        success = tenant_service.activate_tenant(tenant_id, actor=actor)  # Issue #2179: 传入 actor
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403

    if not success:
        return jsonify({"error": "Failed to activate tenant"}), 500

    tenant = tenant_service.get_tenant(tenant_id)
    if tenant is None:
        return jsonify({"error": "Tenant not found"}), 404
    return jsonify(tenant.to_dict())


@tenant_bp.route("/<int:tenant_id>", methods=["DELETE"])
@platform_admin_required
def delete_tenant(tenant_id: int):
    """Delete a tenant (platform admin only).

    Issue #2179: Only platform admins can delete tenants.
    """

    # Issue #2179: 创建 ActorContext 传入 Service 层
    try:
        actor = ActorContext.from_flask_g()
    except ValueError as e:
        return jsonify({"error": f"Authentication context error: {e}"}), 401

    hard = request.args.get("hard", "false").lower() == "true"

    try:
        success = tenant_service.delete_tenant(
            tenant_id, hard=hard, actor=actor
        )  # Issue #2179: 传入 actor
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403

    if not success:
        return jsonify({"error": "Failed to delete tenant"}), 500

    return jsonify({"message": "Tenant deleted"})


@tenant_bp.route("/<int:tenant_id>/usage", methods=["GET"])
@same_tenant_or_platform_admin
def get_tenant_usage(tenant_id: int):
    """Get tenant usage history (same tenant or platform admin).

    Issue #2179: Tenant admins can view their own tenant's usage.
    """

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
@same_tenant_or_platform_admin
def get_tenant_stats(tenant_id: int):
    """Get tenant statistics (same tenant or platform admin).

    Issue #2179: Tenant admins can view their own tenant's statistics.
    """

    stats = tenant_service.get_tenant_stats(tenant_id)

    if not stats:
        return jsonify({"error": "Tenant not found"}), 404

    return jsonify(stats)


@tenant_bp.route("/<int:tenant_id>/check-quota", methods=["POST"])
@same_tenant_or_platform_admin
def check_tenant_quota(tenant_id: int):
    """Check if tenant has quota available (same tenant or platform admin).

    Issue #2179: Tenant admins can check their own tenant's quota.
    """

    data = request.get_json() or {}

    result = tenant_service.check_quota(
        tenant_id,
        tokens=data.get("tokens", 0),
        requests=data.get("requests", 1),
    )

    return jsonify(result)


@tenant_bp.route("/<int:tenant_id>/reset-period", methods=["POST"])
@platform_admin_required
def reset_billing_period(tenant_id: int):
    """Reset billing period for a tenant (platform admin only).

    Issue #3200: 手动重置租户计费周期
    - 仅 platform_admin 可操作
    - 调用 TenantService.reset_billing_period()
    """

    # Issue #2179: 创建 ActorContext 传入 Service 层
    try:
        actor = ActorContext.from_flask_g()
    except ValueError as e:
        return jsonify({"error": f"Authentication context error: {e}"}), 401

    try:
        result = tenant_service.reset_billing_period(tenant_id, actor=actor)
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403

    if not result.success:
        if result.error == "Tenant not found":
            return jsonify({"success": False, "error": result.error}), 404
        return jsonify({"success": False, "error": result.error}), 500

    return jsonify(
        {
            "success": True,
            "message": "Billing period reset successfully",
            "tenant": result.tenant.to_dict() if result.tenant else None,
        }
    )


@tenant_bp.route("/plans", methods=["GET"])
@auth_required
def get_plan_quotas():
    """Get quota configurations for all plans (authenticated users).

    Issue #2179: All authenticated users can view plan configurations.
    """
    quotas = tenant_service.get_plan_quotas()
    return jsonify(quotas)


def register_tenant_routes(app):
    """Register tenant routes with the Flask app."""
    app.register_blueprint(tenant_bp)
