"""
Test for Issue #352: Tool limit hints communication

Tests the _send_tool_limit_hints method in ProcessExecutor that informs
the assistant about platform tool limitations (e.g., run_shell_command timeout).
"""

import json
import subprocess
import sys
import threading
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, Mock, PropertyMock, patch

# Add remote-agent directory to path
remote_agent_dir = Path(__file__).parent.parent.parent.parent / "remote-agent"
sys.path.insert(0, str(remote_agent_dir))


class MockSessionProcess:
    """Mock SessionProcess for testing."""

    def __init__(self, session_id, is_running=True):
        self.session_id = session_id
        self._is_running = is_running
        self.stdin = BytesIO()
        self.process = Mock()
        self.process.stdin = self.stdin
        self.process.pid = 12345

    @property
    def is_running(self):
        return self._is_running


class TestSendToolLimitHints(unittest.TestCase):
    """Test cases for _send_tool_limit_hints method."""

    def setUp(self):
        """Set up test fixtures."""
        # Import ProcessExecutor after path is set
        from executor import ProcessExecutor

        self.executor = ProcessExecutor(
            server_url="http://localhost:5001",
            output_callback=None,
            permission_callback=None,
            usage_callback=None,
        )

    def test_send_hints_to_running_session(self):
        """Test sending tool limit hints to a running session."""
        session_id = "test-session-001"
        mock_session = MockSessionProcess(session_id, is_running=True)

        # Add mock session to executor's sessions dict
        with self.executor._lock:
            self.executor._sessions[session_id] = mock_session

        # Send tool limit hints
        result = self.executor._send_tool_limit_hints(session_id)

        # Verify result
        self.assertTrue(result, "Should return True for successful send")

        # Verify stdin has content written
        # Note: BytesIO doesn't track writes like a real pipe, but we can check
        # that the method executed without errors

    def test_send_hints_to_stopped_session(self):
        """Test sending tool limit hints to a stopped session."""
        session_id = "test-session-002"
        mock_session = MockSessionProcess(session_id, is_running=False)

        # Add mock session to executor's sessions dict
        with self.executor._lock:
            self.executor._sessions[session_id] = mock_session

        # Send tool limit hints
        result = self.executor._send_tool_limit_hints(session_id)

        # Verify result - should be False for stopped session
        self.assertFalse(result, "Should return False for stopped session")

    def test_send_hints_to_nonexistent_session(self):
        """Test sending tool limit hints to a nonexistent session."""
        session_id = "test-session-nonexistent"

        # Don't add any session to executor

        # Send tool limit hints
        result = self.executor._send_tool_limit_hints(session_id)

        # Verify result - should be False for nonexistent session
        self.assertFalse(result, "Should return False for nonexistent session")

    def test_hints_message_format(self):
        """Test that the hints message is properly formatted."""
        session_id = "test-session-003"
        mock_session = MockSessionProcess(session_id, is_running=True)

        # Create a mock stdin that captures writes
        written_content = []

        def capture_write(data):
            written_content.append(data)

        mock_session.process.stdin.write = capture_write
        mock_session.process.stdin.flush = Mock()

        # Add mock session to executor's sessions dict
        with self.executor._lock:
            self.executor._sessions[session_id] = mock_session

        # Send tool limit hints
        result = self.executor._send_tool_limit_hints(session_id)

        # Verify message format
        self.assertTrue(result, "Should return True for successful send")
        self.assertEqual(len(written_content), 1, "Should write exactly one message")

        # Parse the written content
        written_data = written_content[0].decode("utf-8").strip()
        parsed_msg = json.loads(written_data)

        # Verify message structure
        self.assertEqual(parsed_msg["type"], "user", "Message type should be 'user'")
        self.assertEqual(parsed_msg["message"]["role"], "user", "Role should be 'user'")
        self.assertIsInstance(parsed_msg["message"]["content"], list, "Content should be a list")
        self.assertEqual(len(parsed_msg["message"]["content"]), 1, "Should have one content block")

        # Verify content block
        content_block = parsed_msg["message"]["content"][0]
        self.assertEqual(content_block["type"], "text", "Content block type should be 'text'")

        # Verify key information is present in the text
        text_content = content_block["text"]
        self.assertIn("Platform Tool Limits", text_content, "Should mention Platform Tool Limits")
        self.assertIn("run_shell_command", text_content, "Should mention run_shell_command")
        self.assertIn("600 seconds", text_content, "Should mention 600 seconds timeout")
        self.assertIn("is_background=true", text_content, "Should mention background mode")
        self.assertIn(
            "Breaking down into smaller steps",
            text_content,
            "Should suggest task breakdown",
        )

    def test_hints_content_comprehensive(self):
        """Test that hints content covers all required aspects."""
        session_id = "test-session-004"
        mock_session = MockSessionProcess(session_id, is_running=True)

        # Create a mock stdin that captures writes
        written_content = []

        def capture_write(data):
            written_content.append(data)

        mock_session.process.stdin.write = capture_write
        mock_session.process.stdin.flush = Mock()

        # Add mock session to executor's sessions dict
        with self.executor._lock:
            self.executor._sessions[session_id] = mock_session

        # Send tool limit hints
        self.executor._send_tool_limit_hints(session_id)

        # Parse the written content
        written_data = written_content[0].decode("utf-8").strip()
        parsed_msg = json.loads(written_data)
        text_content = parsed_msg["message"]["content"][0]["text"]

        # Check all required information
        required_elements = [
            "Maximum timeout is 600 seconds",
            "is_background=true for long-running processes",
            "Breaking down into smaller steps",
            "polling/checkpointing for progress tracking",
            "explicit error messages",
            "Do not silently switch execution strategies",
            "without user confirmation",
        ]

        for element in required_elements:
            self.assertIn(element, text_content, f"Should contain '{element}'")


