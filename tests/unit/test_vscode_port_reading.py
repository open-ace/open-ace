"""
Unit tests for VS Code port reading functionality.

Issue #2588: Tests for concurrent stdout/stderr reading to support
code-server 4.132.0+ which outputs port information to stderr.
"""

import subprocess
import threading
import time
import unittest
from unittest.mock import MagicMock, Mock, patch

# Import the module under test
import sys
import os

# Add remote-agent to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'remote-agent'))


class MockPipe:
    """Mock pipe object that simulates stdout/stderr behavior."""

    def __init__(self, lines: list, delay: float = 0.0, raise_error: Exception | None = None):
        """
        Initialize mock pipe.

        Args:
            lines: List of lines to return (bytes or str)
            delay: Delay before first line (simulates startup time)
            raise_error: Exception to raise during reading
        """
        self.lines = lines if lines else []
        self.delay = delay
        self.raise_error = raise_error
        self.index = 0
        self.closed = False
        self._lock = threading.Lock()

    def readline(self):
        """Simulate readline() behavior."""
        if self.raise_error:
            raise self.raise_error

        if self.delay > 0 and self.index == 0:
            time.sleep(self.delay)

        with self._lock:
            if self.index >= len(self.lines):
                return b""  # EOF
            line = self.lines[self.index]
            self.index += 1
            # Ensure bytes output
            if isinstance(line, str):
                return line.encode('utf-8')
            return line

    def read(self):
        """Simulate read() behavior - read all remaining."""
        if self.raise_error:
            raise self.raise_error

        result = b""
        with self._lock:
            while self.index < len(self.lines):
                line = self.lines[self.index]
                self.index += 1
                if isinstance(line, str):
                    result += line.encode('utf-8')
                else:
                    result += line
        return result


