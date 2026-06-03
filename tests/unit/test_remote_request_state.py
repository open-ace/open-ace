import json
import unittest
from unittest.mock import MagicMock, patch


class TestRemoteRequestState(unittest.TestCase):
    def setUp(self):
        self.mock_session_mgr = MagicMock()
        self.mock_agent_mgr = MagicMock()

        self.patcher_sm = patch(
            "app.modules.workspace.remote_session_manager.SessionManager",
            return_value=self.mock_session_mgr,
        )
        self.patcher_am = patch(
            "app.modules.workspace.remote_session_manager.get_remote_agent_manager",
            return_value=self.mock_agent_mgr,
        )
        self.patcher_proxy = patch(
            "app.modules.workspace.remote_session_manager.APIKeyProxyService",
        )
        self.patcher_sm.start()
        self.patcher_am.start()
        self.patcher_proxy.start()

        from app.modules.workspace.remote_session_manager import RemoteSessionManager

        self.manager = RemoteSessionManager()

    def tearDown(self):
        self.patcher_sm.stop()
        self.patcher_am.stop()
        self.patcher_proxy.stop()

    def test_abort_request_includes_reason_in_command(self):
        self.manager._get_machine_id = MagicMock(return_value="machine-123")

        ok = self.manager.abort_request("session-123", reason="disconnect")

        self.assertTrue(ok)
        self.mock_agent_mgr.send_command.assert_called_once_with(
            "machine-123",
            {
                "type": "command",
                "command": "abort_request",
                "session_id": "session-123",
                "reason": "disconnect",
            },
        )

    def test_process_request_state_buffers_control_event(self):
        self.manager.process_request_state(
            "session-123",
            "aborted",
            reason="user",
            message="Stopped by user",
        )

        self.mock_agent_mgr.buffer_output.assert_called_once()
        _, output_entry = self.mock_agent_mgr.buffer_output.call_args.args
        self.assertEqual(output_entry["stream"], "request_state")
        self.assertFalse(output_entry["is_complete"])
        self.assertEqual(
            json.loads(output_entry["data"]),
            {
                "type": "aborted",
                "reason": "user",
                "message": "Stopped by user",
            },
        )

    def test_process_request_state_marks_abort_failed_complete(self):
        self.manager.process_request_state(
            "session-123",
            "abort_failed",
            reason="system",
        )

        _, output_entry = self.mock_agent_mgr.buffer_output.call_args.args
        self.assertEqual(output_entry["stream"], "request_state")
        self.assertTrue(output_entry["is_complete"])
        self.assertEqual(
            json.loads(output_entry["data"]),
            {
                "type": "abort_failed",
                "reason": "system",
            },
        )


if __name__ == "__main__":
    unittest.main()
