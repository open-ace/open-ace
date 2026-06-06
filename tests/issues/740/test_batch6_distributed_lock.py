"""
Batch 6 tests — Distributed lock, remote machine admin validation.

Tests for:
- acquire_lock / release_lock logic
- Scheduler _advance_single uses distributed lock
- Remote machine admin permission check in create_workflow
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))


# ── Distributed Lock (unit tests with mocked DB) ────────────────────


class TestDistributedLock(unittest.TestCase):
    """Tests for acquire_lock / release_lock with real SQLite DB."""

    def _make_repo(self, wf_id="wf-lock-test"):
        """Create a repo backed by a fresh in-memory SQLite DB."""
        import sqlite3

        import app.repositories.database as db_mod

        orig = db_mod.adapt_sql
        db_mod.adapt_sql = lambda sql: sql

        # Use in-memory SQLite to avoid file conflicts
        mem_conn = sqlite3.connect(":memory:")
        mem_conn.row_factory = sqlite3.Row

        cursor = mem_conn.cursor()
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT DEFAULT 'user', is_active INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT)"
        )
        cursor.execute(
            "INSERT OR IGNORE INTO users (username, email, password_hash, role) VALUES (?, ?, ?, ?)",
            ("admin", "admin@test.com", "hash123", "admin"),
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS autonomous_workflows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL UNIQUE,
                user_id INTEGER,
                status TEXT DEFAULT 'pending',
                cli_tool TEXT DEFAULT '',
                locked_at TEXT,
                locked_by TEXT DEFAULT ''
            )
            """
        )
        cursor.execute(
            "INSERT INTO autonomous_workflows (workflow_id, user_id, status, cli_tool) VALUES (?, ?, ?, ?)",
            (wf_id, 1, "pending", "claude-code"),
        )
        mem_conn.commit()

        # Wrap in Database-like object with close() as no-op
        mock_db = MagicMock()
        mock_db._is_postgresql = False
        # Return a connection that doesn't actually close (in-memory shared)
        mock_conn = MagicMock(wraps=mem_conn)
        mock_conn.close = MagicMock()  # no-op close
        mock_db.get_connection.return_value = mock_conn

        from app.repositories.autonomous_repo import AutonomousWorkflowRepository

        repo = AutonomousWorkflowRepository(mock_db)
        return repo, mem_conn, orig, db_mod

    def tearDown(self):
        if hasattr(self, "_conn"):
            self._conn.close()
        if hasattr(self, "_db_mod"):
            self._db_mod.adapt_sql = self._orig

    def test_acquire_lock_success(self):
        """Should acquire lock when not held."""
        repo, conn, orig, db_mod = self._make_repo("wf-ok")
        self._conn, self._orig, self._db_mod = conn, orig, db_mod
        result = repo.acquire_lock("wf-ok", "owner-1")
        self.assertTrue(result)

    def test_acquire_lock_fails_when_held(self):
        """Should fail to acquire lock when already held by another owner."""
        repo, conn, orig, db_mod = self._make_repo("wf-held")
        self._conn, self._orig, self._db_mod = conn, orig, db_mod
        repo.acquire_lock("wf-held", "owner-1")
        result = repo.acquire_lock("wf-held", "owner-2")
        self.assertFalse(result)

    def test_release_lock_by_owner(self):
        """Owner can release their own lock."""
        repo, conn, orig, db_mod = self._make_repo("wf-release")
        self._conn, self._orig, self._db_mod = conn, orig, db_mod
        repo.acquire_lock("wf-release", "owner-1")
        repo.release_lock("wf-release", "owner-1")
        result = repo.acquire_lock("wf-release", "owner-2")
        self.assertTrue(result)

    def test_release_lock_wrong_owner_noop(self):
        """Releasing with wrong owner should not clear the lock."""
        repo, conn, orig, db_mod = self._make_repo("wf-wrong")
        self._conn, self._orig, self._db_mod = conn, orig, db_mod
        repo.acquire_lock("wf-wrong", "owner-1")
        repo.release_lock("wf-wrong", "wrong-owner")
        result = repo.acquire_lock("wf-wrong", "owner-2")
        self.assertFalse(result)

    def test_reentrant_lock_by_same_owner(self):
        """Same owner can re-acquire after release."""
        repo, conn, orig, db_mod = self._make_repo("wf-reentrant")
        self._conn, self._orig, self._db_mod = conn, orig, db_mod
        repo.acquire_lock("wf-reentrant", "owner-1")
        repo.release_lock("wf-reentrant", "owner-1")
        result = repo.acquire_lock("wf-reentrant", "owner-1")
        self.assertTrue(result)


