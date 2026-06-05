"""Integration tests for ProjectRepository against real SQLite database."""

import pytest

from app.repositories.project_repo import ProjectRepository


def _insert_user(tmp_db, username="testuser", email=None):
    """Insert a user row for foreign key references."""
    if email is None:
        email = f"{username}@example.com"
    cursor = tmp_db.execute(
        "INSERT INTO users (username, email, password_hash, role) VALUES (?, ?, ?, ?)",
        (username, email, "hashed_pw", "user"),
    )
    return cursor.lastrowid


class TestProjectCRUD:
    """Tests for project create/read/update/delete operations."""

    def test_create_project(self, tmp_db):
        """Create a project and verify it's stored."""
        repo = ProjectRepository(db=tmp_db)

        project_id = repo.create_project(
            path="/projects/my-project",
            name="My Project",
            description="A test project",
        )
        assert project_id is not None

        row = tmp_db.fetch_one("SELECT * FROM projects WHERE id = ?", (project_id,))
        assert row is not None
        assert row["path"] == "/projects/my-project"
        assert row["name"] == "My Project"
        assert row["description"] == "A test project"
        assert row["is_active"] == 1
        assert row["is_shared"] == 0

    def test_create_shared_project(self, tmp_db):
        """Create a shared project."""
        repo = ProjectRepository(db=tmp_db)
        user_id = _insert_user(tmp_db)

        project_id = repo.create_project(
            path="/projects/shared",
            name="Shared",
            is_shared=True,
            created_by=user_id,
        )
        assert project_id is not None

        row = tmp_db.fetch_one("SELECT * FROM projects WHERE id = ?", (project_id,))
        assert row["is_shared"] == 1

        # Should also create user_project entry for creator
        up = tmp_db.fetch_one(
            "SELECT * FROM user_projects WHERE user_id = ? AND project_id = ?",
            (user_id, project_id),
        )
        assert up is not None

    def test_get_project_by_id(self, tmp_db):
        """Get project by ID returns Project model."""
        repo = ProjectRepository(db=tmp_db)

        project_id = repo.create_project(
            path="/projects/test",
            name="Test Project",
        )

        project = repo.get_project_by_id(project_id)
        assert project is not None
        assert project.id == project_id
        assert project.path == "/projects/test"
        assert project.name == "Test Project"
        # SQLite stores booleans as 0/1 integers
        assert project.is_active in (True, 1)

    def test_get_project_by_path(self, tmp_db):
        """Get project by path."""
        repo = ProjectRepository(db=tmp_db)

        repo.create_project(path="/projects/unique-path", name="Path Project")

        project = repo.get_project_by_path("/projects/unique-path")
        assert project is not None
        assert project.name == "Path Project"

    def test_get_project_not_found(self, tmp_db):
        """Getting nonexistent project returns None."""
        repo = ProjectRepository(db=tmp_db)
        assert repo.get_project_by_id(9999) is None
        assert repo.get_project_by_path("/nonexistent") is None

    def test_update_project(self, tmp_db):
        """Update project fields."""
        repo = ProjectRepository(db=tmp_db)

        project_id = repo.create_project(
            path="/projects/update-me",
            name="Old Name",
            description="Old desc",
        )

        result = repo.update_project(
            project_id,
            name="New Name",
            description="New desc",
            is_shared=True,
        )
        assert result is True

        row = tmp_db.fetch_one("SELECT * FROM projects WHERE id = ?", (project_id,))
        assert row["name"] == "New Name"
        assert row["description"] == "New desc"
        assert row["is_shared"] == 1

    def test_update_project_no_changes(self, tmp_db):
        """Update with no fields returns True (no-op)."""
        repo = ProjectRepository(db=tmp_db)
        project_id = repo.create_project(path="/projects/noop")
        assert repo.update_project(project_id) is True

    def test_soft_delete_project(self, tmp_db):
        """Soft delete marks project as inactive."""
        repo = ProjectRepository(db=tmp_db)

        project_id = repo.create_project(path="/projects/to-delete", name="Delete Me")
        assert repo.get_project_by_id(project_id) is not None

        result = repo.delete_project(project_id, soft_delete=True)
        assert result is True

        # No longer visible via get_by_id (IS TRUE filter)
        assert repo.get_project_by_id(project_id) is None

        # Still exists in DB
        row = tmp_db.fetch_one("SELECT * FROM projects WHERE id = ?", (project_id,))
        assert row is not None
        assert row["is_active"] == 0

    def test_hard_delete_project(self, tmp_db):
        """Hard delete removes project and user_projects rows."""
        repo = ProjectRepository(db=tmp_db)
        user_id = _insert_user(tmp_db)

        project_id = repo.create_project(
            path="/projects/hard-delete",
            created_by=user_id,
        )

        # Verify user_project exists
        up = tmp_db.fetch_one("SELECT * FROM user_projects WHERE project_id = ?", (project_id,))
        assert up is not None

        result = repo.delete_project(project_id, soft_delete=False)
        assert result is True

        # Project is gone
        row = tmp_db.fetch_one("SELECT * FROM projects WHERE id = ?", (project_id,))
        assert row is None

        # user_projects is gone too
        up = tmp_db.fetch_one("SELECT * FROM user_projects WHERE project_id = ?", (project_id,))
        assert up is None

    def test_recreate_soft_deleted_project(self, tmp_db):
        """Test recreating a project after soft delete (Issue #119 fix)."""
        repo = ProjectRepository(db=tmp_db)
        user_id = _insert_user(tmp_db)

        # Create a project
        original_path = "/projects/recreate-test"
        project_id = repo.create_project(
            path=original_path,
            name="Original Project",
            description="Original description",
            created_by=user_id,
            is_shared=True,
        )
        assert project_id is not None

        # Soft delete the project
        result = repo.delete_project(project_id, soft_delete=True)
        assert result is True

        # Verify project is no longer visible
        assert repo.get_project_by_path(original_path) is None

        # Recreate project with same path - should restore soft-deleted project
        new_project_id = repo.create_project(
            path=original_path,
            name="New Project",
            description="New description",
            created_by=user_id,
            is_shared=False,
        )

        # Should return the same project ID (restored)
        assert new_project_id == project_id

        # Project should now be visible again
        restored_project = repo.get_project_by_id(new_project_id)
        assert restored_project is not None
        assert restored_project.path == original_path
        assert restored_project.name == "New Project"
        assert restored_project.description == "New description"
        # SQLite uses 1/0 for boolean, PostgreSQL uses True/False
        assert restored_project.is_active in (True, 1)
        assert restored_project.is_shared in (False, 0)

        # Verify there's only one record in database for this path
        all_rows = tmp_db.fetch_all("SELECT * FROM projects WHERE path = ?", (original_path,))
        assert len(all_rows) == 1

    def test_recreate_soft_deleted_project_user_project_association(self, tmp_db):
        """Test that user_projects association is created when restoring soft-deleted project."""
        repo = ProjectRepository(db=tmp_db)
        user_id_original = _insert_user(tmp_db, username="original_creator")
        user_id_new = _insert_user(tmp_db, username="new_creator")

        # Create a project with original user
        original_path = "/projects/recreate-user-test"
        project_id = repo.create_project(
            path=original_path,
            name="Original",
            created_by=user_id_original,
        )
        assert project_id is not None

        # Verify original user has user_projects association
        up_original = tmp_db.fetch_one(
            "SELECT * FROM user_projects WHERE user_id = ? AND project_id = ?",
            (user_id_original, project_id),
        )
        assert up_original is not None

        # Soft delete the project
        repo.delete_project(project_id, soft_delete=True)

        # Restore with a new creator user
        restored_id = repo.create_project(
            path=original_path,
            name="Restored",
            created_by=user_id_new,
        )

        # Should return same project ID
        assert restored_id == project_id

        # Verify new user also has user_projects association
        up_new = tmp_db.fetch_one(
            "SELECT * FROM user_projects WHERE user_id = ? AND project_id = ?",
            (user_id_new, project_id),
        )
        assert up_new is not None

        # Both users should have access to the restored project
        project_users = repo.get_project_users(project_id)
        user_ids = [up.user_id for up in project_users]
        assert user_id_original in user_ids
        assert user_id_new in user_ids


