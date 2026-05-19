"""
Test: Remote session assistant message accumulation and persistence.

Verifies that streaming JSON output from a remote CLI is properly
accumulated per-turn and stored to the database as complete assistant messages.
"""

import json
import unittest
from unittest.mock import MagicMock, patch


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
            call
            for call in self.mock_session_mgr.add_message.call_args_list
            if call.kwargs.get("role") == "assistant"
            or (len(call.args) > 1 and call.args[1] == "assistant")
        ]

    # --- Tests ---

    def test_single_assistant_turn_accumulated_and_flushed(self):
        """Text from multiple assistant chunks is combined and stored on result."""
        self._send_output(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "Hello"}]},
                }
            )
        )
        self._send_output(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": " world"}]},
                }
            )
        )
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
        self._send_output(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "text", "text": "Let me check "},
                            {"type": "tool_use", "id": "tu1", "name": "read_file", "input": {}},
                        ]
                    },
                }
            )
        )
        self._send_output(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "text", "text": "the file."},
                        ]
                    },
                }
            )
        )
        self._send_output(json.dumps({"type": "result", "subtype": "success"}))

        msgs = self._get_stored_assistant_messages()
        self.assertEqual(len(msgs), 1)
        stored_text = msgs[0].kwargs.get("content") or msgs[0].args[2]
        self.assertEqual(stored_text, "Let me check the file.")

    def test_openai_message_format(self):
        """OpenAI-compatible message format is also accumulated."""
        self._send_output(
            json.dumps(
                {
                    "type": "message",
                    "role": "assistant",
                    "content": "OpenAI response text",
                }
            )
        )
        self._send_output(json.dumps({"type": "result", "subtype": "success"}))

        msgs = self._get_stored_assistant_messages()
        self.assertEqual(len(msgs), 1)
        stored_text = msgs[0].kwargs.get("content") or msgs[0].args[2]
        self.assertEqual(stored_text, "OpenAI response text")

    def test_is_complete_flushes_buffer(self):
        """is_complete=True also flushes accumulated text."""
        self._send_output(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "Final output"}]},
                }
            )
        )
        self._send_output("", is_complete=True)

        msgs = self._get_stored_assistant_messages()
        self.assertEqual(len(msgs), 1)
        stored_text = msgs[0].kwargs.get("content") or msgs[0].args[2]
        self.assertEqual(stored_text, "Final output")

    def test_multiple_turns(self):
        """Multiple turns produce separate assistant messages."""
        # Turn 1
        self._send_output(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "First reply"}]},
                }
            )
        )
        self._send_output(json.dumps({"type": "result", "subtype": "success"}))

        # Turn 2
        self._send_output(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "Second reply"}]},
                }
            )
        )
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
        self._send_output(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "tool_use", "id": "tu1", "name": "bash", "input": {}}]
                    },
                }
            )
        )
        self._send_output(json.dumps({"type": "result", "subtype": "success"}))

        msgs = self._get_stored_assistant_messages()
        self.assertEqual(len(msgs), 0)

    def test_buffer_always_works(self):
        """Buffering to agent_manager always happens regardless of content."""
        self._send_output("anything")
        self._send_output("", is_complete=True)
        # buffer_output should be called twice
        self.assertEqual(self.mock_agent_mgr.buffer_output.call_count, 2)