class TestSchedulerLockIntegration(unittest.TestCase):
    """Tests for scheduler _advance_single using distributed lock."""

    def test_skips_locked_workflow(self):
        """_advance_single should skip if lock cannot be acquired."""
        from app.services.autonomous_scheduler import AutonomousScheduler

        scheduler = AutonomousScheduler()
        wf_id = "wf-locked"

        mock_repo = MagicMock()
        mock_repo.acquire_lock.return_value = False

        with patch(
            "app.repositories.autonomous_repo.AutonomousWorkflowRepository", return_value=mock_repo
        ):
            scheduler._advance_single(wf_id)

        mock_repo.release_lock.assert_not_called()

    def test_acquires_and_releases_lock(self):
        """_advance_single should acquire and release lock in normal flow."""
        from app.services.autonomous_scheduler import AutonomousScheduler

        scheduler = AutonomousScheduler()
        wf_id = "wf-normal"

        mock_repo = MagicMock()
        mock_repo.acquire_lock.return_value = True

        with (
            patch(
                "app.modules.workspace.autonomous.orchestrator.AutonomousOrchestrator"
            ) as mock_orch_cls,
            patch(
                "app.repositories.autonomous_repo.AutonomousWorkflowRepository",
                return_value=mock_repo,
            ),
        ):
            mock_orch_cls.return_value = MagicMock()
            scheduler._advance_single(wf_id)

        mock_repo.acquire_lock.assert_called_once()
        mock_repo.release_lock.assert_called_once()

    def test_releases_lock_on_error(self):
        """_advance_single should release lock even on orchestrator error."""
        from app.services.autonomous_scheduler import AutonomousScheduler

        scheduler = AutonomousScheduler()
        wf_id = "wf-error"

        mock_repo = MagicMock()
        mock_repo.acquire_lock.return_value = True

        with (
            patch(
                "app.modules.workspace.autonomous.orchestrator.AutonomousOrchestrator"
            ) as mock_orch_cls,
            patch(
                "app.repositories.autonomous_repo.AutonomousWorkflowRepository",
                return_value=mock_repo,
            ),
        ):
            mock_orch = MagicMock()
            mock_orch.advance.side_effect = RuntimeError("boom")
            mock_orch_cls.return_value = mock_orch
            scheduler._advance_single(wf_id)

        mock_repo.release_lock.assert_called_once()


# ── Remote Machine Admin Validation ─────────────────────────────────


