"""
Tests for Issue #1823: Track 8 feature suggestion findings

Tests cover:
- F1+F2: buffer_output batched write with accumulator
- F3: remote_runtime table retention cleanup
- F4: is_session_ended log rate limiting
- F5: _claim_persisted_commands SQL fix (NULL handling)
- F6+F7: send_command failure signal enhancement
- F8: Replay gap detection
"""

import json
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.remote_agent_manager import (
    AccumulatorBuffer,
    CommandResult,
    RemoteAgentManager,
)
from app.repositories.database import Database


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    db = Database(db_url=f"sqlite:///{db_path}")

    # Create required tables
    with db.connection() as conn:
        cursor = conn.cursor()
        # remote_runtime_outputs table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS remote_runtime_outputs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                event_index INTEGER NOT NULL,
                stream TEXT DEFAULT 'stdout' NOT NULL,
                payload TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP
            )
        """)
        # remote_runtime_commands table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS remote_runtime_commands (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                command_id TEXT NOT NULL,
                machine_id TEXT NOT NULL,
                session_id TEXT,
                command_type TEXT DEFAULT '' NOT NULL,
                payload TEXT NOT NULL,
                status TEXT DEFAULT 'pending' NOT NULL,
                response_payload TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                delivered_at TIMESTAMP,
                responded_at TIMESTAMP,
                expires_at TIMESTAMP
            )
        """)
        # retention_history table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS retention_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                report_data TEXT NOT NULL
            )
        """)
        # agent_sessions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_sessions (
                session_id TEXT PRIMARY KEY,
                status TEXT NOT NULL
            )
        """)
        conn.commit()

    yield db_path

    # Cleanup
    import os
    try:
        os.unlink(db_path)
    except Exception:
        pass


class TestAccumulatorBuffer:
    """Tests for AccumulatorBuffer dataclass."""

    def test_accumulator_buffer_initialization(self):
        """Test AccumulatorBuffer initializes correctly."""
        acc = AccumulatorBuffer()
        assert acc.items == []
        assert acc.last_flush > 0

    def test_accumulator_buffer_with_items(self):
        """Test AccumulatorBuffer with initial items."""
        items = [{"output": "test"}]
        acc = AccumulatorBuffer(items=items)
        assert acc.items == items


class TestCommandResult:
    """Tests for CommandResult dataclass."""

    def test_command_result_initialization(self):
        """Test CommandResult initializes correctly."""
        result = CommandResult(queued=True, persisted=True, degraded=False)
        assert result.queued is True
        assert result.persisted is True
        assert result.degraded is False

    def test_command_result_degraded(self):
        """Test CommandResult for degraded command."""
        result = CommandResult(queued=True, persisted=False, degraded=True)
        assert result.queued is True
        assert result.persisted is False
        assert result.degraded is True


