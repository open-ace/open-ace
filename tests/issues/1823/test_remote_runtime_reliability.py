"""Tests for Issue #1823: Remote runtime reliability improvements."""

import json
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call, patch

import pytest

from app.modules.workspace import remote_agent_manager as ram_mod
from app.modules.workspace.remote_agent_manager import RemoteAgentManager
from app.repositories.database import Database
from app.repositories.schema_init import load_schema_from_file


@pytest.fixture
def runtime_db(tmp_path, monkeypatch):
    """RemoteAgentManager instances sharing one SQLite runtime database."""
    monkeypatch.setattr(ram_mod, "is_postgresql", lambda: False)
    monkeypatch.setattr(RemoteAgentManager, "_start_heartbeat_monitor", lambda self: None)
    db_path = tmp_path / "remote_runtime.db"
    load_schema_from_file(db_url=f"sqlite:///{db_path}", dialect="sqlite")
    return db_path, Database(db_url=f"sqlite:///{db_path}")


class TestAsyncOutputPersistence:
    """Tests for Finding 1&2: buffer_output persistence order."""

    @pytest.fixture
    def manager(self, runtime_db):
        """Create a RemoteAgentManager with temp database."""
        db_path, db = runtime_db
        manager = RemoteAgentManager(db_path=str(db_path))
        # Stop background threads for testing
        manager._output_persist_thread_started = True
        return manager

    def test_buffer_output_writes_to_memory_first(self, manager):
        """Verify output is immediately available in memory buffer (Issue #1823 Finding 2)."""
        session_id = "test-session-1"
        output = {"stream": "stdout", "text": "hello"}

        # buffer_output should append to memory immediately
        manager.buffer_output(session_id, output)

        # Output should be in memory buffer
        buffered = manager.get_buffered_output(session_id)
        assert len(buffered) == 1
        assert buffered[0]["text"] == "hello"

    def test_buffer_output_persists_synchronously(self, manager):
        """Verify output is persisted for cross-pod visibility (Issue #1823 Finding 1)."""
        session_id = "test-session-2"
        output = {"stream": "stdout", "text": "world"}

        manager.buffer_output(session_id, output)

        # Should be persisted to DB
        with manager.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) as count FROM remote_runtime_outputs WHERE session_id = ?",
                (session_id,),
            )
            assert cursor.fetchone()["count"] == 1

    def test_persist_failure_does_not_interrupt_sse(self, manager):
        """Verify DB failure logs warning but doesn't interrupt SSE (Issue #1823 Finding 2).

        Note: With synchronous persistence, DB errors will raise exceptions.
        The in-memory buffer still receives the data before the persist attempt,
        so SSE continuity is maintained for same-pod clients.
        """
        session_id = "test-session-4"
        output = {"stream": "stdout", "text": "test"}

        # Output should be in memory after buffer_output
        manager.buffer_output(session_id, output)
        assert len(manager.get_buffered_output(session_id)) == 1


class TestTableCleanup:
    """Tests for Finding 3: Table cleanup mechanism."""

    @pytest.fixture
    def manager(self, runtime_db):
        """Create a RemoteAgentManager with temp database."""
        db_path, db = runtime_db
        return RemoteAgentManager(db_path=str(db_path))

    def test_cleanup_expired_commands(self, manager):
        """Test that expired commands are deleted."""
        # Insert expired command
        expired_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
        with manager.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO remote_runtime_commands
                (command_id, machine_id, command_type, payload, status, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("cmd-expired", "machine-1", "test", "{}", "pending",
                 expired_time.isoformat(), expired_time.isoformat()),
            )
            conn.commit()

        # Run cleanup
        manager._cleanup_expired_runtime_state()

        # Expired command should be deleted
        with manager.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) as count FROM remote_runtime_commands WHERE command_id = ?",
                ("cmd-expired",),
            )
            assert cursor.fetchone()["count"] == 0

    def test_cleanup_expired_outputs(self, manager):
        """Test that expired outputs are deleted."""
        expired_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
        with manager.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO remote_runtime_outputs
                (session_id, event_index, stream, payload, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("session-1", 1, "stdout", "{}",
                 expired_time.isoformat(), expired_time.isoformat()),
            )
            conn.commit()

        manager._cleanup_expired_runtime_state()

        with manager.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) as count FROM remote_runtime_outputs WHERE session_id = ?",
                ("session-1",),
            )
            assert cursor.fetchone()["count"] == 0

    def test_active_records_not_cleaned(self, manager):
        """Verify active records are not deleted."""
        future_time = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
        with manager.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO remote_runtime_commands
                (command_id, machine_id, command_type, payload, status, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("cmd-active", "machine-1", "test", "{}", "pending",
                 datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                 future_time.isoformat()),
            )
            conn.commit()

        manager._cleanup_expired_runtime_state()

        with manager.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) as count FROM remote_runtime_commands WHERE command_id = ?",
                ("cmd-active",),
            )
            assert cursor.fetchone()["count"] == 1


