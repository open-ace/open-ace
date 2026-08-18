"""
Open ACE - Tool Account Conflict Repository

Repository for tool_account_conflicts table operations.

Issue #2761: Tracks conflicts between predeclared accounts and incoming data.
"""

import logging

from app.models.tool_account_conflict import ToolAccountConflict
from app.repositories.database import Database

logger = logging.getLogger(__name__)


class ToolAccountConflictRepository:
    """Repository for tool account conflict records."""

    def __init__(self, db: Database | None = None):
        self.db = db or Database()

    def create(
        self,
        mapping_id: int,
        conflict_type: str,
        expected_value: str | None = None,
        actual_value: str | None = None,
        details: str | None = None,
    ) -> ToolAccountConflict | None:
        """Create a new conflict record.

        Issue #2761: Records when a predeclared account receives conflicting data.
        """
        from app.repositories.database import is_postgresql

        if is_postgresql():
            query = """
                INSERT INTO tool_account_conflicts
                    (mapping_id, conflict_type, expected_value, actual_value, details, detected_at)
                VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                RETURNING *
            """
        else:
            query = """
                INSERT INTO tool_account_conflicts
                    (mapping_id, conflict_type, expected_value, actual_value, details, detected_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """

        params = (mapping_id, conflict_type, expected_value, actual_value, details)

        try:
            if is_postgresql():
                row = self.db.fetch_one(query, params, commit=True)
            else:
                self.db.execute(query, params)
                row = self.db.fetch_one(
                    """
                    SELECT * FROM tool_account_conflicts
                    WHERE mapping_id = ? AND conflict_type = ?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (mapping_id, conflict_type),
                )
            return self._row_to_model(row) if row else None
        except Exception as e:
            logger.error(f"Error creating conflict record: {e}")
            return None

    def get_unresolved(self, mapping_id: int | None = None) -> list[ToolAccountConflict]:
        """Get all unresolved conflicts.

        Issue #2761: Returns conflicts that need admin attention.
        """
        if mapping_id is not None:
            query = """
                SELECT * FROM tool_account_conflicts
                WHERE mapping_id = ? AND resolved_at IS NULL
                ORDER BY detected_at DESC
            """
            rows = self.db.fetch_all(query, (mapping_id,))
        else:
            query = """
                SELECT * FROM tool_account_conflicts
                WHERE resolved_at IS NULL
                ORDER BY detected_at DESC
            """
            rows = self.db.fetch_all(query)

        return [self._row_to_model(row) for row in rows]

    def resolve(
        self,
        conflict_id: int,
        resolved_by: int,
        resolution_action: str,
    ) -> ToolAccountConflict | None:
        """Mark a conflict as resolved.

        Issue #2761: Records who resolved the conflict and what action was taken.
        """
        from app.repositories.database import is_postgresql

        if is_postgresql():
            query = """
                UPDATE tool_account_conflicts
                SET resolved_at = CURRENT_TIMESTAMP,
                    resolved_by = %s,
                    resolution_action = %s
                WHERE id = %s AND resolved_at IS NULL
                RETURNING *
            """
            params = (resolved_by, resolution_action, conflict_id)
            row = self.db.fetch_one(query, params, commit=True)
        else:
            query = """
                UPDATE tool_account_conflicts
                SET resolved_at = CURRENT_TIMESTAMP,
                    resolved_by = ?,
                    resolution_action = ?
                WHERE id = ? AND resolved_at IS NULL
            """
            params = (resolved_by, resolution_action, conflict_id)
            self.db.execute(query, params)
            row = self.db.fetch_one(
                "SELECT * FROM tool_account_conflicts WHERE id = ?", (conflict_id,)
            )

        return self._row_to_model(row) if row else None

    def get_by_mapping(self, mapping_id: int) -> list[ToolAccountConflict]:
        """Get all conflicts for a specific mapping."""
        query = """
            SELECT * FROM tool_account_conflicts
            WHERE mapping_id = ?
            ORDER BY detected_at DESC
        """
        rows = self.db.fetch_all(query, (mapping_id,))
        return [self._row_to_model(row) for row in rows]

    def _row_to_model(self, row: dict) -> ToolAccountConflict:
        """Convert database row to model."""
        return ToolAccountConflict(
            id=int(row.get("id", 0)),
            mapping_id=int(row.get("mapping_id", 0)),
            conflict_type=row.get("conflict_type", ""),
            expected_value=row.get("expected_value"),
            actual_value=row.get("actual_value"),
            detected_at=row.get("detected_at"),
            resolved_at=row.get("resolved_at"),
            resolved_by=row.get("resolved_by"),
            resolution_action=row.get("resolution_action"),
            details=row.get("details"),
        )
