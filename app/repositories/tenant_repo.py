"""
Open ACE - Tenant Repository

Data access layer for tenant management.
"""

import contextlib
import json
import logging
from datetime import datetime
from typing import Any, Optional, cast

from app.models.tenant import QuotaConfig, Tenant, TenantSettings, TenantUsage
from app.repositories.database import Database

logger = logging.getLogger(__name__)


class TenantRepository:
    """Repository for tenant data access."""

    def __init__(self, db: Optional[Database] = None):
        """
        Initialize tenant repository.

        Args:
            db: Optional Database instance for dependency injection.
        """
        self.db = db or Database()
        # Table structure managed by Alembic migrations

    def create(self, tenant: Tenant) -> Optional[int]:
        """
        Create a new tenant.

        Args:
            tenant: Tenant model instance.

        Returns:
            Optional[int]: Tenant ID if successful, None otherwise.
        """
        try:
            from app.repositories.database import adapt_sql, is_postgresql

            with self.db.connection() as conn:
                cursor = conn.cursor()

                # Insert tenant - use RETURNING for PostgreSQL
                if is_postgresql():
                    cursor.execute(
                        """
                        INSERT INTO tenants
                        (name, slug, status, plan, contact_email, contact_phone,
                         contact_name, quota, settings, trial_ends_at, subscription_ends_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                    """,
                        (
                            tenant.name,
                            tenant.slug,
                            tenant.status,
                            tenant.plan,
                            tenant.contact_email,
                            tenant.contact_phone,
                            tenant.contact_name,
                            json.dumps(tenant.quota.to_dict()),
                            json.dumps(tenant.settings.to_dict()),
                            tenant.trial_ends_at,
                            tenant.subscription_ends_at,
                        ),
                    )
                    result = cursor.fetchone()
                    tenant_id = result[0] if result else None
                else:
                    cursor.execute(
                        """
                        INSERT INTO tenants
                        (name, slug, status, plan, contact_email, contact_phone,
                         contact_name, quota, settings, trial_ends_at, subscription_ends_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        (
                            tenant.name,
                            tenant.slug,
                            tenant.status,
                            tenant.plan,
                            tenant.contact_email,
                            tenant.contact_phone,
                            tenant.contact_name,
                            json.dumps(tenant.quota.to_dict()),
                            json.dumps(tenant.settings.to_dict()),
                            tenant.trial_ends_at,
                            tenant.subscription_ends_at,
                        ),
                    )
                    tenant_id = cursor.lastrowid

                # Insert tenant_quotas
                quota_dict = tenant.quota.to_dict()
                cursor.execute(
                    adapt_sql("""
                    INSERT INTO tenant_quotas
                    (tenant_id, daily_token_limit, monthly_token_limit,
                     daily_request_limit, monthly_request_limit, max_users, max_sessions_per_user)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """),
                    (
                        tenant_id,
                        quota_dict.get("daily_token_limit", 1000000),
                        quota_dict.get("monthly_token_limit", 30000000),
                        quota_dict.get("daily_request_limit", 10000),
                        quota_dict.get("monthly_request_limit", 300000),
                        quota_dict.get("max_users", 100),
                        quota_dict.get("max_sessions_per_user", 5),
                    ),
                )

                # Insert tenant_settings
                settings_dict = tenant.settings.to_dict()
                # PostgreSQL uses TRUE/FALSE, SQLite uses 1/0
                if self.db.is_postgresql:
                    content_filter_val = settings_dict.get("content_filter_enabled", True)
                    audit_log_val = settings_dict.get("audit_log_enabled", True)
                    sso_val = settings_dict.get("sso_enabled", False)
                else:
                    content_filter_val = (
                        1 if settings_dict.get("content_filter_enabled", True) else 0
                    )
                    audit_log_val = 1 if settings_dict.get("audit_log_enabled", True) else 0
                    sso_val = 1 if settings_dict.get("sso_enabled", False) else 0

                cursor.execute(
                    adapt_sql("""
                    INSERT INTO tenant_settings
                    (tenant_id, content_filter_enabled, audit_log_enabled,
                     audit_log_retention_days, data_retention_days, sso_enabled, sso_provider)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """),
                    (
                        tenant_id,
                        content_filter_val,
                        audit_log_val,
                        settings_dict.get("audit_log_retention_days", 90),
                        settings_dict.get("data_retention_days", 365),
                        sso_val,
                        settings_dict.get("sso_provider"),
                    ),
                )

                conn.commit()

            logger.info(f"Created tenant: {tenant.name} (ID: {tenant_id})")
            return tenant_id

        except Exception as e:
            logger.error(f"Failed to create tenant: {e}")
            return None

    def get_by_id(self, tenant_id: int, include_deleted: bool = False) -> Optional[Tenant]:
        """
        Get tenant by ID.

        Args:
            tenant_id: Tenant ID.
            include_deleted: Whether to include soft-deleted tenants.

        Returns:
            Optional[Tenant]: Tenant instance or None.
        """
        if include_deleted:
            query = "SELECT * FROM tenants WHERE id = ?"
        else:
            query = "SELECT * FROM tenants WHERE id = ? AND deleted_at IS NULL"

        row = self.db.fetch_one(query, (tenant_id,))
        return self._row_to_tenant(row) if row else None

    def get_by_slug(self, slug: str, include_deleted: bool = False) -> Optional[Tenant]:
        """
        Get tenant by slug.

        Args:
            slug: Tenant slug.
            include_deleted: Whether to include soft-deleted tenants.

        Returns:
            Optional[Tenant]: Tenant instance or None.
        """
        if include_deleted:
            query = "SELECT * FROM tenants WHERE slug = ?"
        else:
            query = "SELECT * FROM tenants WHERE slug = ? AND deleted_at IS NULL"

        row = self.db.fetch_one(query, (slug,))
        return self._row_to_tenant(row) if row else None

    def get_all(
        self,
        status: Optional[str] = None,
        plan: Optional[str] = None,
        include_deleted: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Tenant]:
        """
        Get all tenants with optional filters.

        Args:
            status: Filter by status.
            plan: Filter by plan.
            include_deleted: Whether to include soft-deleted tenants.
            limit: Maximum results.
            offset: Offset for pagination.

        Returns:
            List[Tenant]: List of tenants.
        """
        conditions = []
        params = []

        if not include_deleted:
            conditions.append("deleted_at IS NULL")

        if status:
            conditions.append("status = ?")
            params.append(status)

        if plan:
            conditions.append("plan = ?")
            params.append(plan)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        query = f"""
            SELECT * FROM tenants
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """

        rows = self.db.fetch_all(query, tuple(params + [limit, offset]))
        return [self._row_to_tenant(row) for row in rows]

    def update(self, tenant_id: int, updates: dict) -> bool:
        """
        Update tenant fields.

        Args:
            tenant_id: Tenant ID.
            updates: Dictionary of fields to update.

        Returns:
            bool: True if successful.
        """
        if not updates:
            return False

        # Handle nested objects
        if "quota" in updates and isinstance(updates["quota"], dict):
            updates["quota"] = json.dumps(updates["quota"])
        if "settings" in updates and isinstance(updates["settings"], dict):
            updates["settings"] = json.dumps(updates["settings"])

        set_clauses = []
        params = []

        for key, value in updates.items():
            if key in (
                "name",
                "slug",
                "status",
                "plan",
                "contact_email",
                "contact_phone",
                "contact_name",
                "quota",
                "settings",
                "trial_ends_at",
                "subscription_ends_at",
                "user_count",
                "total_tokens_used",
                "total_requests_made",
            ):
                set_clauses.append(f"{key} = ?")
                params.append(value)

        if not set_clauses:
            return False

        set_clauses.append("updated_at = ?")
        params.append(datetime.utcnow())
        params.append(tenant_id)

        query = f"UPDATE tenants SET {', '.join(set_clauses)} WHERE id = ?"

        try:
            cursor = self.db.execute(query, tuple(params))
            return cast("bool", cursor.rowcount > 0)

        except Exception as e:
            logger.error(f"Failed to update tenant: {e}")
            return False

    def delete(self, tenant_id: int) -> bool:
        """
        Soft delete a tenant by setting deleted_at timestamp.

        Args:
            tenant_id: Tenant ID.

        Returns:
            bool: True if successful.
        """
        query = (
            "UPDATE tenants SET deleted_at = ?, updated_at = ? WHERE id = ? AND deleted_at IS NULL"
        )

        try:
            cursor = self.db.execute(query, (datetime.utcnow(), datetime.utcnow(), tenant_id))
            return cast("bool", cursor.rowcount > 0)
        except Exception as e:
            logger.error(f"Failed to soft delete tenant: {e}")
            return False

    def restore(self, tenant_id: int) -> bool:
        """
        Restore a soft-deleted tenant.

        Args:
            tenant_id: Tenant ID.

        Returns:
            bool: True if successful.
        """
        query = "UPDATE tenants SET deleted_at = NULL, updated_at = ? WHERE id = ?"

        try:
            cursor = self.db.execute(query, (datetime.utcnow(), tenant_id))
            return cast("bool", cursor.rowcount > 0)
        except Exception as e:
            logger.error(f"Failed to restore tenant: {e}")
            return False

    def hard_delete(self, tenant_id: int) -> bool:
        """
        Permanently delete a tenant.

        Args:
            tenant_id: Tenant ID.

        Returns:
            bool: True if successful.
        """
        try:
            from app.repositories.database import adapt_sql

            with self.db.connection() as conn:
                cursor = conn.cursor()
                # Delete related records first
                cursor.execute(
                    adapt_sql("DELETE FROM tenant_usage WHERE tenant_id = ?"), (tenant_id,)
                )
                cursor.execute(adapt_sql("DELETE FROM tenants WHERE id = ?"), (tenant_id,))
                conn.commit()
                return cast("bool", cursor.rowcount > 0)

        except Exception as e:
            logger.error(f"Failed to hard delete tenant: {e}")
            return False

    def record_usage(
        self, tenant_id: int, tokens: int = 0, requests: int = 1, date: Optional[str] = None
    ) -> bool:
        """
        Record usage for a tenant.

        Args:
            tenant_id: Tenant ID.
            tokens: Tokens used.
            requests: Requests made.
            date: Date string (YYYY-MM-DD), defaults to today.

        Returns:
            bool: True if successful.
        """
        if date is None:
            date = datetime.utcnow().strftime("%Y-%m-%d")

        try:
            from app.repositories.database import adapt_sql

            with self.db.connection() as conn:
                cursor = conn.cursor()

                # Insert or update usage
                cursor.execute(
                    adapt_sql("""
                    INSERT INTO tenant_usage (tenant_id, date, tokens_used, requests_made)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(tenant_id, date) DO UPDATE SET
                        tokens_used = tokens_used + ?,
                        requests_made = requests_made + ?
                """),
                    (tenant_id, date, tokens, requests, tokens, requests),
                )

                # Update tenant totals
                cursor.execute(
                    adapt_sql("""
                    UPDATE tenants SET
                        total_tokens_used = total_tokens_used + ?,
                        total_requests_made = total_requests_made + ?,
                        updated_at = ?
                    WHERE id = ?
                """),
                    (tokens, requests, datetime.utcnow(), tenant_id),
                )

                conn.commit()
                return True

        except Exception as e:
            logger.error(f"Failed to record tenant usage: {e}")
            return False

    def get_usage(
        self,
        tenant_id: int,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 30,
    ) -> list[TenantUsage]:
        """
        Get usage history for a tenant.

        Args:
            tenant_id: Tenant ID.
            start_date: Start date filter.
            end_date: End date filter.
            limit: Maximum results.

        Returns:
            List[TenantUsage]: Usage records.
        """
        conditions = ["tenant_id = ?"]
        params: list[Any] = [tenant_id]

        if start_date:
            conditions.append("date >= ?")
            params.append(start_date)

        if end_date:
            conditions.append("date <= ?")
            params.append(end_date)

        query = f"""
            SELECT * FROM tenant_usage
            WHERE {' AND '.join(conditions)}
            ORDER BY date DESC
            LIMIT ?
        """

        rows = self.db.fetch_all(query, tuple(params + [limit]))

        return [
            TenantUsage(
                tenant_id=row["tenant_id"],
                date=row["date"],
                tokens_used=row["tokens_used"],
                requests_made=row["requests_made"],
                active_users=row["active_users"],
                new_users=row["new_users"],
            )
            for row in rows
        ]

    def update_user_count(self, tenant_id: int, delta: int = 1) -> bool:
        """
        Update user count for a tenant.

        Args:
            tenant_id: Tenant ID.
            delta: Change in user count (positive or negative).

        Returns:
            bool: True if successful.
        """
        try:
            from app.repositories.database import adapt_sql

            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    adapt_sql("""
                    UPDATE tenants SET
                        user_count = MAX(0, user_count + ?),
                        updated_at = ?
                    WHERE id = ?
                """),
                    (delta, datetime.utcnow(), tenant_id),
                )
                conn.commit()
                return cast("bool", cursor.rowcount > 0)

        except Exception as e:
            logger.error(f"Failed to update user count: {e}")
            return False

    def count(self, status: Optional[str] = None) -> int:
        """
        Count tenants.

        Args:
            status: Filter by status.

        Returns:
            int: Number of tenants.
        """
        if status:
            result = self.db.fetch_one(
                "SELECT COUNT(*) as count FROM tenants WHERE status = ?", (status,)
            )
        else:
            result = self.db.fetch_one("SELECT COUNT(*) as count FROM tenants")

        return result["count"] if result else 0

    def _row_to_tenant(self, row: dict) -> Tenant:
        """Convert database row to Tenant model."""
        quota = QuotaConfig()
        settings = TenantSettings()
        tenant_id = row.get("id")

        # Try to load from dedicated tables first
        if tenant_id:
            try:
                quota_row = self.db.fetch_one(
                    "SELECT * FROM tenant_quotas WHERE tenant_id = ?", (tenant_id,)
                )
                if quota_row:
                    quota = QuotaConfig(
                        daily_token_limit=quota_row.get("daily_token_limit", 1000000),
                        monthly_token_limit=quota_row.get("monthly_token_limit", 30000000),
                        daily_request_limit=quota_row.get("daily_request_limit", 10000),
                        monthly_request_limit=quota_row.get("monthly_request_limit", 300000),
                        max_users=quota_row.get("max_users", 100),
                        max_sessions_per_user=quota_row.get("max_sessions_per_user", 5),
                    )
            except Exception:
                pass

            try:
                settings_row = self.db.fetch_one(
                    "SELECT * FROM tenant_settings WHERE tenant_id = ?", (tenant_id,)
                )
                if settings_row:
                    settings = TenantSettings(
                        content_filter_enabled=bool(settings_row.get("content_filter_enabled", 1)),
                        audit_log_enabled=bool(settings_row.get("audit_log_enabled", 1)),
                        audit_log_retention_days=settings_row.get("audit_log_retention_days", 90),
                        data_retention_days=settings_row.get("data_retention_days", 365),
                        sso_enabled=bool(settings_row.get("sso_enabled", 0)),
                        sso_provider=settings_row.get("sso_provider"),
                    )
            except Exception:
                pass

        # Fallback to JSON fields if dedicated tables don't have data
        if quota == QuotaConfig() and row.get("quota"):
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                quota = QuotaConfig.from_dict(json.loads(row["quota"]))

        if settings == TenantSettings() and row.get("settings"):
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                settings = TenantSettings.from_dict(json.loads(row["settings"]))

        return Tenant(
            id=row.get("id"),
            name=row.get("name", ""),
            slug=row.get("slug", ""),
            status=row.get("status", "active"),
            plan=row.get("plan", "standard"),
            contact_email=row.get("contact_email", ""),
            contact_phone=row.get("contact_phone"),
            contact_name=row.get("contact_name"),
            quota=quota,
            settings=settings,
            created_at=datetime.fromisoformat(row["created_at"]) if row.get("created_at") else None,
            updated_at=datetime.fromisoformat(row["updated_at"]) if row.get("updated_at") else None,
            trial_ends_at=(
                datetime.fromisoformat(row["trial_ends_at"]) if row.get("trial_ends_at") else None
            ),
            subscription_ends_at=(
                datetime.fromisoformat(row["subscription_ends_at"])
                if row.get("subscription_ends_at")
                else None
            ),
            user_count=row.get("user_count", 0),
            total_tokens_used=row.get("total_tokens_used", 0),
            total_requests_made=row.get("total_requests_made", 0),
        )