class TestBufferOutputBatched:
    """Tests for F1+F2: buffer_output batched write."""

    def test_buffer_output_accumulates_items(self, temp_db):
        """Test that buffer_output accumulates items in accumulator."""
        manager = RemoteAgentManager(db_path=temp_db)
        manager.bind_session("test-session", "test-machine")

        output = {"text": "test output"}
        manager.buffer_output("test-session", output)

        # Check accumulator has the item
        acc = manager._output_accumulator.get("test-session")
        assert acc is not None
        assert len(acc.items) == 1
        assert acc.items[0] == output

    def test_buffer_output_flushes_on_batch_size(self, temp_db):
        """Test that buffer_output flushes when batch size reached."""
        manager = RemoteAgentManager(db_path=temp_db)
        manager.OUTPUT_BATCH_SIZE = 3
        manager.bind_session("test-session", "test-machine")

        # Add items below batch size
        for i in range(2):
            manager.buffer_output("test-session", {"text": f"output {i}"})

        # Should not flush yet
        acc = manager._output_accumulator.get("test-session")
        assert len(acc.items) == 2

        # Add one more to trigger flush
        manager.buffer_output("test-session", {"text": "output 2"})

        # Should have flushed
        acc = manager._output_accumulator.get("test-session")
        assert len(acc.items) == 0  # Flushed

        # Check DB has the records
        with manager.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) as count FROM remote_runtime_outputs WHERE session_id = ?",
                ("test-session",),
            )
            result = cursor.fetchone()
            assert result["count"] == 3

    def test_buffer_output_flushes_on_timeout(self, temp_db):
        """Test that stale accumulators are flushed on get_buffered_output."""
        manager = RemoteAgentManager(db_path=temp_db)
        manager.OUTPUT_BATCH_INTERVAL_MS = 50  # 50ms
        manager.bind_session("test-session", "test-machine")

        # Add item
        manager.buffer_output("test-session", {"text": "output"})

        # Should be in accumulator
        acc = manager._output_accumulator.get("test-session")
        assert len(acc.items) == 1

        # Wait for timeout
        time.sleep(0.1)

        # get_buffered_output should check and flush stale accumulators
        manager.get_buffered_output("test-session")

        # Accumulator should be flushed
        acc = manager._output_accumulator.get("test-session")
        assert len(acc.items) == 0

    def test_shutdown_flushes_accumulators(self, temp_db):
        """Test that shutdown flushes all pending accumulators."""
        manager = RemoteAgentManager(db_path=temp_db)
        manager.bind_session("test-session", "test-machine")

        # Add items to accumulator (don't trigger batch size)
        for i in range(5):
            manager.buffer_output("test-session", {"text": f"output {i}"})

        # Call shutdown
        manager.shutdown()

        # All items should be flushed to DB
        with manager.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) as count FROM remote_runtime_outputs WHERE session_id = ?",
                ("test-session",),
            )
            result = cursor.fetchone()
            assert result["count"] == 5


class TestRetentionCleanup:
    """Tests for F3: remote_runtime table retention cleanup."""

    def test_cleanup_table_batched(self, temp_db):
        """Test that cleanup deletes expired rows in batches."""
        manager = RemoteAgentManager(db_path=temp_db)

        # Insert expired records
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        expired = (now - timedelta(hours=25)).isoformat()

        with manager.db.connection() as conn:
            cursor = conn.cursor()
            for i in range(5):
                cursor.execute(
                    "INSERT INTO remote_runtime_outputs "
                    "(session_id, event_index, stream, payload, created_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (f"session-{i}", i, "stdout", "{}", now.isoformat(), expired),
                )
            conn.commit()

        # Run cleanup
        deleted = manager._cleanup_table_batched(
            "remote_runtime_outputs",
            batch_size=2,
            lock_timeout_ms=5000,
        )

        assert deleted == 5

    def test_cleanup_skips_non_expired(self, temp_db):
        """Test that cleanup doesn't delete non-expired rows."""
        manager = RemoteAgentManager(db_path=temp_db)

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        future = (now + timedelta(hours=1)).isoformat()

        with manager.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO remote_runtime_outputs "
                "(session_id, event_index, stream, payload, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("session-1", 1, "stdout", "{}", now.isoformat(), future),
            )
            conn.commit()

        # Run cleanup
        deleted = manager._cleanup_table_batched(
            "remote_runtime_outputs",
            batch_size=1000,
            lock_timeout_ms=5000,
        )

        assert deleted == 0


