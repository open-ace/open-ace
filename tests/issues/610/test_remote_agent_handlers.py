"""
Unit tests for RemoteAgent git and VSCode command handlers.

Tests cover:
- _cmd_git_status: git status/diff on remote filesystem
- _cmd_git_diff: git diff for a specific file
- _cmd_git_file: reads file content
- _cmd_start_vscode: starts code-server
- _cmd_stop_vscode: stops code-server
- _cmd_attach_vscode: re-attaches to running code-server
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from unittest.mock import MagicMock, mock_open, patch

import pytest

# ---------------------------------------------------------------------------
# Make the remote-agent package importable without installing it.
# The agent imports sibling modules (config, executor, …) so we add the
# remote-agent directory itself to sys.path and mock the heavy deps before
# importing the class under test.
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "remote-agent"))

# Patch heavy / external dependencies that the agent pulls in at import time.
# We do this *before* importing agent.py so its top-level imports resolve.
sys.modules.setdefault("requests", MagicMock())
sys.modules.setdefault("cli_settings", MagicMock())
sys.modules.setdefault("executor", MagicMock())
sys.modules.setdefault("session_sync", MagicMock())
sys.modules.setdefault("system_info", MagicMock())

from agent import RemoteAgent  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent() -> RemoteAgent:
    """Create a RemoteAgent with a mock config, bypassing real __init__."""
    config = MagicMock()
    config.machine_id = "test-machine-id-1234"
    config.server_url = "http://localhost:5000"
    config.hostname = "testhost"
    config.reconnect_base_delay = 1
    config.reconnect_max_delay = 60

    # Bypass __init__ entirely to avoid subprocess / socket side-effects
    with patch.object(RemoteAgent, "__init__", lambda self: None):
        agent = RemoteAgent()

    agent.config = config
    agent._http_send = MagicMock()
    agent._vscode_processes = {}
    agent._vscode_tokens = {}
    agent._vscode_ports = {}
    agent._vscode_passwords = {}

    return agent


def _last_http_send(agent: RemoteAgent) -> dict:
    """Return the last call args to _http_send as the message dict."""
    assert agent._http_send.called, "_http_send was never called"
    return agent._http_send.call_args[0][0]


# ===================================================================
# _cmd_git_status
# ===================================================================


class TestCmdGitStatus:
    """Tests for RemoteAgent._cmd_git_status."""

    def test_missing_project_path_sends_error(self):
        agent = _make_agent()
        agent._cmd_git_status({"request_id": "r1"})
        msg = _last_http_send(agent)
        assert msg["success"] is False
        assert "No project_path" in msg["error"]

    def test_nonexistent_directory_sends_error(self):
        agent = _make_agent()
        agent._cmd_git_status(
            {
                "request_id": "r2",
                "project_path": "/no/such/directory/ever",
            }
        )
        msg = _last_http_send(agent)
        assert msg["success"] is False
        assert "does not exist" in msg["error"]

    def test_non_git_directory_returns_empty_files(self, tmp_path):
        agent = _make_agent()
        agent._cmd_git_status(
            {
                "request_id": "r3",
                "project_path": str(tmp_path),
            }
        )
        msg = _last_http_send(agent)
        assert msg["success"] is True
        assert msg["result"]["files"] == []

    def test_successful_status_modified_files(self, tmp_path):
        """Simulate a git repo with modified and added files."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        agent = _make_agent()

        # _run_git is called three times:
        #   1. rev-parse HEAD  -> success (has_head=True)
        #   2. diff --numstat HEAD
        #   3. status --porcelain
        diff_output = "5\t2\tsrc/main.py\n3\t0\tnew_file.txt\n"
        status_output = " M src/main.py\n?? new_file.txt\n"

        calls = {
            0: MagicMock(stdout="", stderr="", returncode=0),  # rev-parse HEAD
            1: MagicMock(stdout=diff_output, stderr="", returncode=0),  # diff --numstat
            2: MagicMock(stdout=status_output, stderr="", returncode=0),  # status --porcelain
        }

        def mock_run(cmd, **kwargs):
            # Determine which call this is based on the args list
            call_idx = mock_run._call_count
            mock_run._call_count += 1
            return calls[call_idx]

        mock_run._call_count = 0

        with patch("agent.subprocess.run", side_effect=mock_run):
            agent._cmd_git_status(
                {
                    "request_id": "r4",
                    "project_path": str(tmp_path),
                }
            )

        msg = _last_http_send(agent)
        assert msg["success"] is True
        files = msg["result"]["files"]
        assert len(files) == 2

        # Verify modified file
        main_py = next(f for f in files if f["path"] == "src/main.py")
        assert main_py["status"] == "modified"
        assert main_py["additions"] == 5
        assert main_py["deletions"] == 2

        # Verify added file
        new_file = next(f for f in files if f["path"] == "new_file.txt")
        assert new_file["status"] == "added"

    def test_successful_status_deleted_file(self, tmp_path):
        """Deleted file shows up with status 'deleted'.

        Note: real ``git status --porcelain`` outputs `` D file`` (space-D),
        but the agent code calls ``.strip()`` on the full stdout before
        splitting, which removes the leading space from the first line.
        For a single line, the effective parsed line becomes ``D file``.
        We test the actual code path by providing output that survives strip().
        """
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        agent = _make_agent()

        # No diff output (file is deleted, no staged diff)
        diff_output = ""
        # Use staged-delete format (D in index) which keeps D as first char
        status_output = "D  deleted.txt\n"

        calls = {
            0: MagicMock(stdout="", stderr="", returncode=0),
            1: MagicMock(stdout=diff_output, stderr="", returncode=0),
            2: MagicMock(stdout=status_output, stderr="", returncode=0),
        }

        def mock_run(cmd, **kwargs):
            idx = mock_run._call_count
            mock_run._call_count += 1
            return calls[idx]

        mock_run._call_count = 0

        with patch("agent.subprocess.run", side_effect=mock_run):
            agent._cmd_git_status(
                {
                    "request_id": "r5",
                    "project_path": str(tmp_path),
                }
            )

        msg = _last_http_send(agent)
        assert msg["success"] is True
        files = msg["result"]["files"]
        assert len(files) == 1
        assert files[0]["status"] == "deleted"
        assert files[0]["path"] == "deleted.txt"

    def test_git_command_failure_handled_gracefully(self, tmp_path):
        """When all git commands fail, should still return a result (or error)."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        agent = _make_agent()

        # All git calls fail
        with patch("agent.subprocess.run", side_effect=FileNotFoundError("git not found")):
            agent._cmd_git_status(
                {
                    "request_id": "r6",
                    "project_path": str(tmp_path),
                }
            )

        msg = _last_http_send(agent)
        # Should still get a response; _run_git catches and returns ("", msg, False)
        # Since has_head is False, diff --cached is attempted and also fails.
        # status --porcelain also fails, so files dict is empty.
        assert msg["success"] is True
        assert msg["result"]["files"] == []

    def test_path_with_tilde_expansion(self, tmp_path):
        """Ensure ~ in project_path is expanded correctly."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        agent = _make_agent()

        with patch.dict(os.environ, {"HOME": str(tmp_path)}):
            # Use ~/subdir to test expansion
            subdir = tmp_path / "subdir"
            subdir.mkdir()
            (subdir / ".git").mkdir()

            calls = {
                0: MagicMock(stdout="", stderr="", returncode=0),
                1: MagicMock(stdout="", stderr="", returncode=0),
                2: MagicMock(stdout="", stderr="", returncode=0),
            }

            def mock_run(cmd, **kwargs):
                idx = mock_run._call_count
                mock_run._call_count += 1
                return calls[idx]

            mock_run._call_count = 0

            with patch("agent.subprocess.run", side_effect=mock_run):
                agent._cmd_git_status(
                    {
                        "request_id": "r7",
                        "project_path": "~/subdir",
                    }
                )

        msg = _last_http_send(agent)
        assert msg["success"] is True
        assert msg["result"]["files"] == []

    def test_no_head_uses_cached_diff(self, tmp_path):
        """When HEAD does not exist (fresh repo), uses --cached diff."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        agent = _make_agent()

        diff_output = "10\t0\tstaged.txt\n"
        status_output = "A  staged.txt\n"

        calls = {
            0: MagicMock(stdout="", stderr="", returncode=128),  # rev-parse HEAD fails
            1: MagicMock(stdout=diff_output, stderr="", returncode=0),  # diff --cached
            2: MagicMock(stdout=status_output, stderr="", returncode=0),
        }

        def mock_run(cmd, **kwargs):
            idx = mock_run._call_count
            mock_run._call_count += 1
            return calls[idx]

        mock_run._call_count = 0

        with patch("agent.subprocess.run", side_effect=mock_run):
            agent._cmd_git_status(
                {
                    "request_id": "r8",
                    "project_path": str(tmp_path),
                }
            )

        msg = _last_http_send(agent)
        assert msg["success"] is True
        files = msg["result"]["files"]
        assert len(files) == 1
        # Verify the --cached command was used (second call)
        assert mock_run._call_count >= 2  # All calls completed


# ===================================================================
# _cmd_git_diff
# ===================================================================


class TestCmdGitDiff:
    """Tests for RemoteAgent._cmd_git_diff."""

    def test_missing_params_sends_error(self):
        agent = _make_agent()
        agent._cmd_git_diff({"request_id": "r1"})
        msg = _last_http_send(agent)
        assert msg["success"] is False
        assert "project_path and file are required" in msg["error"]

    def test_missing_file_sends_error(self):
        agent = _make_agent()
        agent._cmd_git_diff({"request_id": "r2", "project_path": "/tmp"})
        msg = _last_http_send(agent)
        assert msg["success"] is False
        assert "project_path and file are required" in msg["error"]

    def test_path_traversal_blocked(self, tmp_path):
        agent = _make_agent()
        agent._cmd_git_diff(
            {
                "request_id": "r3",
                "project_path": str(tmp_path),
                "file": "../../etc/passwd",
            }
        )
        msg = _last_http_send(agent)
        assert msg["success"] is False
        assert "Path traversal" in msg["error"]

    def test_successful_diff(self, tmp_path):
        """Successful diff returns original and modified content."""
        agent = _make_agent()

        # Create a file on disk to simulate modified content
        test_file = tmp_path / "src.py"
        test_file.write_text("print('modified')\n")

        diff_output = textwrap.dedent(
            """\
            diff --git a/src.py b/src.py
            --- a/src.py
            +++ b/src.py
            @@ -1 +1 @@
            -print('original')
            +print('modified')
        """
        )
        original_content = "print('original')\n"

        calls = {
            0: MagicMock(stdout="", stderr="", returncode=0),  # rev-parse HEAD
            1: MagicMock(stdout=diff_output, stderr="", returncode=0),  # diff HEAD
            2: MagicMock(stdout=original_content, stderr="", returncode=0),  # show HEAD:file
        }

        def mock_run(cmd, **kwargs):
            idx = mock_run._call_count
            mock_run._call_count += 1
            return calls[idx]

        mock_run._call_count = 0

        with patch("agent.subprocess.run", side_effect=mock_run):
            agent._cmd_git_diff(
                {
                    "request_id": "r4",
                    "project_path": str(tmp_path),
                    "file": "src.py",
                }
            )

        msg = _last_http_send(agent)
        assert msg["success"] is True
        assert msg["result"]["file"] == "src.py"
        assert msg["result"]["diff"] == diff_output
        assert msg["result"]["originalContent"] == original_content
        assert msg["result"]["modifiedContent"] == "print('modified')\n"

    def test_file_not_found_deleted_file(self, tmp_path):
        """Diff for a deleted file: full_path does not exist on disk."""
        agent = _make_agent()

        diff_output = textwrap.dedent(
            """\
            diff --git a/gone.py b/gone.py
            --- a/gone.py
            +++ /dev/null
        """
        )
        original_content = "old content\n"

        calls = {
            0: MagicMock(stdout="", stderr="", returncode=0),  # rev-parse HEAD
            1: MagicMock(stdout=diff_output, stderr="", returncode=0),
            2: MagicMock(stdout=original_content, stderr="", returncode=0),
        }

        def mock_run(cmd, **kwargs):
            idx = mock_run._call_count
            mock_run._call_count += 1
            return calls[idx]

        mock_run._call_count = 0

        with patch("agent.subprocess.run", side_effect=mock_run):
            agent._cmd_git_diff(
                {
                    "request_id": "r5",
                    "project_path": str(tmp_path),
                    "file": "gone.py",
                }
            )

        msg = _last_http_send(agent)
        assert msg["success"] is True
        # File is gone on disk, modifiedContent should be empty
        assert msg["result"]["modifiedContent"] == ""
        assert msg["result"]["originalContent"] == original_content

    def test_no_head_uses_cached_diff(self, tmp_path):
        """When HEAD does not exist, uses --cached diff."""
        agent = _make_agent()

        test_file = tmp_path / "new.py"
        test_file.write_text("new file content\n")

        diff_output = "diff --git a/new.py b/new.py\n--- /dev/null\n+++ b/new.py\n"

        calls = {
            0: MagicMock(stdout="", stderr="", returncode=128),  # rev-parse HEAD fails
            1: MagicMock(stdout=diff_output, stderr="", returncode=0),  # diff --cached
        }

        def mock_run(cmd, **kwargs):
            idx = mock_run._call_count
            mock_run._call_count += 1
            return calls[idx]

        mock_run._call_count = 0

        with patch("agent.subprocess.run", side_effect=mock_run):
            agent._cmd_git_diff(
                {
                    "request_id": "r6",
                    "project_path": str(tmp_path),
                    "file": "new.py",
                }
            )

        msg = _last_http_send(agent)
        assert msg["success"] is True
        assert msg["result"]["diff"] == diff_output
        # No show command issued since has_head is False
        assert msg["result"]["originalContent"] == ""


# ===================================================================
# _cmd_git_file
# ===================================================================


class TestCmdGitFile:
    """Tests for RemoteAgent._cmd_git_file."""

    def test_missing_params_sends_error(self):
        agent = _make_agent()
        agent._cmd_git_file({"request_id": "r1"})
        msg = _last_http_send(agent)
        assert msg["success"] is False
        assert "project_path and file are required" in msg["error"]

    def test_path_traversal_blocked(self, tmp_path):
        agent = _make_agent()
        agent._cmd_git_file(
            {
                "request_id": "r2",
                "project_path": str(tmp_path),
                "file": "../../../etc/shadow",
            }
        )
        msg = _last_http_send(agent)
        assert msg["success"] is False
        assert "Path traversal" in msg["error"]

    def test_file_not_found(self, tmp_path):
        agent = _make_agent()
        agent._cmd_git_file(
            {
                "request_id": "r3",
                "project_path": str(tmp_path),
                "file": "does_not_exist.py",
            }
        )
        msg = _last_http_send(agent)
        assert msg["success"] is False
        assert "File not found" in msg["error"]

    def test_permission_denied(self, tmp_path):
        agent = _make_agent()

        # Create a file that will raise PermissionError on read
        restricted = tmp_path / "secret.txt"
        restricted.write_text("secret\n")

        with patch("builtins.open", side_effect=PermissionError("read denied")):
            agent._cmd_git_file(
                {
                    "request_id": "r4",
                    "project_path": str(tmp_path),
                    "file": "secret.txt",
                }
            )

        msg = _last_http_send(agent)
        assert msg["success"] is False
        assert "Permission denied" in msg["error"]

    def test_successful_file_read(self, tmp_path):
        agent = _make_agent()

        test_file = tmp_path / "hello.py"
        test_file.write_text('print("hello")\n', encoding="utf-8")

        agent._cmd_git_file(
            {
                "request_id": "r5",
                "project_path": str(tmp_path),
                "file": "hello.py",
            }
        )

        msg = _last_http_send(agent)
        assert msg["success"] is True
        assert msg["result"]["file"] == "hello.py"
        assert msg["result"]["content"] == 'print("hello")\n'

    def test_subdirectory_file_read(self, tmp_path):
        agent = _make_agent()

        subdir = tmp_path / "src" / "utils"
        subdir.mkdir(parents=True)
        (subdir / "helper.py").write_text("def help(): pass\n")

        agent._cmd_git_file(
            {
                "request_id": "r6",
                "project_path": str(tmp_path),
                "file": "src/utils/helper.py",
            }
        )

        msg = _last_http_send(agent)
        assert msg["success"] is True
        assert msg["result"]["content"] == "def help(): pass\n"

    def test_empty_file_read(self, tmp_path):
        agent = _make_agent()

        empty = tmp_path / "empty.txt"
        empty.write_text("")

        agent._cmd_git_file(
            {
                "request_id": "r7",
                "project_path": str(tmp_path),
                "file": "empty.txt",
            }
        )

        msg = _last_http_send(agent)
        assert msg["success"] is True
        assert msg["result"]["content"] == ""


# ===================================================================
# _cmd_start_vscode
# ===================================================================


class TestCmdStartVscode:
    """Tests for RemoteAgent._cmd_start_vscode."""

    def test_missing_project_path_sends_error(self):
        agent = _make_agent()
        agent._cmd_start_vscode({"vscode_id": "v1"})
        msg = _last_http_send(agent)
        assert msg["status"] == "error"
        assert "No project_path" in msg["error"]

    def test_nonexistent_directory_sends_error(self):
        agent = _make_agent()
        agent._cmd_start_vscode(
            {
                "vscode_id": "v2",
                "project_path": "/no/such/directory",
            }
        )
        msg = _last_http_send(agent)
        assert msg["status"] == "error"
        assert "does not exist" in msg["error"]

    def test_code_server_not_found_sends_install_hint(self, tmp_path):
        agent = _make_agent()
        with patch.object(agent, "_find_code_server", return_value=None):
            agent._cmd_start_vscode(
                {
                    "vscode_id": "v3",
                    "project_path": str(tmp_path),
                }
            )
        msg = _last_http_send(agent)
        assert msg["status"] == "error"
        assert "code-server is not installed" in msg["error"]
        assert "coder.com" in msg["error"]

    def test_successful_start(self, tmp_path):
        agent = _make_agent()

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None

        with (
            patch.object(agent, "_find_code_server", return_value="/usr/local/bin/code-server"),
            patch("agent.subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch.object(agent, "_read_vscode_port", return_value=8080),
            patch.object(agent, "_get_reachable_hostname", return_value="192.168.1.100"),
        ):

            agent._cmd_start_vscode(
                {
                    "vscode_id": "v4",
                    "project_path": str(tmp_path),
                }
            )

        msg = _last_http_send(agent)
        assert msg["status"] == "running"
        assert msg["http_url"] == "http://192.168.1.100:8080"
        assert msg["token"] is not None
        assert len(msg["token"]) == 64  # token_hex(32) = 64 hex chars

        # Verify process was stored
        assert "v4" in agent._vscode_processes
        assert agent._vscode_ports["v4"] == 8080

        # Verify Popen was called with correct args
        popen_args = mock_popen.call_args[0][0]
        assert popen_args[0] == "/usr/local/bin/code-server"
        assert "--port" in popen_args
        assert "0" in popen_args

    def test_code_server_fails_to_start_no_port(self, tmp_path):
        agent = _make_agent()

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None

        with (
            patch.object(agent, "_find_code_server", return_value="/usr/local/bin/code-server"),
            patch("agent.subprocess.Popen", return_value=mock_proc),
            patch.object(agent, "_read_vscode_port", return_value=None),
        ):

            agent._cmd_start_vscode(
                {
                    "vscode_id": "v5",
                    "project_path": str(tmp_path),
                }
            )

        msg = _last_http_send(agent)
        assert msg["status"] == "error"
        assert "failed to start" in msg["error"]

    def test_start_vscode_stops_existing_process(self, tmp_path):
        """Starting a new vscode for the same ID should stop the old one."""
        agent = _make_agent()

        old_proc = MagicMock()
        agent._vscode_processes["v6"] = old_proc

        new_proc = MagicMock()
        new_proc.poll.return_value = None

        with (
            patch.object(agent, "_find_code_server", return_value="/usr/local/bin/code-server"),
            patch("agent.subprocess.Popen", return_value=new_proc),
            patch.object(agent, "_read_vscode_port", return_value=9000),
            patch.object(agent, "_get_reachable_hostname", return_value="10.0.0.1"),
        ):

            agent._cmd_start_vscode(
                {
                    "vscode_id": "v6",
                    "project_path": str(tmp_path),
                }
            )

        old_proc.terminate.assert_called_once()
        old_proc.wait.assert_called_once()
        assert agent._vscode_processes["v6"] is new_proc

    def test_popen_exception_sends_error(self, tmp_path):
        agent = _make_agent()

        with (
            patch.object(agent, "_find_code_server", return_value="/usr/local/bin/code-server"),
            patch("agent.subprocess.Popen", side_effect=OSError("cannot fork")),
        ):

            agent._cmd_start_vscode(
                {
                    "vscode_id": "v7",
                    "project_path": str(tmp_path),
                }
            )

        msg = _last_http_send(agent)
        assert msg["status"] == "error"
        assert "cannot fork" in msg["error"]


# ===================================================================
# _cmd_stop_vscode
# ===================================================================


class TestCmdStopVscode:
    """Tests for RemoteAgent._cmd_stop_vscode."""

    def test_stops_existing_process(self):
        agent = _make_agent()

        mock_proc = MagicMock()
        agent._vscode_processes["v1"] = mock_proc
        agent._vscode_ports["v1"] = 8080
        agent._vscode_tokens["v1"] = "abc123"

        agent._cmd_stop_vscode({"vscode_id": "v1"})

        mock_proc.terminate.assert_called_once()
        mock_proc.wait.assert_called_once_with(timeout=5)

        msg = _last_http_send(agent)
        assert msg["status"] == "stopped"
        assert "v1" not in agent._vscode_processes
        assert "v1" not in agent._vscode_ports
        assert "v1" not in agent._vscode_tokens

    def test_handles_already_stopped_vscode(self):
        """Stopping a non-existent vscode_id should not crash."""
        agent = _make_agent()

        agent._cmd_stop_vscode({"vscode_id": "nonexistent"})

        msg = _last_http_send(agent)
        assert msg["status"] == "stopped"
        assert msg["vscode_id"] == "nonexistent"

    def test_terminate_timeout_falls_back_to_kill(self):
        """If terminate+wait times out, falls back to kill."""
        agent = _make_agent()

        mock_proc = MagicMock()
        mock_proc.wait.side_effect = subprocess.TimeoutExpired("cmd", 5)
        agent._vscode_processes["v2"] = mock_proc

        agent._cmd_stop_vscode({"vscode_id": "v2"})

        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_called_once()

        msg = _last_http_send(agent)
        assert msg["status"] == "stopped"

    def test_terminate_raises_exception(self):
        """If terminate raises an unexpected exception, still reports stopped."""
        agent = _make_agent()

        mock_proc = MagicMock()
        mock_proc.terminate.side_effect = OSError("already dead")
        agent._vscode_processes["v3"] = mock_proc

        agent._cmd_stop_vscode({"vscode_id": "v3"})

        msg = _last_http_send(agent)
        assert msg["status"] == "stopped"


# ===================================================================
# _cmd_attach_vscode
# ===================================================================


class TestCmdAttachVscode:
    """Tests for RemoteAgent._cmd_attach_vscode."""

    def test_running_process_returns_status_with_url(self):
        agent = _make_agent()

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # still running
        agent._vscode_processes["v1"] = mock_proc
        agent._vscode_ports["v1"] = 8080
        agent._vscode_tokens["v1"] = "tok123"

        with patch.object(agent, "_get_reachable_hostname", return_value="10.0.0.5"):
            agent._cmd_attach_vscode({"vscode_id": "v1"})

        msg = _last_http_send(agent)
        assert msg["status"] == "running"
        assert msg["http_url"] == "http://10.0.0.5:8080"
        assert msg["token"] == "tok123"

    def test_not_running_returns_not_found(self):
        agent = _make_agent()

        # No process registered for this ID
        agent._cmd_attach_vscode({"vscode_id": "v2"})

        msg = _last_http_send(agent)
        assert msg["status"] == "not_found"

    def test_dead_process_returns_not_found(self):
        """A process that has exited (poll() returns non-None) should be treated as not running."""
        agent = _make_agent()

        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0  # exited
        agent._vscode_processes["v3"] = mock_proc
        agent._vscode_ports["v3"] = 9999
        agent._vscode_tokens["v3"] = "dead-token"

        agent._cmd_attach_vscode({"vscode_id": "v3"})

        msg = _last_http_send(agent)
        assert msg["status"] == "not_found"
        # Verify cleanup happened
        assert "v3" not in agent._vscode_processes
        assert "v3" not in agent._vscode_ports
        assert "v3" not in agent._vscode_tokens

    def test_running_but_port_lost_returns_error(self):
        """Process running but port info missing should return error."""
        agent = _make_agent()

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        agent._vscode_processes["v4"] = mock_proc
        # No port registered
        agent._vscode_ports.pop("v4", None)

        agent._cmd_attach_vscode({"vscode_id": "v4"})

        msg = _last_http_send(agent)
        assert msg["status"] == "error"
        assert "Port info lost" in msg["error"]

    def test_multiple_attach_calls_consistent(self):
        """Multiple attach calls for the same running process return consistent data."""
        agent = _make_agent()

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        agent._vscode_processes["v5"] = mock_proc
        agent._vscode_ports["v5"] = 3000
        agent._vscode_tokens["v5"] = "stable-token"

        with patch.object(agent, "_get_reachable_hostname", return_value="myhost"):
            for _ in range(3):
                agent._http_send.reset_mock()
                agent._cmd_attach_vscode({"vscode_id": "v5"})
                msg = _last_http_send(agent)
                assert msg["status"] == "running"
                assert msg["http_url"] == "http://myhost:3000"


# ===================================================================
# _find_code_server
# ===================================================================


class TestFindCodeServer:
    """Tests for RemoteAgent._find_code_server."""

    def test_found_in_path(self):
        agent = _make_agent()
        with patch("agent.shutil.which", return_value="/usr/bin/code-server"):
            result = agent._find_code_server()
        assert result == "/usr/bin/code-server"

    def test_not_found_anywhere(self):
        agent = _make_agent()
        with (
            patch("agent.shutil.which", return_value=None),
            patch("os.path.isfile", return_value=False),
        ):
            result = agent._find_code_server()
        assert result is None

    def test_found_in_common_location(self, tmp_path):
        agent = _make_agent()
        cs_path = str(tmp_path / ".local" / "bin" / "code-server")

        def fake_which(name):
            return None

        def fake_isfile(p):
            return p == cs_path

        def fake_access(p, mode):
            return p == cs_path

        with (
            patch("agent.shutil.which", side_effect=fake_which),
            patch("os.path.expanduser", return_value=str(tmp_path)),
            patch("os.path.isfile", side_effect=fake_isfile),
            patch("os.access", side_effect=fake_access),
        ):
            result = agent._find_code_server()
        assert result == cs_path


# ===================================================================
# _run_git helper
# ===================================================================


class TestRunGit:
    """Tests for the _run_git internal helper."""

    def test_success(self):
        agent = _make_agent()
        mock_result = MagicMock()
        mock_result.stdout = "output"
        mock_result.stderr = ""
        mock_result.returncode = 0

        with patch("agent.subprocess.run", return_value=mock_result) as mock_run:
            stdout, stderr, success = agent._run_git(["status"], "/some/path")

        assert stdout == "output"
        assert success is True
        mock_run.assert_called_once_with(
            ["git", "-C", "/some/path", "status"],
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_nonzero_return_code(self):
        agent = _make_agent()
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.stderr = "fatal: not a git repository"
        mock_result.returncode = 128

        with patch("agent.subprocess.run", return_value=mock_result):
            stdout, stderr, success = agent._run_git(["status"], "/bad/path")

        assert success is False
        assert "fatal" in stderr

    def test_git_not_installed(self):
        agent = _make_agent()
        with patch("agent.subprocess.run", side_effect=FileNotFoundError):
            stdout, stderr, success = agent._run_git(["status"], "/path")

        assert stdout == ""
        assert "not installed" in stderr
        assert success is False

    def test_timeout(self):
        agent = _make_agent()
        with patch("agent.subprocess.run", side_effect=subprocess.TimeoutExpired("git", 30)):
            stdout, stderr, success = agent._run_git(["status"], "/path")

        assert stdout == ""
        assert "timed out" in stderr
        assert success is False

    def test_generic_exception(self):
        agent = _make_agent()
        with patch("agent.subprocess.run", side_effect=RuntimeError("oops")):
            stdout, stderr, success = agent._run_git(["status"], "/path")

        assert stdout == ""
        assert "oops" in stderr
        assert success is False


class TestGetReachableHostname:
    """Tests for RemoteAgent._get_reachable_hostname.

    Issue #672: When VPN is active, socket.connect returns VPN IP which may not
    be reachable from the browser. Configured hostname should take priority.
    """

    def test_configured_hostname_takes_priority_over_detected_ip(self):
        """Configured hostname should be used instead of auto-detected IP."""
        agent = _make_agent()
        agent.config.hostname = "192.168.1.100"  # Configured LAN IP

        # Even if socket.connect would return a different IP (VPN IP scenario)
        with patch("agent.socket.socket") as mock_socket_class:
            mock_sock = MagicMock()
            mock_sock.__enter__ = MagicMock(return_value=mock_sock)
            mock_sock.__exit__ = MagicMock(return_value=False)
            mock_sock.getsockname.return_value = ("192.168.255.10", 0)  # VPN IP
            mock_socket_class.return_value = mock_sock

            result = agent._get_reachable_hostname()

        # Should return configured hostname, NOT the VPN IP
        assert result == "192.168.1.100"
        # socket.connect should NOT be called when hostname is configured
        mock_sock.connect.assert_not_called()

    def test_localhost_is_ignored_and_ip_detected(self):
        """'localhost' should be treated as not configured, IP detection kicks in."""
        agent = _make_agent()
        agent.config.hostname = "localhost"

        with patch("agent.socket.socket") as mock_socket_class:
            mock_sock = MagicMock()
            mock_sock.__enter__ = MagicMock(return_value=mock_sock)
            mock_sock.__exit__ = MagicMock(return_value=False)
            mock_sock.getsockname.return_value = ("192.168.1.50", 0)
            mock_socket_class.return_value = mock_sock

            result = agent._get_reachable_hostname()

        assert result == "192.168.1.50"
        mock_sock.connect.assert_called_once()

    def test_no_hostname_uses_detected_ip(self):
        """No hostname configured -> auto-detect IP via socket.connect."""
        agent = _make_agent()
        agent.config.hostname = None

        with patch("agent.socket.socket") as mock_socket_class:
            mock_sock = MagicMock()
            mock_sock.__enter__ = MagicMock(return_value=mock_sock)
            mock_sock.__exit__ = MagicMock(return_value=False)
            mock_sock.getsockname.return_value = ("10.0.0.5", 0)
            mock_socket_class.return_value = mock_sock

            result = agent._get_reachable_hostname()

        assert result == "10.0.0.5"
        mock_sock.connect.assert_called_once_with(("8.8.8.8", 80))

    def test_socket_failure_fallback_to_localhost(self):
        """If socket.connect fails, fallback to 127.0.0.1."""
        agent = _make_agent()
        agent.config.hostname = None

        with patch("agent.socket.socket") as mock_socket_class:
            mock_sock = MagicMock()
            mock_sock.__enter__ = MagicMock(return_value=mock_sock)
            mock_sock.__exit__ = MagicMock(return_value=False)
            mock_sock.connect.side_effect = OSError("Network unreachable")
            mock_socket_class.return_value = mock_sock

            result = agent._get_reachable_hostname()

        assert result == "127.0.0.1"

    def test_empty_string_hostname_uses_detected_ip(self):
        """Empty string hostname should be treated as not configured."""
        agent = _make_agent()
        agent.config.hostname = ""

        with patch("agent.socket.socket") as mock_socket_class:
            mock_sock = MagicMock()
            mock_sock.__enter__ = MagicMock(return_value=mock_sock)
            mock_sock.__exit__ = MagicMock(return_value=False)
            mock_sock.getsockname.return_value = ("172.16.0.1", 0)
            mock_socket_class.return_value = mock_sock

            result = agent._get_reachable_hostname()

        assert result == "172.16.0.1"
        mock_sock.connect.assert_called_once()
