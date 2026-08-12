"""
Tests for Scheduler Execution Guard (Issue #2333)

Unit tests for unified scheduler locking with fail-closed guarantees.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.repositories.database import Database
from app.services.scheduler_guard import (
    LockAcquisitionError,
    SchedulerExecutionGuard,
    check_scheduler_process_guard,
    generate_leader_id,
    job_name_to_lock_key,
)


@pytest.fixture
def mock_db():
    """Create mock database instance."""
    db = MagicMock(spec=Database)
    db.is_postgresql = False  # Default to SQLite for unit tests
    return db


@pytest.fixture
def mock_postgres_db():
    """Create mock PostgreSQL database instance."""
    db = MagicMock(spec=Database)
    db.is_postgresql = True
    return db


class TestSchedulerGuardHelpers:
    """Tests for helper functions."""

    def test_generate_leader_id_unique(self):
        """Test that leader IDs are unique."""
        id1 = generate_leader_id()
        id2 = generate_leader_id()

        assert id1 != id2
        assert "-" in id1
        assert len(id1) > 10

    def test_job_name_to_lock_key_consistent(self):
        """Test lock key is consistent for same job name."""
        key1 = job_name_to_lock_key("test_job")
        key2 = job_name_to_lock_key("test_job")

        assert key1 == key2
        assert isinstance(key1, int)

    def test_job_name_to_lock_key_different(self):
        """Test lock keys are different for different job names."""
        key1 = job_name_to_lock_key("job1")
        key2 = job_name_to_lock_key("job2")

        assert key1 != key2


class TestSchedulerGuardSessionLock:
    """Tests for session_lock strategy."""

    def test_guard_raises_error_invalid_strategy(self, mock_db):
        """Test that invalid strategy raises error."""
        with pytest.raises(ValueError, match="Invalid strategy"):
            SchedulerExecutionGuard("test_job", mock_db, strategy="invalid")

    def test_guard_skip_execution_on_lock_failure_sqlite(self, mock_db):
        """Test that SQLite guard proceeds without real lock (development mode)."""
        with patch("app.services.scheduler_guard.is_postgresql", return_value=False):
            guard = SchedulerExecutionGuard("test_job", mock_db, strategy="session_lock")

            # SQLite should proceed without actual lock
            executed = False
            with guard:
                # Job body runs
                executed = True

            # Job should have executed inside the context
            assert executed

    def test_guard_releases_on_normal_exit(self, mock_db):
        """Test that guard releases lock on normal exit."""
        with patch("app.services.scheduler_guard.is_postgresql", return_value=False):
            guard = SchedulerExecutionGuard("test_job", mock_db, strategy="session_lock")

            with guard:
                # Inside context - should have timestamps
                assert guard.lock_acquired_at is not None

            # After exit - should have released timestamp
            assert guard.lock_released_at is not None

    def test_guard_releases_on_exception(self, mock_db):
        """Test that guard releases lock even on exception."""
        with patch("app.services.scheduler_guard.is_postgresql", return_value=False):
            guard = SchedulerExecutionGuard("test_job", mock_db, strategy="session_lock")

            with pytest.raises(RuntimeError):
                with guard:
                    raise RuntimeError("Job failed")

            # Should still have released
            assert guard.lock_acquired_at is not None
            assert guard.lock_released_at is not None

    def test_guard_postgres_session_lock_acquisition(self, mock_postgres_db):
        """Test PostgreSQL session lock acquisition with held connection."""
        # Mock connection and cursor
        mock_connection = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (True,)  # Lock acquired
        mock_connection.cursor.return_value = mock_cursor
        mock_postgres_db.get_connection.return_value = mock_connection

        with patch("app.services.scheduler_guard.is_postgresql", return_value=True):
            guard = SchedulerExecutionGuard("test_job", mock_postgres_db, strategy="session_lock")

            executed = False
            with guard:
                # Job body runs
                executed = True

            # Should have called pg_try_advisory_lock
            mock_cursor.execute.assert_called()
            assert executed

    def test_guard_postgres_session_lock_timeout(self, mock_postgres_db):
        """Test PostgreSQL session lock timeout."""
        # Mock connection that never acquires lock
        mock_connection = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (False,)  # Lock not acquired
        mock_connection.cursor.return_value = mock_cursor
        mock_postgres_db.get_connection.return_value = mock_connection

        with patch("app.services.scheduler_guard.is_postgresql", return_value=True):
            guard = SchedulerExecutionGuard(
                "test_job",
                mock_postgres_db,
                strategy="session_lock",
            )
            # Set very short timeout for test
            guard._lock_acquisition_timeout = 0.1

            with pytest.raises(LockAcquisitionError, match="Could not acquire session lock"):
                with guard:
                    pass


class TestSchedulerGuardHeartbeat:
    """Tests for heartbeat strategy."""

    def test_guard_heartbeat_skips_on_lock_failure(self, mock_postgres_db):
        """Test heartbeat strategy skips when lease unavailable."""
        # Mock database that returns no leader match
        mock_postgres_db.fetch_one.return_value = None
        mock_postgres_db.get_connection.return_value.cursor.return_value.fetchone.return_value = (
            1,
        )

        with patch("app.services.scheduler_guard.is_postgresql", return_value=True):
            guard = SchedulerExecutionGuard("test_job", mock_postgres_db, strategy="heartbeat")

            with pytest.raises(LockAcquisitionError, match="Could not acquire heartbeat lease"):
                with guard:
                    pass

    def test_guard_heartbeat_acquires_lease(self, mock_postgres_db):
        """Test heartbeat strategy acquires lease successfully."""
        # Mock fencing token sequence
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (12345,)
        mock_postgres_db.get_connection.return_value.cursor.return_value = mock_cursor

        # Mock leader verification
        mock_postgres_db.fetch_one.return_value = {
            "leader_id": "test-leader",
            "fencing_token": 12345,
        }

        with patch("app.services.scheduler_guard.is_postgresql", return_value=True):
            # Use leader_id that matches what we'll verify
            guard = SchedulerExecutionGuard(
                "test_job",
                mock_postgres_db,
                strategy="heartbeat",
                leader_id="test-leader",
            )

            with guard:
                # Should have fencing token
                assert guard.get_fencing_token() == 12345
                # Should be acquired inside the context
                assert guard.acquired

    def test_check_lease_valid_returns_true_initially(self, mock_db):
        """Test check_lease_valid returns True initially."""
        with patch("app.services.scheduler_guard.is_postgresql", return_value=False):
            guard = SchedulerExecutionGuard("test_job", mock_db, strategy="heartbeat")

            with guard:
                assert guard.check_lease_valid() is True

    def test_check_lease_valid_returns_false_after_loss(self, mock_db):
        """Test check_lease_valid returns False after lease loss."""
        with patch("app.services.scheduler_guard.is_postgresql", return_value=False):
            guard = SchedulerExecutionGuard("test_job", mock_db, strategy="heartbeat")

            with guard:
                # Simulate lease loss
                guard._lease_lost_event.set()
                assert guard.check_lease_valid() is False


class TestSchedulerGuardRecording:
    """Tests for run recording."""

    def test_guard_records_run_on_success(self, mock_db):
        """Test guard records successful run."""
        with patch("app.services.scheduler_guard.is_postgresql", return_value=False):
            guard = SchedulerExecutionGuard("test_job", mock_db, strategy="session_lock")

            with guard:
                pass

            # Should have recorded the run
            mock_db.execute.assert_called()

    def test_guard_records_run_on_failure(self, mock_db):
        """Test guard records failed run."""
        with patch("app.services.scheduler_guard.is_postgresql", return_value=False):
            guard = SchedulerExecutionGuard("test_job", mock_db, strategy="session_lock")

            with pytest.raises(RuntimeError):
                with guard:
                    raise RuntimeError("Job failed")

            # Should have recorded the failure
            mock_db.execute.assert_called()


class TestSchedulerGuardConnectionSafety:
    """Tests for connection lifecycle safety."""

    def test_guard_closes_connection_on_exception(self, mock_postgres_db):
        """Test guard closes connection even on exception."""
        mock_connection = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (True,)
        mock_connection.cursor.return_value = mock_cursor
        mock_postgres_db.get_connection.return_value = mock_connection

        with patch("app.services.scheduler_guard.is_postgresql", return_value=True):
            guard = SchedulerExecutionGuard("test_job", mock_postgres_db, strategy="session_lock")

            with pytest.raises(RuntimeError):
                with guard:
                    raise RuntimeError("Job failed")

            # Connection should be closed
            mock_connection.close.assert_called()

    def test_guard_handles_close_error_gracefully(self, mock_postgres_db):
        """Test guard handles connection close errors."""
        mock_connection = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (True,)
        mock_connection.cursor.return_value = mock_cursor
        mock_connection.close.side_effect = Exception("Close failed")
        mock_postgres_db.get_connection.return_value = mock_connection

        with patch("app.services.scheduler_guard.is_postgresql", return_value=True):
            guard = SchedulerExecutionGuard("test_job", mock_postgres_db, strategy="session_lock")

            # Should not raise even if close fails
            with guard:
                # Should be acquired inside the context
                assert guard.acquired


class TestCheckSchedulerProcessGuard:
    """Tests for process guard checks."""

    def test_returns_true_in_scheduler_mode(self):
        """Test returns True when SCHEDULER_MODE=scheduler."""
        with patch.dict("os.environ", {"SCHEDULER_MODE": "scheduler"}):
            assert check_scheduler_process_guard("test_job") is True

    def test_returns_false_in_web_mode(self):
        """Test returns False and logs warning when SCHEDULER_MODE=web."""
        with patch.dict("os.environ", {"SCHEDULER_MODE": "web"}):
            result = check_scheduler_process_guard("test_job")
            assert result is False

    def test_defaults_to_web_mode(self):
        """Test defaults to web mode when SCHEDULER_MODE not set."""
        # Ensure SCHEDULER_MODE is not set
        env_copy = os.environ.copy()
        if "SCHEDULER_MODE" in env_copy:
            del env_copy["SCHEDULER_MODE"]
        with patch.dict("os.environ", env_copy, clear=True):
            result = check_scheduler_process_guard("test_job")
            assert result is False


class TestSchedulerGuardProperties:
    """Tests for guard properties."""

    def test_acquired_property(self, mock_db):
        """Test acquired property."""
        with patch("app.services.scheduler_guard.is_postgresql", return_value=False):
            guard = SchedulerExecutionGuard("test_job", mock_db, strategy="session_lock")

            assert guard.acquired is False

            with guard:
                assert guard.acquired is True

            assert guard.acquired is False  # Reset after exit

    def test_timing_properties(self, mock_db):
        """Test lock timing properties."""
        with patch("app.services.scheduler_guard.is_postgresql", return_value=False):
            guard = SchedulerExecutionGuard("test_job", mock_db, strategy="session_lock")

            assert guard.lock_acquired_at is None
            assert guard.lock_released_at is None

            with guard:
                pass

            assert guard.lock_acquired_at is not None
            assert guard.lock_released_at is not None
            assert guard.lock_released_at >= guard.lock_acquired_at
