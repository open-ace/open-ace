"""Integration tests for Issue #2596: Machine deregistration session cascade.

These tests verify the complete deregistration flow with a real database,
including session termination, machine existence checks, and compensation
mechanisms.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.remote_agent_manager import (
    DEREGISTER_BATCH_SIZE,
    RemoteAgentManager,
    get_remote_agent_manager,
)
from app.repositories.database import Database, _param


@pytest.fixture
def sqlite_db(tmp_path, monkeypatch):
    """Create a SQLite database for testing."""
    import app.repositories.database as db_mod

    db_path = str(tmp_path / "test_deregister.db")
    monkeypatch.setattr(db_mod, "is_postgresql", lambda: False)
    monkeypatch.setattr(db_mod, "DB_PATH", db_path)

    db = Database(db_url=f"sqlite:///{db_path}")

    # Create required tables
    with db.connection() as conn:
        cursor = conn.cursor()

        # Create remote_machines table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS remote_machines (
                machine_id TEXT PRIMARY KEY,
                machine_name TEXT,
                hostname TEXT,
                os_type TEXT,
                os_version TEXT,
                ip_address TEXT,
                status TEXT DEFAULT 'online',
                agent_version TEXT,
                capabilities TEXT,
                tenant_id INTEGER,
                created_by INTEGER,
                created_at TEXT,
                updated_at TEXT,
                last_heartbeat TEXT
            )
        """)

        # Create agent_sessions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_sessions (
                session_id TEXT PRIMARY KEY,
                user_id INTEGER,
                tenant_id INTEGER,
                status TEXT DEFAULT 'active',
                workspace_type TEXT,
                remote_machine_id TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        """)

        # Create machine_assignments table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS machine_assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                machine_id TEXT,
                user_id INTEGER,
                permission TEXT,
                granted_by INTEGER,
                granted_at TEXT
            )
        """)

        # Create agent_tokens table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_hash TEXT,
                machine_id TEXT,
                token_version INTEGER,
                created_at TEXT,
                is_revoked INTEGER DEFAULT 0
            )
        """)

        # Create deregister_failures table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS deregister_failures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                machine_id TEXT,
                batch_index INTEGER,
                session_ids TEXT,
                error_message TEXT,
                retry_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                created_at TEXT,
                updated_at TEXT
            )
        """)

        conn.commit()

    return db


@pytest.fixture
def manager(sqlite_db, monkeypatch):
    """Create a RemoteAgentManager with mocked dependencies."""
    from app.modules.workspace.remote_agent_manager import RemoteAgentManager

    manager = RemoteAgentManager.__new__(RemoteAgentManager)
    manager.db = sqlite_db
    manager._connections = {}
    manager._session_machines = {}
    manager._output_buffers = {}
    manager._buffer_offsets = {}
    manager._command_queues = {}
    manager._session_end_flags = {}
    manager._last_delivered = {}
    manager._last_heartbeat_db_write = {}
    manager._browse_results = {}
    manager._pending_requests = {}
    manager._lock = MagicMock()
    manager._persist_output_lock = MagicMock()
    manager._output_accumulator = {}
    manager._token_cleanup_started = False
    manager._log_rate_limit_cache = {}
    manager._last_heartbeat_check_time = 0.0

    yield manager


