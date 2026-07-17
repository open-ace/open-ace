"""Unit tests for ProjectRepository.

Note: SQL string assertions verify key query structure. See issue #525 for
integration test plans.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.repositories.project_repo import ProjectRepository


class TestProjectRepository:
    """Tests for ProjectRepository."""

    def setup_method(self):
        self.db = MagicMock()
        self.db.is_postgresql = False
        self.repo = ProjectRepository(db=self.db)

    # -------------------------------------------------------------------------
    # create_project
    # -------------------------------------------------------------------------

    def test_create_project_sqlite(self):
        # First fetch_one checks for soft-deleted project (returns None)
        self.db.fetch_one.return_value = None
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 1
        self.db.execute.return_value = mock_cursor

        result = self.repo.create_project(
            path="/home/user/project1",
            name="Project One",
            description="Test project",
            created_by=5,
            is_shared=False,
            tenant_id=1,
        )
        assert result == 1
        # create_project also calls add_user_project when created_by is set
        assert self.db.execute.call_count == 2

    def test_create_project_sqlite_auto_adds_user_project(self):
        """When created_by is specified, add_user_project should be called."""
        # First fetch_one checks for soft-deleted project (returns None)
        self.db.fetch_one.return_value = None
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 10
        self.db.execute.return_value = mock_cursor

        with patch.object(self.repo, "add_user_project", return_value=100) as mock_add:
            result = self.repo.create_project(path="/test", name="T", created_by=5, tenant_id=1)
        assert result == 10
        mock_add.assert_called_once_with(5, 10)

    def test_create_project_no_creator(self):
        """Without created_by, add_user_project should NOT be called."""
        # First fetch_one checks for soft-deleted project (returns None)
        self.db.fetch_one.return_value = None
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 2
        self.db.execute.return_value = mock_cursor

        with patch.object(self.repo, "add_user_project") as mock_add:
            result = self.repo.create_project(path="/test", name="T")
        assert result == 2
        mock_add.assert_not_called()

    def test_create_project_postgresql(self):
        self.db.is_postgresql = True
        # First fetch_one checks for soft-deleted project (returns None)
        # Second fetch_one returns the new project ID
        self.db.fetch_one.side_effect = [None, {"id": 3}]

        with patch.object(self.repo, "add_user_project", return_value=1):
            result = self.repo.create_project(
                path="/test/pg", name="PG", created_by=1, tenant_id=1
            )
        assert result == 3
        # fetch_one called twice: check soft-deleted, then insert with RETURNING
        assert self.db.fetch_one.call_count == 2
        # Check that second call has RETURNING id
        second_call_query = self.db.fetch_one.call_args_list[1][0][0]
        assert "RETURNING id" in second_call_query

    def test_create_project_exception(self):
        self.db.execute.side_effect = Exception("DB error")
        result = self.repo.create_project(path="/test")
        assert result is None

    def test_create_project_restore_soft_deleted_sqlite(self):
        """Test restoring a soft-deleted project (SQLite)."""
        # First fetch_one finds soft-deleted project
        self.db.fetch_one.return_value = {"id": 5}
        mock_cursor = MagicMock()
        self.db.execute.return_value = mock_cursor

        with patch.object(self.repo, "add_user_project", return_value=50) as mock_add:
            result = self.repo.create_project(
                path="/test/restored",
                name="Restored Project",
                description="Restored desc",
                created_by=10,
                tenant_id=1,
            )

        # Should return the restored project ID
        assert result == 5
        # Should call add_user_project for restored project
        mock_add.assert_called_once_with(10, 5)
        # Should call execute for UPDATE
        assert self.db.execute.call_count == 1

    def test_create_project_restore_soft_deleted_postgresql(self):
        """Test restoring a soft-deleted project (PostgreSQL)."""
        self.db.is_postgresql = True
        # First fetch_one finds soft-deleted project
        self.db.fetch_one.return_value = {"id": 7}

        with patch.object(self.repo, "add_user_project", return_value=70) as mock_add:
            result = self.repo.create_project(
                path="/test/pg-restored",
                name="PG Restored",
                created_by=12,
                tenant_id=1,
            )

        # Should return the restored project ID
        assert result == 7
        # Should call add_user_project for restored project
        mock_add.assert_called_once_with(12, 7)

    # -------------------------------------------------------------------------
    # get_project_by_id
    # -------------------------------------------------------------------------

    def test_get_project_by_id_found(self):
        self.db.fetch_one.return_value = {
            "id": 1,
            "tenant_id": 1,
            "path": "/test",
            "name": "Test",
            "is_active": True,
        }
        result = self.repo.get_project_by_id(1)
        assert result is not None
        assert result.path == "/test"
        self.db.fetch_one.assert_called_once()
        assert "WHERE id = ?" in self.db.fetch_one.call_args[0][0]

    def test_get_project_by_id_not_found(self):
        self.db.fetch_one.return_value = None
        result = self.repo.get_project_by_id(999)
        assert result is None

    # -------------------------------------------------------------------------
    # get_project_by_path
    # -------------------------------------------------------------------------

    def test_get_project_by_path_found(self):
        self.db.fetch_one.return_value = {
            "id": 1,
            "tenant_id": 1,
            "path": "/home/user/proj",
            "name": "Proj",
        }
        result = self.repo.get_project_by_path("/home/user/proj")
        assert result is not None
        assert result.path == "/home/user/proj"

    def test_get_project_by_path_not_found(self):
        self.db.fetch_one.return_value = None
        result = self.repo.get_project_by_path("/nonexistent")
        assert result is None

    # -------------------------------------------------------------------------
    # get_all_projects
    # -------------------------------------------------------------------------

    def test_get_all_projects_default(self):
        self.db.fetch_all.return_value = [
            {"id": 1, "path": "/a", "name": "A"},
            {"id": 2, "path": "/b", "name": "B"},
        ]
        result = self.repo.get_all_projects()
        assert len(result) == 2
        query = self.db.fetch_all.call_args[0][0]
        assert "is_active IS TRUE" in query

    def test_get_all_projects_include_inactive(self):
        self.db.fetch_all.return_value = [
            {"id": 1, "path": "/a", "name": "A"},
        ]
        result = self.repo.get_all_projects(include_inactive=True)
        assert len(result) == 1
        query = self.db.fetch_all.call_args[0][0]
        assert "is_active" not in query

    def test_get_all_projects_filter_by_creator(self):
        self.db.fetch_all.return_value = []
        self.repo.get_all_projects(created_by=5)
        query = self.db.fetch_all.call_args[0][0]
        assert "created_by = ?" in query
        params = self.db.fetch_all.call_args[0][1]
        assert params == (5,)

    def test_get_all_projects_filter_by_tenant(self):
        self.db.fetch_all.return_value = []
        self.repo.get_all_projects(tenant_id=7)
        query = self.db.fetch_all.call_args[0][0]
        params = self.db.fetch_all.call_args[0][1]
        assert "tenant_id = ?" in query
        assert params == (7,)

    # -------------------------------------------------------------------------
    # get_user_projects
    # -------------------------------------------------------------------------

    def test_get_user_projects(self):
        self.db.fetch_all.return_value = [
            {"id": 1, "path": "/proj1", "name": "Proj1"},
        ]
        result = self.repo.get_user_projects(user_id=5)
        assert len(result) == 1
        query = self.db.fetch_all.call_args[0][0]
        assert "INNER JOIN user_projects" in query
        assert "up.user_id = ?" in query

    def test_get_user_projects_filters_by_tenant(self):
        self.db.fetch_all.return_value = []
        self.repo.get_user_projects(user_id=5, tenant_id=2)
        query = self.db.fetch_all.call_args[0][0]
        params = self.db.fetch_all.call_args[0][1]
        assert "p.tenant_id = ?" in query
        assert params == (5, 2)

    # -------------------------------------------------------------------------
    # update_project
    # -------------------------------------------------------------------------

    def test_update_project_name_and_description(self):
        self.db.execute.return_value = MagicMock()
        result = self.repo.update_project(project_id=1, name="New Name", description="New desc")
        assert result is True
        call_args = self.db.execute.call_args
        query = call_args[0][0]
        assert "name = ?" in query
        assert "description = ?" in query
        assert "updated_at = ?" in query

    def test_update_project_nothing(self):
        """No updates should return True without executing."""
        result = self.repo.update_project(project_id=1)
        assert result is True
        self.db.execute.assert_not_called()

    def test_update_project_is_shared_sqlite(self):
        self.db.execute.return_value = MagicMock()
        self.db.is_postgresql = False

        result = self.repo.update_project(project_id=1, is_shared=True)
        assert result is True
        params = self.db.execute.call_args[0][1]
        # SQLite: True -> 1
        assert 1 in params

    def test_update_project_is_shared_postgresql(self):
        self.db.execute.return_value = MagicMock()
        self.db.is_postgresql = True

        result = self.repo.update_project(project_id=1, is_shared=False)
        assert result is True
        params = self.db.execute.call_args[0][1]
        # PostgreSQL: False stays as bool
        assert False in params

    def test_update_project_exception(self):
        self.db.execute.side_effect = Exception("DB error")
        result = self.repo.update_project(project_id=1, name="New")
        assert result is False

    # -------------------------------------------------------------------------
    # delete_project
    # -------------------------------------------------------------------------

    def test_delete_project_soft(self):
        self.db.execute.return_value = MagicMock()
        result = self.repo.delete_project(1, soft_delete=True)
        assert result is True
        call_args = self.db.execute.call_args
        query = call_args[0][0]
        assert "is_active = FALSE" in query

    def test_delete_project_hard(self):
        self.db.execute.return_value = MagicMock()
        result = self.repo.delete_project(1, soft_delete=False)
        assert result is True
        # Should execute two DELETE statements
        assert self.db.execute.call_count == 2
        first_query = self.db.execute.call_args_list[0][0][0]
        second_query = self.db.execute.call_args_list[1][0][0]
        assert "DELETE FROM user_projects" in first_query
        assert "DELETE FROM projects" in second_query

    def test_delete_project_exception(self):
        self.db.execute.side_effect = Exception("DB error")
        result = self.repo.delete_project(1)
        assert result is False

    # -------------------------------------------------------------------------
    # add_user_project
    # -------------------------------------------------------------------------

    def test_add_user_project_sqlite(self):
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 42
        self.db.execute.return_value = mock_cursor
        self.db.is_postgresql = False

        result = self.repo.add_user_project(user_id=5, project_id=1)
        assert result == 42
        call_args = self.db.execute.call_args
        query = call_args[0][0]
        assert "INSERT OR REPLACE INTO user_projects" in query
        assert "COALESCE" in query

    def test_add_user_project_postgresql(self):
        self.db.is_postgresql = True
        self.db.fetch_one.return_value = {"id": 55}

        result = self.repo.add_user_project(user_id=5, project_id=1)
        assert result == 55
        call_args = self.db.fetch_one.call_args
        query = call_args[0][0]
        assert "ON CONFLICT" in query
        assert "RETURNING id" in query

    def test_add_user_project_exception(self):
        self.db.execute.side_effect = Exception("DB error")
        result = self.repo.add_user_project(user_id=5, project_id=1)
        assert result is None

    # -------------------------------------------------------------------------
    # update_user_project_stats
    # -------------------------------------------------------------------------

    def test_update_user_project_stats(self):
        self.db.execute.return_value = MagicMock()
        result = self.repo.update_user_project_stats(
            user_id=5,
            project_id=1,
            sessions_delta=2,
            tokens_delta=1000,
            requests_delta=10,
            duration_delta=300,
        )
        assert result is True
        call_args = self.db.execute.call_args
        query = call_args[0][0]
        assert "total_sessions = total_sessions + ?" in query
        assert "total_tokens = total_tokens + ?" in query
        params = call_args[0][1]
        assert params[1] == 2  # sessions_delta
        assert params[2] == 1000  # tokens_delta
        assert params[3] == 10  # requests_delta
        assert params[4] == 300  # duration_delta

    def test_update_user_project_stats_default_deltas(self):
        self.db.execute.return_value = MagicMock()
        result = self.repo.update_user_project_stats(user_id=5, project_id=1)
        assert result is True
        params = self.db.execute.call_args[0][1]
        assert params[1] == 0  # sessions_delta default
        assert params[2] == 0  # tokens_delta default

    def test_update_user_project_stats_exception(self):
        self.db.execute.side_effect = Exception("DB error")
        result = self.repo.update_user_project_stats(user_id=5, project_id=1)
        assert result is False

    # -------------------------------------------------------------------------
    # get_user_project
    # -------------------------------------------------------------------------

    def test_get_user_project_found(self):
        self.db.fetch_one.return_value = {"user_id": 5, "project_id": 1, "total_sessions": 10}
        result = self.repo.get_user_project(user_id=5, project_id=1)
        assert result is not None

    def test_get_user_project_not_found(self):
        self.db.fetch_one.return_value = None
        result = self.repo.get_user_project(user_id=5, project_id=999)
        assert result is None

    # -------------------------------------------------------------------------
    # get_project_users
    # -------------------------------------------------------------------------

    def test_get_project_users(self):
        self.db.fetch_all.return_value = [
            {"user_id": 1, "project_id": 1, "username": "alice"},
            {"user_id": 2, "project_id": 1, "username": "bob"},
        ]
        result = self.repo.get_project_users(project_id=1)
        assert len(result) == 2
        query = self.db.fetch_all.call_args[0][0]
        assert "LEFT JOIN users" in query

    # -------------------------------------------------------------------------
    # get_project_stats
    # -------------------------------------------------------------------------

    def test_get_project_stats(self):
        # First call: get_project_by_id
        self.db.fetch_one.side_effect = [
            {"id": 1, "tenant_id": 1, "path": "/proj", "name": "Proj", "is_active": True},
            {  # aggregate stats
                "total_users": 3,
                "total_sessions": 100,
                "total_tokens": 50000,
                "total_requests": 500,
                "total_duration_seconds": 3600,
                "first_access": "2024-01-01T00:00:00",
                "last_access": "2024-06-01T00:00:00",
            },
        ]
        self.db.fetch_all.return_value = [
            {"user_id": 1, "project_id": 1, "username": "alice"},
        ]

        result = self.repo.get_project_stats(project_id=1)
        assert result is not None
        assert result.project_id == 1
        assert result.project_path == "/proj"
        assert result.total_users == 3
        assert result.total_sessions == 100

    def test_get_project_stats_project_not_found(self):
        self.db.fetch_one.return_value = None
        result = self.repo.get_project_stats(project_id=999)
        assert result is None

    # -------------------------------------------------------------------------
    # get_all_project_stats
    # -------------------------------------------------------------------------

    def test_get_all_project_stats(self):
        self.db.fetch_all.return_value = [
            {
                "project_id": 1,
                "project_path": "/a",
                "project_name": "A",
                "is_shared": 0,
                "total_users": 2,
                "total_sessions": 50,
                "total_duration_seconds": 1000,
                "first_access": None,
                "last_access": None,
                "total_tokens": 10000,
                "total_requests": 100,
            },
        ]
        result = self.repo.get_all_project_stats()
        assert len(result) == 1
        assert result[0].project_id == 1

    # -------------------------------------------------------------------------
    # get_project_daily_stats
    # -------------------------------------------------------------------------

    def test_get_project_daily_stats_basic(self):
        self.db.fetch_one.return_value = {
            "id": 1,
            "tenant_id": 1,
            "path": "/p",
            "name": "Project",
            "is_active": True,
        }
        self.db.fetch_all.return_value = [
            {
                "date": "2024-01-01",
                "project_id": 1,
                "project_path": "/p",
                "total_tokens": 1000,
                "total_input_tokens": 600,
                "total_output_tokens": 400,
                "total_requests": 50,
                "active_users": 3,
            },
        ]
        result = self.repo.get_project_daily_stats(project_id=1)
        assert len(result) == 1
        assert result[0].date == "2024-01-01"
        assert result[0].total_tokens == 1000

    def test_get_project_daily_stats_with_date_range(self):
        self.db.fetch_one.return_value = {
            "id": 1,
            "tenant_id": 1,
            "path": "/p",
            "name": "Project",
            "is_active": True,
        }
        self.db.fetch_all.return_value = []
        self.repo.get_project_daily_stats(
            project_id=1, start_date="2024-01-01", end_date="2024-12-31"
        )
        query = self.db.fetch_all.call_args[0][0]
        assert "date >= ?" in query
        assert "date <= ?" in query
        params = self.db.fetch_all.call_args[0][1]
        assert params == (1, "2024-01-01", "2024-12-31")

    def test_get_project_daily_stats_handles_none_values(self):
        self.db.fetch_one.return_value = {
            "id": 1,
            "tenant_id": 1,
            "path": "/p",
            "name": "Project",
            "is_active": True,
        }
        self.db.fetch_all.return_value = [
            {
                "date": "2024-01-01",
                "project_id": 1,
                "project_path": "/p",
                "total_tokens": None,
                "total_input_tokens": None,
                "total_output_tokens": None,
                "total_requests": None,
                "active_users": None,
            },
        ]
        result = self.repo.get_project_daily_stats(project_id=1)
        assert result[0].total_tokens == 0
        assert result[0].total_requests == 0
        assert result[0].active_users == 0
