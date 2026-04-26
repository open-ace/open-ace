"""
Test: Remote session assistant message accumulation and persistence.

Verifies that streaming JSON output from a remote CLI is properly
accumulated per-turn and stored to the database as complete assistant messages.
"""

import json
import unittest
from unittest.mock import MagicMock, patch, PropertyMock


class TestAssistantMessageAccumulation(unittest.TestCase):
    """Test the streaming JSON accumulation logic in RemoteSessionManager."""

    def setUp(self):
        """Set up mocks for SessionManager and RemoteAgentManager."""
        self.mock_session_mgr = MagicMock()
        self.mock_agent_mgr = MagicMock()

        # Clear the class-level buffer before each test
        from app.modules.workspace.remote_session_manager import RemoteSessionManager
        RemoteSessionManager._assistant_text_buffer.clear()

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

    # --- Helper ---

    def _send_output(self, data, stream="stdout", is_complete=False):
        self.manager.process_session_output("test-session", data, stream, is_complete)

    def _get_stored_assistant_messages(self):
        """Return all add_message calls with role='assistant'."""
        return [
            call for call in self.mock_session_mgr.add_message.call_args_list
            if call.kwargs.get("role") == "assistant" or
               (len(call.args) > 1 and call.args[1] == "assistant")
        ]

    # --- Tests ---

    def test_single_assistant_turn_accumulated_and_flushed(self):
        """Text from multiple assistant chunks is combined and stored on result."""
        self._send_output(json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Hello"}]},
        }))
        self._send_output(json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": " world"}]},
        }))
        # No message stored yet — still accumulating
        self.mock_session_mgr.add_message.assert_not_called()

        # Result signals end of turn
        self._send_output(json.dumps({"type": "result", "subtype": "success"}))

        # One assistant message should be stored with combined text
        msgs = self._get_stored_assistant_messages()
        self.assertEqual(len(msgs), 1)
        stored_text = msgs[0].kwargs.get("content") or msgs[0].args[2]
        self.assertEqual(stored_text, "Hello world")

    def test_tool_use_blocks_ignored(self):
        """tool_use and thinking blocks should not appear in stored text."""
        self._send_output(json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "text", "text": "Let me check "},
                {"type": "tool_use", "id": "tu1", "name": "read_file", "input": {}},
            ]},
        }))
        self._send_output(json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "text", "text": "the file."},
            ]},
        }))
        self._send_output(json.dumps({"type": "result", "subtype": "success"}))

        msgs = self._get_stored_assistant_messages()
        self.assertEqual(len(msgs), 1)
        stored_text = msgs[0].kwargs.get("content") or msgs[0].args[2]
        self.assertEqual(stored_text, "Let me check the file.")

    def test_openai_message_format(self):
        """OpenAI-compatible message format is also accumulated."""
        self._send_output(json.dumps({
            "type": "message",
            "role": "assistant",
            "content": "OpenAI response text",
        }))
        self._send_output(json.dumps({"type": "result", "subtype": "success"}))

        msgs = self._get_stored_assistant_messages()
        self.assertEqual(len(msgs), 1)
        stored_text = msgs[0].kwargs.get("content") or msgs[0].args[2]
        self.assertEqual(stored_text, "OpenAI response text")

    def test_is_complete_flushes_buffer(self):
        """is_complete=True also flushes accumulated text."""
        self._send_output(json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Final output"}]},
        }))
        self._send_output("", is_complete=True)

        msgs = self._get_stored_assistant_messages()
        self.assertEqual(len(msgs), 1)
        stored_text = msgs[0].kwargs.get("content") or msgs[0].args[2]
        self.assertEqual(stored_text, "Final output")

    def test_multiple_turns(self):
        """Multiple turns produce separate assistant messages."""
        # Turn 1
        self._send_output(json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "First reply"}]},
        }))
        self._send_output(json.dumps({"type": "result", "subtype": "success"}))

        # Turn 2
        self._send_output(json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Second reply"}]},
        }))
        self._send_output(json.dumps({"type": "result", "subtype": "success"}))

        msgs = self._get_stored_assistant_messages()
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0].kwargs.get("content") or msgs[0].args[2], "First reply")
        self.assertEqual(msgs[1].kwargs.get("content") or msgs[1].args[2], "Second reply")

    def test_non_json_ignored(self):
        """Non-JSON output should not crash or store anything."""
        self._send_output("plain text output")
        self._send_output("not json at all")
        self._send_output(json.dumps({"type": "result", "subtype": "success"}))

        # No assistant message stored (no text was accumulated)
        msgs = self._get_stored_assistant_messages()
        self.assertEqual(len(msgs), 0)

    def test_system_message_stored_on_complete(self):
        """System messages are stored when is_complete=True."""
        self._send_output("system info", stream="system", is_complete=True)

        self.mock_session_mgr.add_message.assert_called_once_with(
            session_id="test-session",
            role="system",
            content="system info",
        )

    def test_system_message_not_stored_without_complete(self):
        """System messages without is_complete are NOT stored."""
        self._send_output("system info", stream="system", is_complete=False)
        self.mock_session_mgr.add_message.assert_not_called()

    def test_empty_text_not_stored(self):
        """Empty accumulated text should not produce a DB record."""
        # Only tool_use blocks — no text
        self._send_output(json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "id": "tu1", "name": "bash", "input": {}}]},
        }))
        self._send_output(json.dumps({"type": "result", "subtype": "success"}))

        msgs = self._get_stored_assistant_messages()
        self.assertEqual(len(msgs), 0)

    def test_buffer_always_works(self):
        """Buffering to agent_manager always happens regardless of content."""
        self._send_output("anything")
        self._send_output("", is_complete=True)
        # buffer_output should be called twice
        self.assertEqual(self.mock_agent_mgr.buffer_output.call_count, 2)


if __name__ == "__main__":
    unittest.main()
