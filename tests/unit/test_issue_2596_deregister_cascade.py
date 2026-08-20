"""Unit tests for Issue #2596: Machine deregistration cascade session termination."""

from __future__ import annotations

import pytest

from app.modules.workspace.remote_agent_manager import (
    DEREGISTER_BATCH_SIZE,
    SESSION_STATES_TERMINAL,
    SESSION_STATES_TO_TERMINATE,
)


class TestDeregisterMachineSessionTermination:
    """Tests for session termination during machine deregistration."""

    def test_batch_size_constant(self):
        """Test that batch size is correctly defined."""
        assert DEREGISTER_BATCH_SIZE == 100

    def test_session_states_to_terminate(self):
        """Test that all non-terminal states are included."""
        expected_states = ["active", "paused", "pending", "starting", "stopping"]
        assert expected_states == SESSION_STATES_TO_TERMINATE

    def test_session_states_terminal(self):
        """Test that terminal states are correctly defined."""
        expected_states = ["completed", "stopped", "error", "timeout"]
        assert expected_states == SESSION_STATES_TERMINAL


class TestMachineCheckForSession:
    """Tests for machine existence check in session operations."""

    def test_check_machine_exists_for_session_returns_none_when_machine_exists(self, app_context):
        """Test that check passes when machine exists."""
        from unittest.mock import patch

        from app.routes.remote import _check_machine_exists_for_session

        session_info = {
            "session_id": "test-session",
            "remote_machine_id": "test-machine",
        }

        with patch("app.routes.remote.get_remote_agent_manager") as mock_mgr:
            mock_mgr.return_value.get_machine.return_value = {"machine_id": "test-machine"}
            result = _check_machine_exists_for_session(session_info, "test_op")
            assert result is None

    def test_check_machine_exists_for_session_returns_error_when_machine_missing(self, app_context):
        """Test that check returns 409 when machine has been deregistered."""
        from unittest.mock import patch

        from app.routes.remote import _check_machine_exists_for_session

        session_info = {
            "session_id": "test-session",
            "remote_machine_id": "deregistered-machine",
        }

        with patch("app.routes.remote.get_remote_agent_manager") as mock_mgr:
            mock_mgr.return_value.get_machine.return_value = None
            result = _check_machine_exists_for_session(session_info, "test_op")
            assert result is not None
            # Result is (jsonify_response, 409)
            assert result[1] == 409


class TestDeregisterCompensationWorker:
    """Tests for the compensation worker."""

    def test_compensation_worker_initialization(self):
        """Test that compensation worker can be initialized."""
        from app.repositories.database import Database
        from app.services.deregister_compensation_worker import DeregisterCompensationWorker

        db = Database(db_url="sqlite:///:memory:")
        worker = DeregisterCompensationWorker(db)

        assert worker.db is not None
        assert worker._running is False

    def test_retry_terminate_sessions_empty_list(self):
        """Test that retry with empty session list succeeds."""
        from app.repositories.database import Database
        from app.services.deregister_compensation_worker import DeregisterCompensationWorker

        db = Database(db_url="sqlite:///:memory:")
        worker = DeregisterCompensationWorker(db)

        result = worker._retry_terminate_sessions([])
        assert result is True


# Fixtures
@pytest.fixture
def app_context():
    """Create a Flask application context for testing."""
    from app import create_app

    app = create_app()
    with app.app_context():
        yield