class TestDeregisterSessionTermination:
    """Tests for session termination during machine deregistration."""

    def test_deregister_terminates_sessions_integration(self, manager, sqlite_db):
        """Test that deregister terminates all active sessions."""
        machine_id = "test-machine-123"
        session_id_1 = "session-1"
        session_id_2 = "session-2"

        # Setup: Create machine with active sessions
        with sqlite_db.connection() as conn:
            cursor = conn.cursor()
            now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

            # Create machine
            cursor.execute(
                f"""
                INSERT INTO remote_machines
                (machine_id, machine_name, status, created_at, updated_at)
                VALUES ({_param()}, {_param()}, {_param()}, {_param()}, {_param()})
                """,
                (machine_id, "Test Machine", "online", now, now),
            )

            # Create active sessions
            cursor.execute(
                f"""
                INSERT INTO agent_sessions
                (session_id, status, workspace_type, remote_machine_id, created_at, updated_at)
                VALUES ({_param()}, {_param()}, {_param()}, {_param()}, {_param()}, {_param()})
                """,
                (session_id_1, "active", "remote", machine_id, now, now),
            )
            cursor.execute(
                f"""
                INSERT INTO agent_sessions
                (session_id, status, workspace_type, remote_machine_id, created_at, updated_at)
                VALUES ({_param()}, {_param()}, {_param()}, {_param()}, {_param()}, {_param()})
                """,
                (session_id_2, "paused", "remote", machine_id, now, now),
            )

            conn.commit()

        # Execute deregistration
        result = manager.deregister_machine(machine_id)

        # Verify: Machine removed and sessions terminated
        assert result is True

        with sqlite_db.connection() as conn:
            cursor = conn.cursor()

            # Machine should be deleted
            cursor.execute(
                f"SELECT * FROM remote_machines WHERE machine_id = {_param()}",
                (machine_id,),
            )
            assert cursor.fetchone() is None

            # Sessions should be stopped
            cursor.execute(
                f"SELECT status FROM agent_sessions WHERE session_id = {_param()}",
                (session_id_1,),
            )
            row = cursor.fetchone()
            assert row is not None
            assert row["status"] == "stopped"

            cursor.execute(
                f"SELECT status FROM agent_sessions WHERE session_id = {_param()}",
                (session_id_2,),
            )
            row = cursor.fetchone()
            assert row is not None
            assert row["status"] == "stopped"

    def test_machine_check_returns_409(self, sqlite_db):
        """Test that session operations return 409 after machine deregistered."""
        from flask import Flask

        from app.routes.remote import _check_machine_exists_for_session

        # Create a Flask app for context
        app = Flask(__name__)

        session_info = {
            "session_id": "test-session",
            "remote_machine_id": "deregistered-machine",
        }

        # Mock get_remote_agent_manager to return None for machine
        with app.app_context():
            with patch("app.routes.remote.get_remote_agent_manager") as mock_mgr:
                mock_manager = MagicMock()
                mock_manager.get_machine.return_value = None
                mock_mgr.return_value = mock_manager

                result = _check_machine_exists_for_session(session_info, "test_op")

        # Should return (jsonify_response, 409)
        assert result is not None
        assert result[1] == 409

    def test_compensation_worker_retries(self, sqlite_db, tmp_path):
        """Test that compensation worker retries failed session terminations."""
        from datetime import timedelta

        from app.services.deregister_compensation_worker import (
            DeregisterCompensationWorker,
        )

        machine_id = "test-machine-comp"
        session_id = "session-comp"

        # Set created_at to 2 minutes ago to ensure backoff period has passed
        created_at_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=2)
        created_at = created_at_time.isoformat()

        # Setup: Create a failure record
        with sqlite_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                INSERT INTO deregister_failures
                (machine_id, batch_index, session_ids, status, created_at, updated_at)
                VALUES ({_param()}, {_param()}, {_param()}, {_param()}, {_param()}, {_param()})
                """,
                (machine_id, 0, f'["{session_id}"]', "pending", created_at, created_at),
            )
            conn.commit()

        # Setup: Create a session that needs termination
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        with sqlite_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                INSERT INTO agent_sessions
                (session_id, status, created_at, updated_at)
                VALUES ({_param()}, {_param()}, {_param()}, {_param()})
                """,
                (session_id, "active", now, now),
            )
            conn.commit()

        # Execute compensation worker retry
        worker = DeregisterCompensationWorker(sqlite_db)
        worker._process_pending_failures()

        # Verify: Session should be stopped
        with sqlite_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT status FROM agent_sessions WHERE session_id = {_param()}",
                (session_id,),
            )
            row = cursor.fetchone()
            assert row is not None
            assert row["status"] == "stopped"

            # Failure record should be resolved
            cursor.execute(
                f"SELECT status FROM deregister_failures WHERE machine_id = {_param()}",
                (machine_id,),
            )
            row = cursor.fetchone()
            assert row is not None
            assert row["status"] == "resolved"

    def test_concurrent_deregistration(self, manager, sqlite_db):
        """Test concurrent deregistration of the same machine."""
        machine_id = "test-machine-concurrent"
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

        # Setup: Create machine
        with sqlite_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                INSERT INTO remote_machines
                (machine_id, machine_name, status, created_at, updated_at)
                VALUES ({_param()}, {_param()}, {_param()}, {_param()}, {_param()})
                """,
                (machine_id, "Test Machine", "online", now, now),
            )
            conn.commit()

        # Note: Advisory lock test requires PostgreSQL, SQLite doesn't support it
        # For SQLite, we test that both attempts return success without error
        results = []

        def deregister():
            result = manager.deregister_machine(machine_id)
            results.append(result)

        # Execute concurrent deregistrations
        with ThreadPoolExecutor(max_workers=2) as executor:
            future1 = executor.submit(deregister)
            future2 = executor.submit(deregister)

            future1.result(timeout=10)
            future2.result(timeout=10)

        # Both should complete without error
        # First succeeds (True), second finds no machine (False)
        assert True in results
        assert False in results

    def test_advisory_lock_timeout(self, sqlite_db):
        """Test advisory lock timeout handling.

        Note: This test verifies graceful degradation on PostgreSQL
        when lock acquisition times out. For SQLite, no lock is used.
        """
        # This test requires mocking PostgreSQL behavior
        # For now, we verify that the code handles lock failures gracefully
        with patch("app.modules.workspace.remote_agent_manager.is_postgresql") as mock_pg:
            mock_pg.return_value = True

            manager = RemoteAgentManager.__new__(RemoteAgentManager)
            manager.db = sqlite_db
            manager._connections = {}
            manager._session_machines = {}
            manager._output_buffers = {}
            manager._buffer_offsets = {}
            manager._command_queues = {}
            manager._session_end_flags = {}
            manager._last_delivered = {}
            manager._last_heartbeat_db_write = {}
            manager._lock = MagicMock()
            manager._persist_output_lock = MagicMock()
            manager._output_accumulator = {}

            machine_id = "test-machine-lock"
            now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

            # Create machine
            with sqlite_db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    f"""
                    INSERT INTO remote_machines
                    (machine_id, machine_name, status, created_at, updated_at)
                    VALUES ({_param()}, {_param()}, {_param()}, {_param()}, {_param()})
                    """,
                    (machine_id, "Test Machine", "online", now, now),
                )
                conn.commit()

            # Attempt deregistration - should handle gracefully even if lock fails
            result = manager.deregister_machine(machine_id)

            # Should still succeed despite lock mechanism
            assert result is True


class TestDeregisterBatchTermination:
    """Tests for batch session termination."""

    def test_batch_termination_large_session_count(self, manager, sqlite_db):
        """Test termination of more than 100 sessions (batch size)."""
        machine_id = "test-machine-batch"
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

        # Setup: Create machine
        with sqlite_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                INSERT INTO remote_machines
                (machine_id, machine_name, status, created_at, updated_at)
                VALUES ({_param()}, {_param()}, {_param()}, {_param()}, {_param()})
                """,
                (machine_id, "Test Machine", "online", now, now),
            )
            conn.commit()

            # Create 150 sessions (more than batch size of 100)
            for i in range(150):
                cursor.execute(
                    f"""
                    INSERT INTO agent_sessions
                    (session_id, status, workspace_type, remote_machine_id, created_at, updated_at)
                    VALUES ({_param()}, {_param()}, {_param()}, {_param()}, {_param()}, {_param()})
                    """,
                    (f"session-{i}", "active", "remote", machine_id, now, now),
                )
            conn.commit()

        # Execute deregistration
        result = manager.deregister_machine(machine_id)

        # Verify: All sessions should be stopped
        assert result is True

        with sqlite_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT COUNT(*) as count FROM agent_sessions WHERE remote_machine_id = {_param()} AND status = 'stopped'",
                (machine_id,),
            )
            row = cursor.fetchone()
            assert row["count"] == 150

            # Verify machine deleted
            cursor.execute(
                f"SELECT * FROM remote_machines WHERE machine_id = {_param()}",
                (machine_id,),
            )
            assert cursor.fetchone() is None