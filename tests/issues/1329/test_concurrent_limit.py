"""
Tests for Issue #1329: max_sessions_per_user concurrent limit enforcement.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

import app.repositories.database as db_mod
from app.repositories.database import Database


@pytest.fixture
def auto_db(tmp_path):
    """Create a temporary SQLite database with autonomous tables."""
    with patch.object(db_mod, "is_postgresql", return_value=False):
        orig = db_mod.adapt_sql
        db_mod.adapt_sql = lambda q: q
        try:
            db_path = str(tmp_path / "test_concurrent.db")
            db = Database(db_url=f"sqlite:///{db_path}")
            conn = db.get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT UNIQUE NOT NULL,
                        email TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        role TEXT DEFAULT 'user',
                        tenant_id INTEGER,
                        is_active INTEGER DEFAULT 1
                    )
                """)
                cursor.execute("INSERT INTO users (username, email, password_hash, role, tenant_id) VALUES (?, ?, ?, ?, ?)", ("testuser", "test@test.com", "hash123", "user", 1))
                cursor.execute("INSERT INTO users (username, email, password_hash, role, tenant_id) VALUES (?, ?, ?, ?, ?)", ("otheruser", "other@test.com", "hash456", "user", 1))
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS tenants (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        slug TEXT UNIQUE NOT NULL,
                        quota TEXT
                    )
                """)
                cursor.execute("INSERT INTO tenants (name, slug, quota) VALUES (?, ?, ?)", ("Test Tenant", "test-tenant", '{"max_sessions_per_user": 3}'))
                from app.modules.workspace.autonomous import get_ddl_statements
                for sql in get_ddl_statements():
                    try:
                        cursor.execute(sql)
                    except Exception:
                        pass
                conn.commit()
            finally:
                conn.close()
            yield db
        finally:
            db_mod.adapt_sql = orig
            try:
                os.unlink(db_path)
            except OSError:
                pass


@pytest.fixture
def flask_app():
    """Create Flask app for testing jsonify responses."""
    from app import create_app
    app = create_app({"TESTING": True})
    return app


@pytest.fixture
def repo(auto_db):
    from app.repositories.autonomous_repo import AutonomousWorkflowRepository
    return AutonomousWorkflowRepository(auto_db)


class TestCountActiveWorkflowsByUser:
    def test_count_zero_when_no_workflows(self, repo):
        count = repo.count_active_workflows_by_user(user_id=1)
        assert count == 0

    def test_count_active_workflows_only(self, repo):
        import uuid
        repo.create_workflow({"workflow_id": str(uuid.uuid4()), "user_id": 1, "title": "Active", "status": "developing", "cli_tool": "claude-code"})
        repo.create_workflow({"workflow_id": str(uuid.uuid4()), "user_id": 1, "title": "Done", "status": "completed", "cli_tool": "claude-code"})
        count = repo.count_active_workflows_by_user(user_id=1)
        assert count == 1

    def test_count_multiple_active_statuses(self, repo):
        import uuid
        for i, status in enumerate(["pending", "planning", "developing", "waiting"]):
            repo.create_workflow({"workflow_id": str(uuid.uuid4()), "user_id": 1, "title": f"T{i}", "status": status, "cli_tool": "claude-code"})
        assert repo.count_active_workflows_by_user(user_id=1) == 4

    def test_count_only_specific_user(self, repo):
        import uuid
        repo.create_workflow({"workflow_id": str(uuid.uuid4()), "user_id": 1, "title": "U1", "status": "developing", "cli_tool": "claude-code"})
        repo.create_workflow({"workflow_id": str(uuid.uuid4()), "user_id": 2, "title": "U2", "status": "developing", "cli_tool": "claude-code"})
        assert repo.count_active_workflows_by_user(user_id=1) == 1
        assert repo.count_active_workflows_by_user(user_id=2) == 1


class TestCheckUserConcurrentLimit:
    def test_returns_none_when_under_limit(self, auto_db):
        from app.routes.autonomous import _check_user_concurrent_limit
        with patch("app.routes.autonomous._get_repo") as mock_get_repo:
            mock_repo = MagicMock()
            mock_repo.count_active_workflows_by_user.return_value = 1
            mock_get_repo.return_value = mock_repo
            with patch("app.routes.autonomous.user_repo") as mock_user_repo:
                mock_user_repo.get_user_by_id.return_value = {"id": 1, "tenant_id": 1}
                with patch("app.repositories.tenant_repo.TenantRepository") as mock_tenant_repo_class:
                    mock_tenant_repo = MagicMock()
                    mock_tenant_repo.get_tenant_by_id.return_value = {"id": 1, "quota": {"max_sessions_per_user": 3}}
                    mock_tenant_repo_class.return_value = mock_tenant_repo
                    result = _check_user_concurrent_limit(user_id=1)
        assert result is None

    def test_returns_429_when_at_limit(self, auto_db, flask_app):
        """Should return 429 Response when user has reached the limit."""
        from app.routes.autonomous import _check_user_concurrent_limit
        with flask_app.app_context():
            with patch("app.routes.autonomous._get_repo") as mock_get_repo:
                mock_repo = MagicMock()
                mock_repo.count_active_workflows_by_user.return_value = 3
                mock_get_repo.return_value = mock_repo
                with patch("app.routes.autonomous.user_repo") as mock_user_repo:
                    mock_user_repo.get_user_by_id.return_value = {"id": 1, "tenant_id": 1}
                    with patch("app.repositories.tenant_repo.TenantRepository") as mock_tenant_repo_class:
                        mock_tenant_repo = MagicMock()
                        mock_tenant_repo.get_tenant_by_id.return_value = {"id": 1, "quota": {"max_sessions_per_user": 3}}
                        mock_tenant_repo_class.return_value = mock_tenant_repo
                        result = _check_user_concurrent_limit(user_id=1)
        assert result is not None
        assert result[1] == 429

    def test_uses_default_when_no_tenant(self, auto_db):
        from app.routes.autonomous import _check_user_concurrent_limit
        with patch("app.routes.autonomous._get_repo") as mock_get_repo:
            mock_repo = MagicMock()
            mock_repo.count_active_workflows_by_user.return_value = 3
            mock_get_repo.return_value = mock_repo
            with patch("app.routes.autonomous.user_repo") as mock_user_repo:
                mock_user_repo.get_user_by_id.return_value = {"id": 1, "tenant_id": None}
                result = _check_user_concurrent_limit(user_id=1)
        assert result is None

    def test_fail_open_on_exception(self, auto_db):
        from app.routes.autonomous import _check_user_concurrent_limit
        with patch("app.routes.autonomous._get_repo") as mock_get_repo:
            mock_repo = MagicMock()
            mock_repo.count_active_workflows_by_user.side_effect = Exception("DB error")
            mock_get_repo.return_value = mock_repo
            with patch("app.routes.autonomous.user_repo") as mock_user_repo:
                mock_user_repo.get_user_by_id.return_value = {"id": 1, "tenant_id": 1}
                result = _check_user_concurrent_limit(user_id=1)
        assert result is None
