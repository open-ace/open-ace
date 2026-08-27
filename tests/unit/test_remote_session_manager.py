"""
Unit tests for RemoteSessionManager.

Issue #2597: Prevent zombie sessions during heartbeat tolerance window.
Issue #3139: Auto-resume for loop detection aborts in autonomous modes.
"""

from unittest.mock import MagicMock, patch

import pytest


class TestProcessRequestStateLoopAbortAutoResume:
    """Tests for auto-resume after loop detection abort (Issue #3139)."""

    @pytest.fixture
    def mock_agent_manager(self):
        """Create a mock agent manager."""
        mock = MagicMock()
        mock.buffer_output = MagicMock()
        return mock

    @pytest.fixture
    def mock_session_manager(self):
        """Create a mock session manager."""
        mock = MagicMock()
        return mock

    @pytest.fixture
    def manager(self, mock_agent_manager, mock_session_manager):
        """Create a RemoteSessionManager with mocked dependencies."""
        with patch(
            "app.modules.workspace.remote_session_manager.get_remote_agent_manager",
            return_value=mock_agent_manager,
        ):
            with patch(
                "app.modules.workspace.remote_session_manager.SessionManager",
                return_value=mock_session_manager,
            ):
                with patch(
                    "app.modules.workspace.remote_session_manager.APIKeyProxyService",
                ):
                    from app.modules.workspace.remote_session_manager import RemoteSessionManager

                    mgr = RemoteSessionManager()
                    mgr._agent_manager = mock_agent_manager
                    mgr._session_manager = mock_session_manager
                    yield mgr

    def test_auto_resumes_yolo_mode_on_loop_abort(self, manager, mock_session_manager):
        """
        Issue #3139: Should auto-resume when loop detection abort occurs
        in yolo mode session.
        """
        session_id = "test-session-id"

        # Set permission mode to yolo
        manager._session_permission_modes[session_id] = "yolo"

        # Mock session as active
        mock_session = MagicMock()
        mock_session.status = "active"
        mock_session_manager.get_session.return_value = mock_session

        # Mock send_message to capture the resume call
        with patch.object(manager, "send_message") as mock_send:
            mock_send.return_value = True

            # Process loop detection abort
            manager.process_request_state(
                session_id=session_id,
                state="aborted",
                reason="loop",
            )

            # Wait for timer to execute
            import time

            time.sleep(0.6)

            # Verify send_message was called with "继续"
            mock_send.assert_called_once_with(session_id, "继续")

    def test_auto_resumes_auto_edit_mode_on_loop_abort(self, manager, mock_session_manager):
        """
        Issue #3139: Should auto-resume when loop detection abort occurs
        in auto-edit mode session.
        """
        session_id = "test-session-id"

        # Set permission mode to auto-edit
        manager._session_permission_modes[session_id] = "auto-edit"

        # Mock session as active
        mock_session = MagicMock()
        mock_session.status = "active"
        mock_session_manager.get_session.return_value = mock_session

        # Mock send_message
        with patch.object(manager, "send_message") as mock_send:
            mock_send.return_value = True

            manager.process_request_state(
                session_id=session_id,
                state="aborted",
                reason="system",
            )

            import time

            time.sleep(0.6)

            mock_send.assert_called_once_with(session_id, "继续")

    def test_no_auto_resume_for_user_abort(self, manager, mock_session_manager):
        """
        Issue #3139: Should NOT auto-resume for user-initiated abort
        even in yolo mode.
        """
        session_id = "test-session-id"

        manager._session_permission_modes[session_id] = "yolo"

        mock_session = MagicMock()
        mock_session.status = "active"
        mock_session_manager.get_session.return_value = mock_session

        with patch.object(manager, "send_message") as mock_send:
            manager.process_request_state(
                session_id=session_id,
                state="aborted",
                reason="user",
            )

            import time

            time.sleep(0.6)

            # Should NOT auto-resume for user abort
            mock_send.assert_not_called()

    def test_no_auto_resume_for_default_mode(self, manager, mock_session_manager):
        """
        Issue #3139: Should NOT auto-resume for default permission mode
        even on loop detection abort.
        """
        session_id = "test-session-id"

        # Default mode (no entry means default)
        manager._session_permission_modes[session_id] = "default"

        mock_session = MagicMock()
        mock_session.status = "active"
        mock_session_manager.get_session.return_value = mock_session

        with patch.object(manager, "send_message") as mock_send:
            manager.process_request_state(
                session_id=session_id,
                state="aborted",
                reason="loop",
            )

            import time

            time.sleep(0.6)

            # Should NOT auto-resume for default mode
            mock_send.assert_not_called()

    def test_no_auto_resume_for_stopped_session(self, manager, mock_session_manager):
        """
        Issue #3139: Should NOT auto-resume if session is already stopped.
        """
        session_id = "test-session-id"

        manager._session_permission_modes[session_id] = "yolo"

        # Session is stopped
        mock_session = MagicMock()
        mock_session.status = "stopped"
        mock_session_manager.get_session.return_value = mock_session

        with patch.object(manager, "send_message") as mock_send:
            manager.process_request_state(
                session_id=session_id,
                state="aborted",
                reason="loop",
            )

            import time

            time.sleep(0.6)

            # Should NOT auto-resume stopped session
            mock_send.assert_not_called()

    def test_auto_resume_handles_internal_abort_reason(self, manager, mock_session_manager):
        """
        Issue #3139: Should auto-resume for 'internal_abort' reason.
        """
        session_id = "test-session-id"

        manager._session_permission_modes[session_id] = "yolo"

        mock_session = MagicMock()
        mock_session.status = "active"
        mock_session_manager.get_session.return_value = mock_session

        with patch.object(manager, "send_message") as mock_send:
            mock_send.return_value = True

            manager.process_request_state(
                session_id=session_id,
                state="aborted",
                reason="internal_abort",
            )

            import time

            time.sleep(0.6)

            mock_send.assert_called_once_with(session_id, "继续")


