"""Tests for session_manager.create_session() workspace_type/remote_machine_id (Issue #764)."""

from unittest.mock import MagicMock

import pytest


class TestCreateSessionAcceptsWorkspaceParams:
    """Verify create_session() accepts workspace_type and remote_machine_id."""

    def _make_session_manager(self):
        from app.modules.workspace.session_manager import SessionManager

        sm = SessionManager.__new__(SessionManager)
        sm.get_session = MagicMock(return_value=None)

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 1
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.commit.return_value = None
        mock_conn.close.return_value = None
        sm._get_connection = MagicMock(return_value=mock_conn)
        return sm

    def test_create_session_with_workspace_type(self):
        sm = self._make_session_manager()
        session = sm.create_session(
            tool_name="claude-code",
            title="Autonomous: test",
            project_path="/tmp/test",
            workspace_type="remote",
            remote_machine_id="machine-123",
        )
        assert session is not None
        assert session.workspace_type == "remote"
        assert session.remote_machine_id == "machine-123"

    def test_create_session_defaults_to_local(self):
        sm = self._make_session_manager()
        session = sm.create_session(tool_name="claude-code", title="Test session")
        assert session.workspace_type == "local"
        assert session.remote_machine_id is None

    def test_create_session_includes_workspace_in_insert(self):
        sm = self._make_session_manager()
        sm.create_session(
            tool_name="qwen-code-cli",
            workspace_type="remote",
            remote_machine_id="abc-def",
        )
        mock_cursor = sm._get_connection.return_value.cursor.return_value
        mock_cursor.execute.assert_called_once()
        call_args = mock_cursor.execute.call_args
        sql = call_args[0][0]
        params = list(call_args[0][1])
        assert "workspace_type" in sql
        assert "remote_machine_id" in sql
        assert "remote" in params
        assert "abc-def" in params

    def test_create_session_type_error_fixed(self):
        sm = self._make_session_manager()
        try:
            sm.create_session(
                session_id="test-session-id",
                session_type="chat",
                title="Autonomous: abc12345",
                tool_name="claude-code",
                project_path="/tmp/project",
                workspace_type="local",
                remote_machine_id=None,
                context={"workflow_id": "test-wf"},
            )
        except TypeError as e:
            pytest.fail(f"create_session raised TypeError (bug not fixed): {e}")