class TestSystemMessageInStdout(unittest.TestCase):
    """Test system messages sent via stdout stream (JSON format).

    This addresses Issue #442: system messages like init were not being
    stored because they were sent via stdout as JSON, not via the dedicated
    'system' stream.
    """

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

    def _send_output(self, data, stream="stdout", is_complete=False):
        self.manager.process_session_output("test-session", data, stream, is_complete)

    def _get_stored_system_messages(self):
        """Return all add_message calls with role='system'."""
        return [
            call
            for call in self.mock_session_mgr.add_message.call_args_list
            if call.kwargs.get("role") == "system"
            or (len(call.args) > 1 and call.args[1] == "system")
        ]

    def test_system_init_message_in_stdout(self):
        """System init messages sent via stdout are stored (Issue #442)."""
        self._send_output(
            json.dumps(
                {
                    "type": "system",
                    "subtype": "init",
                    "session_id": "test-session-123",
                    "model": "qwen3-coder-plus",
                    "permission_mode": "default",
                }
            )
        )

        msgs = self._get_stored_system_messages()
        self.assertEqual(len(msgs), 1)

        # Check the content contains relevant info
        stored_content = msgs[0].kwargs.get("content") or msgs[0].args[2]
        self.assertIn("init", stored_content)
        self.assertIn("qwen3-coder-plus", stored_content)

    def test_system_initialized_message_in_stdout(self):
        """System initialized messages sent via stdout are stored."""
        self._send_output(
            json.dumps(
                {
                    "type": "system",
                    "subtype": "initialized",
                    "session_id": "test-session-456",
                }
            )
        )

        msgs = self._get_stored_system_messages()
        self.assertEqual(len(msgs), 1)

        stored_content = msgs[0].kwargs.get("content") or msgs[0].args[2]
        self.assertIn("initialized", stored_content)

    def test_system_message_with_content_field(self):
        """System messages with 'content' field are stored."""
        self._send_output(
            json.dumps(
                {
                    "type": "system",
                    "content": "System notification message",
                }
            )
        )

        msgs = self._get_stored_system_messages()
        self.assertEqual(len(msgs), 1)
        stored_content = msgs[0].kwargs.get("content") or msgs[0].args[2]
        self.assertEqual(stored_content, "System notification message")

    def test_system_message_with_message_field(self):
        """System messages with 'message' field are stored."""
        self._send_output(
            json.dumps(
                {
                    "type": "system",
                    "message": {"text": "Some message"},
                }
            )
        )

        msgs = self._get_stored_system_messages()
        self.assertEqual(len(msgs), 1)

    def test_empty_system_message_not_stored(self):
        """Empty system messages are not stored."""
        self._send_output(
            json.dumps(
                {
                    "type": "system",
                    "subtype": "init",
                }
            )
        )

        msgs = self._get_stored_system_messages()
        self.assertEqual(len(msgs), 0)

    def test_system_message_dict_content_serialized(self):
        """System message with dict content is JSON-serialized."""
        self._send_output(
            json.dumps(
                {
                    "type": "system",
                    "message": {"key": "value", "nested": {"a": 1}},
                }
            )
        )

        msgs = self._get_stored_system_messages()
        self.assertEqual(len(msgs), 1)
        stored_content = msgs[0].kwargs.get("content") or msgs[0].args[2]
        # Should be valid JSON
        parsed = json.loads(stored_content)
        self.assertEqual(parsed["key"], "value")


    def test_init_message_preserves_content_field(self):
        """Init message with content field preserves it in stored content."""
        self._send_output(
            json.dumps(
                {
                    "type": "system",
                    "subtype": "init",
                    "session_id": "test-123",
                    "model": "qwen3-coder",
                    "content": "Initializing session for project",
                }
            )
        )

        msgs = self._get_stored_system_messages()
        self.assertEqual(len(msgs), 1)
        stored_content = msgs[0].kwargs.get("content") or msgs[0].args[2]
        # Should contain both key info and original content
        self.assertIn("test-123", stored_content)
        self.assertIn("Initializing session for project", stored_content)
        parsed = json.loads(stored_content)
        self.assertEqual(parsed["content"], "Initializing session for project")

    def test_init_message_preserves_message_field(self):
        """Init message with message field preserves it in stored content."""
        self._send_output(
            json.dumps(
                {
                    "type": "system",
                    "subtype": "init",
                    "session_id": "test-456",
                    "message": {"status": "ready", "info": "test"},
                }
            )
        )

        msgs = self._get_stored_system_messages()
        self.assertEqual(len(msgs), 1)
        stored_content = msgs[0].kwargs.get("content") or msgs[0].args[2]
        parsed = json.loads(stored_content)
        # message field should be preserved as content
        self.assertIn("content", parsed)
        self.assertEqual(parsed["content"]["status"], "ready")

    def test_content_vs_message_priority(self):
        """Content field takes priority over message field."""
        self._send_output(
            json.dumps(
                {
                    "type": "system",
                    "content": "Primary content",
                    "message": "Secondary message",
                }
            )
        )

        msgs = self._get_stored_system_messages()
        self.assertEqual(len(msgs), 1)
        stored_content = msgs[0].kwargs.get("content") or msgs[0].args[2]
        self.assertEqual(stored_content, "Primary content")

    def test_message_fallback_when_content_empty(self):
        """Message field is used when content is empty."""
        self._send_output(
            json.dumps(
                {
                    "type": "system",
                    "content": "",
                    "message": "Fallback message",
                }
            )
        )

        msgs = self._get_stored_system_messages()
        self.assertEqual(len(msgs), 1)
        stored_content = msgs[0].kwargs.get("content") or msgs[0].args[2]
        self.assertEqual(stored_content, "Fallback message")

    def test_content_message_both_empty_not_stored(self):
        """System message with both content and message empty is not stored."""
        self._send_output(
            json.dumps(
                {
                    "type": "system",
                    "content": "",
                    "message": "",
                }
            )
        )

        msgs = self._get_stored_system_messages()
        self.assertEqual(len(msgs), 0)

    def test_system_message_list_content(self):
        """System message with list content is stored."""
        self._send_output(
            json.dumps(
                {
                    "type": "system",
                    "content": ["item1", "item2", "item3"],
                }
            )
        )

        msgs = self._get_stored_system_messages()
        self.assertEqual(len(msgs), 1)
        stored_content = msgs[0].kwargs.get("content") or msgs[0].args[2]
        # List should be serialized as JSON string
        parsed = json.loads(stored_content)
        self.assertEqual(parsed, ["item1", "item2", "item3"])

    def test_system_message_numeric_content(self):
        """System message with numeric content is stored."""
        self._send_output(
            json.dumps(
                {
                    "type": "system",
                    "content": 42,
                }
            )
        )

        msgs = self._get_stored_system_messages()
        # Numeric content should not be stored (not string or dict)
        # but it will be stored as the value 42 (which is truthy but not str)
        # Actually it will pass because we serialize to str after
        self.assertEqual(len(msgs), 1)
        stored_content = msgs[0].kwargs.get("content") or msgs[0].args[2]
        # Should be string "42"
        self.assertEqual(stored_content, "42")


if __name__ == "__main__":
    unittest.main()