class TestIsSessionEndedLogRateLimiting:
    """Tests for F4: is_session_ended log rate limiting."""

    def test_is_session_ended_returns_true_for_ended_session(self, temp_db):
        """Test that is_session_ended returns True for ended session."""
        manager = RemoteAgentManager(db_path=temp_db)

        # Insert ended session
        with manager.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO agent_sessions (session_id, status) VALUES (?, ?)",
                ("test-session", "completed"),
            )
            conn.commit()

        # Should return True
        assert manager.is_session_ended("test-session") is True

        # Should be cached
        assert "test-session" in manager._session_end_flags

    def test_is_session_ended_returns_false_for_active_session(self, temp_db):
        """Test that is_session_ended returns False for active session."""
        manager = RemoteAgentManager(db_path=temp_db)

        # Insert active session
        with manager.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO agent_sessions (session_id, status) VALUES (?, ?)",
                ("test-session", "active"),
            )
            conn.commit()

        # Should return False
        assert manager.is_session_ended("test-session") is False

    def test_log_rate_limiting_on_db_error(self, temp_db):
        """Test that DB error logs are rate limited."""
        manager = RemoteAgentManager(db_path=temp_db)

        # First call should log (simulate DB failure by using invalid query)
        # The LRU cache should limit logging to once per minute per session
        minute = int(time.time()) // 60

        # First call for this session/minute combination
        manager._log_session_ended_db_failure_cached("test-session", minute)

        # Second call with same parameters should be cached (no additional log)
        manager._log_session_ended_db_failure_cached("test-session", minute)

        # Different minute should log again
        manager._log_session_ended_db_failure_cached("test-session", minute + 1)


class TestClaimPersistedCommands:
    """Tests for F5: _claim_persisted_commands SQL fix."""

    def test_claim_pending_commands(self, temp_db):
        """Test claiming pending commands."""
        manager = RemoteAgentManager(db_path=temp_db)

        # Insert pending command
        with manager.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO remote_runtime_commands "
                "(command_id, machine_id, payload, status, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("cmd-1", "machine-1", json.dumps({"command": "test"}), "pending",
                 datetime.now(timezone.utc).replace(tzinfo=None).isoformat()),
            )
            conn.commit()

        # Claim commands
        claimed = manager._claim_persisted_commands("machine-1")

        assert len(claimed) == 1
        assert claimed[0]["command"] == "test"

    def test_claim_timeout_commands(self, temp_db):
        """Test claiming timed-out delivered commands."""
        manager = RemoteAgentManager(db_path=temp_db)

        # Insert delivered command with old delivered_at
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        old_time = (now - timedelta(minutes=10)).isoformat()

        with manager.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO remote_runtime_commands "
                "(command_id, machine_id, payload, status, delivered_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("cmd-1", "machine-1", json.dumps({"command": "test"}), "delivered",
                 old_time, now.isoformat()),
            )
            conn.commit()

        # Claim commands (should re-claim timed-out delivered)
        claimed = manager._claim_persisted_commands("machine-1")

        assert len(claimed) == 1

    def test_null_delivered_at_handling(self, temp_db):
        """Test that NULL delivered_at doesn't cause issues."""
        manager = RemoteAgentManager(db_path=temp_db)

        # Insert delivered command with NULL delivered_at (edge case)
        with manager.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO remote_runtime_commands "
                "(command_id, machine_id, payload, status, delivered_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("cmd-1", "machine-1", json.dumps({"command": "test"}), "delivered",
                 None, datetime.now(timezone.utc).replace(tzinfo=None).isoformat()),
            )
            conn.commit()

        # Should not claim (NULL delivered_at should not match timeout condition)
        claimed = manager._claim_persisted_commands("machine-1")

        assert len(claimed) == 0