class TestCreateRemoteSessionDBStatusCheck:
    """Tests for DB status validation in create_remote_session (Issue #2597)."""

    @pytest.fixture
    def mock_agent_manager(self):
        """Create a mock agent manager."""
        mock = MagicMock()
        mock.check_user_access.return_value = True
        return mock

    @pytest.fixture
    def mock_session_manager(self):
        """Create a mock session manager."""
        mock = MagicMock()
        mock.create_session.return_value = MagicMock(
            context={},
            created_at=None,
        )
        return mock

    @pytest.fixture
    def mock_api_key_proxy(self):
        """Create a mock API key proxy service."""
        mock = MagicMock()
        mock.generate_proxy_token.return_value = "test-proxy-token"
        mock.get_cli_settings_for_tool.return_value = {}
        return mock

    @pytest.fixture
    def manager(self, mock_agent_manager, mock_session_manager, mock_api_key_proxy):
        """Create a RemoteSessionManager with mocked dependencies."""
        with patch(
            "app.modules.workspace.remote_session_manager.get_remote_agent_manager",
            return_value=mock_agent_manager,
        ):
            with patch(
                "app.modules.workspace.remote_session_manager.SessionManager",
                return_value=mock_session_manager,
            ):
                with patch(
                    "app.modules.workspace.remote_session_manager.APIKeyProxyService",
                    return_value=mock_api_key_proxy,
                ):
                    from app.modules.workspace.remote_session_manager import RemoteSessionManager

                    mgr = RemoteSessionManager()
                    mgr._agent_manager = mock_agent_manager
                    mgr._session_manager = mock_session_manager
                    mgr._api_key_proxy = mock_api_key_proxy
                    yield mgr

    def test_rejects_session_when_db_status_is_offline_but_connected(
        self, manager, mock_agent_manager
    ):
        """
        Issue #2597: Should reject session creation when DB status is 'offline'
        even if is_connected() returns True (heartbeat tolerance window scenario).

        This test simulates the zombie session bug:
        - is_connected() returns True (agent within 180s tolerance window)
        - But DB status shows 'offline' (agent actually stopped)
        - Session creation should be rejected to prevent zombie sessions
        """
        machine_id = "test-machine-id"
        user_id = 1

        # is_connected returns True (within heartbeat tolerance window)
        mock_agent_manager.is_connected.return_value = True

        # check_user_access returns True
        mock_agent_manager.check_user_access.return_value = True

        # get_machine returns machine with status='offline' (agent actually stopped)
        mock_agent_manager.get_machine.return_value = {
            "machine_id": machine_id,
            "machine_name": "Test Machine",
            "hostname": "test-host",
            "status": "offline",  # DB status indicates offline
            "tenant_id": 1,
        }

        # Attempt to create session
        result = manager.create_remote_session(
            user_id=user_id,
            machine_id=machine_id,
            project_path="/test/project",
        )

        # Should be rejected because DB status is not 'online'
        assert result is None, "Expected session creation to be rejected when DB status is offline"

        # Verify that is_connected was called (baseline check)
        mock_agent_manager.is_connected.assert_called_once_with(machine_id)

        # Verify that get_machine was called to check DB status
        mock_agent_manager.get_machine.assert_called_once_with(machine_id)

    def test_rejects_session_when_db_status_is_empty(self, manager, mock_agent_manager):
        """
        Issue #2597: Should reject session creation when DB status is empty
        or missing, treating it as not 'online'.
        """
        machine_id = "test-machine-id"
        user_id = 1

        mock_agent_manager.is_connected.return_value = True
        mock_agent_manager.check_user_access.return_value = True

        # Machine dict with empty/missing status
        mock_agent_manager.get_machine.return_value = {
            "machine_id": machine_id,
            "machine_name": "Test Machine",
            "hostname": "test-host",
            # No 'status' key, or status is empty string
            "tenant_id": 1,
        }

        result = manager.create_remote_session(
            user_id=user_id,
            machine_id=machine_id,
            project_path="/test/project",
        )

        assert result is None, "Expected session creation to be rejected when DB status is empty"

    def test_accepts_session_when_db_status_is_online_and_connected(
        self, manager, mock_agent_manager, mock_session_manager
    ):
        """
        Issue #2597: Should accept session creation when:
        - is_connected() returns True
        - DB status is 'online'
        - All other checks pass
        """
        machine_id = "test-machine-id"
        user_id = 1
        session_id = "test-session-id"

        mock_agent_manager.is_connected.return_value = True
        mock_agent_manager.check_user_access.return_value = True

        # Machine with 'online' status
        mock_agent_manager.get_machine.return_value = {
            "machine_id": machine_id,
            "machine_name": "Test Machine",
            "hostname": "test-host",
            "status": "online",
            "tenant_id": 1,
        }

        # Mock session creation
        mock_session = MagicMock()
        mock_session.context = {}
        mock_session.created_at = None
        mock_session_manager.create_session.return_value = mock_session

        # Mock send_command
        mock_agent_manager.send_command = MagicMock()

        with patch("uuid.uuid4", return_value=session_id):
            # Use claude-code to avoid ha_pool_token requirement
            result = manager.create_remote_session(
                user_id=user_id,
                machine_id=machine_id,
                project_path="/test/project",
                cli_tool="claude-code",
            )

        # Should succeed
        assert result is not None, "Expected session creation to succeed when DB status is online"
        assert result["session_id"] == session_id
        assert result["machine_id"] == machine_id
        assert result["status"] == "active"

    def test_rejects_session_when_db_status_is_offline_regardless_of_connected(
        self, manager, mock_agent_manager
    ):
        """
        Issue #2597: DB status check should act as additional validation,
        rejecting sessions even if connected check passes.
        """
        machine_id = "test-machine-id"
        user_id = 1

        # Simulate various connection states combined with offline DB status
        for connected_state in [True, False]:
            mock_agent_manager.reset_mock()
            mock_agent_manager.is_connected.return_value = connected_state
            mock_agent_manager.check_user_access.return_value = True
            mock_agent_manager.get_machine.return_value = {
                "machine_id": machine_id,
                "machine_name": "Test Machine",
                "hostname": "test-host",
                "status": "offline",
                "tenant_id": 1,
            }

            result = manager.create_remote_session(
                user_id=user_id,
                machine_id=machine_id,
                project_path="/test/project",
            )

            assert (
                result is None
            ), f"Expected rejection when DB status is offline (connected={connected_state})"