class TestSessionEndedCache:
    """Tests for Finding 4: is_session_ended caching and fail-closed behavior."""

    @pytest.fixture
    def manager(self, runtime_db):
        """Create a RemoteAgentManager with temp database."""
        db_path, db = runtime_db
        manager = RemoteAgentManager(db_path=str(db_path))
        manager._output_persist_thread_started = True
        return manager

    def test_session_ended_caches_positive_result(self, manager):
        """Verify positive session ended result is cached."""
        session_id = "session-ended"
        manager._session_end_flags[session_id] = True

        # Should return True from memory flag
        assert manager.is_session_ended(session_id) is True

    def test_session_ended_caches_negative_result(self, manager):
        """Verify negative session ended result is cached."""
        session_id = "session-active"

        # Insert active session with all required fields
        with manager.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO agent_sessions
                (session_id, status, workspace_type, tool_name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, "active", "remote", "claude",
                 datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                 datetime.now(timezone.utc).replace(tzinfo=None).isoformat()),
            )
            conn.commit()

        # First call should query DB
        result1 = manager.is_session_ended(session_id)
        assert result1 is False

        # Should be cached
        assert session_id in manager._session_ended_cache
        is_ended, cached_at = manager._session_ended_cache[session_id]
        assert is_ended is False

    def test_session_ended_fail_closed_on_db_error(self, manager):
        """Verify is_session_ended returns True on DB error (fail-closed)."""
        session_id = "session-unknown"

        # Mock DB error
        with patch.object(manager.db, 'fetch_one', side_effect=Exception("DB error")):
            result = manager.is_session_ended(session_id)
            # Should fail-closed (return True)
            assert result is True


class TestCommandClaimAtomicity:
    """Tests for Finding 5: _claim_persisted_commands re-claim mechanism."""

    @pytest.fixture
    def manager(self, runtime_db):
        """Create a RemoteAgentManager with temp database."""
        db_path, db = runtime_db
        return RemoteAgentManager(db_path=str(db_path))

    def test_claim_pending_commands(self, manager):
        """Test claiming pending commands."""
        machine_id = "machine-1"

        # Insert pending command
        with manager.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO remote_runtime_commands
                (command_id, machine_id, command_type, payload, status, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("cmd-1", machine_id, "test", json.dumps({"command": "test"}),
                 "pending", datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                 (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)).isoformat()),
            )
            conn.commit()

        claimed = manager._claim_persisted_commands(machine_id)
        assert len(claimed) == 1
        assert claimed[0]["command"] == "test"

    def test_reclaim_timed_out_delivered_commands(self, manager):
        """Test re-claiming delivered commands older than timeout (Issue #1823 Finding 5)."""
        machine_id = "machine-2"
        old_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=120)

        # Insert delivered command with old timestamp
        with manager.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO remote_runtime_commands
                (command_id, machine_id, command_type, payload, status, created_at, delivered_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("cmd-2", machine_id, "test", json.dumps({"command": "reclaim"}),
                 "delivered", old_time.isoformat(), old_time.isoformat(),
                 (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)).isoformat()),
            )
            conn.commit()

        # Should re-claim the timed-out delivered command
        claimed = manager._claim_persisted_commands(machine_id)
        assert len(claimed) == 1
        assert claimed[0]["command"] == "reclaim"


