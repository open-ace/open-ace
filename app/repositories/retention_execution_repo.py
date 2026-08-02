"""
Open ACE - Retention Execution Repository

Repository for retention execution history.
Issue #2188: Execution tracking with batch recovery support.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.repositories.database import Database, adapt_sql

logger = logging.getLogger(__name__)


class RetentionExecutionRepository:
    """Repository for retention execution CRUD operations."""

    def __init__(self, db: Database | None = None):
        """Initialize repository.

        Args:
            db: Optional Database instance.
        """
        self.db = db or Database()

    def create_execution(
        self,
        execution_id: str,
        tenant_id: int,
        policy_id: int | None = None,
        dry_run: bool = False,
        batch_size: int = 1000,
        max_records_override: int | None = None,
    ) -> dict[str, Any]:
        """Create a new execution record.

        Args:
            execution_id: Unique execution ID.
            tenant_id: Tenant ID.
            policy_id: Policy ID.
            dry_run: Whether this is a dry run.
            batch_size: Batch size.
            max_records_override: Override max records limit.

        Returns:
            Created execution dict.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                adapt_sql("""
                    INSERT INTO retention_executions (
                        execution_id, tenant_id, policy_id, status, dry_run,
                        batch_size, max_records_override, started_at, created_at
                    ) VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?)
                """),
                (
                    execution_id,
                    tenant_id,
                    policy_id,
                    dry_run,
                    batch_size,
                    max_records_override,
                    now,
                    now,
                ),
            )
            conn.commit()

        return self.get_execution_by_id(execution_id)

    def get_execution_by_id(self, execution_id: str) -> dict[str, Any] | None:
        """Get execution by ID.

        Args:
            execution_id: Execution ID.

        Returns:
            Execution dict or None.
        """
        return self.db.fetch_one(
            adapt_sql("SELECT * FROM retention_executions WHERE execution_id = ?"),
            (execution_id,),
        )

    # Allowed update fields (whitelist for SQL injection protection)
    ALLOWED_UPDATE_FIELDS = {
        "status",
        "records_scanned",
        "records_affected",
        "records_skipped",
        "records_archived",
        "records_anonymized",
        "records_in_recycle_bin",
        "error_message",
        "error_details",
        "last_batch_id",
        "total_batches",
        "last_batch_status",
        "completed_at",
    }

    def update_execution(
        self,
        execution_id: str,
        status: str | None = None,
        records_scanned: int | None = None,
        records_affected: int | None = None,
        records_skipped: int | None = None,
        records_archived: int | None = None,
        records_anonymized: int | None = None,
        records_in_recycle_bin: int | None = None,
        error_message: str | None = None,
        error_details: dict | None = None,
        last_batch_id: int | None = None,
        total_batches: int | None = None,
        last_batch_status: str | None = None,
        completed_at: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Update execution record.

        Args:
            execution_id: Execution ID.
            status: New status.
            records_scanned: Records scanned.
            records_affected: Records affected.
            records_skipped: Records skipped.
            records_archived: Records archived.
            records_anonymized: Records anonymized.
            records_in_recycle_bin: Records moved to recycle bin.
            error_message: Error message.
            error_details: Error details dict.
            last_batch_id: Last batch ID processed.
            total_batches: Total batches.
            last_batch_status: Last batch status.
            completed_at: Completion timestamp.

        Returns:
            Updated execution dict or None.
        """
        updates = []
        params = []

        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if records_scanned is not None:
            updates.append("records_scanned = ?")
            params.append(records_scanned)
        if records_affected is not None:
            updates.append("records_affected = ?")
            params.append(records_affected)
        if records_skipped is not None:
            updates.append("records_skipped = ?")
            params.append(records_skipped)
        if records_archived is not None:
            updates.append("records_archived = ?")
            params.append(records_archived)
        if records_anonymized is not None:
            updates.append("records_anonymized = ?")
            params.append(records_anonymized)
        if records_in_recycle_bin is not None:
            updates.append("records_in_recycle_bin = ?")
            params.append(records_in_recycle_bin)
        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)
        if error_details is not None:
            updates.append("error_details = ?")
            params.append(json.dumps(error_details))
        if last_batch_id is not None:
            updates.append("last_batch_id = ?")
            params.append(last_batch_id)
        if total_batches is not None:
            updates.append("total_batches = ?")
            params.append(total_batches)
        if last_batch_status is not None:
            updates.append("last_batch_status = ?")
            params.append(last_batch_status)
        if completed_at is not None:
            updates.append("completed_at = ?")
            params.append(completed_at)

        if not updates:
            return self.get_execution_by_id(execution_id)

        params.append(execution_id)

        # Validate field names against whitelist for SQL injection protection
        # Extract field names from updates list (e.g., "status = ?" -> "status")
        for update in updates:
            field_name = update.split(" = ")[0]
            if field_name not in self.ALLOWED_UPDATE_FIELDS:
                raise ValueError(f"Invalid field name: {field_name}")

        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                adapt_sql(f"""
                    UPDATE retention_executions
                    SET {', '.join(updates)}
                    WHERE execution_id = ?
                """),
                params,
            )
            conn.commit()

        return self.get_execution_by_id(execution_id)

    def get_executions_for_tenant(
        self,
        tenant_id: int,
        status: str | None = None,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        """Get executions for a tenant.

        Args:
            tenant_id: Tenant ID.
            status: Filter by status.
            limit: Maximum number of records.

        Returns:
            List of execution dicts.
        """
        if status:
            return self.db.fetch_all(
                adapt_sql("""
                    SELECT * FROM retention_executions
                    WHERE tenant_id = ? AND status = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """),
                (tenant_id, status, limit),
            )
        else:
            return self.db.fetch_all(
                adapt_sql("""
                    SELECT * FROM retention_executions
                    WHERE tenant_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """),
                (tenant_id, limit),
            )

    def acquire_lock(
        self,
        execution_id: str,
        lock_timeout_seconds: int = 1800,
    ) -> bool:
        """Acquire execution lock for leader election.

        Args:
            execution_id: Execution ID.
            lock_timeout_seconds: Lock timeout in seconds.

        Returns:
            True if lock acquired, False otherwise.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        expires_at = datetime.now(timezone.utc).replace(tzinfo=None)

        with self.db.connection() as conn:
            cursor = conn.cursor()
            # Try to acquire lock
            cursor.execute(
                adapt_sql("""
                    UPDATE retention_executions
                    SET lock_acquired_at = ?, lock_expires_at = ?
                    WHERE execution_id = ?
                    AND (lock_acquired_at IS NULL OR lock_expires_at < ?)
                """),
                (now, expires_at, execution_id, now),
            )
            conn.commit()
            return cursor.rowcount > 0

    def release_lock(self, execution_id: str) -> bool:
        """Release execution lock.

        Args:
            execution_id: Execution ID.

        Returns:
            True if lock released, False otherwise.
        """
        with self.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                adapt_sql("""
                    UPDATE retention_executions
                    SET lock_acquired_at = NULL, lock_expires_at = NULL
                    WHERE execution_id = ?
                """),
                (execution_id,),
            )
            conn.commit()
            return cursor.rowcount > 0

    def check_existing_execution(self, execution_id: str) -> bool:
        """Check if execution already exists (idempotency check).

        Args:
            execution_id: Execution ID.

        Returns:
            True if execution exists.
        """
        row = self.db.fetch_one(
            adapt_sql(
                "SELECT COUNT(*) as count FROM retention_executions WHERE execution_id = ?"
            ),
            (execution_id,),
        )
        return row["count"] > 0 if row else False
