#!/usr/bin/env python3
"""
Open ACE - User Tool Account Repository

Repository for user_tool_accounts table operations.
"""

import logging
from typing import Dict, List, Optional

from app.models.user_tool_account import UserToolAccount
from app.repositories.database import Database

logger = logging.getLogger(__name__)


class UserToolAccountRepository:
    """Repository for user tool account mappings."""

    def __init__(self, db: Optional[Database] = None):
        self.db = db or Database()

    def get_all(self) -> List[UserToolAccount]:
        """Get all tool account mappings."""
        query = """
            SELECT * FROM user_tool_accounts
            ORDER BY user_id, tool_type, tool_account
        """
        rows = self.db.fetch_all(query)
        return [self._row_to_model(row) for row in rows]

    def get_by_user_id(self, user_id: int) -> List[UserToolAccount]:
        """Get all tool accounts for a specific user."""
        query = """
            SELECT * FROM user_tool_accounts
            WHERE user_id = ?
            ORDER BY tool_type, tool_account
        """
        rows = self.db.fetch_all(query, (user_id,))
        return [self._row_to_model(row) for row in rows]

    def get_by_tool_account(self, tool_account: str) -> Optional[UserToolAccount]:
        """Get mapping by tool account name."""
        query = "SELECT * FROM user_tool_accounts WHERE tool_account = ?"
        row = self.db.fetch_one(query, (tool_account,))
        return self._row_to_model(row) if row else None

    def get_unmapped_tool_accounts(self) -> List[Dict]:
        """Get sender_names from daily_messages that are not mapped to any user."""
        query = """
            SELECT DISTINCT dm.sender_name,
                   COUNT(*) as message_count,
                   MIN(dm.date) as first_date,
                   MAX(dm.date) as last_date
            FROM daily_messages dm
            WHERE dm.sender_name IS NOT NULL
              AND dm.sender_name != ''
              AND NOT EXISTS (
                  SELECT 1 FROM user_tool_accounts uta
                  WHERE uta.tool_account = dm.sender_name
              )
              AND NOT EXISTS (
                  SELECT 1 FROM users u
                  WHERE u.username = dm.sender_name
              )
            GROUP BY dm.sender_name
            ORDER BY message_count DESC
        """
        return self.db.fetch_all(query)

    def create(
        self,
        user_id: int,
        tool_account: str,
        tool_type: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Optional[UserToolAccount]:
        """Create a new tool account mapping."""
        from app.repositories.database import is_postgresql

        if is_postgresql():
            query = """
                INSERT INTO user_tool_accounts (user_id, tool_account, tool_type, description)
                VALUES (%s, %s, %s, %s)
                RETURNING *
            """
        else:
            query = """
                INSERT INTO user_tool_accounts (user_id, tool_account, tool_type, description)
                VALUES (?, ?, ?, ?)
            """

        params = (user_id, tool_account, tool_type, description)

        try:
            if is_postgresql():
                row = self.db.fetch_one(query, params)
            else:
                self.db.execute(query, params)
                row = self.db.fetch_one(
                    "SELECT * FROM user_tool_accounts WHERE tool_account = ?", (tool_account,)
                )
            return self._row_to_model(row) if row else None
        except Exception as e:
            logger.error(f"Error creating tool account mapping: {e}")
            return None

    def update(
        self,
        id: int,
        user_id: Optional[int] = None,
        tool_account: Optional[str] = None,
        tool_type: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Optional[UserToolAccount]:
        """Update a tool account mapping."""
        updates = []
        params = []

        if user_id is not None:
            updates.append("user_id = ?")
            params.append(user_id)
        if tool_account is not None:
            updates.append("tool_account = ?")
            params.append(tool_account)
        if tool_type is not None:
            updates.append("tool_type = ?")
            params.append(tool_type)
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
                UPDATE user_tool_accounts
                SET {', '.join(updates)}
                WHERE id = %s
                RETURNING *
            """
            row = self.db.fetch_one(query, tuple(params))
        else:
            query = f"""
                UPDATE user_tool_accounts
                SET {', '.join(updates)}
                WHERE id = ?
            """
            self.db.execute(query, tuple(params))
            row = self.db.fetch_one("SELECT * FROM user_tool_accounts WHERE id = ?", (id,))

        return self._row_to_model(row) if row else None

    def delete(self, id: int) -> bool:
        """Delete a tool account mapping."""
        query = "DELETE FROM user_tool_accounts WHERE id = ?"
        try:
            self.db.execute(query, (id,))
            return True
        except Exception as e:
            logger.error(f"Error deleting tool account mapping: {e}")
            return False

    def get_by_id(self, id: int) -> Optional[UserToolAccount]:
        """Get mapping by ID."""
        query = "SELECT * FROM user_tool_accounts WHERE id = ?"
        row = self.db.fetch_one(query, (id,))
        return self._row_to_model(row) if row else None

    def _row_to_model(self, row: Dict) -> UserToolAccount:
        """Convert database row to model."""
        return UserToolAccount(
            id=row.get("id"),
            user_id=row.get("user_id"),
            tool_account=row.get("tool_account"),
            tool_type=row.get("tool_type"),
            description=row.get("description"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    def update_daily_messages_user_id(self, tool_account: str, user_id: int) -> int:
        """Update user_id in daily_messages for a specific tool_account."""
        query = """
            UPDATE daily_messages
            SET user_id = ?
            WHERE sender_name = ? AND user_id IS NULL
        """
        from app.repositories.database import is_postgresql

        if is_postgresql():
            query = """
                UPDATE daily_messages
                SET user_id = %s
                WHERE sender_name = %s AND user_id IS NULL
            """

        try:
            result = self.db.execute(query, (user_id, tool_account))
            return result if isinstance(result, int) else 0
        except Exception as e:
            logger.error(f"Error updating daily_messages user_id: {e}")
            return 0

    def batch_create_for_user(
        self, user_id: int, tool_accounts: List[Dict]
    ) -> List[UserToolAccount]:
        """Batch create tool account mappings for a user."""
        results = []
        for account in tool_accounts:
            mapping = self.create(
                user_id=user_id,
                tool_account=account.get("tool_account"),
                tool_type=account.get("tool_type"),
                description=account.get("description"),
            )
            if mapping:
                # Update daily_messages user_id
                self.update_daily_messages_user_id(mapping.tool_account, user_id)
                results.append(mapping)
        return results
