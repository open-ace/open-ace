"""
Project Category Repository

Issue #1278: Data access for project categories
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, List, Optional

from app.models.project_category import ProjectCategory
from app.repositories.database import Database

logger = logging.getLogger(__name__)


class ProjectCategoryRepository:
    """Repository for project category operations."""

    def __init__(self, db: Optional[Database] = None):
        self.db = db or Database()

    def list_categories(self, active_only: bool = True) -> List[ProjectCategory]:
        """List all project categories."""
        conditions = []
        if active_only:
            if self.db.is_postgresql:
                conditions.append("is_active IS TRUE")
            else:
                conditions.append("is_active = 1")

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"SELECT * FROM project_categories {where_clause} ORDER BY sort_order, name"

        results = self.db.fetch_all(query)
        return [ProjectCategory.from_dict(r) for r in results]

    def get_category(self, category_id: int) -> Optional[ProjectCategory]:
        """Get a single category by ID."""
        query = "SELECT * FROM project_categories WHERE id = ?"
        result = self.db.fetch_one(query, (category_id,))
        return ProjectCategory.from_dict(result) if result else None

    def create_category(
        self,
        name: str,
        key_patterns: List[str],
        sort_order: int = 0,
    ) -> Optional[int]:
        """Create a new category."""
        try:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            patterns_json = json.dumps(key_patterns)

            if self.db.is_postgresql:
                result = self.db.fetch_one(
                    """
                    INSERT INTO project_categories (name, key_patterns, sort_order, is_active, created_at, updated_at)
                    VALUES (?, ?, ?, TRUE, ?, ?)
                    RETURNING id
                    """,
                    (name, patterns_json, sort_order, now, now),
                    commit=True,
                )
                return result["id"] if result else None
            else:
                cursor = self.db.execute(
                    """
                    INSERT INTO project_categories (name, key_patterns, sort_order, is_active, created_at, updated_at)
                    VALUES (?, ?, ?, 1, ?, ?)
                    """,
                    (name, patterns_json, sort_order, now, now),
                )
                return cursor.lastrowid
        except Exception as e:
            logger.error(f"Error creating category: {e}")
            return None

    def update_category(
        self,
        category_id: int,
        name: Optional[str] = None,
        key_patterns: Optional[List[str]] = None,
        sort_order: Optional[int] = None,
        is_active: Optional[bool] = None,
    ) -> bool:
        """Update a category."""
        try:
            updates = []
            params: List[Any] = []

            if name is not None:
                updates.append("name = ?")
                params.append(name)
            if key_patterns is not None:
                updates.append("key_patterns = ?")
                params.append(json.dumps(key_patterns))
            if sort_order is not None:
                updates.append("sort_order = ?")
                params.append(sort_order)
            if is_active is not None:
                updates.append("is_active = ?")
                if self.db.is_postgresql:
                    params.append(is_active)
                else:
                    params.append(1 if is_active else 0)

            if not updates:
                return True

            updates.append("updated_at = ?")
            params.append(datetime.now(timezone.utc).replace(tzinfo=None))
            params.append(category_id)

            query = f"UPDATE project_categories SET {', '.join(updates)} WHERE id = ?"
            self.db.execute(query, tuple(params))
            return True
        except Exception as e:
            logger.error(f"Error updating category: {e}")
            return False

    def delete_category(self, category_id: int) -> bool:
        """Delete a category (soft delete)."""
        try:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            if self.db.is_postgresql:
                query = (
                    "UPDATE project_categories SET is_active = FALSE, updated_at = ? WHERE id = ?"
                )
            else:
                query = "UPDATE project_categories SET is_active = 0, updated_at = ? WHERE id = ?"
            self.db.execute(query, (now, category_id))
            return True
        except Exception as e:
            logger.error(f"Error deleting category: {e}")
            return False
