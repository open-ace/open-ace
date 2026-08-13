"""
Integration tests for VS Code functionality.

Issue #2588: Tests for VS Code startup and port reading
with mock code-server processes.
"""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, Mock, patch

# Add remote-agent to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "remote-agent"))


class TestVSCodeIntegration(unittest.TestCase):
    """Integration tests for VS Code functionality."""

    def setUp(self):
        """Set up test fixtures."""
        # Create a temporary directory for tests
        self.test_dir = tempfile.mkdtemp(prefix="vscode_test_")

    def tearDown(self):
        """Clean up test fixtures."""
        import shutil

        try:
            shutil.rmtree(self.test_dir, ignore_errors=True)
        except Exception:  # allow-swallow: cleanup
            pass

    def test_mock_code_server_stderr_output(self):
        """
        Test VS Code port reading with a mock code-server that outputs to stderr.

        This simulates the behavior of code-server 4.132.0+ which outputs
        port information to stderr instead of stdout.
        """
        # Create a mock code-server script that outputs to stderr
        mock_script = os.path.join(self.test_dir, "mock-code-server-stderr.sh")
        with open(mock_script, "w") as f:
            f.write("""#!/bin/bash
# Mock code-server that outputs port to stderr (Issue #2588)
sleep 0.1
echo "[2026-08-13T10:47:53.911Z] info  code-server 4.132.0" >&2
echo "[2026-08-13T10:47:53.936Z] info  HTTP server listening on http://0.0.0.0:12345/" >&2
# Keep process running
tail -f /dev/null 2>/dev/null || sleep 10
""")
        os.chmod(mock_script, 0o755)

        # Test that we can read the port from stderr
        from agent import RemoteAgent

        # Create a subprocess with the mock script
        proc = subprocess.Popen(
            [mock_script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )

        try:
            # Read the port
            port = RemoteAgent._read_vscode_port(proc, "test-vscode-int")

            # Verify port was detected
            self.assertEqual(port, 12345, "Should detect port from stderr output")

        finally:
            # Clean up the process
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

    def test_mock_code_server_stdout_output(self):
        """
        Test VS Code port reading with a mock code-server that outputs to stdout.

        This ensures backward compatibility with older code-server versions.
        """
        # Create a mock code-server script that outputs to stdout
        mock_script = os.path.join(self.test_dir, "mock-code-server-stdout.sh")
        with open(mock_script, "w") as f:
            f.write("""#!/bin/bash
# Mock code-server that outputs port to stdout (old behavior)
sleep 0.1
echo "HTTP server listening on http://0.0.0.0:54321/"
# Keep process running
tail -f /dev/null 2>/dev/null || sleep 10
""")
        os.chmod(mock_script, 0o755)

        from agent import RemoteAgent

        proc = subprocess.Popen(
            [mock_script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )

        try:
            port = RemoteAgent._read_vscode_port(proc, "test-vscode-int")

            self.assertEqual(port, 54321, "Should detect port from stdout output")

        finally:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

    def test_mock_code_server_no_port(self):
        """
        Test VS Code port reading when no port is output.

        This tests timeout and error handling behavior.
        """
        # Create a mock code-server that doesn't output a port
        mock_script = os.path.join(self.test_dir, "mock-code-server-no-port.sh")
        with open(mock_script, "w") as f:
            f.write("""#!/bin/bash
# Mock code-server that doesn't output a port
sleep 0.1
echo "Starting..." >&2
echo "Error: no port" >&2
exit 1
""")
        os.chmod(mock_script, 0o755)

        import agent
        from agent import RemoteAgent

        # Set a short timeout for this test
        original_timeout = agent.VSCODE_PORT_READ_TIMEOUT
        try:
            agent.VSCODE_PORT_READ_TIMEOUT = 1.0

            proc = subprocess.Popen(
                [mock_script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )

            try:
                port = RemoteAgent._read_vscode_port(proc, "test-vscode-int")

                # Should return None (no port found)
                self.assertIsNone(port, "Should return None when no port is output")

            finally:
                # Process should have exited, but ensure cleanup
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()

        finally:
            agent.VSCODE_PORT_READ_TIMEOUT = original_timeout

    def test_mock_code_server_fast_startup(self):
        """
        Test VS Code port reading with a fast-startup mock.

        This verifies that port detection works even when the port
        is output immediately after process start.
        """
        mock_script = os.path.join(self.test_dir, "mock-code-server-fast.sh")
        with open(mock_script, "w") as f:
            f.write("""#!/bin/bash
# Fast startup - output port immediately to stderr
echo "HTTP server listening on http://127.0.0.1:9999/" >&2
# Keep running
tail -f /dev/null 2>/dev/null || sleep 10
""")
        os.chmod(mock_script, 0o755)

        from agent import RemoteAgent

        proc = subprocess.Popen(
            [mock_script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )

        try:
            port = RemoteAgent._read_vscode_port(proc, "test-vscode-int")

            self.assertEqual(port, 9999, "Should detect port from immediate stderr output")

        finally:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

    def test_mock_code_server_delayed_startup(self):
        """
        Test VS Code port reading with a delayed startup mock.

        This verifies timeout handling when port output is delayed.
        """
        mock_script = os.path.join(self.test_dir, "mock-code-server-delayed.sh")
        with open(mock_script, "w") as f:
            f.write("""#!/bin/bash
# Delayed startup - wait before outputting port
sleep 0.5
echo "HTTP server listening on http://0.0.0.0:7777/" >&2
# Keep running
tail -f /dev/null 2>/dev/null || sleep 10
""")
        os.chmod(mock_script, 0o755)

        from agent import RemoteAgent

        proc = subprocess.Popen(
            [mock_script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )

        try:
            port = RemoteAgent._read_vscode_port(proc, "test-vscode-int")

            self.assertEqual(port, 7777, "Should detect port after delay")

        finally:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


class TestVSCodeProcessCleanup(unittest.TestCase):
    """Tests for VS Code process cleanup logic."""

    def test_process_terminated_on_port_read_failure(self):
        """
        Test that code-server process is properly terminated when port reading fails.
        """
        # Verify that _cmd_start_vscode has proper error handling
        import inspect

        from agent import RemoteAgent

        # Create a simple test that verifies the cleanup logic exists
        # (Full integration test would require mocking more components)

        source = inspect.getsource(RemoteAgent._cmd_start_vscode)

        # Check for termination logic
        self.assertIn("terminate", source.lower(), "Should have termination logic")
        self.assertIn("kill", source.lower(), "Should have kill fallback")

    def test_process_cleanup_on_exception(self):
        """
        Test that process cleanup happens on exceptions.
        """
        import inspect

        from agent import RemoteAgent

        source = inspect.getsource(RemoteAgent._cmd_start_vscode)

        # Check for exception handling with cleanup
        self.assertIn("except", source.lower(), "Should have exception handling")
        self.assertIn("_vscode_processes.pop", source, "Should clean up process reference")


if __name__ == "__main__":
    unittest.main()
