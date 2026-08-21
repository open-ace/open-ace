"""
Unit tests for DeregisterCompensationWorker.

Issue #2596: Background worker for retrying failed session terminations.
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.services.deregister_compensation_worker import (
    COMPENSATION_BACKOFF_BASE,
    COMPENSATION_MAX_RETRIES,
    DeregisterCompensationWorker,
)


class TestDeregisterCompensationWorker:
    """Tests for DeregisterCompensationWorker (Issue #2596)."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database."""
        db = MagicMock()
        return db

    @pytest.fixture
    def worker(self, mock_db):
        """Create a compensation worker with mocked database."""
        return DeregisterCompensationWorker(db=mock_db)

    def test_compensation_worker_retry(self, worker, mock_db):
        """Test that compensation worker retries failed batches."""
        # Mock a pending failure record
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        failure_record = {
            "id": 1,
            "machine_id": "test-machine-123",
            "batch_index": 0,
            "session_ids": json.dumps(["session-1", "session-2"]),
            "error_message": "Previous error",
            "retry_count": 0,
            "created_at": now - timedelta(minutes=2),  # Old enough to retry
        }

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [failure_record]
        mock_cursor.rowcount = 2
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.connection.return_value = mock_conn

        # Mock _retry_terminate_sessions to succeed
        with patch.object(worker, "_retry_terminate_sessions", return_value=True) as mock_retry:
            worker._process_single_failure(failure_record)

        # Verify retry was called
        mock_retry.assert_called_once_with(["session-1", "session-2"])

    def test_compensation_worker_max_retries(self, worker, mock_db):
        """Test that worker marks failures as failed after max retries."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        failure_record = {
            "id": 2,
            "machine_id": "test-machine-456",
            "batch_index": 0,
            "session_ids": json.dumps(["session-3"]),
            "error_message": "Persistent error",
            "retry_count": COMPENSATION_MAX_RETRIES,  # Already at max
            "created_at": now,
        }

        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.connection.return_value = mock_conn

        worker._process_single_failure(failure_record)

        # Verify _mark_failure_failed was called (UPDATE statement)
        update_calls = [
            call for call in mock_cursor.execute.call_args_list if "UPDATE" in str(call)
        ]
        assert len(update_calls) > 0

    def test_compensation_worker_backoff(self, worker, mock_db):
        """Test that worker respects exponential backoff."""
        # Create failure that is NOT yet ready for retry (created too recently)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        failure_record = {
            "id": 3,
            "machine_id": "test-machine-789",
            "batch_index": 0,
            "session_ids": json.dumps(["session-4"]),
            "error_message": "Recent failure",
            "retry_count": 1,  # Second retry
            "created_at": now - timedelta(seconds=30),  # Only 30s ago
        }

        # For retry_count=1, backoff is 60 * 2^1 = 120 seconds
        # 30s is not enough, so should NOT retry yet
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.connection.return_value = mock_conn

        with patch.object(worker, "_retry_terminate_sessions", return_value=True) as mock_retry:
            worker._process_single_failure(failure_record)

        # Should NOT have retried due to backoff
        mock_retry.assert_not_called()

    def test_retry_terminate_sessions_success(self, worker, mock_db):
        """Test successful session termination retry."""
        session_ids = ["session-a", "session-b"]

        mock_cursor = MagicMock()
        mock_cursor.rowcount = 2
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.connection.return_value = mock_conn

        result = worker._retry_terminate_sessions(session_ids)

        assert result is True

    def test_retry_terminate_sessions_failure(self, worker, mock_db):
        """Test session termination retry failure."""
        session_ids = ["session-x"]

        mock_conn = MagicMock()
        mock_conn.cursor.side_effect = Exception("Database error")
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.connection.return_value = mock_conn

        result = worker._retry_terminate_sessions(session_ids)

        assert result is False

    def test_retry_terminate_empty_sessions(self, worker, mock_db):
        """Test retry with empty session list."""
        result = worker._retry_terminate_sessions([])

        assert result is True

    def test_compensation_worker_invalid_json(self, worker, mock_db):
        """Test handling of invalid session_ids JSON."""
        failure_record = {
            "id": 4,
            "machine_id": "test-machine-bad",
            "batch_index": 0,
            "session_ids": "not-valid-json{",
            "error_message": "Bad data",
            "retry_count": 0,
            "created_at": datetime.now(timezone.utc).replace(tzinfo=None),
        }

        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.connection.return_value = mock_conn

        worker._process_single_failure(failure_record)

        # Should mark as resolved with error message
        update_calls = [
            call for call in mock_cursor.execute.call_args_list if "UPDATE" in str(call)
        ]
        assert len(update_calls) > 0


class TestCompensationWorkerLifecycle:
    """Tests for worker lifecycle methods."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database."""
        return MagicMock()

    def test_start_stop_worker(self, mock_db):
        """Test starting and stopping the worker."""
        worker = DeregisterCompensationWorker(db=mock_db)

        # Start the worker
        worker.start()
        assert worker._running is True

        # Stop the worker
        worker.stop()
        assert worker._running is False

    def test_double_start(self, mock_db):
        """Test that double start is idempotent."""
        worker = DeregisterCompensationWorker(db=mock_db)

        worker.start()
        first_state = worker._running

        worker.start()  # Should be idempotent

        assert worker._running == first_state

        worker.stop()


class TestCompensationIntegration:
    """Integration tests for compensation with datetime objects."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database."""
        db = MagicMock()
        return db

    @pytest.fixture
    def worker(self, mock_db):
        """Create a compensation worker."""
        return DeregisterCompensationWorker(db=mock_db)

    def test_datetime_parameter_not_string(self, worker, mock_db):
        """Verify that datetime objects are passed, not strings."""
        session_ids = ["session-1"]

        captured_args = []

        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1

        def capture_execute(query, args):
            captured_args.append(args)

        mock_cursor.execute = capture_execute
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.connection.return_value = mock_conn

        result = worker._retry_terminate_sessions(session_ids)

        assert result is True
        assert len(captured_args) == 1

        # Verify first argument is datetime object, not string
        first_arg = captured_args[0][0]
        assert isinstance(first_arg, datetime)
        assert not isinstance(first_arg, str)