class TestToolLimitHintsIntegration(unittest.TestCase):
    """Integration tests for tool limit hints feature."""

    def test_executor_has_send_tool_limit_hints_method(self):
        """Test that ProcessExecutor has the _send_tool_limit_hints method."""
        from executor import ProcessExecutor

        executor = ProcessExecutor(
            server_url="http://localhost:5001",
        )

        # Verify method exists
        self.assertTrue(
            hasattr(executor, "_send_tool_limit_hints"),
            "ProcessExecutor should have _send_tool_limit_hints method",
        )

        # Verify method is callable
        self.assertTrue(
            callable(executor._send_tool_limit_hints),
            "_send_tool_limit_hints should be callable",
        )


class TestToolLimitHintsExceptionHandling(unittest.TestCase):
    """Test exception handling in _send_tool_limit_hints method."""

    def setUp(self):
        """Set up test fixtures."""
        from executor import ProcessExecutor

        self.executor = ProcessExecutor(
            server_url="http://localhost:5001",
        )

    def test_os_error_handling(self):
        """Test handling of OSError when writing to stdin."""
        session_id = "test-session-os-error"
        mock_session = MockSessionProcess(session_id, is_running=True)

        # Mock stdin.write to raise OSError
        def raise_os_error(data):
            raise OSError("Mock OS error")

        mock_session.process.stdin.write = raise_os_error
        mock_session.process.stdin.flush = Mock()

        # Add mock session to executor's sessions dict
        with self.executor._lock:
            self.executor._sessions[session_id] = mock_session

        # Send tool limit hints - should handle exception gracefully
        result = self.executor._send_tool_limit_hints(session_id)

        # Verify result - should return False on error
        self.assertFalse(result, "Should return False on OSError")

    def test_broken_pipe_error_handling(self):
        """Test handling of BrokenPipeError when writing to stdin."""
        session_id = "test-session-broken-pipe"
        mock_session = MockSessionProcess(session_id, is_running=True)

        # Mock stdin.write to raise BrokenPipeError
        def raise_broken_pipe(data):
            raise BrokenPipeError("Mock broken pipe")

        mock_session.process.stdin.write = raise_broken_pipe
        mock_session.process.stdin.flush = Mock()

        # Add mock session to executor's sessions dict
        with self.executor._lock:
            self.executor._sessions[session_id] = mock_session

        # Send tool limit hints - should handle exception gracefully
        result = self.executor._send_tool_limit_hints(session_id)

        # Verify result - should return False on error
        self.assertFalse(result, "Should return False on BrokenPipeError")

    def test_attribute_error_handling(self):
        """Test handling of AttributeError when stdin is None."""
        session_id = "test-session-attr-error"
        mock_session = MockSessionProcess(session_id, is_running=True)

        # Set stdin to None to trigger AttributeError
        mock_session.process.stdin = None

        # Add mock session to executor's sessions dict
        with self.executor._lock:
            self.executor._sessions[session_id] = mock_session

        # Send tool limit hints - should handle exception gracefully
        result = self.executor._send_tool_limit_hints(session_id)

        # Verify result - should return False on error
        self.assertFalse(result, "Should return False on AttributeError")


class TestToolTimeoutConfiguration(unittest.TestCase):
    """Test configurable tool timeout feature."""

    def test_default_timeout_value(self):
        """Test that default timeout is 600 seconds."""
        from executor import ProcessExecutor

        executor = ProcessExecutor(
            server_url="http://localhost:5001",
        )

        # Verify default timeout
        self.assertEqual(executor._tool_timeout, 600, "Default timeout should be 600 seconds")

    def test_custom_timeout_value(self):
        """Test that custom timeout value is properly set."""
        from executor import ProcessExecutor

        executor = ProcessExecutor(
            server_url="http://localhost:5001",
            tool_timeout=300,
        )

        # Verify custom timeout
        self.assertEqual(executor._tool_timeout, 300, "Custom timeout should be 300 seconds")

    def test_timeout_in_hints_message(self):
        """Test that custom timeout appears in hints message."""
        from executor import ProcessExecutor

        executor = ProcessExecutor(
            server_url="http://localhost:5001",
            tool_timeout=300,  # 5 minutes
        )

        session_id = "test-session-custom-timeout"
        mock_session = MockSessionProcess(session_id, is_running=True)

        # Create a mock stdin that captures writes
        written_content = []

        def capture_write(data):
            written_content.append(data)

        mock_session.process.stdin.write = capture_write
        mock_session.process.stdin.flush = Mock()

        # Add mock session to executor's sessions dict
        with executor._lock:
            executor._sessions[session_id] = mock_session

        # Send tool limit hints
        executor._send_tool_limit_hints(session_id)

        # Parse the written content
        written_data = written_content[0].decode("utf-8").strip()
        parsed_msg = json.loads(written_data)
        text_content = parsed_msg["message"]["content"][0]["text"]

        # Verify custom timeout appears in message
        self.assertIn("300 seconds", text_content, "Custom timeout should appear in message")
        self.assertIn("5 minutes", text_content, "Timeout in minutes should appear in message")


if __name__ == "__main__":
    unittest.main()
