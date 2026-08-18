"""
Open ACE - Backfill Log Repository

Repository for backfill_logs table operations.

Issue #2761: Tracks message backfill operations when mappings are activated.
"""

import logging

from app.models.tool_account_conflict import BackfillLog
from app.repositories.database import Database

logger = logging.getLogger(__name__)


class BackfillLogRepository:
    """Repository for backfill log records."""

    def __init__(self, db: Database | None = None):
        self.db = db or Database()

    def create(
        self,
        mapping_id: int,
        backfilled_count: int,
        first_date: str | None = None,
        last_date: str | None = None,
        status: str = "completed",
    ) -> BackfillLog | None:
        """Create a backfill log entry.

        Issue #2761: Records when messages are backfilled for a mapping.
        """
        from app.repositories.database import is_postgresql

        if is_postgresql():
            query = """
                INSERT INTO backfill_logs
                    (mapping_id, backfilled_count, first_date, last_date, started_at, completed_at, status)
                VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, %s)
                RETURNING *
            """
        else:
            query = """
                INSERT INTO backfill_logs
                    (mapping_id, backfilled_count, first_date, last_date, started_at, completed_at, status)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?)
            """

        params = (mapping_id, backfilled_count, first_date, last_date, status)

        try:
            if is_postgresql():
                row = self.db.fetch_one(query, params, commit=True)
            else:
                self.db.execute(query, params)
                row = self.db.fetch_one(
                    """
                    SELECT * FROM backfill_logs
                    WHERE mapping_id = ?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (mapping_id,),
                )
            return self._row_to_model(row) if row else None
        except Exception as e:
            logger.error(f"Error creating backfill log: {e}")
            return None

    def start_backfill(
        self,
        mapping_id: int,
    ) -> BackfillLog | None:
        """Create a running backfill log entry.

        Issue #2761: Called at start of backfill, mark as running.
        """
        from app.repositories.database import is_postgresql

        if is_postgresql():
            query = """
                INSERT INTO backfill_logs
                    (mapping_id, backfilled_count, started_at, status)
                VALUES (%s, 0, CURRENT_TIMESTAMP, 'running')
                RETURNING *
            """
        else:
            query = """
                INSERT INTO backfill_logs
                    (mapping_id, backfilled_count, started_at, status)
                VALUES (?, 0, CURRENT_TIMESTAMP, 'running')
            """

        params = (mapping_id,)

        try:
            if is_postgresql():
                row = self.db.fetch_one(query, params, commit=True)
            else:
                self.db.execute(query, params)
                row = self.db.fetch_one(
                    """
                    SELECT * FROM backfill_logs
                    WHERE mapping_id = ?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (mapping_id,),
                )
            return self._row_to_model(row) if row else None
        except Exception as e:
            logger.error(f"Error starting backfill log: {e}")
            return None

    def complete_backfill(
        self,
        log_id: int,
        backfilled_count: int,
        first_date: str | None = None,
        last_date: str | None = None,
    ) -> BackfillLog | None:
        """Mark a backfill log as completed.

        Issue #2761: Called after successful backfill.
        """
        from app.repositories.database import is_postgresql

        if is_postgresql():
            query = """
                UPDATE backfill_logs
                SET backfilled_count = %s,
                    first_date = %s,
                    last_date = %s,
                    completed_at = CURRENT_TIMESTAMP,
                    status = 'completed'
                WHERE id = %s
                RETURNING *
            """
            params = (backfilled_count, first_date, last_date, log_id)
            row = self.db.fetch_one(query, params, commit=True)
        else:
            query = """
                UPDATE backfill_logs
                SET backfilled_count = ?,
                    first_date = ?,
                    last_date = ?,
                    completed_at = CURRENT_TIMESTAMP,
                    status = 'completed'
                WHERE id = ?
            """
            params = (backfilled_count, first_date, last_date, log_id)
            self.db.execute(query, params)
            row = self.db.fetch_one("SELECT * FROM backfill_logs WHERE id = ?", (log_id,))

        return self._row_to_model(row) if row else None

    def fail_backfill(
        self,
        log_id: int,
    ) -> BackfillLog | None:
        """Mark a backfill log as failed.

        Issue #2761: Called when backfill encounters an error.
        """
        from app.repositories.database import is_postgresql

        if is_postgresql():
            query = """
                UPDATE backfill_logs
                SET completed_at = CURRENT_TIMESTAMP,
                    status = 'failed'
                WHERE id = %s
                RETURNING *
            """
            params = (log_id,)
            row = self.db.fetch_one(query, params, commit=True)
        else:
            query = """
                UPDATE backfill_logs
                SET completed_at = CURRENT_TIMESTAMP,
                    status = 'failed'
                WHERE id = ?
            """
            params = (log_id,)
            self.db.execute(query, params)
            row = self.db.fetch_one("SELECT * FROM backfill_logs WHERE id = ?", (log_id,))

        return self._row_to_model(row) if row else None

    def get_by_mapping(self, mapping_id: int) -> list[BackfillLog]:
        """Get all backfill logs for a specific mapping."""
        query = """
            SELECT * FROM backfill_logs
            WHERE mapping_id = ?
            ORDER BY started_at DESC
        """
        rows = self.db.fetch_all(query, (mapping_id,))
        return [self._row_to_model(row) for row in rows]

    def _row_to_model(self, row: dict) -> BackfillLog:
        """Convert database row to model."""
        return BackfillLog(
            id=int(row.get("id", 0)),
            mapping_id=int(row.get("mapping_id", 0)),
            backfilled_count=int(row.get("backfilled_count", 0)),
            first_date=row.get("first_date"),
            last_date=row.get("last_date"),
            started_at=row.get("started_at"),
            completed_at=row.get("completed_at"),
            status=row.get("status", "completed"),
        )
