"""Integration tests for ProjectRepository against real PostgreSQL database."""

import pytest

from app.repositories.project_repo import ProjectRepository


def _insert_user(pg_db, username="testuser", email=None):
    """Insert a user row for foreign key references."""
    if email is None:
        email = f"{username}@example.com"
    pg_db.execute(
        "INSERT INTO users (username, email, password_hash, role) VALUES (%s, %s, %s, %s)",
        (username, email, "hashed_pw", "user"),
    )
    # For PostgreSQL, execute returns cursor but lastrowid may not work
    # Use RETURNING via fetch_one instead
    row = pg_db.fetch_one("SELECT id FROM users WHERE username = %s", (username,), commit=True)
    return row["id"]


class TestProjectCRUD:
    """Tests for project CRUD via PostgreSQL RETURNING path."""

    def test_create_project(self, pg_db):
        repo = ProjectRepository(db=pg_db)

        project_id = repo.create_project(
            path="/projects/my-project",
            name="My Project",
            description="A test project",
        )
        assert project_id is not None

        row = pg_db.fetch_one("SELECT * FROM projects WHERE id = %s", (project_id,))
        assert row is not None
        assert row["path"] == "/projects/my-project"
        assert row["name"] == "My Project"
        assert row["is_active"] in (True, 1)
        assert row["is_shared"] in (False, 0)

    def test_create_shared_project(self, pg_db):
        repo = ProjectRepository(db=pg_db)
        user_id = _insert_user(pg_db)

        project_id = repo.create_project(
            path="/projects/shared",
            name="Shared",
            is_shared=True,
            created_by=user_id,
        )
        assert project_id is not None

        row = pg_db.fetch_one("SELECT * FROM projects WHERE id = %s", (project_id,))
        assert row["is_shared"] in (True, 1)

    def test_get_project_by_id(self, pg_db):
        repo = ProjectRepository(db=pg_db)
        project_id = repo.create_project(path="/projects/test", name="Test")

        project = repo.get_project_by_id(project_id)
        assert project is not None
        assert project.path == "/projects/test"

    def test_get_project_by_path(self, pg_db):
        repo = ProjectRepository(db=pg_db)
        repo.create_project(path="/projects/unique-path", name="Path Project")

        project = repo.get_project_by_path("/projects/unique-path")
        assert project is not None
        assert project.name == "Path Project"

    def test_update_project(self, pg_db):
        repo = ProjectRepository(db=pg_db)
        project_id = repo.create_project(path="/projects/up", name="Old")

        result = repo.update_project(project_id, name="New", description="New desc", is_shared=True)
        assert result is True

        row = pg_db.fetch_one("SELECT * FROM projects WHERE id = %s", (project_id,))
        assert row["name"] == "New"

    def test_soft_delete_project(self, pg_db):
        repo = ProjectRepository(db=pg_db)
        project_id = repo.create_project(path="/projects/del", name="Del")

        assert repo.delete_project(project_id, soft_delete=True) is True
        assert repo.get_project_by_id(project_id) is None

        row = pg_db.fetch_one("SELECT * FROM projects WHERE id = %s", (project_id,))
        assert row is not None
        assert row["is_active"] in (False, 0)

    def test_hard_delete_project(self, pg_db):
        repo = ProjectRepository(db=pg_db)
        user_id = _insert_user(pg_db)
        project_id = repo.create_project(path="/projects/hard", created_by=user_id)

        assert repo.delete_project(project_id, soft_delete=False) is True
        assert repo.get_project_by_id(project_id) is None

    def test_recreate_soft_deleted_project(self, pg_db):
        """Test recreating a project after soft delete (Issue #119 fix)."""
        repo = ProjectRepository(db=pg_db)
        user_id = _insert_user(pg_db)

        # Create a project
        original_path = "/projects/recreate-test-pg"
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
        assert restored_project.is_active is True
        assert restored_project.is_shared is False

        # Verify there's only one record in database for this path
        all_rows = pg_db.fetch_all("SELECT * FROM projects WHERE path = ?", (original_path,))
        assert len(all_rows) == 1

    def test_recreate_soft_deleted_project_user_project_association(self, pg_db):
        """Test that user_projects association is created when restoring soft-deleted project."""
        repo = ProjectRepository(db=pg_db)
        user_id_original = _insert_user(pg_db, username="original_creator")
        user_id_new = _insert_user(pg_db, username="new_creator")

        # Create a project with original user
        original_path = "/projects/recreate-user-test-pg"
        project_id = repo.create_project(
            path=original_path,
            name="Original",
            created_by=user_id_original,
        )
        assert project_id is not None

        # Verify original user has user_projects association
        up_original = pg_db.fetch_one(
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
        up_new = pg_db.fetch_one(
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
    """Tests for user-project via PostgreSQL ON CONFLICT path."""

    def test_add_user_project(self, pg_db):
        repo = ProjectRepository(db=pg_db)
        user_id = _insert_user(pg_db, username="user1")
        project_id = repo.create_project(path="/projects/up-test")

        result = repo.add_user_project(user_id, project_id)
        assert result is not None

    def test_get_user_project(self, pg_db):
        repo = ProjectRepository(db=pg_db)
        user_id = _insert_user(pg_db, username="user2")
        project_id = repo.create_project(path="/projects/get-up")
        repo.add_user_project(user_id, project_id)

        up = repo.get_user_project(user_id, project_id)
        assert up is not None
        assert up.user_id == user_id
        assert up.project_id == project_id

    def test_update_user_project_stats(self, pg_db):
        repo = ProjectRepository(db=pg_db)
        user_id = _insert_user(pg_db, username="user3")
        project_id = repo.create_project(path="/projects/stats")
        repo.add_user_project(user_id, project_id)

        repo.update_user_project_stats(user_id, project_id, sessions_delta=2, tokens_delta=500)

        up = repo.get_user_project(user_id, project_id)
        assert up.total_sessions == 2
        assert up.total_tokens == 500

    def test_get_all_projects(self, pg_db):
        repo = ProjectRepository(db=pg_db)
        user_id = _insert_user(pg_db, username="creator")

        repo.create_project(path="/projects/all-1", created_by=user_id)
        repo.create_project(path="/projects/all-2")

        assert len(repo.get_all_projects()) == 2
        assert len(repo.get_all_projects(created_by=user_id)) == 1
