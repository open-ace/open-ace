"""
Open ACE - Retention Policy Repository

Repository for retention policy persistence and inheritance.
Issue #2188: Persistent, versioned, tenant-scoped retention policies.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.repositories.database import Database, adapt_sql

logger = logging.getLogger(__name__)


class RetentionPolicyRepository:
    """Repository for retention policy CRUD operations."""

    def __init__(self, db: Database | None = None):
        """Initialize repository.

        Args:
            db: Optional Database instance.
        """
        self.db = db or Database()

    def get_policy(
        self, tenant_id: int | None, data_type: str
    ) -> dict[str, Any] | None:
        """Get effective policy for a data type (with inheritance).

        Issue #2188: Policy inheritance semantics
        1. Look for tenant-level policy (tenant_id = {tenant_id})
        2. If not found, look for global default (tenant_id IS NULL)
        3. If still not found, return None (data type not configured)

        Args:
            tenant_id: Tenant ID (None for global queries).
            data_type: Data type (e.g., 'audit_logs', 'sessions').

        Returns:
            Policy dict or None if not configured.
        """
        # Try tenant-level policy first
        if tenant_id is not None:
            row = self.db.fetch_one(
                adapt_sql("""
                    SELECT * FROM retention_policies
                    WHERE tenant_id = ? AND data_type = ? AND enabled = 1
                    ORDER BY version DESC LIMIT 1
                """),
                (tenant_id, data_type),
            )
            if row:
                return self._row_to_dict(row)

        # Fallback to global default
        row = self.db.fetch_one(
            adapt_sql("""
                SELECT * FROM retention_policies
                WHERE tenant_id IS NULL AND data_type = ? AND enabled = 1
                ORDER BY version DESC LIMIT 1
            """),
            (data_type,),
        )
        if row:
            result = self._row_to_dict(row)
            result["policy_source"] = "global"
            return result

        return None

    def get_all_policies(
        self, tenant_id: int | None, include_disabled: bool = False
    ) -> list[dict[str, Any]]:
        """Get all policies for a tenant.

        Args:
            tenant_id: Tenant ID (None for global policies).
            include_disabled: Whether to include disabled policies.

        Returns:
            List of policy dicts.
        """
        if tenant_id is not None:
            if include_disabled:
                rows = self.db.fetch_all(
                    adapt_sql("""
                        SELECT * FROM retention_policies
                        WHERE tenant_id = ?
                        ORDER BY data_type, version DESC
                    """),
                    (tenant_id,),
                )
            else:
                rows = self.db.fetch_all(
                    adapt_sql("""
                        SELECT * FROM retention_policies
                        WHERE tenant_id = ? AND enabled = 1
                        ORDER BY data_type, version DESC
                    """),
                    (tenant_id,),
                )
        else:
            if include_disabled:
                rows = self.db.fetch_all(
                    """
                    SELECT * FROM retention_policies
                    WHERE tenant_id IS NULL
                    ORDER BY data_type, version DESC
                """
                )
            else:
                rows = self.db.fetch_all(
                    """
                    SELECT * FROM retention_policies
                    WHERE tenant_id IS NULL AND enabled = 1
                    ORDER BY data_type, version DESC
                """
                )

        return [self._row_to_dict(row) for row in rows]

    def create_policy(
        self,
        tenant_id: int | None,
        data_type: str,
        retention_days: int,
        action: str,
        archive_target: str | None = None,
        archive_config: dict | None = None,
        anonymize_fields: dict | None = None,
        backup_before_anonymize: bool = False,
        created_by: int | None = None,
    ) -> dict[str, Any]:
        """Create a new retention policy.

        Args:
            tenant_id: Tenant ID (None for global policy).
            data_type: Data type.
            retention_days: Number of days to retain.
            action: Action to take ('delete', 'archive', 'anonymize').
            archive_target: Archive target ('local_file', 's3').
            archive_config: Archive configuration dict.
            anonymize_fields: Field anonymization strategies.
            backup_before_anonymize: Whether to backup before anonymize.
            created_by: User ID who created the policy.

        Returns:
            Created policy dict.
        """
        # Validate action-specific requirements
        if action == "archive" and not archive_target:
            raise ValueError("archive_target is required for archive action")
        if action == "anonymize" and not anonymize_fields:
            raise ValueError("anonymize_fields is required for anonymize action")

        # Get next version number
        version = self._get_next_version(tenant_id, data_type)

        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                adapt_sql("""
                    INSERT INTO retention_policies (
                        tenant_id, data_type, retention_days, action, version,
                        archive_target, archive_config, anonymize_fields,
                        backup_before_anonymize, created_by, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """),
                (
                    tenant_id,
                    data_type,
                    retention_days,
                    action,
                    version,
                    archive_target,
                    json.dumps(archive_config) if archive_config else None,
                    json.dumps(anonymize_fields) if anonymize_fields else None,
                    1 if backup_before_anonymize else 0,
                    created_by,
                    datetime.now(timezone.utc).replace(tzinfo=None),
                    datetime.now(timezone.utc).replace(tzinfo=None),
                ),
            )
            conn.commit()
            policy_id = cursor.lastrowid

        return self.get_policy_by_id(policy_id)

    def update_policy(
        self,
        policy_id: int,
        retention_days: int | None = None,
        action: str | None = None,
        archive_target: str | None = None,
        archive_config: dict | None = None,
        anonymize_fields: dict | None = None,
        backup_before_anonymize: bool | None = None,
        enabled: bool | None = None,
        updated_by: int | None = None,
    ) -> dict[str, Any] | None:
        """Update a retention policy.

        Args:
            policy_id: Policy ID.
            retention_days: New retention days.
            action: New action.
            archive_target: New archive target.
            archive_config: New archive config.
            anonymize_fields: New anonymize fields.
            backup_before_anonymize: New backup setting.
            enabled: New enabled status.
            updated_by: User ID who updated the policy.

        Returns:
            Updated policy dict or None.
        """
        # Build update clauses
        updates = []
        params = []

        if retention_days is not None:
            updates.append("retention_days = ?")
            params.append(retention_days)
        if action is not None:
            updates.append("action = ?")
            params.append(action)
        if archive_target is not None:
            updates.append("archive_target = ?")
            params.append(archive_target)
        if archive_config is not None:
            updates.append("archive_config = ?")
            params.append(json.dumps(archive_config))
        if anonymize_fields is not None:
            updates.append("anonymize_fields = ?")
            params.append(json.dumps(anonymize_fields))
        if backup_before_anonymize is not None:
            updates.append("backup_before_anonymize = ?")
            params.append(1 if backup_before_anonymize else 0)
        if enabled is not None:
            updates.append("enabled = ?")
            params.append(1 if enabled else 0)

        if not updates:
            return self.get_policy_by_id(policy_id)

        updates.append("updated_at = ?")
        params.append(datetime.now(timezone.utc).replace(tzinfo=None))
        if updated_by is not None:
            updates.append("updated_by = ?")
            params.append(updated_by)

        params.append(policy_id)

        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                adapt_sql(f"""
                    UPDATE retention_policies
                    SET {', '.join(updates)}
                    WHERE id = ?
                """),
                params,
            )
            conn.commit()

        return self.get_policy_by_id(policy_id)

    def get_policy_by_id(self, policy_id: int) -> dict[str, Any] | None:
        """Get policy by ID.

        Args:
            policy_id: Policy ID.

        Returns:
            Policy dict or None.
        """
        row = self.db.fetch_one(
            "SELECT * FROM retention_policies WHERE id = ?", (policy_id,)
        )
        return self._row_to_dict(row) if row else None

    def delete_policy(self, policy_id: int) -> bool:
        """Delete a policy.

        Args:
            policy_id: Policy ID.

        Returns:
            True if deleted, False otherwise.
        """
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM retention_policies WHERE id = ?", (policy_id,)
            )
            conn.commit()
            return cursor.rowcount > 0

    def _get_next_version(self, tenant_id: int | None, data_type: str) -> int:
        """Get next version number for a policy.

        Args:
            tenant_id: Tenant ID.
            data_type: Data type.

        Returns:
            Next version number.
        """
        if tenant_id is not None:
            row = self.db.fetch_one(
                adapt_sql("""
                    SELECT MAX(version) as max_version FROM retention_policies
                    WHERE tenant_id = ? AND data_type = ?
                """),
                (tenant_id, data_type),
            )
        else:
            row = self.db.fetch_one(
                """
                    SELECT MAX(version) as max_version FROM retention_policies
                    WHERE tenant_id IS NULL AND data_type = ?
                """,
                (data_type,),
            )

        return (row["max_version"] or 0) + 1 if row else 1

    def _row_to_dict(self, row: dict) -> dict[str, Any]:
        """Convert database row to dict.

        Args:
            row: Database row.

        Returns:
            Policy dict.
        """
        result = dict(row)

        # Parse JSON fields
        if result.get("archive_config"):
            try:
                result["archive_config"] = json.loads(result["archive_config"])
            except (json.JSONDecodeError, TypeError):
                pass
        if result.get("anonymize_fields"):
            try:
                result["anonymize_fields"] = json.loads(result["anonymize_fields"])
            except (json.JSONDecodeError, TypeError):
                pass

        # Add policy source
        if result.get("tenant_id") is not None:
            result["policy_source"] = "tenant"
        else:
            result["policy_source"] = "global"

        return result