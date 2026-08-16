#!/usr/bin/env python3
"""
Tests for Remote Session Status Update

Unit tests for process_session_status_update() method, covering
TypeError fix, cli_session_id backfill, and error handling.
"""

import logging
import threading
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.session_manager import SessionStatus, SessionType

# ==================== Fixtures ====================


@pytest.fixture
def mock_session_manager():
    """Create a mock SessionManager."""
    return MagicMock()


@pytest.fixture
def mock_agent_manager():
    """Create a mock RemoteAgentManager."""
    mgr = MagicMock()
    mgr.unbind_session = MagicMock()
    mgr.mark_session_ended = MagicMock()
    return mgr


@pytest.fixture
def mock_session():
    """Create a mock AgentSession with default values."""
    session = MagicMock()
    session.session_id = "test-session-123"
    session.status = "active"
    session.paused_at = None
    session.cli_session_id = ""
    session.user_id = 1
    session.tenant_id = 1
    return session


@pytest.fixture
def remote_session_manager(mock_session_manager, mock_agent_manager):
    """Create a RemoteSessionManager with mocked dependencies."""
    from app.modules.workspace.remote_session_manager import RemoteSessionManager

    with (
        patch(
            "app.modules.workspace.remote_session_manager.SessionManager",
            return_value=mock_session_manager,
        ),
        patch(
            "app.modules.workspace.remote_session_manager.get_remote_agent_manager",
            return_value=mock_agent_manager,
        ),
        patch("app.modules.workspace.remote_session_manager.APIKeyProxyService"),
        patch("app.modules.workspace.remote_session_manager.MessageRepository"),
        patch("app.modules.workspace.remote_session_manager.UserRepository"),
        patch("app.modules.workspace.remote_session_manager.get_run_recorder"),
        patch("app.modules.workspace.remote_session_manager.get_evaluator"),
    ):
        mgr = RemoteSessionManager()
        mgr._session_manager = mock_session_manager
        mgr._agent_manager = mock_agent_manager
        mgr._session_permission_modes = {}
        mgr._run_recorder = MagicMock()
        yield mgr


# ==================== Test Cases ====================


class TestProcessSessionStatusUpdate:
    """Tests for process_session_status_update() method."""

    # TC-1: Normal status update without cli_session_id
    def test_tc1_normal_status_update_without_cli_session_id(
        self, remote_session_manager, mock_session_manager, mock_session
    ):
        """Session status should update correctly without cli_session_id."""
        mock_session_manager.get_session.return_value = mock_session

        remote_session_manager.process_session_status_update(
            session_id="test-session-123", status="paused"
        )

        assert mock_session.status == "paused"
        assert mock_session.paused_at is not None
        mock_session_manager.update_session.assert_called_once_with(mock_session)

    # TC-2: Status update with cli_session_id backfill
    def test_tc2_status_update_with_cli_session_id_backfill(
        self, remote_session_manager, mock_session_manager, mock_session
    ):
        """cli_session_id should be backfilled when provided and session has none."""
        mock_session_manager.get_session.return_value = mock_session

        remote_session_manager.process_session_status_update(
            session_id="test-session-123",
            status="running",
            cli_session_id="cli-sess-789",
        )

        assert mock_session.status == "active"
        assert mock_session.cli_session_id == "cli-sess-789"
        mock_session_manager.update_session.assert_called_once_with(mock_session)

    # TC-3: cli_session_id is empty string
    def test_tc3_cli_session_id_empty_string(
        self, remote_session_manager, mock_session_manager, mock_session
    ):
        """Empty cli_session_id should not trigger backfill."""
        mock_session_manager.get_session.return_value = mock_session

        remote_session_manager.process_session_status_update(
            session_id="test-session-123", status="running", cli_session_id=""
        )

        assert mock_session.status == "active"
        assert mock_session.cli_session_id == ""
        mock_session_manager.update_session.assert_called_once_with(mock_session)

    # TC-4: Session does not exist
    def test_tc4_session_not_exist(self, remote_session_manager, mock_session_manager):
        """Method should return silently when session does not exist."""
        mock_session_manager.get_session.return_value = None

        # Should not raise any exception
        remote_session_manager.process_session_status_update(
            session_id="non-existent-session", status="paused"
        )

        # update_session should not be called
        mock_session_manager.update_session.assert_not_called()

    # TC-5: Session already has cli_session_id
    def test_tc5_session_already_has_cli_session_id(
        self, remote_session_manager, mock_session_manager, mock_session
    ):
        """Should not overwrite existing cli_session_id."""
        mock_session.cli_session_id = "existing-cli-id"
        mock_session_manager.get_session.return_value = mock_session

        remote_session_manager.process_session_status_update(
            session_id="test-session-123",
            status="running",
            cli_session_id="new-cli-id",
        )

        assert mock_session.status == "active"
        assert mock_session.cli_session_id == "existing-cli-id"
        mock_session_manager.update_session.assert_called_once_with(mock_session)

    # TC-6: Rapid consecutive updates (idempotency)
    def test_tc6_rapid_consecutive_updates_idempotent(
        self, remote_session_manager, mock_session_manager, mock_session
    ):
        """Consecutive updates should be idempotent for cli_session_id."""
        mock_session_manager.get_session.return_value = mock_session

        # First update with cli_session_id
        remote_session_manager.process_session_status_update(
            session_id="test-session-123",
            status="paused",
            cli_session_id="cli-sess-789",
        )

        first_cli_id = mock_session.cli_session_id
        assert first_cli_id == "cli-sess-789"

        # Reset mock to simulate second call getting the same session
        mock_session_manager.update_session.reset_mock()
        mock_session.cli_session_id = "cli-sess-789"  # Simulate persisted state

        # Second update with different cli_session_id (should not overwrite)
        remote_session_manager.process_session_status_update(
            session_id="test-session-123",
            status="running",
            cli_session_id="different-cli-id",
        )

        assert mock_session.cli_session_id == "cli-sess-789"
        mock_session_manager.update_session.assert_called_once()