class TestRemoteMachineAdminValidation(unittest.TestCase):
    """Tests for remote machine admin permission check in create_workflow."""

    def _make_client(self):
        import app.repositories.database as db_mod

        db_path = tempfile.mktemp(suffix=".db")
        orig = db_mod.adapt_sql
        db_mod.adapt_sql = lambda sql: sql

        db = db_mod.Database(db_path)
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT DEFAULT 'user', is_active INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT)"
            )
            cursor.execute(
                "INSERT OR IGNORE INTO users (username, email, password_hash, role) VALUES (?, ?, ?, ?)",
                ("admin", "admin@test.com", "hash123", "admin"),
            )
            cursor.execute(
                "INSERT OR IGNORE INTO users (username, email, password_hash, role) VALUES (?, ?, ?, ?)",
                ("testuser", "user@test.com", "hash123", "user"),
            )
            from app.modules.workspace.autonomous import get_ddl_statements

            for sql in get_ddl_statements():
                cursor.execute(sql)
            conn.commit()

        from app import create_app

        app = create_app({"TESTING": True})
        c = app.test_client()
        c.set_cookie("session_token", "test-token")
        return c, db_path, orig, db_mod

    def _mock_auth(self, user_id=1, role="admin"):
        return patch(
            "app.auth.decorators._load_user_from_token",
            return_value={
                "id": user_id,
                "username": "admin" if role == "admin" else "testuser",
                "email": f"{role}@test.com",
                "role": role,
            },
        )

    def test_rejects_non_admin_remote_workflow(self):
        """Non-admin user without machine admin permission should be rejected."""
        c, db_path, orig, db_mod = self._make_client()
        try:
            mock_repo = MagicMock()
            mock_repo.create_workflow.return_value = {"workflow_id": "wf-1"}

            with self._mock_auth(user_id=2, role="user"):
                with patch("app.routes.autonomous._get_repo", return_value=mock_repo):
                    with patch("app.auth.decorators._check_machine_admin", return_value=False):
                        resp = c.post(
                            "/api/autonomous/workflows",
                            json={
                                "requirements_text": "test",
                                "cli_tool": "claude-code",
                                "project_path": "/tmp/project",
                                "workspace_type": "remote",
                                "remote_machine_id": "machine-123",
                            },
                        )

            self.assertEqual(resp.status_code, 403)
            data = resp.get_json()
            self.assertIn("machine admin", data["error"].lower())
        finally:
            db_mod.adapt_sql = orig
            try:
                os.unlink(db_path)
            except OSError:
                pass

    def test_allows_admin_remote_workflow(self):
        """System admin should be able to create remote workflows."""
        c, db_path, orig, db_mod = self._make_client()
        try:
            mock_repo = MagicMock()
            mock_repo.create_workflow.return_value = {
                "workflow_id": "wf-1",
                "title": "",
            }

            with self._mock_auth(user_id=1, role="admin"):
                with patch("app.routes.autonomous._get_repo", return_value=mock_repo):
                    with patch("app.routes.autonomous._get_event_emitter"):
                        resp = c.post(
                            "/api/autonomous/workflows",
                            json={
                                "requirements_text": "test",
                                "cli_tool": "claude-code",
                                "project_path": "/tmp/project",
                                "workspace_type": "remote",
                                "remote_machine_id": "machine-123",
                            },
                        )

            self.assertEqual(resp.status_code, 201)
        finally:
            db_mod.adapt_sql = orig
            try:
                os.unlink(db_path)
            except OSError:
                pass

    def test_allows_local_workflow_without_check(self):
        """Local workflows should not require machine admin check."""
        c, db_path, orig, db_mod = self._make_client()
        try:
            mock_repo = MagicMock()
            mock_repo.create_workflow.return_value = {
                "workflow_id": "wf-1",
                "title": "",
            }

            with self._mock_auth(user_id=2, role="user"):
                with patch("app.routes.autonomous._get_repo", return_value=mock_repo):
                    with patch("app.routes.autonomous._get_event_emitter"):
                        resp = c.post(
                            "/api/autonomous/workflows",
                            json={
                                "requirements_text": "test",
                                "cli_tool": "claude-code",
                                "project_path": "/tmp/project",
                                "workspace_type": "local",
                            },
                        )

            self.assertEqual(resp.status_code, 201)
        finally:
            db_mod.adapt_sql = orig
            try:
                os.unlink(db_path)
            except OSError:
                pass

    def test_allows_machine_admin_remote_workflow(self):
        """Machine admin (non-system admin) should be able to create remote workflows."""
        c, db_path, orig, db_mod = self._make_client()
        try:
            mock_repo = MagicMock()
            mock_repo.create_workflow.return_value = {
                "workflow_id": "wf-1",
                "title": "",
            }

            with self._mock_auth(user_id=2, role="user"):
                with patch("app.routes.autonomous._get_repo", return_value=mock_repo):
                    with patch("app.auth.decorators._check_machine_admin", return_value=True):
                        with patch("app.routes.autonomous._get_event_emitter"):
                            resp = c.post(
                                "/api/autonomous/workflows",
                                json={
                                    "requirements_text": "test",
                                    "cli_tool": "claude-code",
                                    "project_path": "/tmp/project",
                                    "workspace_type": "remote",
                                    "remote_machine_id": "machine-123",
                                },
                            )

            self.assertEqual(resp.status_code, 201)
        finally:
            db_mod.adapt_sql = orig
            try:
                os.unlink(db_path)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