class TestReadVSCodePort(unittest.TestCase):
    """Test suite for _read_vscode_port functionality."""

    def setUp(self):
        """Set up test fixtures."""
        # Import after setting up path
        from agent import RemoteAgent, AgentConfig

        # Create a minimal mock agent for testing
        self.config = Mock(spec=AgentConfig)
        self.config.machine_id = "test-machine-id"
        self.config.server_url = "http://localhost:5000"
        self.config.heartbeat_interval = 30
        self.config.reconnect_base_delay = 5
        self.config.reconnect_max_delay = 300

        # We'll test the method directly without full agent initialization
        # to avoid dependency issues

    def _create_mock_proc(self, stdout_lines=None, stderr_lines=None,
                          stdout_delay=0.0, stderr_delay=0.0,
                          poll_result=None):
        """
        Create a mock subprocess.Popen object.

        Args:
            stdout_lines: Lines to return from stdout
            stderr_lines: Lines to return from stderr
            stdout_delay: Delay before stdout output
            stderr_delay: Delay before stderr output
            poll_result: Return value for poll()
        """
        mock_proc = Mock(spec=subprocess.Popen)
        mock_proc.stdout = MockPipe(stdout_lines, stdout_delay) if stdout_lines else MockPipe([])
        mock_proc.stderr = MockPipe(stderr_lines, stderr_delay) if stderr_lines else MockPipe([])
        mock_proc.poll = Mock(return_value=poll_result)
        mock_proc.pid = 12345
        return mock_proc

    def test_port_in_stdout(self):
        """Test port detection when port is in stdout (old code-server versions)."""
        from agent import RemoteAgent

        stdout_lines = [
            b"info code-server 4.90.0\n",
            b"HTTP server listening on http://0.0.0.0:12345/\n",
        ]
        mock_proc = self._create_mock_proc(stdout_lines=stdout_lines)

        # Access the method via class
        port = RemoteAgent._read_vscode_port(None, mock_proc, "test-vscode")

        self.assertEqual(port, 12345)

    def test_port_in_stderr(self):
        """Test port detection when port is in stderr (code-server 4.132.0+)."""
        from agent import RemoteAgent

        # Simulate code-server 4.132.0 behavior: port in stderr
        stderr_lines = [
            b"[2026-08-13T10:47:53.911Z] info  code-server 4.132.0\n",
            b"[2026-08-13T10:47:53.936Z] info  HTTP server listening on http://0.0.0.0:54321/\n",
        ]
        mock_proc = self._create_mock_proc(stderr_lines=stderr_lines)

        port = RemoteAgent._read_vscode_port(None, mock_proc, "test-vscode")

        self.assertEqual(port, 54321)

    def test_port_in_both_streams_first_match(self):
        """Test that first port match wins when both streams have ports."""
        from agent import RemoteAgent

        # stdout outputs port first
        stdout_lines = [
            b"HTTP server listening on http://0.0.0.0:11111/\n",
        ]
        # stderr also outputs port (later)
        stderr_lines = [
            b"HTTP server listening on http://0.0.0.0:22222/\n",
        ]
        mock_proc = self._create_mock_proc(
            stdout_lines=stdout_lines,
            stderr_lines=stderr_lines,
            stdout_delay=0.0,
            stderr_delay=0.1  # stderr slightly delayed
        )

        port = RemoteAgent._read_vscode_port(None, mock_proc, "test-vscode")

        # Should return the first match (stdout)
        self.assertEqual(port, 11111)

    def test_port_in_stderr_first(self):
        """Test port detection when stderr outputs before stdout."""
        from agent import RemoteAgent

        stdout_lines = [
            b"HTTP server listening on http://0.0.0.0:33333/\n",
        ]
        stderr_lines = [
            b"HTTP server listening on http://0.0.0.0:44444/\n",
        ]
        mock_proc = self._create_mock_proc(
            stdout_lines=stdout_lines,
            stderr_lines=stderr_lines,
            stdout_delay=0.1,  # stdout delayed
            stderr_delay=0.0   # stderr immediate
        )

        port = RemoteAgent._read_vscode_port(None, mock_proc, "test-vscode")

        # Should return stderr port (first match)
        self.assertEqual(port, 44444)

    def test_process_exit_immediately(self):
        """Test handling when process exits immediately."""
        from agent import RemoteAgent

        # Process exits without output
        mock_proc = self._create_mock_proc(
            poll_result=1  # Non-zero exit code
        )

        port = RemoteAgent._read_vscode_port(None, mock_proc, "test-vscode")

        # Should return None (no port found)
        self.assertIsNone(port)

    def test_timeout_no_output(self):
        """Test timeout handling when no output is produced."""
        from agent import RemoteAgent
        import agent

        # Save original timeout
        original_timeout = agent.VSCODE_PORT_READ_TIMEOUT
        try:
            # Set very short timeout for testing
            agent.VSCODE_PORT_READ_TIMEOUT = 0.5

            # Create process that never outputs
            mock_proc = self._create_mock_proc(
                stdout_lines=[],
                stderr_lines=[],
                stdout_delay=10.0,  # Long delay
                stderr_delay=10.0
            )

            port = RemoteAgent._read_vscode_port(None, mock_proc, "test-vscode")

            # Should timeout and return None
            self.assertIsNone(port)
        finally:
            # Restore original timeout
            agent.VSCODE_PORT_READ_TIMEOUT = original_timeout

    def test_malformed_port_format(self):
        """Test handling of malformed port format."""
        from agent import RemoteAgent

        # Malformed port format
        stderr_lines = [
            b"HTTP server listening on http://invalid:port/\n",
        ]
        mock_proc = self._create_mock_proc(stderr_lines=stderr_lines)

        port = RemoteAgent._read_vscode_port(None, mock_proc, "test-vscode")

        # Should return None (no valid port found)
        self.assertIsNone(port)

    def test_multiple_ports_in_output(self):
        """Test that first valid port is returned when multiple ports are present."""
        from agent import RemoteAgent

        stderr_lines = [
            b"HTTP server listening on http://0.0.0.0:10000/\n",
            b"Another service on http://0.0.0.0:20000/\n",
        ]
        mock_proc = self._create_mock_proc(stderr_lines=stderr_lines)

        port = RemoteAgent._read_vscode_port(None, mock_proc, "test-vscode")

        # Should return first port
        self.assertEqual(port, 10000)

    def test_stdout_banner_stderr_port(self):
        """Test port detection when stdout has banner and stderr has port."""
        from agent import RemoteAgent

        stdout_lines = [
            b"Starting code-server...\n",
            b"Use --help for usage information\n",
        ]
        stderr_lines = [
            b"[INFO] code-server 4.132.0\n",
            b"HTTP server listening on http://0.0.0.0:12345/\n",
        ]
        mock_proc = self._create_mock_proc(
            stdout_lines=stdout_lines,
            stderr_lines=stderr_lines
        )

        port = RemoteAgent._read_vscode_port(None, mock_proc, "test-vscode")

        self.assertEqual(port, 12345)

    def test_stderr_banner_stdout_port(self):
        """Test port detection when stderr has banner and stdout has port."""
        from agent import RemoteAgent

        stdout_lines = [
            b"HTTP server listening on http://0.0.0.0:54321/\n",
        ]
        stderr_lines = [
            b"[INFO] Initializing...\n",
            b"[INFO] Ready\n",
        ]
        mock_proc = self._create_mock_proc(
            stdout_lines=stdout_lines,
            stderr_lines=stderr_lines
        )

        port = RemoteAgent._read_vscode_port(None, mock_proc, "test-vscode")

        self.assertEqual(port, 54321)

    def test_thread_exception_handling(self):
        """Test that thread exceptions don't crash the method."""
        from agent import RemoteAgent

        # Create pipe that raises exception
        mock_proc = Mock(spec=subprocess.Popen)
        mock_proc.stdout = MockPipe([], raise_error=IOError("Pipe closed"))
        mock_proc.stderr = MockPipe([])
        mock_proc.poll = Mock(return_value=None)

        port = RemoteAgent._read_vscode_port(None, mock_proc, "test-vscode")

        # Should handle exception gracefully and return None
        self.assertIsNone(port)

    def test_localhost_port_format(self):
        """Test port detection with localhost format."""
        from agent import RemoteAgent

        stderr_lines = [
            b"HTTP server listening on http://127.0.0.1:8080/\n",
        ]
        mock_proc = self._create_mock_proc(stderr_lines=stderr_lines)

        port = RemoteAgent._read_vscode_port(None, mock_proc, "test-vscode")

        self.assertEqual(port, 8080)

    def test_https_format(self):
        """Test port detection with HTTPS format."""
        from agent import RemoteAgent

        stderr_lines = [
            b"HTTPS server listening on https://0.0.0.0:8443/\n",
        ]
        mock_proc = self._create_mock_proc(stderr_lines=stderr_lines)

        port = RemoteAgent._read_vscode_port(None, mock_proc, "test-vscode")

        self.assertEqual(port, 8443)