class TestUserProject:
    """Tests for user-project relationship operations."""

    def test_add_user_project(self, tmp_db):
        """Add a user-project relationship."""
        repo = ProjectRepository(db=tmp_db)
        user_id = _insert_user(tmp_db, username="user1")

        project_id = repo.create_project(path="/projects/up-test")
        result = repo.add_user_project(user_id, project_id)
        assert result is not None

        up = tmp_db.fetch_one(
            "SELECT * FROM user_projects WHERE user_id = ? AND project_id = ?",
            (user_id, project_id),
        )
        assert up is not None
        assert up["total_sessions"] == 0
        assert up["total_tokens"] == 0

    def test_get_user_project(self, tmp_db):
        """Get user-project relationship."""
        repo = ProjectRepository(db=tmp_db)
        user_id = _insert_user(tmp_db, username="user2")

        project_id = repo.create_project(path="/projects/get-up")
        repo.add_user_project(user_id, project_id)

        up = repo.get_user_project(user_id, project_id)
        assert up is not None
        assert up.user_id == user_id
        assert up.project_id == project_id

    def test_update_user_project_stats(self, tmp_db):
        """Update user-project statistics."""
        repo = ProjectRepository(db=tmp_db)
        user_id = _insert_user(tmp_db, username="user3")

        project_id = repo.create_project(path="/projects/stats")
        repo.add_user_project(user_id, project_id)

        repo.update_user_project_stats(
            user_id,
            project_id,
            sessions_delta=2,
            tokens_delta=500,
            requests_delta=10,
            duration_delta=120,
        )

        up = repo.get_user_project(user_id, project_id)
        assert up.total_sessions == 2
        assert up.total_tokens == 500
        assert up.total_requests == 10
        assert up.total_duration_seconds == 120

        # Incremental update
        repo.update_user_project_stats(
            user_id,
            project_id,
            sessions_delta=1,
            tokens_delta=100,
        )

        up = repo.get_user_project(user_id, project_id)
        assert up.total_sessions == 3
        assert up.total_tokens == 600

    def test_get_user_projects(self, tmp_db):
        """Get all projects for a user."""
        repo = ProjectRepository(db=tmp_db)
        user_id = _insert_user(tmp_db, username="user4")

        p1 = repo.create_project(path="/projects/user-p1")
        p2 = repo.create_project(path="/projects/user-p2")
        repo.add_user_project(user_id, p1)
        repo.add_user_project(user_id, p2)

        projects = repo.get_user_projects(user_id)
        assert len(projects) == 2

    def test_get_all_projects(self, tmp_db):
        """Get all projects with optional filters."""
        repo = ProjectRepository(db=tmp_db)
        user_id = _insert_user(tmp_db, username="creator")

        repo.create_project(path="/projects/all-1", created_by=user_id)
        repo.create_project(path="/projects/all-2")

        all_projects = repo.get_all_projects()
        assert len(all_projects) == 2

        by_creator = repo.get_all_projects(created_by=user_id)
        assert len(by_creator) == 1

    def test_get_all_projects_include_inactive(self, tmp_db):
        """Get all projects including inactive ones."""
        repo = ProjectRepository(db=tmp_db)

        repo.create_project(path="/projects/active-1")
        p2 = repo.create_project(path="/projects/inactive-1")
        repo.delete_project(p2, soft_delete=True)

        active_only = repo.get_all_projects()
        assert len(active_only) == 1

        with_inactive = repo.get_all_projects(include_inactive=True)
        assert len(with_inactive) == 2
