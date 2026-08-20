"""
Unit tests for RemoteAgentManager.deregister_machine.

Issue #2596: Cascade termination of active sessions during machine deregistration.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


class TestDeregisterMachine:
    """Tests for deregister_machine method (Issue #2596)."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database."""
        db = MagicMock()
        return db

    @pytest.fixture
    def mock_manager(self, mock_db):
        """Create a RemoteAgentManager with mocked dependencies."""
        # Import inside fixture to avoid import-time side effects
        from app.modules.workspace.remote_agent_manager import RemoteAgentManager

        manager = RemoteAgentManager.__new__(RemoteAgentManager)
        manager.db = mock_db
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

    def test_deregister_terminates_active_sessions(self, mock_manager, mock_db):
        """Test that deregister terminates all active sessions."""
        machine_id = "test-machine-id-123"
        session_ids = ["session-1", "session-2", "session-3"]

        # Mock database cursor
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.connection.return_value = mock_conn

        # Mock methods by replacing them
        mock_manager._get_sessions_to_terminate = MagicMock(return_value=session_ids)
        mock_manager._terminate_sessions_batch = MagicMock(return_value={"success": True})
        mock_manager._cleanup_runtime_commands = MagicMock(return_value=5)
        mock_manager._cleanup_runtime_outputs = MagicMock(return_value=10)
        mock_manager._notify_agent_deregister = MagicMock(return_value=True)
        mock_manager._record_failed_batches = MagicMock()

        mock_cursor.rowcount = 1
        result = mock_manager.deregister_machine(machine_id)

        assert result is True
        mock_manager._get_sessions_to_terminate.assert_called_once_with(machine_id)

    def test_deregister_cleans_commands(self, mock_manager, mock_db):
        """Test that deregister cleans up runtime commands."""
        machine_id = "test-machine-id-456"
        session_ids = ["session-1"]

        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.connection.return_value = mock_conn

        mock_manager._get_sessions_to_terminate = MagicMock(return_value=session_ids)
        mock_manager._terminate_sessions_batch = MagicMock(return_value={"success": True})
        mock_manager._cleanup_runtime_commands = MagicMock(return_value=3)
        mock_manager._cleanup_runtime_outputs = MagicMock(return_value=0)
        mock_manager._notify_agent_deregister = MagicMock(return_value=False)
        mock_manager._record_failed_batches = MagicMock()

        mock_cursor.rowcount = 1
        mock_manager.deregister_machine(machine_id)

        mock_manager._cleanup_runtime_commands.assert_called_once_with(machine_id)

    def test_deregister_cleans_outputs(self, mock_manager, mock_db):
        """Test that deregister cleans up runtime outputs."""
        machine_id = "test-machine-id-789"
        session_ids = ["session-1", "session-2"]

        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.connection.return_value = mock_conn

        mock_manager._get_sessions_to_terminate = MagicMock(return_value=session_ids)
        mock_manager._terminate_sessions_batch = MagicMock(return_value={"success": True})
        mock_manager._cleanup_runtime_commands = MagicMock(return_value=0)
        mock_manager._cleanup_runtime_outputs = MagicMock(return_value=15)
        mock_manager._notify_agent_deregister = MagicMock(return_value=False)
        mock_manager._record_failed_batches = MagicMock()

        mock_cursor.rowcount = 1
        mock_manager.deregister_machine(machine_id)

        mock_manager._cleanup_runtime_outputs.assert_called_once_with(session_ids)

    def test_deregister_batch_failure_handling(self, mock_manager, mock_db):
        """Test that failed batches are recorded for compensation."""
        machine_id = "test-machine-id-fail"
        session_ids = ["session-1", "session-2"]

        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.connection.return_value = mock_conn

        mock_manager._get_sessions_to_terminate = MagicMock(return_value=session_ids)
        # Single batch fails (2 sessions < batch_size=100, so only one batch)
        mock_manager._terminate_sessions_batch = MagicMock(
            return_value={"success": False, "error": "Database error"}
        )
        mock_manager._cleanup_runtime_commands = MagicMock(return_value=0)
        mock_manager._cleanup_runtime_outputs = MagicMock(return_value=0)
        mock_manager._notify_agent_deregister = MagicMock(return_value=False)
        mock_manager._record_failed_batches = MagicMock()

        mock_cursor.rowcount = 1
        mock_manager.deregister_machine(machine_id)

        # Verify failed batch was recorded
        mock_manager._record_failed_batches.assert_called_once()
        call_args = mock_manager._record_failed_batches.call_args
        assert call_args[0][0] == machine_id
        failed_batches = call_args[0][1]
        assert len(failed_batches) == 1
        assert failed_batches[0]["batch_index"] == 0

    def test_deregister_concurrent_safety(self, mock_manager, mock_db):
        """Test that advisory lock is used for concurrent safety on PostgreSQL."""
        machine_id = "test-machine-id-lock"

        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.connection.return_value = mock_conn

        mock_manager._get_sessions_to_terminate = MagicMock(return_value=[])
        mock_manager._notify_agent_deregister = MagicMock(return_value=False)
        mock_manager._record_failed_batches = MagicMock()

        # Mock is_postgresql to return True
        with patch(
            "app.modules.workspace.remote_agent_manager.is_postgresql",
            return_value=True,
        ):
            mock_cursor.rowcount = 1
            result = mock_manager.deregister_machine(machine_id)

        assert result is True
        # Verify pg_advisory_lock was called
        lock_calls = [
            call for call in mock_cursor.execute.call_args_list if "pg_advisory_lock" in str(call)
        ]
        assert len(lock_calls) > 0

    def test_deregister_no_sessions(self, mock_manager, mock_db):
        """Test deregistration with no active sessions."""
        machine_id = "test-machine-id-empty"

        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.connection.return_value = mock_conn

        mock_manager._get_sessions_to_terminate = MagicMock(return_value=[])
        mock_manager._notify_agent_deregister = MagicMock(return_value=False)
        mock_manager._record_failed_batches = MagicMock()

        mock_cursor.rowcount = 1
        result = mock_manager.deregister_machine(machine_id)

        assert result is True


class TestTerminateSessionsBatch:
    """Tests for _terminate_sessions_batch method."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database."""
        db = MagicMock()
        return db

    @pytest.fixture
    def mock_manager(self, mock_db):
        """Create a RemoteAgentManager with mocked dependencies."""
        from app.modules.workspace.remote_agent_manager import RemoteAgentManager

        manager = RemoteAgentManager.__new__(RemoteAgentManager)
        manager.db = mock_db
        manager._lock = MagicMock()
        yield manager

    def test_batch_terminate_success(self, mock_manager, mock_db):
        """Test successful batch termination."""
        machine_id = "test-machine"
        session_ids = ["s1", "s2"]

        mock_cursor = MagicMock()
        mock_cursor.rowcount = 2
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.connection.return_value = mock_conn

        result = mock_manager._terminate_sessions_batch(machine_id, session_ids, 0)

        assert result["success"] is True

    def test_batch_terminate_failure(self, mock_manager, mock_db):
        """Test batch termination failure handling."""
        machine_id = "test-machine"
        session_ids = ["s1", "s2"]

        mock_conn = MagicMock()
        mock_conn.cursor.side_effect = Exception("Database error")
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.connection.return_value = mock_conn

        result = mock_manager._terminate_sessions_batch(machine_id, session_ids, 0)

        assert result["success"] is False
        assert "error" in result