class TestVSCodePortReadingIntegration(unittest.TestCase):
    """Integration tests for VS Code port reading with actual threads."""

    def test_concurrent_stream_reading(self):
        """Test that both streams are read concurrently without blocking."""
        from agent import RemoteAgent
        import agent

        # This test verifies that reading doesn't block when one stream
        # has no output
        original_timeout = agent.VSCODE_PORT_READ_TIMEOUT
        try:
            agent.VSCODE_PORT_READ_TIMEOUT = 2.0

            # stdout has immediate output, stderr has delayed output
            stdout_lines = [
                b"HTTP server listening on http://0.0.0.0:3000/\n",
            ]
            stderr_lines = [
                b"Delayed output\n",
            ]

            mock_proc = Mock(spec=subprocess.Popen)
            mock_proc.stdout = MockPipe(stdout_lines, delay=0.0)
            mock_proc.stderr = MockPipe(stderr_lines, delay=0.5)
            mock_proc.poll = Mock(return_value=None)

            start = time.time()
            port = RemoteAgent._read_vscode_port(None, mock_proc, "test-vscode")
            elapsed = time.time() - start

            # Should return quickly (not wait for stderr delay)
            self.assertEqual(port, 3000)
            self.assertLess(elapsed, 1.0, "Should return before full timeout")
        finally:
            agent.VSCODE_PORT_READ_TIMEOUT = original_timeout


if __name__ == '__main__':
    unittest.main()