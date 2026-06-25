"""
Business Project Repository

Issue #871: Data access layer for business projects
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.repositories.base import BaseRepository, DatabaseConnection
from app.utils.db_utils import get_db_connection, is_postgresql


class BusinessProjectRepository(BaseRepository):
    """Repository for business project operations."""

    def _get_connection(self) -> DatabaseConnection:
        """Get database connection."""
        return get_db_connection()

    def _json_dumps(self, data: Any) -> Optional[str]:
        """Serialize data to JSON string."""
        if data is None:
            return None
        return json.dumps(data) if isinstance(data, (list, dict)) else str(data)

    def _json_loads(self, data: Optional[str], default: Any = None) -> Any:
        """Deserialize JSON string."""
        if data is None:
            return default or []
        try:
            return json.loads(data) if data else (default or [])
        except json.JSONDecodeError:
            return default or []

    def list_projects(
        self,
        include_deleted: bool = False,
        active_only: bool = True,
    ) -> List[Dict[str, Any]]:
        """List all business projects."""
        conn = self._get_connection()
        try:
            if is_postgresql():
                sql = """
                    SELECT bp.*, u.username as created_by_username
                    FROM business_projects bp
                    LEFT JOIN users u ON bp.created_by = u.id
                    WHERE 1=1
                """
                if not include_deleted:
                    sql += " AND bp.deleted_at IS NULL"
                if active_only:
                    sql += " AND bp.is_active = true"
                sql += " ORDER BY bp.name"
            else:
                sql = """
                    SELECT bp.*, u.username as created_by_username
                    FROM business_projects bp
                    LEFT JOIN users u ON bp.created_by = u.id
                    WHERE 1=1
                """
                if not include_deleted:
                    sql += " AND bp.deleted_at IS NULL"
                if active_only:
                    sql += " AND bp.is_active = 1"
                sql += " ORDER BY bp.name"

            rows = conn.execute(sql).fetchall()
            return [self._row_to_dict(row) for row in rows]
        finally:
            conn.close()

    def get_project(self, project_id: int) -> Optional[Dict[str, Any]]:
        """Get a single business project by ID."""
        conn = self._get_connection()
        try:
            sql = """
                SELECT bp.*, u.username as created_by_username
                FROM business_projects bp
                LEFT JOIN users u ON bp.created_by = u.id
                WHERE bp.id = ?
            """
            row = conn.execute(sql, (project_id,)).fetchone()
            return self._row_to_dict(row) if row else None
        finally:
            conn.close()

    def get_project_by_code(self, code: str) -> Optional[Dict[str, Any]]:
        """Get a business project by code."""
        conn = self._get_connection()
        try:
            sql = """
                SELECT bp.*, u.username as created_by_username
                FROM business_projects bp
                LEFT JOIN users u ON bp.created_by = u.id
                WHERE bp.code = ?
            """
            row = conn.execute(sql, (code,)).fetchone()
            return self._row_to_dict(row) if row else None
        finally:
            conn.close()

    def create_project(
        self,
        name: str,
        code: str,
        description: Optional[str] = None,
        key_patterns: Optional[List[str]] = None,
        created_by: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Create a new business project."""
        conn = self._get_connection()
        try:
            now = datetime.utcnow()
            patterns_json = self._json_dumps(key_patterns or [])

            if is_postgresql():
                sql = """
                    INSERT INTO business_projects
                    (name, code, description, key_patterns, is_active, created_by, created_at, updated_at)
                    VALUES (?, ?, ?, ?, true, ?, ?, ?)
                    RETURNING id
                """
                result = conn.execute(
                    sql, (name, code, description, patterns_json, created_by, now, now)
                ).fetchone()
                project_id = result["id"]
                conn.commit()
            else:
                sql = """
                    INSERT INTO business_projects
                    (name, code, description, key_patterns, is_active, created_by, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                """
                cursor = conn.execute(
                    sql, (name, code, description, patterns_json, created_by, now, now)
                )
                project_id = cursor.lastrowid
                conn.commit()

            return self.get_project(project_id)
        finally:
            conn.close()

    def update_project(
        self,
        project_id: int,
        name: Optional[str] = None,
        code: Optional[str] = None,
        description: Optional[str] = None,
        key_patterns: Optional[List[str]] = None,
        is_active: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update a business project."""
        conn = self._get_connection()
        try:
            updates = []
            params = []

            if name is not None:
                updates.append("name = ?")
                params.append(name)
            if code is not None:
                updates.append("code = ?")
                params.append(code)
            if description is not None:
                updates.append("description = ?")
                params.append(description)
            if key_patterns is not None:
                updates.append("key_patterns = ?")
                params.append(self._json_dumps(key_patterns))
            if is_active is not None:
                if is_postgresql():
                    updates.append("is_active = ?")
                    params.append(is_active)
                else:
                    updates.append("is_active = ?")
                    params.append(1 if is_active else 0)

            if not updates:
                return self.get_project(project_id)

            updates.append("updated_at = ?")
            params.append(datetime.utcnow())
            params.append(project_id)

            sql = f"UPDATE business_projects SET {', '.join(updates)} WHERE id = ?"
            conn.execute(sql, params)
            conn.commit()

            return self.get_project(project_id)
        finally:
            conn.close()

    def delete_project(self, project_id: int) -> bool:
        """Soft delete a business project."""
        conn = self._get_connection()
        try:
            sql = "UPDATE business_projects SET deleted_at = ?, updated_at = ? WHERE id = ?"
            now = datetime.utcnow()
            conn.execute(sql, (now, now, project_id))
            conn.commit()
            return True
        finally:
            conn.close()

    def get_members(self, project_id: int) -> List[Dict[str, Any]]:
        """Get members of a business project."""
        conn = self._get_connection()
        try:
            sql = """
                SELECT bpm.*, u.username
                FROM business_project_members bpm
                JOIN users u ON bpm.user_id = u.id
                WHERE bpm.business_project_id = ?
                ORDER BY bpm.added_at DESC
            """
            rows = conn.execute(sql, (project_id,)).fetchall()
            return [self._row_to_dict(row) for row in rows]
        finally:
            conn.close()

    def add_member(self, project_id: int, user_id: int) -> Dict[str, Any]:
        """Add a member to a business project."""
        conn = self._get_connection()
        try:
            now = datetime.utcnow()

            if is_postgresql():
                sql = """
                    INSERT INTO business_project_members
                    (business_project_id, user_id, added_at)
                    VALUES (?, ?, ?)
                    RETURNING id
                """
                result = conn.execute(sql, (project_id, user_id, now)).fetchone()
                member_id = result["id"]
                conn.commit()
            else:
                sql = """
                    INSERT INTO business_project_members
                    (business_project_id, user_id, added_at)
                    VALUES (?, ?, ?)
                """
                cursor = conn.execute(sql, (project_id, user_id, now))
                member_id = cursor.lastrowid
                conn.commit()

            sql = """
                SELECT bpm.*, u.username
                FROM business_project_members bpm
                JOIN users u ON bpm.user_id = u.id
                WHERE bpm.id = ?
            """
            row = conn.execute(sql, (member_id,)).fetchone()
            return self._row_to_dict(row)
        finally:
            conn.close()

    def remove_member(self, project_id: int, member_id: int) -> bool:
        """Remove a member from a business project."""
        conn = self._get_connection()
        try:
            sql = """
                DELETE FROM business_project_members
                WHERE id = ? AND business_project_id = ?
            """
            conn.execute(sql, (member_id, project_id))
            conn.commit()
            return True
        finally:
            conn.close()

    def get_project_stats(self, project_id: int) -> Optional[Dict[str, Any]]:
        """Get statistics for a business project."""
        conn = self._get_connection()
        try:
            sql = """
                SELECT
                    bp.id as business_project_id,
                    bp.name as project_name,
                    bp.code as project_code,
                    COUNT(DISTINCT p.id) as total_workspaces,
                    COALESCE(SUM(up.total_tokens), 0) as total_tokens,
                    COALESCE(SUM(up.total_requests), 0) as total_requests,
                    COALESCE(SUM(up.total_duration_seconds), 0) as total_duration_seconds,
                    MIN(up.first_access_at) as first_access,
                    MAX(up.last_access_at) as last_access
                FROM business_projects bp
                LEFT JOIN projects p ON p.business_project_id = bp.id
                LEFT JOIN user_projects up ON up.project_id = p.id
                WHERE bp.id = ?
                GROUP BY bp.id, bp.name, bp.code
            """
            row = conn.execute(sql, (project_id,)).fetchone()
            return self._row_to_dict(row) if row else None
        finally:
            conn.close()

    def match_workspace_to_project(self, project_path: str) -> Optional[int]:
        """Match a workspace path to a business project based on key patterns."""
        conn = self._get_connection()
        try:
            if is_postgresql():
                sql = """
                    SELECT id, key_patterns
                    FROM business_projects
                    WHERE is_active = true AND deleted_at IS NULL AND key_patterns IS NOT NULL
                """
            else:
                sql = """
                    SELECT id, key_patterns
                    FROM business_projects
                    WHERE is_active = 1 AND deleted_at IS NULL AND key_patterns IS NOT NULL
                """

            rows = conn.execute(sql).fetchall()
            for row in rows:
                patterns = self._json_loads(row["key_patterns"])
                for pattern in patterns:
                    if pattern and pattern.lower() in project_path.lower():
                        return row["id"]
            return None
        finally:
            conn.close()

    def _row_to_dict(self, row: Any) -> Dict[str, Any]:
        """Convert database row to dictionary."""
        if row is None:
            return {}
        if hasattr(row, "keys"):
            return dict(row)
        return {}