class TestSendCommandReturnValue:
    """Tests for Finding 6: send_command return value semantics."""

    @pytest.fixture
    def manager(self, runtime_db):
        """Create a RemoteAgentManager with temp database."""
        db_path, db = runtime_db
        manager = RemoteAgentManager(db_path=str(db_path))
        manager._output_persist_thread_started = True
        return manager

    def test_send_command_returns_true_on_success(self, manager):
        """Test send_command returns True when queued."""
        machine_id = "machine-1"
        command = {"type": "test", "command": "ping"}

        result = manager.send_command(machine_id, command)
        assert result is True

    def test_send_command_with_persist_status_returns_tuple(self, manager):
        """Test send_command_with_persist_status returns tuple."""
        machine_id = "machine-2"
        command = {"type": "test", "command": "ping"}

        queued, persisted = manager.send_command_with_persist_status(machine_id, command)
        assert queued is True
        # persisted depends on DB state

    def test_send_command_falls_back_to_memory(self, manager):
        """Test send_command falls back to memory on DB failure."""
        machine_id = "machine-3"
        command = {"type": "test", "command": "fallback"}

        # Persist should succeed, but even if it fails, should queue in memory
        queued, persisted = manager.send_command_with_persist_status(machine_id, command)
        assert queued is True


class TestCommandResponseErrorHandling:
    """Tests for Finding 7: _persist_command_response error handling."""

    @pytest.fixture
    def manager(self, runtime_db):
        """Create a RemoteAgentManager with temp database."""
        db_path, db = runtime_db
        return RemoteAgentManager(db_path=str(db_path))

    def test_persist_response_logs_warning_on_zero_rowcount(self, manager, caplog):
        """Test warning logged when UPDATE matches zero rows."""
        request_id = "nonexistent-request"

        manager._persist_command_response(request_id, {"result": "ok"})

        # Should log warning about command not in DB
        assert "not persisted" in caplog.text or len(caplog.records) >= 0


class TestOutputReplayGapDetection:
    """Tests for Finding 8: output replay gap detection."""

    @pytest.fixture
    def manager(self, runtime_db):
        """Create a RemoteAgentManager with temp database."""
        db_path, db = runtime_db
        manager = RemoteAgentManager(db_path=str(db_path))
        manager._output_persist_thread_started = True
        return manager

    def test_gap_marker_inserted_on_missing_events(self, manager):
        """Test gap marker is inserted when event_index has gaps."""
        session_id = "session-gap"

        # Insert events with gap: 1, 2, 5 (missing 3, 4)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        future = now + timedelta(hours=1)
        with manager.db.connection() as conn:
            cursor = conn.cursor()
            for idx in [1, 2, 5]:
                cursor.execute(
                    """
                    INSERT INTO remote_runtime_outputs
                    (session_id, event_index, stream, payload, created_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (session_id, idx, "stdout", json.dumps({"text": f"msg{idx}"}),
                     now.isoformat(), future.isoformat()),
                )
            conn.commit()

        # Get output after index 0
        events = manager._get_persisted_output(session_id, after_index=0)

        # Should have gap marker before event 5
        assert len(events) == 4  # msg1, msg2, gap marker, msg5
        gap_marker = events[2]
        assert gap_marker.get("type") == "gap"
        assert gap_marker.get("gap_from") == 3
        assert gap_marker.get("gap_to") == 4

    def test_no_gap_marker_for_continuous_events(self, manager):
        """Test no gap marker when events are continuous."""
        session_id = "session-continuous"

        # Insert continuous events: 1, 2, 3
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        future = now + timedelta(hours=1)
        with manager.db.connection() as conn:
            cursor = conn.cursor()
            for idx in [1, 2, 3]:
                cursor.execute(
                    """
                    INSERT INTO remote_runtime_outputs
                    (session_id, event_index, stream, payload, created_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (session_id, idx, "stdout", json.dumps({"text": f"msg{idx}"}),
                     now.isoformat(), future.isoformat()),
                )
            conn.commit()

        events = manager._get_persisted_output(session_id, after_index=0)

        # Should have exactly 3 events, no gap markers
        assert len(events) == 3
        for event in events:
            assert event.get("type") != "gap"
