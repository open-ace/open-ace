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
