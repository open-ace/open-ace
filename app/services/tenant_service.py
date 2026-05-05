"""
Open ACE - AI Computing Explorer - Tenant Service

Business logic for multi-tenant management.
"""

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Optional

from app.models.tenant import QuotaConfig, Tenant, TenantSettings, TenantUsage
from app.repositories.tenant_repo import TenantRepository
from app.repositories.user_repo import UserRepository

logger = logging.getLogger(__name__)


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
        tenant_repo: Optional[TenantRepository] = None,
        user_repo: Optional[UserRepository] = None,
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
        slug: Optional[str] = None,
        plan: str = "standard",
        contact_email: str = "",
        contact_name: Optional[str] = None,
        trial_days: Optional[int] = None,
    ) -> Optional[Tenant]:
        """
        Create a new tenant.

        Args:
            name: Tenant name.
            slug: URL-friendly identifier (auto-generated if not provided).
            plan: Subscription plan.
            contact_email: Contact email.
            contact_name: Contact name.
            trial_days: Number of trial days (for trial tenants).

        Returns:
            Optional[Tenant]: Created tenant or None on failure.
        """
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
            tenant.trial_ends_at = datetime.utcnow() + timedelta(days=trial_days)

        tenant_id = self.tenant_repo.create(tenant)
        if tenant_id:
            tenant.id = tenant_id
            logger.info(f"Created tenant: {name} (ID: {tenant_id}, Plan: {plan})")
            return tenant

        return None

    def get_tenant(self, tenant_id: int) -> Optional[Tenant]:
        """
        Get tenant by ID.

        Args:
            tenant_id: Tenant ID.

        Returns:
            Optional[Tenant]: Tenant or None.
        """
        return self.tenant_repo.get_by_id(tenant_id)

    def get_tenant_by_slug(self, slug: str) -> Optional[Tenant]:
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
        status: Optional[str] = None,
        plan: Optional[str] = None,
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

    def update_tenant(self, tenant_id: int, updates: dict[str, Any]) -> bool:
        """
        Update tenant fields.

        Args:
            tenant_id: Tenant ID.
            updates: Dictionary of fields to update.

        Returns:
            bool: True if successful.
        """
        # Handle quota updates
        if "plan" in updates:
            new_plan = updates["plan"]
            if new_plan in self.PLAN_QUOTAS:
                updates["quota"] = self.PLAN_QUOTAS[new_plan].to_dict()

        return self.tenant_repo.update(tenant_id, updates)

    def update_quota(self, tenant_id: int, quota_updates: dict[str, int]) -> bool:
        """
        Update tenant quota configuration.

        Args:
            tenant_id: Tenant ID.
            quota_updates: Quota fields to update.

        Returns:
            bool: True if successful.
        """
        tenant = self.get_tenant(tenant_id)
        if not tenant:
            return False

        current_quota = tenant.quota.to_dict()
        current_quota.update(quota_updates)

        return self.tenant_repo.update(tenant_id, {"quota": current_quota})

    def update_settings(self, tenant_id: int, settings_updates: dict[str, Any]) -> bool:
        """
        Update tenant settings.

        Args:
            tenant_id: Tenant ID.
            settings_updates: Settings fields to update.

        Returns:
            bool: True if successful.
        """
        tenant = self.get_tenant(tenant_id)
        if not tenant:
            return False

        current_settings = tenant.settings.to_dict()
        current_settings.update(settings_updates)

        return self.tenant_repo.update(tenant_id, {"settings": current_settings})

    def suspend_tenant(self, tenant_id: int, reason: Optional[str] = None) -> bool:
        """
        Suspend a tenant.

        Args:
            tenant_id: Tenant ID.
            reason: Reason for suspension.

        Returns:
            bool: True if successful.
        """
        logger.info(f"Suspending tenant {tenant_id}: {reason or 'No reason provided'}")
        return self.tenant_repo.update(tenant_id, {"status": "suspended"})

    def activate_tenant(self, tenant_id: int) -> bool:
        """
        Activate a suspended tenant.

        Args:
            tenant_id: Tenant ID.

        Returns:
            bool: True if successful.
        """
        return self.tenant_repo.update(tenant_id, {"status": "active"})

    def delete_tenant(self, tenant_id: int, hard: bool = False) -> bool:
        """
        Delete a tenant.

        Args:
            tenant_id: Tenant ID.
            hard: If True, permanently delete; otherwise soft delete.

        Returns:
            bool: True if successful.
        """
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
        start_date = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
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
        today = datetime.utcnow().strftime("%Y-%m-%d")
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
