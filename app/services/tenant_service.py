"""
Open ACE - AI Computing Explorer - Tenant Service

Business logic for multi-tenant management.

Issue #2179: 租户管理员权限模型
- Service 层必须验证 actor 权限
- 涉及租户修改、删除、暂停、配额和设置的写操作必须接收或验证 actor scope

Issue #2790: 租户设置修改审计日志
- update_settings 返回 UpdateSettingsResult 包含 diff 信息
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.actor_context import ActorContext
from app.models.tenant import QuotaConfig, Tenant, TenantSettings, TenantUsage
from app.repositories.tenant_repo import TenantRepository
from app.repositories.user_repo import UserRepository

logger = logging.getLogger(__name__)


# Issue #2790: 租户设置修改审计日志 - 允许修改的字段白名单
ALLOWED_SETTINGS_FIELDS = {
    "allowed_tools",
    "content_filter_enabled",
    "audit_log_enabled",
    "audit_log_retention_days",
    "data_retention_days",
    "sso_enabled",
    "sso_provider",
    "auto_provision_users",
    "custom_branding",
    "branding_name",
    "branding_logo_url",
    "roi_assumptions",
    "block_sensitive_keyword",
    "sensitive_keyword_match_mode",
}


@dataclass
class UpdateSettingsResult:
    """Issue #2790: 租户设置修改结果，包含 diff 信息用于审计。

    Attributes:
        success: 是否成功
        tenant_id: 目标租户 ID
        changed_fields: 变更的字段列表
        old_values: 变更前的值（脱敏后）
        new_values: 变更后的值（脱敏后）
        error: 错误消息（失败时）
        error_type: 错误类型（'permission', 'validation', 'not_found', 'unknown'）
    """

    success: bool
    tenant_id: int
    changed_fields: list[str] = field(default_factory=list)
    old_values: dict[str, Any] = field(default_factory=dict)
    new_values: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    error_type: str | None = None

    def __bool__(self) -> bool:
        """支持 `if result:` 语法，兼容现有代码。"""
        return self.success


class TenantService:
    """Service for tenant-related business logic."""

    # Default quota limits by plan
    PLAN_QUOTAS = {
        "free": QuotaConfig(
            daily_token_limit=100_000,
            monthly_token_limit=1_000_000,
            daily_request_limit=100,
            monthly_request_limit=1_000,
            max_users=5,
            max_sessions_per_user=2,
        ),
        "standard": QuotaConfig(
            daily_token_limit=1_000_000,
            monthly_token_limit=30_000_000,
            daily_request_limit=1_000,
            monthly_request_limit=30_000,
            max_users=50,
            max_sessions_per_user=5,
        ),
        "premium": QuotaConfig(
            daily_token_limit=10_000_000,
            monthly_token_limit=300_000_000,
            daily_request_limit=10_000,
            monthly_request_limit=300_000,
            max_users=200,
            max_sessions_per_user=10,
        ),
        "enterprise": QuotaConfig(
            daily_token_limit=100_000_000,
            monthly_token_limit=3_000_000_000,
            daily_request_limit=100_000,
            monthly_request_limit=3_000_000,
            max_users=1000,
            max_sessions_per_user=20,
        ),
    }

    def __init__(
        self,
        tenant_repo: TenantRepository | None = None,
        user_repo: UserRepository | None = None,
    ):
        """
        Initialize tenant service.

        Args:
            tenant_repo: Optional TenantRepository instance.
            user_repo: Optional UserRepository instance.
        """
        self.tenant_repo = tenant_repo or TenantRepository()
        self.user_repo = user_repo or UserRepository()

    def create_tenant(
        self,
        name: str,
        slug: str | None = None,
        plan: str = "standard",
        contact_email: str = "",
        contact_name: str | None = None,
        trial_days: int | None = None,
        actor: ActorContext | None = None,
    ) -> Tenant | None:
        """
        Create a new tenant.

        Issue #2179: 需要 platform_admin 权限

        Args:
            name: Tenant name.
            slug: URL-friendly identifier (auto-generated if not provided).
            plan: Subscription plan.
            contact_email: Contact email.
            contact_name: Contact name.
            trial_days: Number of trial days (for trial tenants).
            actor: 操作者上下文（必须为 platform_admin）

        Returns:
            Optional[Tenant]: Created tenant or None on failure.

        Raises:
            PermissionError: 权限不足
        """
        # 权限验证：只有 platform_admin 可以创建租户
        if actor and not actor.is_platform_admin():
            raise PermissionError(f"用户 {actor.user_id} 无权创建租户，需要 platform_admin 权限")

        # Generate slug if not provided
        if not slug:
            slug = self._generate_slug(name)

        # Check if slug already exists
        existing = self.tenant_repo.get_by_slug(slug)
        if existing:
            logger.warning(f"Tenant slug already exists: {slug}")
            return None

        # Get quota for plan
        quota = self.PLAN_QUOTAS.get(plan, self.PLAN_QUOTAS["standard"])

        # Create tenant
        tenant = Tenant(
            name=name,
            slug=slug,
            status="trial" if trial_days else "active",
            plan=plan,
            contact_email=contact_email,
            contact_name=contact_name,
            quota=quota,
            settings=TenantSettings(),
        )

        # Set trial end date if applicable
        if trial_days:
            tenant.trial_ends_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
                days=trial_days
            )

        tenant_id = self.tenant_repo.create(tenant)
        if tenant_id:
            tenant.id = tenant_id
            logger.info(f"Created tenant: {name} (ID: {tenant_id}, Plan: {plan})")
            return tenant

        return None

    def get_tenant(self, tenant_id: int) -> Tenant | None:
        """
        Get tenant by ID.

        Args:
            tenant_id: Tenant ID.

        Returns:
            Optional[Tenant]: Tenant or None.
        """
        return self.tenant_repo.get_by_id(tenant_id)

    def get_tenant_by_slug(self, slug: str) -> Tenant | None:
        """
        Get tenant by slug.

        Args:
            slug: Tenant slug.

        Returns:
            Optional[Tenant]: Tenant or None.
        """
        return self.tenant_repo.get_by_slug(slug)

    def list_tenants(
        self,
        status: str | None = None,
        plan: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Tenant]:
        """
        List tenants with optional filters.

        Args:
            status: Filter by status.
            plan: Filter by plan.
            limit: Maximum results.
            offset: Offset for pagination.

        Returns:
            List[Tenant]: List of tenants.
        """
        return self.tenant_repo.get_all(status=status, plan=plan, limit=limit, offset=offset)

    def update_tenant(
        self, tenant_id: int, updates: dict[str, Any], actor: ActorContext | None = None
    ) -> bool:
        """
        Update tenant fields.

        Issue #2179: 需要 platform_admin 权限

        Args:
            tenant_id: Tenant ID.
            updates: Dictionary of fields to update.
            actor: 操作者上下文（必须有权访问该租户）

        Returns:
            bool: True if successful.

        Raises:
            PermissionError: 权限不足
        """
        # 权限验证
        if actor and not actor.can_access_tenant(tenant_id):
            raise PermissionError(f"用户 {actor.user_id} 无权修改租户 {tenant_id}")

        # Handle quota updates
        if "plan" in updates:
            new_plan = updates["plan"]
            if new_plan in self.PLAN_QUOTAS:
                updates["quota"] = self.PLAN_QUOTAS[new_plan].to_dict()

        return self.tenant_repo.update(tenant_id, updates)

    def update_quota(
        self, tenant_id: int, quota_updates: dict[str, int], actor: ActorContext | None = None
    ) -> bool:
        """
        Update tenant quota configuration.

        Issue #2179: 需要 platform_admin 权限

        Args:
            tenant_id: Tenant ID.
            quota_updates: Quota fields to update.
            actor: 操作者上下文（必须有权访问该租户）

        Returns:
            bool: True if successful.

        Raises:
            PermissionError: 权限不足
        """
        # 权限验证
        if actor and not actor.can_access_tenant(tenant_id):
            raise PermissionError(f"用户 {actor.user_id} 无权修改租户 {tenant_id} 的配额")

        tenant = self.get_tenant(tenant_id)
        if not tenant:
            return False

        current_quota = tenant.quota.to_dict()
        current_quota.update(quota_updates)

        return self.tenant_repo.update(tenant_id, {"quota": current_quota})

    def update_settings(
        self, tenant_id: int, settings_updates: dict[str, Any], actor: ActorContext | None = None
    ) -> UpdateSettingsResult:
        """
        Update tenant settings.

        Issue #2179: tenant_admin 可修改自己租户，platform_admin 可修改任意租户
        Issue #2790: 返回 UpdateSettingsResult 包含 diff 信息用于审计

        Args:
            tenant_id: Tenant ID.
            settings_updates: Settings fields to update.
            actor: 操作者上下文（必须有权访问该租户）

        Returns:
            UpdateSettingsResult: 包含成功状态、diff 信息和错误信息
        """
        # 权限验证
        if actor and not actor.can_access_tenant(tenant_id):
            return UpdateSettingsResult(
                success=False,
                tenant_id=tenant_id,
                error=f"用户 {actor.user_id} 无权修改租户 {tenant_id} 的设置",
                error_type="permission",
            )

        tenant = self.get_tenant(tenant_id)
        if not tenant:
            return UpdateSettingsResult(
                success=False,
                tenant_id=tenant_id,
                error="Tenant not found",
                error_type="not_found",
            )

        # Issue #2790: 字段白名单过滤
        filtered_updates = {
            k: v for k, v in settings_updates.items() if k in ALLOWED_SETTINGS_FIELDS
        }
        if not filtered_updates:
            return UpdateSettingsResult(
                success=False,
                tenant_id=tenant_id,
                error="No valid fields to update",
                error_type="validation",
            )

        # Issue #2128: 软废弃警告 - sso_enabled 和 sso_provider 字段已迁移到全局设置
        deprecated_fields = {"sso_enabled", "sso_provider"}
        used_deprecated = [k for k in filtered_updates if k in deprecated_fields]
        if used_deprecated:
            logger.warning(
                "Issue #2128: Tenant-level SSO settings are deprecated: tenant_id=%s, fields=%s. "
                "Use system-level sso_enabled (via /api/system/settings) and register SSO providers instead. "
                "These fields will be removed in a future version.",
                tenant_id,
                used_deprecated,
            )

        # Issue #2790: 验证 sensitive_keyword_match_mode 枚举值
        if "sensitive_keyword_match_mode" in filtered_updates:
            mode = filtered_updates["sensitive_keyword_match_mode"]
            if mode not in ("word_boundary", "substring"):
                return UpdateSettingsResult(
                    success=False,
                    tenant_id=tenant_id,
                    error=f"Invalid value for sensitive_keyword_match_mode: {mode}",
                    error_type="validation",
                )

        # Detect concurrent updates (5-second window)
        last_update = getattr(tenant, "_last_settings_update", None)
        if (
            last_update
            and (datetime.now(timezone.utc).replace(tzinfo=None) - last_update).seconds < 5
        ):
            logger.warning("Concurrent tenant settings update detected for tenant_id=%s", tenant_id)

        tenant._last_settings_update = datetime.now(timezone.utc).replace(tzinfo=None)

        # Issue #2790: 计算字段级 diff
        old_settings = tenant.settings.to_dict()
        changed_fields = []
        old_values = {}
        new_values = {}

        for key, new_value in filtered_updates.items():
            old_value = old_settings.get(key)
            if old_value != new_value:
                changed_fields.append(key)
                old_values[key] = self._sanitize_value_for_audit(key, old_value)
                new_values[key] = self._sanitize_value_for_audit(key, new_value)

        # 如果没有实际变更，返回成功但不记录审计
        if not changed_fields:
            return UpdateSettingsResult(
                success=True,
                tenant_id=tenant_id,
                changed_fields=[],
                old_values={},
                new_values={},
            )

        # 持久化更新
        current_settings = old_settings.copy()
        current_settings.update(filtered_updates)

        result = self.tenant_repo.update(tenant_id, {"settings": current_settings})

        # Clear ROI cache if roi_assumptions was updated
        if result and "roi_assumptions" in filtered_updates:
            self._clear_roi_cache_for_tenant(tenant_id)

        if result:
            return UpdateSettingsResult(
                success=True,
                tenant_id=tenant_id,
                changed_fields=changed_fields,
                old_values=old_values,
                new_values=new_values,
            )
        else:
            return UpdateSettingsResult(
                success=False,
                tenant_id=tenant_id,
                error="Failed to update tenant settings",
                error_type="unknown",
            )

    def _sanitize_value_for_audit(self, key: str, value: Any) -> Any:
        """Issue #2790: 审计记录值脱敏。

        Args:
            key: 字段名
            value: 原始值

        Returns:
            脱敏后的值
        """
        if value is None:
            return None

        # branding_logo_url: 截断至 200 字符
        if key == "branding_logo_url" and isinstance(value, str):
            if len(value) > 200:
                return value[:200]

        # roi_assumptions: 仅记录变更键名
        if key == "roi_assumptions" and isinstance(value, dict):
            return {"changed_keys": list(value.keys())}

        # allowed_tools: 记录长度
        if key == "allowed_tools" and isinstance(value, list):
            return {"total": len(value), "tools": value}

        return value

    def _clear_roi_cache_for_tenant(self, tenant_id: int) -> None:
        """Clear ROI cache entries for a specific tenant.

        Uses iterative approach since the cache system doesn't support
        pattern-based deletion.

        Args:
            tenant_id: Tenant ID whose cache should be cleared.
        """
        from datetime import timedelta

        from app.utils.cache import get_cache

        cache = get_cache()
        today = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")

        # Common date range combinations
        date_ranges = [
            (today, today),  # Today
            (
                (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)).strftime(
                    "%Y-%m-%d"
                ),
                today,
            ),  # 7 days
            (
                (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)).strftime(
                    "%Y-%m-%d"
                ),
                today,
            ),  # 30 days
            (
                (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=90)).strftime(
                    "%Y-%m-%d"
                ),
                today,
            ),  # 90 days
        ]

        # Main ROI endpoints
        endpoints = [
            "calculate_roi",
            "get_roi_trend",
            "get_roi_by_tool",
            "get_roi_by_user",
            "get_cost_breakdown",
            "get_daily_costs",
            "get_summary_stats",
        ]

        cleared_count = 0
        for endpoint in endpoints:
            for start_date, end_date in date_ranges:
                # Cache key format matches @cached decorator in roi_calculator.py
                key = f"roi:{endpoint}:{start_date}:{end_date}::tenant_id={tenant_id}"
                if cache.delete(key):
                    cleared_count += 1

        logger.info("Cleared %d ROI cache entries for tenant_id=%s", cleared_count, tenant_id)

    def suspend_tenant(
        self, tenant_id: int, reason: str | None = None, actor: ActorContext | None = None
    ) -> bool:
        """
        Suspend a tenant.

        Issue #2179: 需要 platform_admin 权限

        Args:
            tenant_id: Tenant ID.
            reason: Reason for suspension.
            actor: 操作者上下文（必须为 platform_admin）

        Returns:
            bool: True if successful.

        Raises:
            PermissionError: 权限不足
        """
        # 权限验证：只有 platform_admin 可以暂停租户
        if actor and not actor.is_platform_admin():
            raise PermissionError(f"用户 {actor.user_id} 无权暂停租户，需要 platform_admin 权限")

        logger.info(f"Suspending tenant {tenant_id}: {reason or 'No reason provided'}")
        return self.tenant_repo.update(tenant_id, {"status": "suspended"})

    def activate_tenant(self, tenant_id: int, actor: ActorContext | None = None) -> bool:
        """
        Activate a suspended tenant.

        Issue #2179: 需要 platform_admin 权限

        Args:
            tenant_id: Tenant ID.
            actor: 操作者上下文（必须为 platform_admin）

        Returns:
            bool: True if successful.

        Raises:
            PermissionError: 权限不足
        """
        # 权限验证：只有 platform_admin 可以激活租户
        if actor and not actor.is_platform_admin():
            raise PermissionError(f"用户 {actor.user_id} 无权激活租户，需要 platform_admin 权限")

        return self.tenant_repo.update(tenant_id, {"status": "active"})

    def delete_tenant(
        self, tenant_id: int, hard: bool = False, actor: ActorContext | None = None
    ) -> bool:
        """
        Delete a tenant.

        Issue #2179: 需要 platform_admin 权限

        Args:
            tenant_id: Tenant ID.
            hard: If True, permanently delete; otherwise soft delete.
            actor: 操作者上下文（必须为 platform_admin）

        Returns:
            bool: True if successful.

        Raises:
            PermissionError: 权限不足
        """
        # 权限验证：只有 platform_admin 可以删除租户
        if actor and not actor.is_platform_admin():
            raise PermissionError(f"用户 {actor.user_id} 无权删除租户，需要 platform_admin 权限")

        if hard:
            logger.warning(f"Hard deleting tenant {tenant_id}")
            return self.tenant_repo.hard_delete(tenant_id)
        else:
            return self.tenant_repo.delete(tenant_id)

    def record_usage(self, tenant_id: int, tokens: int = 0, requests: int = 1) -> bool:
        """
        Record usage for a tenant.

        Args:
            tenant_id: Tenant ID.
            tokens: Tokens used.
            requests: Requests made.

        Returns:
            bool: True if successful.
        """
        return self.tenant_repo.record_usage(tenant_id, tokens, requests)

    def get_usage_history(self, tenant_id: int, days: int = 30) -> list[TenantUsage]:
        """
        Get usage history for a tenant.

        Args:
            tenant_id: Tenant ID.
            days: Number of days to retrieve.

        Returns:
            List[TenantUsage]: Usage records.
        """
        start_date = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        ).strftime("%Y-%m-%d")
        return self.tenant_repo.get_usage(tenant_id, start_date=start_date)

    def check_quota(self, tenant_id: int, tokens: int = 0, requests: int = 1) -> dict[str, Any]:
        """
        Check if tenant has quota available.

        Args:
            tenant_id: Tenant ID.
            tokens: Tokens to check.
            requests: Requests to check.

        Returns:
            Dict with 'allowed', 'reason', and 'tenant' keys.
        """
        tenant = self.get_tenant(tenant_id)
        if not tenant:
            return {"allowed": False, "reason": "Tenant not found", "tenant": None}

        if not tenant.is_active():
            return {"allowed": False, "reason": "Tenant is not active", "tenant": tenant.to_dict()}

        # Get today's usage
        today = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
        usage_records = self.tenant_repo.get_usage(tenant_id, start_date=today, end_date=today)

        today_tokens = sum(u.tokens_used for u in usage_records)
        today_requests = sum(u.requests_made for u in usage_records)

        # Check limits
        if (today_tokens + tokens) >= tenant.quota.daily_token_limit:
            return {
                "allowed": False,
                "reason": f"Daily token quota exceeded. Used: {today_tokens}/{tenant.quota.daily_token_limit}",
                "tenant": tenant.to_dict(),
            }

        if (today_requests + requests) >= tenant.quota.daily_request_limit:
            return {
                "allowed": False,
                "reason": f"Daily request quota exceeded. Used: {today_requests}/{tenant.quota.daily_request_limit}",
                "tenant": tenant.to_dict(),
            }

        return {
            "allowed": True,
            "reason": None,
            "tenant": tenant.to_dict(),
        }

    def can_add_user(self, tenant_id: int) -> bool:
        """
        Check if tenant can add more users.

        Args:
            tenant_id: Tenant ID.

        Returns:
            bool: True if can add users.
        """
        tenant = self.get_tenant(tenant_id)
        if not tenant:
            return False

        return tenant.can_add_users()

    def increment_user_count(self, tenant_id: int) -> bool:
        """
        Increment user count for a tenant.

        Args:
            tenant_id: Tenant ID.

        Returns:
            bool: True if successful.
        """
        return self.tenant_repo.update_user_count(tenant_id, 1)

    def decrement_user_count(self, tenant_id: int) -> bool:
        """
        Decrement user count for a tenant.

        Args:
            tenant_id: Tenant ID.

        Returns:
            bool: True if successful.
        """
        return self.tenant_repo.update_user_count(tenant_id, -1)

    def get_tenant_stats(self, tenant_id: int) -> dict[str, Any]:
        """
        Get statistics for a tenant.

        Args:
            tenant_id: Tenant ID.

        Returns:
            Dict with tenant statistics.
        """
        tenant = self.get_tenant(tenant_id)
        if not tenant:
            return {}

        # Get usage for last 30 days
        usage = self.get_usage_history(tenant_id, days=30)

        total_tokens = sum(u.tokens_used for u in usage)
        total_requests = sum(u.requests_made for u in usage)

        return {
            "tenant": tenant.to_dict(),
            "usage_30_days": {
                "tokens": total_tokens,
                "requests": total_requests,
                "daily_average": {
                    "tokens": total_tokens // 30 if total_tokens else 0,
                    "requests": total_requests // 30 if total_requests else 0,
                },
            },
            "quota_usage": {
                "daily_tokens": {
                    "used": usage[0].tokens_used if usage else 0,
                    "limit": tenant.quota.daily_token_limit,
                    "percentage": round(
                        (
                            (usage[0].tokens_used / tenant.quota.daily_token_limit * 100)
                            if usage and tenant.quota.daily_token_limit > 0
                            else 0
                        ),
                        2,
                    ),
                },
                "daily_requests": {
                    "used": usage[0].requests_made if usage else 0,
                    "limit": tenant.quota.daily_request_limit,
                    "percentage": round(
                        (
                            (usage[0].requests_made / tenant.quota.daily_request_limit * 100)
                            if usage and tenant.quota.daily_request_limit > 0
                            else 0
                        ),
                        2,
                    ),
                },
            },
            "users": {
                "count": tenant.user_count,
                "limit": tenant.quota.max_users,
            },
        }

    def _generate_slug(self, name: str) -> str:
        """
        Generate a URL-friendly slug from a name.

        Args:
            name: Tenant name.

        Returns:
            str: Generated slug.
        """
        # Convert to lowercase and replace non-alphanumeric with hyphens
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower())
        # Remove leading/trailing hyphens
        slug = slug.strip("-")
        # Limit length
        slug = slug[:50]

        # Check uniqueness and append number if needed
        base_slug = slug
        counter = 1
        while self.tenant_repo.get_by_slug(slug):
            slug = f"{base_slug}-{counter}"
            counter += 1

        return slug

    def get_plan_quotas(self) -> dict[str, dict[str, Any]]:
        """
        Get quota configurations for all plans.

        Returns:
            Dict mapping plan names to quota configurations.
        """
        return {plan: quota.to_dict() for plan, quota in self.PLAN_QUOTAS.items()}
