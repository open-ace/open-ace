"""Tests for Issue #740 Batch 6 — Distributed lock and scheduler lock usage (unit half).

Migrated from tests/issues/740/test_batch6_distributed_lock.py. The remote
machine admin validation route tests moved to
tests/integration/routes/test_remote_machine_admin_validation_740.py.

Covers:
- acquire_lock / release_lock logic (in-memory SQLite, no ambient DB)
- Scheduler _advance_single uses distributed lock
"""

from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(740)]

# ── Distributed Lock (unit tests with mocked DB) ────────────────────


class TestDistributedLock:
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
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS autonomous_workflows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL UNIQUE,
                user_id INTEGER,
                status TEXT DEFAULT 'pending',
                cli_tool TEXT DEFAULT '',
                locked_at TEXT,
                locked_by TEXT DEFAULT ''
            )
            """)
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

    def test_acquire_lock_success(self):
        """Should acquire lock when not held."""
        repo, conn, orig, db_mod = self._make_repo("wf-ok")
        try:
            result = repo.acquire_lock("wf-ok", "owner-1")
            assert result
        finally:
            conn.close()
            db_mod.adapt_sql = orig

    def test_acquire_lock_fails_when_held(self):
        """Should fail to acquire lock when already held by another owner."""
        repo, conn, orig, db_mod = self._make_repo("wf-held")
        try:
            repo.acquire_lock("wf-held", "owner-1")
            result = repo.acquire_lock("wf-held", "owner-2")
            assert not result
        finally:
            conn.close()
            db_mod.adapt_sql = orig

    def test_release_lock_by_owner(self):
        """Owner can release their own lock."""
        repo, conn, orig, db_mod = self._make_repo("wf-release")
        try:
            repo.acquire_lock("wf-release", "owner-1")
            repo.release_lock("wf-release", "owner-1")
            result = repo.acquire_lock("wf-release", "owner-2")
            assert result
        finally:
            conn.close()
            db_mod.adapt_sql = orig

    def test_release_lock_wrong_owner_noop(self):
        """Releasing with wrong owner should not clear the lock."""
        repo, conn, orig, db_mod = self._make_repo("wf-wrong")
        try:
            repo.acquire_lock("wf-wrong", "owner-1")
            repo.release_lock("wf-wrong", "wrong-owner")
            result = repo.acquire_lock("wf-wrong", "owner-2")
            assert not result
        finally:
            conn.close()
            db_mod.adapt_sql = orig

    def test_reentrant_lock_by_same_owner(self):
        """Same owner can re-acquire after release."""
        repo, conn, orig, db_mod = self._make_repo("wf-reentrant")
        try:
            repo.acquire_lock("wf-reentrant", "owner-1")
            repo.release_lock("wf-reentrant", "owner-1")
            result = repo.acquire_lock("wf-reentrant", "owner-1")
            assert result
        finally:
            conn.close()
            db_mod.adapt_sql = orig


class TestSchedulerLockIntegration:
    """Tests for scheduler _advance_single using distributed lock."""

    def test_skips_locked_workflow(self):
        """_advance_single should skip if lock cannot be acquired."""
        from app.services.autonomous_scheduler import AutonomousScheduler

        scheduler = AutonomousScheduler()
        wf_id = "wf-locked"

        mock_repo = MagicMock()
        mock_repo.acquire_lock.return_value = False

        with patch("app.routes.autonomous._get_repo", return_value=mock_repo):
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
            patch("app.routes.autonomous._get_repo", return_value=mock_repo),
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
            patch("app.routes.autonomous._get_repo", return_value=mock_repo),
        ):
            mock_orch = MagicMock()
            mock_orch.advance.side_effect = RuntimeError("boom")
            mock_orch_cls.return_value = mock_orch
            scheduler._advance_single(wf_id)

        mock_repo.release_lock.assert_called_once()