class TestStatusTransitions:
    """Tests for various status transition scenarios."""

    def test_status_transition_running_to_active(
        self, remote_session_manager, mock_session_manager, mock_session
    ):
        """Running status should set session to active."""
        mock_session_manager.get_session.return_value = mock_session

        remote_session_manager.process_session_status_update(
            session_id="test-session-123", status="running"
        )

        assert mock_session.status == "active"
        assert mock_session.paused_at is None

    def test_status_transition_stopped_remains_stopped(
        self, remote_session_manager, mock_session_manager, mock_session, mock_agent_manager
    ):
        """Stopped status should remain as stopped (not converted to completed)."""
        mock_session_manager.get_session.return_value = mock_session

        remote_session_manager.process_session_status_update(
            session_id="test-session-123", status="stopped"
        )

        assert mock_session.status == "stopped"
        assert mock_session.paused_at is None
        mock_agent_manager.unbind_session.assert_called_once_with("test-session-123")
        mock_agent_manager.mark_session_ended.assert_called_once_with("test-session-123")

    def test_status_transition_error(
        self, remote_session_manager, mock_session_manager, mock_session, mock_agent_manager
    ):
        """Error status should set session to error and clean up."""
        mock_session_manager.get_session.return_value = mock_session

        remote_session_manager.process_session_status_update(
            session_id="test-session-123", status="error"
        )

        assert mock_session.status == "error"
        assert mock_session.paused_at is None
        mock_agent_manager.mark_session_ended.assert_called_once_with("test-session-123")

    def test_status_transition_exited_keeps_active(
        self, remote_session_manager, mock_session_manager, mock_session
    ):
        """Exited status should keep session active for follow-up messages."""
        mock_session_manager.get_session.return_value = mock_session

        remote_session_manager.process_session_status_update(
            session_id="test-session-123", status="exited"
        )

        assert mock_session.status == "active"


class TestBackfillLogging:
    """Tests for backfill logging behavior."""

    def test_backfill_logs_info(
        self, remote_session_manager, mock_session_manager, mock_session, caplog
    ):
        """Successful backfill should log INFO."""
        mock_session_manager.get_session.return_value = mock_session

        with caplog.at_level(logging.INFO):
            remote_session_manager.process_session_status_update(
                session_id="test-session-123",
                status="running",
                cli_session_id="cli-sess-789",
            )

        assert any("Backfilled cli_session_id" in record.message for record in caplog.records)

    def test_no_backfill_when_session_has_cli_id(
        self, remote_session_manager, mock_session_manager, mock_session, caplog
    ):
        """No backfill log when session already has cli_session_id."""
        mock_session.cli_session_id = "existing-id"
        mock_session_manager.get_session.return_value = mock_session

        with caplog.at_level(logging.INFO):
            remote_session_manager.process_session_status_update(
                session_id="test-session-123",
                status="running",
                cli_session_id="new-id",
            )

        # Should not log backfill since session already has cli_session_id
        assert not any("Backfilled cli_session_id" in record.message for record in caplog.records)
