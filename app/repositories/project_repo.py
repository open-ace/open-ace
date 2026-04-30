#!/usr/bin/env python3
"""
Open ACE - Project Repository

Repository for project data access operations.
"""

import logging
from datetime import datetime
from typing import List, Optional

from app.models.project import Project, ProjectDailyStats, ProjectStats, UserProject
from app.repositories.database import Database

logger = logging.getLogger(__name__)


class ProjectRepository:
    """Repository for project data operations."""

    def __init__(self, db: Optional[Database] = None):
        """
        Initialize repository.

        Args:
            db: Optional Database instance for dependency injection.
        """
        self.db = db or Database()

    def create_project(
        self,
        path: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        created_by: Optional[int] = None,
        is_shared: bool = False,
    ) -> Optional[int]:
        """
        Create a new project.

        Args:
            path: Project absolute path.
            name: Project name (optional, defaults to path last segment).
            description: Project description.
            created_by: User ID of creator.
            is_shared: Whether project is shared for collaboration.

        Returns:
            Optional[int]: Project ID if successful, None otherwise.
        """
        try:
            now = datetime.utcnow()

            if self.db.is_postgresql:
                # PostgreSQL uses TRUE/FALSE for boolean columns
                result = self.db.fetch_one(
                    """
                    INSERT INTO projects (path, name, description, created_by, created_at,
                                          updated_at, is_active, is_shared)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    RETURNING id
                    """,
                    (path, name, description, created_by, now, now, True, is_shared),
                    commit=True,
                )
                project_id = result["id"] if result else None
            else:
                # SQLite uses 1/0 for boolean columns
                is_shared_int = 1 if is_shared else 0
                is_active_int = 1
                cursor = self.db.execute(
                    """
                    INSERT INTO projects (path, name, description, created_by, created_at,
                                          updated_at, is_active, is_shared)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (path, name, description, created_by, now, now, is_active_int, is_shared_int),
                )
                project_id = cursor.lastrowid

            # If creator is specified, add them to user_projects
            if project_id and created_by:
                self.add_user_project(created_by, project_id)

            return project_id
        except Exception as e:
            logger.error(f"Error creating project: {e}")
            return None

    def get_project_by_id(self, project_id: int) -> Optional[Project]:
        """
        Get project by ID.

        Args:
            project_id: Project ID.

        Returns:
            Optional[Project]: Project data or None.
        """
        query = "SELECT * FROM projects WHERE id = ? AND is_active IS TRUE"
        result = self.db.fetch_one(query, (project_id,))
        return Project.from_dict(result) if result else None

    def get_project_by_path(self, path: str) -> Optional[Project]:
        """
        Get project by path.

        Args:
            path: Project path.

        Returns:
            Optional[Project]: Project data or None.
        """
        query = "SELECT * FROM projects WHERE path = ? AND is_active IS TRUE"
        result = self.db.fetch_one(query, (path,))
        return Project.from_dict(result) if result else None

    def get_all_projects(
        self,
        include_inactive: bool = False,
        created_by: Optional[int] = None,
    ) -> List[Project]:
        """
        Get all projects.

        Args:
            include_inactive: Whether to include inactive projects.
            created_by: Filter by creator user ID.

        Returns:
            List[Project]: List of projects.
        """
        conditions = []
        params = []

        if not include_inactive:
            conditions.append("is_active IS TRUE")

        if created_by:
            conditions.append("created_by = ?")
            params.append(created_by)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        query = f"SELECT * FROM projects {where_clause} ORDER BY created_at DESC"
        results = self.db.fetch_all(query, tuple(params))
        return [Project.from_dict(r) for r in results]

    def get_user_projects(self, user_id: int) -> List[Project]:
        """
        Get projects accessible by a user.

        Args:
            user_id: User ID.

        Returns:
            List[Project]: List of projects the user can access.
        """
        query = """
            SELECT p.* FROM projects p
            INNER JOIN user_projects up ON p.id = up.project_id
            WHERE up.user_id = ? AND p.is_active IS TRUE
            ORDER BY up.last_access_at DESC
        """
        results = self.db.fetch_all(query, (user_id,))
        return [Project.from_dict(r) for r in results]

    def update_project(
        self,
        project_id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        is_shared: Optional[bool] = None,
    ) -> bool:
        """
        Update project information.

        Args:
            project_id: Project ID.
            name: New name.
            description: New description.
            is_shared: New shared status.

        Returns:
            bool: True if successful.
        """
        try:
            updates = []
            params = []

            if name is not None:
                updates.append("name = ?")
                params.append(name)

            if description is not None:
                updates.append("description = ?")
                params.append(description)

            if is_shared is not None:
                updates.append("is_shared = ?")
                # PostgreSQL uses boolean, SQLite uses integer
                if self.db.is_postgresql:
                    params.append(is_shared)
                else:
                    params.append(1 if is_shared else 0)

            if not updates:
                return True

            updates.append("updated_at = ?")
            params.append(datetime.utcnow())

            params.append(project_id)

            query = f"UPDATE projects SET {', '.join(updates)} WHERE id = ?"
            self.db.execute(query, tuple(params))
            return True
        except Exception as e:
            logger.error(f"Error updating project: {e}")
            return False

    def delete_project(self, project_id: int, soft_delete: bool = True) -> bool:
        """
        Delete a project.

        Args:
            project_id: Project ID.
            soft_delete: Whether to soft delete (mark as inactive).

        Returns:
            bool: True if successful.
        """
        try:
            if soft_delete:
                query = "UPDATE projects SET is_active = FALSE, updated_at = ? WHERE id = ?"
                self.db.execute(query, (datetime.utcnow(), project_id))
            else:
                # Hard delete - remove user_projects first
                self.db.execute("DELETE FROM user_projects WHERE project_id = ?", (project_id,))
                self.db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            return True
        except Exception as e:
            logger.error(f"Error deleting project: {e}")
            return False

    def add_user_project(
        self,
        user_id: int,
        project_id: int,
    ) -> Optional[int]:
        """
        Add a user-project relationship.

        Args:
            user_id: User ID.
            project_id: Project ID.

        Returns:
            Optional[int]: Relationship ID if successful.
        """
        try:
            now = datetime.utcnow()

            if self.db.is_postgresql:
                result = self.db.fetch_one(
                    """
                    INSERT INTO user_projects (user_id, project_id, first_access_at, last_access_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT (user_id, project_id) DO UPDATE SET last_access_at = ?
                    RETURNING id
                    """,
                    (user_id, project_id, now, now, now),
                    commit=True,
                )
                return result["id"] if result else None
            else:
                cursor = self.db.execute(
                    """
                    INSERT OR REPLACE INTO user_projects
                    (user_id, project_id, first_access_at, last_access_at,
                     total_sessions, total_tokens, total_requests, total_duration_seconds)
                    VALUES (?, ?, ?, ?,
                            COALESCE((SELECT total_sessions FROM user_projects
                                      WHERE user_id = ? AND project_id = ?), 0),
                            COALESCE((SELECT total_tokens FROM user_projects
                                      WHERE user_id = ? AND project_id = ?), 0),
                            COALESCE((SELECT total_requests FROM user_projects
                                      WHERE user_id = ? AND project_id = ?), 0),
                            COALESCE((SELECT total_duration_seconds FROM user_projects
                                      WHERE user_id = ? AND project_id = ?), 0))
                    """,
                    (
                        user_id,
                        project_id,
                        now,
                        now,
                        user_id,
                        project_id,
                        user_id,
                        project_id,
                        user_id,
                        project_id,
                        user_id,
                        project_id,
                    ),
                )
                return cursor.lastrowid
        except Exception as e:
            logger.error(f"Error adding user project: {e}")
            return None

    def update_user_project_stats(
        self,
        user_id: int,
        project_id: int,
        sessions_delta: int = 0,
        tokens_delta: int = 0,
        requests_delta: int = 0,
        duration_delta: int = 0,
    ) -> bool:
        """
        Update user-project statistics.

        Args:
            user_id: User ID.
            project_id: Project ID.
            sessions_delta: Sessions increment.
            tokens_delta: Tokens increment.
            requests_delta: Requests increment.
            duration_delta: Duration increment (seconds).

        Returns:
            bool: True if successful.
        """
        try:
            query = """
                UPDATE user_projects SET
                    last_access_at = ?,
                    total_sessions = total_sessions + ?,
                    total_tokens = total_tokens + ?,
                    total_requests = total_requests + ?,
                    total_duration_seconds = total_duration_seconds + ?
                WHERE user_id = ? AND project_id = ?
            """
            self.db.execute(
                query,
                (
                    datetime.utcnow(),
                    sessions_delta,
                    tokens_delta,
                    requests_delta,
                    duration_delta,
                    user_id,
                    project_id,
                ),
            )
            return True
        except Exception as e:
            logger.error(f"Error updating user project stats: {e}")
            return False

    def get_user_project(self, user_id: int, project_id: int) -> Optional[UserProject]:
        """
        Get user-project relationship.

        Args:
            user_id: User ID.
            project_id: Project ID.

        Returns:
            Optional[UserProject]: Relationship data or None.
        """
        query = "SELECT * FROM user_projects WHERE user_id = ? AND project_id = ?"
        result = self.db.fetch_one(query, (user_id, project_id))
        return UserProject.from_dict(result) if result else None

    def get_project_users(self, project_id: int) -> List[UserProject]:
        """
        Get all users associated with a project.

        Args:
            project_id: Project ID.

        Returns:
            List[UserProject]: List of user-project relationships.
        """
        query = """
            SELECT up.*, u.username
            FROM user_projects up
            LEFT JOIN users u ON up.user_id = u.id
            WHERE up.project_id = ?
            ORDER BY up.last_access_at DESC
        """
        results = self.db.fetch_all(query, (project_id,))
        return [UserProject.from_dict(r) for r in results]

    def get_project_stats(self, project_id: int) -> Optional[ProjectStats]:
        """
        Get aggregated statistics for a project.

        Args:
            project_id: Project ID.

        Returns:
            Optional[ProjectStats]: Project statistics or None.
        """
        # Get project info
        project = self.get_project_by_id(project_id)
        if not project:
            return None

        # Aggregate user_projects data
        query = """
            SELECT
                COUNT(*) as total_users,
                SUM(total_sessions) as total_sessions,
                SUM(total_tokens) as total_tokens,
                SUM(total_requests) as total_requests,
                SUM(total_duration_seconds) as total_duration_seconds,
                MIN(first_access_at) as first_access,
                MAX(last_access_at) as last_access
            FROM user_projects
            WHERE project_id = ?
        """
        result = self.db.fetch_one(query, (project_id,))

        # Get user-level stats
        user_stats = self.get_project_users(project_id)

        return ProjectStats(
            project_id=project_id,
            project_path=project.path,
            project_name=project.name,
            total_users=result.get("total_users", 0) or 0,
            total_sessions=result.get("total_sessions", 0) or 0,
            total_tokens=int(result.get("total_tokens", 0) or 0),
            total_requests=int(result.get("total_requests", 0) or 0),
            total_duration_seconds=int(result.get("total_duration_seconds", 0) or 0),
            first_access=(
                datetime.fromisoformat(result["first_access"])
                if result.get("first_access")
                else None
            ),
            last_access=(
                datetime.fromisoformat(result["last_access"]) if result.get("last_access") else None
            ),
            user_stats=user_stats,
        )

    def get_all_project_stats(self) -> List[ProjectStats]:
        """
        Get statistics for all active projects.

        Returns:
            List[ProjectStats]: List of project statistics.
        """
        query = """
            SELECT
                p.id as project_id,
                p.path as project_path,
                p.name as project_name,
                p.is_shared,
                COUNT(up.id) as total_users,
                COALESCE(SUM(up.total_sessions), 0) as total_sessions,
                COALESCE(SUM(up.total_tokens), 0) as total_tokens,
                COALESCE(SUM(up.total_requests), 0) as total_requests,
                COALESCE(SUM(up.total_duration_seconds), 0) as total_duration_seconds,
                MIN(up.first_access_at) as first_access,
                MAX(up.last_access_at) as last_access
            FROM projects p
            LEFT JOIN user_projects up ON p.id = up.project_id
            WHERE p.is_active IS TRUE
            GROUP BY p.id
            ORDER BY total_tokens DESC
        """
        results = self.db.fetch_all(query)

        stats_list = []
        for r in results:
            project_id = r["project_id"]
            user_stats = self.get_project_users(project_id)

            def parse_datetime(value):
                if value is None:
                    return None
                if isinstance(value, datetime):
                    return value
                if isinstance(value, str):
                    return datetime.fromisoformat(value)
                return None

            stats_list.append(
                ProjectStats(
                    project_id=project_id,
                    project_path=r["project_path"],
                    project_name=r["project_name"],
                    total_users=r.get("total_users", 0) or 0,
                    total_sessions=r.get("total_sessions", 0) or 0,
                    total_tokens=int(r.get("total_tokens", 0) or 0),
                    total_requests=int(r.get("total_requests", 0) or 0),
                    total_duration_seconds=int(r.get("total_duration_seconds", 0) or 0),
                    first_access=parse_datetime(r.get("first_access")),
                    last_access=parse_datetime(r.get("last_access")),
                    user_stats=user_stats,
                )
            )

        return stats_list

    def get_project_daily_stats(
        self,
        project_id: int,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[ProjectDailyStats]:
        """
        Get daily statistics for a project from daily_stats table.

        Args:
            project_id: Project ID.
            start_date: Optional start date.
            end_date: Optional end date.

        Returns:
            List[ProjectDailyStats]: List of daily statistics.
        """
        conditions = ["project_id = ?"]
        params = [project_id]

        if start_date:
            conditions.append("date >= ?")
            params.append(start_date)

        if end_date:
            conditions.append("date <= ?")
            params.append(end_date)

        where_clause = f"WHERE {' AND '.join(conditions)}"

        query = f"""
            SELECT
                date,
                project_id,
                project_path,
                SUM(total_tokens) as total_tokens,
                SUM(total_input_tokens) as total_input_tokens,
                SUM(total_output_tokens) as total_output_tokens,
                SUM(message_count) as total_requests,
                COUNT(DISTINCT sender_name) as active_users
            FROM daily_stats
            {where_clause}
            GROUP BY date
            ORDER BY date ASC
        """
        results = self.db.fetch_all(query, tuple(params))

        return [
            ProjectDailyStats(
                date=r["date"],
                project_id=r["project_id"],
                project_path=r.get("project_path", ""),
                total_tokens=int(r.get("total_tokens", 0) or 0),
                total_input_tokens=int(r.get("total_input_tokens", 0) or 0),
                total_output_tokens=int(r.get("total_output_tokens", 0) or 0),
                total_requests=int(r.get("total_requests", 0) or 0),
                active_users=int(r.get("active_users", 0) or 0),
            )
            for r in results
        ]