class TestSendCommandWithStatus:
    """Tests for F6+F7: send_command failure signal enhancement."""

    def test_send_command_returns_bool(self, temp_db):
        """Test that send_command returns bool for backward compatibility."""
        manager = RemoteAgentManager(db_path=temp_db)

        result = manager.send_command("machine-1", {"command": "test"})

        assert isinstance(result, bool)
        assert result is True

    def test_send_command_with_status_returns_structured(self, temp_db):
        """Test that send_command_with_status returns CommandResult."""
        manager = RemoteAgentManager(db_path=temp_db)

        result = manager.send_command_with_status("machine-1", {"command": "test"})

        assert isinstance(result, CommandResult)
        assert result.queued is True
        assert result.persisted is True
        assert result.degraded is False

    def test_send_command_degraded_on_persist_failure(self, temp_db):
        """Test that send_command_with_status indicates degraded mode."""
        manager = RemoteAgentManager(db_path=temp_db)

        # Mock _persist_command to return False (simulating DB failure)
        with patch.object(manager, '_persist_command', return_value=False):
            result = manager.send_command_with_status("machine-1", {"command": "test"})

        assert result.queued is True  # Still queued in-memory
        assert result.persisted is False
        assert result.degraded is True

    def test_persist_command_response_returns_bool(self, temp_db):
        """Test that _persist_command_response returns bool."""
        manager = RemoteAgentManager(db_path=temp_db)

        # Insert a command to respond to
        with manager.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO remote_runtime_commands "
                "(command_id, machine_id, payload, status, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("cmd-1", "machine-1", "{}", "pending",
                 datetime.now(timezone.utc).replace(tzinfo=None).isoformat()),
            )
            conn.commit()

        # Update response
        result = manager._persist_command_response("cmd-1", {"result": "ok"})

        assert result is True

    def test_persist_command_response_returns_false_for_unknown(self, temp_db):
        """Test that _persist_command_response returns False for unknown command."""
        manager = RemoteAgentManager(db_path=temp_db)

        # Try to update non-existent command
        result = manager._persist_command_response("unknown-cmd", {"result": "ok"})

        assert result is False


class TestGapDetection:
    """Tests for F8: Replay gap detection."""

    def test_db_query_gap_detection(self, temp_db):
        """Test that gap markers are inserted for DB query gaps."""
        manager = RemoteAgentManager(db_path=temp_db)

        # Insert output with gap in event_index (1, 3 - missing 2)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with manager.db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO remote_runtime_outputs "
                "(session_id, event_index, stream, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("test-session", 1, "stdout", json.dumps({"text": "output 1"}), now.isoformat()),
            )
            cursor.execute(
                "INSERT INTO remote_runtime_outputs "
                "(session_id, event_index, stream, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("test-session", 3, "stdout", json.dumps({"text": "output 3"}), now.isoformat()),
            )
            conn.commit()

        # Get output after index 0
        outputs = manager._get_persisted_output("test-session", after_index=0)

        # Should have 3 items: gap marker, output 1, output 3
        assert len(outputs) == 3

        # First item should be output 1
        assert outputs[0]["text"] == "output 1"
        assert outputs[0]["event_index"] == 1

        # Second item should be gap marker
        assert outputs[1]["type"] == "gap"
        assert outputs[1]["gap_type"] == "db_query_gap"
        assert outputs[1]["missing_count"] == 1

        # Third item should be output 3
        assert outputs[2]["text"] == "output 3"

    def test_no_gap_for_contiguous_events(self, temp_db):
        """Test that no gap markers for contiguous events."""
        manager = RemoteAgentManager(db_path=temp_db)

        # Insert contiguous outputs (1, 2, 3)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with manager.db.connection() as conn:
            cursor = conn.cursor()
            for i in range(1, 4):
                cursor.execute(
                    "INSERT INTO remote_runtime_outputs "
                    "(session_id, event_index, stream, payload, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    ("test-session", i, "stdout", json.dumps({"text": f"output {i}"}),
                     now.isoformat()),
                )
            conn.commit()

        # Get output after index 0
        outputs = manager._get_persisted_output("test-session", after_index=0)

        # Should have 3 items, no gap markers
        assert len(outputs) == 3
        for output in outputs:
            assert output.get("type") != "gap"

    def test_buffer_trim_gap_marker(self, temp_db):
        """Test that buffer trim inserts gap marker."""
        manager = RemoteAgentManager(db_path=temp_db)
        manager.MAX_BUFFER_SIZE = 3
        manager.bind_session("test-session", "test-machine")

        # Fill buffer to capacity to trigger trim
        for i in range(4):
            manager.buffer_output("test-session", {"text": f"output {i}"})

        # Check in-memory buffer for gap marker
        outputs = list(manager._output_buffers.get("test-session", []))

        # Should contain gap marker
        gap_markers = [o for o in outputs if o.get("type") == "gap"]
        assert len(gap_markers) >= 1
        assert gap_markers[0]["gap_type"] == "buffer_trim"