"""Integration tests for Issue #1329: counting a user's active workflows.

Covers ``AutonomousWorkflowRepository.count_active_workflows_by_user`` — the
repository query backing the max_sessions_per_user concurrent limit — against
a real temporary SQLite database initialized from the authoritative schema
(``load_schema_from_file``). Pure repository layer: no threads, no races.

Migrated from tests/issues/1329/test_concurrent_limit.py
(TestCountActiveWorkflowsByUser). Hermeticity hardening vs. the legacy
harness: DATABASE_URL is pinned (via monkeypatch) to the tmp_db fixture's
SQLite file for the duration of each test, so dynamic ``is_postgresql()`` /
``get_database_url()`` calls — including in modules that hold a local
``from app.repositories.database import is_postgresql`` reference — never
resolve to the ambient ``~/.open-ace/config.json`` Postgres URL.
"""

import uuid

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(1329)]


@pytest.fixture
def repo(tmp_db, monkeypatch):
    """AutonomousWorkflowRepository on the shared tmp SQLite database.

    The env pin matters even though tmp_db already patches the database
    module: autonomous_repo imported ``is_postgresql`` by value, so its calls
    resolve ``get_database_url()`` dynamically (env first, config file after).
    """
    monkeypatch.setenv("DATABASE_URL", tmp_db.db_url)

    # Seed the users referenced by autonomous_workflows.user_id (SQLite
    # connections run with PRAGMA foreign_keys = ON).
    conn = tmp_db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO users (username, email, password_hash, role, tenant_id) "
            "VALUES (?, ?, ?, ?, ?)",
            ("testuser", "test@test.com", "hash123", "user", 1),
        )
        cursor.execute(
            "INSERT INTO users (username, email, password_hash, role, tenant_id) "
            "VALUES (?, ?, ?, ?, ?)",
            ("otheruser", "other@test.com", "hash456", "user", 1),
        )
        conn.commit()
    finally:
        conn.close()

    from app.repositories.autonomous_repo import AutonomousWorkflowRepository

    return AutonomousWorkflowRepository(tmp_db)


class TestCountActiveWorkflowsByUser:
    def test_count_zero_when_no_workflows(self, repo):
        count = repo.count_active_workflows_by_user(user_id=1)
        assert count == 0

    def test_count_active_workflows_only(self, repo):
        repo.create_workflow(
            {
                "workflow_id": str(uuid.uuid4()),
                "user_id": 1,
                "title": "Active",
                "status": "developing",
                "cli_tool": "claude-code",
            }
        )
        repo.create_workflow(
            {
                "workflow_id": str(uuid.uuid4()),
                "user_id": 1,
                "title": "Done",
                "status": "completed",
                "cli_tool": "claude-code",
            }
        )
        count = repo.count_active_workflows_by_user(user_id=1)
        assert count == 1

    def test_count_multiple_active_statuses(self, repo):
        for i, status in enumerate(["pending", "planning", "developing", "waiting"]):
            repo.create_workflow(
                {
                    "workflow_id": str(uuid.uuid4()),
                    "user_id": 1,
                    "title": f"T{i}",
                    "status": status,
                    "cli_tool": "claude-code",
                }
            )
        assert repo.count_active_workflows_by_user(user_id=1) == 4

    def test_count_only_specific_user(self, repo):
        repo.create_workflow(
            {
                "workflow_id": str(uuid.uuid4()),
                "user_id": 1,
                "title": "U1",
                "status": "developing",
                "cli_tool": "claude-code",
            }
        )
        repo.create_workflow(
            {
                "workflow_id": str(uuid.uuid4()),
                "user_id": 2,
                "title": "U2",
                "status": "developing",
                "cli_tool": "claude-code",
            }
        )
        assert repo.count_active_workflows_by_user(user_id=1) == 1
        assert repo.count_active_workflows_by_user(user_id=2) == 1
