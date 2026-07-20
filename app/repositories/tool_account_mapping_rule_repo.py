"""
Open ACE - Tool Account Mapping Rule Repository

Repository for tool_account_mapping_rules table operations.
"""

import logging
from typing import Any

from app.models.tool_account_mapping_rule import ToolAccountMappingRule
from app.repositories.database import Database, adapt_boolean_condition

logger = logging.getLogger(__name__)


class ToolAccountMappingRuleRepository:
    """Repository for tool account mapping rules."""

    def __init__(self, db: Database | None = None):
        self.db = db or Database()

    def get_all(self) -> list[ToolAccountMappingRule]:
        """Get all mapping rules."""
        query = """
            SELECT * FROM tool_account_mapping_rules
            ORDER BY priority DESC, user_id, pattern
        """
        rows = self.db.fetch_all(query)
        return [self._row_to_model(row) for row in rows]

    def get_active_rules(self) -> list[ToolAccountMappingRule]:
        """Get all active rules ordered by priority."""
        query = f"""
            SELECT * FROM tool_account_mapping_rules
            WHERE {adapt_boolean_condition('is_active', True)}
            ORDER BY priority DESC, user_id, pattern
        """
        rows = self.db.fetch_all(query)
        return [self._row_to_model(row) for row in rows]

    def get_auto_rules(self) -> list[ToolAccountMappingRule]:
        """Get all rules that can be auto-applied."""
        query = f"""
            SELECT * FROM tool_account_mapping_rules
            WHERE {adapt_boolean_condition('is_active', True)}
              AND {adapt_boolean_condition('is_auto', True)}
            ORDER BY priority DESC, user_id, pattern
        """
        rows = self.db.fetch_all(query)
        return [self._row_to_model(row) for row in rows]

    def get_by_user_id(self, user_id: int) -> list[ToolAccountMappingRule]:
        """Get all rules for a specific user."""
        query = """
            SELECT * FROM tool_account_mapping_rules
            WHERE user_id = ?
            ORDER BY priority DESC, pattern
        """
        rows = self.db.fetch_all(query, (user_id,))
        return [self._row_to_model(row) for row in rows]

    def get_by_id(self, id: int) -> ToolAccountMappingRule | None:
        """Get rule by ID."""
        query = "SELECT * FROM tool_account_mapping_rules WHERE id = ?"
        row = self.db.fetch_one(query, (id,))
        return self._row_to_model(row) if row else None

    def create(
        self,
        user_id: int,
        pattern: str,
        match_type: str = "exact",
        tool_type: str | None = None,
        priority: int = 0,
        is_auto: bool = True,
        is_active: bool = True,
        description: str | None = None,
    ) -> ToolAccountMappingRule | None:
        """Create a new mapping rule."""
        from app.repositories.database import is_postgresql

        if is_postgresql():
            query = """
                INSERT INTO tool_account_mapping_rules
                (user_id, pattern, match_type, tool_type, priority, is_auto, is_active, description)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
            """
        else:
            query = """
                INSERT INTO tool_account_mapping_rules
                (user_id, pattern, match_type, tool_type, priority, is_auto, is_active, description)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """

        params = (
            user_id,
            pattern,
            match_type,
            tool_type,
            priority,
            is_auto,
            is_active,
            description,
        )

        try:
            if is_postgresql():
                row = self.db.fetch_one(query, params, commit=True)
            else:
                self.db.execute(query, params)
                row = self.db.fetch_one(
                    "SELECT * FROM tool_account_mapping_rules "
                    "WHERE user_id = ? AND pattern = ? AND match_type = ?",
                    (user_id, pattern, match_type),
                )
            return self._row_to_model(row) if row else None
        except Exception as e:
            logger.error(f"Error creating mapping rule: {e}")
            return None

    def update(
        self,
        id: int,
        user_id: int | None = None,
        pattern: str | None = None,
        match_type: str | None = None,
        tool_type: str | None = None,
        priority: int | None = None,
        is_auto: bool | None = None,
        is_active: bool | None = None,
        description: str | None = None,
    ) -> ToolAccountMappingRule | None:
        """Update a mapping rule."""
        updates = []
        params: list[Any] = []

        if user_id is not None:
            updates.append("user_id = ?")
            params.append(user_id)
        if pattern is not None:
            updates.append("pattern = ?")
            params.append(pattern)
        if match_type is not None:
            updates.append("match_type = ?")
            params.append(match_type)
        if tool_type is not None:
            updates.append("tool_type = ?")
            params.append(tool_type)
        if priority is not None:
            updates.append("priority = ?")
            params.append(priority)
        if is_auto is not None:
            updates.append("is_auto = ?")
            params.append(is_auto)
        if is_active is not None:
            updates.append("is_active = ?")
            params.append(is_active)
        if description is not None:
            updates.append("description = ?")
            params.append(description)

        if not updates:
            return self.get_by_id(id)

        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(id)

        from app.repositories.database import is_postgresql

        if is_postgresql():
            query = f"""
                UPDATE tool_account_mapping_rules
                SET {", ".join(updates)}
                WHERE id = %s
                RETURNING *
            """
            row = self.db.fetch_one(query, tuple(params), commit=True)
        else:
            query = f"""
                UPDATE tool_account_mapping_rules
                SET {", ".join(updates)}
                WHERE id = ?
            """
            self.db.execute(query, tuple(params))
            row = self.db.fetch_one("SELECT * FROM tool_account_mapping_rules WHERE id = ?", (id,))

        return self._row_to_model(row) if row else None

    def delete(self, id: int) -> bool:
        """Delete a mapping rule."""
        query = "DELETE FROM tool_account_mapping_rules WHERE id = ?"
        try:
            self.db.execute(query, (id,))
            return True
        except Exception as e:
            logger.error(f"Error deleting mapping rule: {e}")
            return False

    def _row_to_model(self, row: dict) -> ToolAccountMappingRule:
        """Convert database row to model."""
        return ToolAccountMappingRule(
            id=int(row.get("id", 0)),
            user_id=int(row.get("user_id", 0)),
            pattern=str(row.get("pattern", "")),
            match_type=row.get("match_type", "exact"),
            tool_type=row.get("tool_type"),
            priority=int(row.get("priority", 0)),
            is_auto=bool(row.get("is_auto", True)),
            is_active=bool(row.get("is_active", True)),
            description=row.get("description"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    def batch_create_for_user(
        self, user_id: int, rules: list[dict]
    ) -> list[ToolAccountMappingRule]:
        """Batch create rules for a user."""
        results = []
        for rule in rules:
            created = self.create(
                user_id=user_id,
                pattern=rule.get("pattern", ""),
                match_type=rule.get("match_type", "exact"),
                tool_type=rule.get("tool_type"),
                priority=rule.get("priority", 0),
                is_auto=rule.get("is_auto", True),
                is_active=rule.get("is_active", True),
                description=rule.get("description"),
            )
            if created:
                results.append(created)
        return results
