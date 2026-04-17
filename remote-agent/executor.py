#!/usr/bin/env python3
"""
Open ACE Remote Agent - CLI Subprocess Executor

Manages the lifecycle of CLI subprocesses (qwen-code-cli, claude-code, etc.)
for remote sessions. Handles starting, feeding input, reading output via
non-blocking I/O, and stopping processes.

Uses CLI adapters from cli_adapters to build per-tool command lines and
environment variables.
"""

import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from cli_adapters import get_adapter
from cli_adapters.base import BaseCLIAdapter

logger = logging.getLogger(__name__)


class SessionProcess:
    """
    Represents a running CLI subprocess for a single remote session.

    Manages the process lifecycle, stdin/stdout/stderr pipes, and reader
    threads for non-blocking output collection.
    """

    def __init__(
        self,
        session_id: str,
        process: subprocess.Popen,
        project_path: str,
        cli_tool: str,
        output_callback: Callable[[str, str, str, bool], None],
    ):
        """
        Args:
            session_id: Unique session identifier.
            process: The subprocess.Popen instance.
            project_path: Working directory of the process.
            cli_tool: Name of the CLI tool being run.
            output_callback: Called with (session_id, data, stream, is_complete)
                when output is received.
        """
        self.session_id = session_id
        self.process = process
        self.project_path = project_path
        self.cli_tool = cli_tool
        self.output_callback = output_callback

        self._stdout_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._stopped = threading.Event()

    @property
    def pid(self) -> Optional[int]:
        """Process ID, or None if the process has not started or has exited."""
        return self.process.pid if self.process.returncode is None else None

    @property
    def is_running(self) -> bool:
        """Whether the subprocess is still alive."""
        return self.process.returncode is None

    def start_readers(self) -> None:
        """Start background threads to read stdout and stderr."""
        self._stdout_thread = threading.Thread(
            target=self._read_stream,
            args=(self.process.stdout, "stdout"),
            name=f"stdout-{self.session_id[:8]}",
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stream,
            args=(self.process.stderr, "stderr"),
            name=f"stderr-{self.session_id[:8]}",
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _read_stream(self, stream: Any, stream_name: str) -> None:
        """
        Continuously read lines from a subprocess stream and forward them
        via the output callback.
        """
        try:
            while not self._stopped.is_set():
                line = stream.readline()
                if not line:
                    # EOF -- process has closed this stream or exited
                    break
                if isinstance(line, bytes):
                    text = line.decode("utf-8", errors="replace")
                else:
                    text = line
                self.output_callback(self.session_id, text, stream_name, False)
        except (OSError, ValueError) as e:
            if not self._stopped.is_set():
                logger.debug(
                    "Stream reader %s/%s ended: %s",
                    self.session_id[:8],
                    stream_name,
                    e,
                )
        finally:
            # Signal completion when stdout reader finishes
            if stream_name == "stdout":
                self.output_callback(self.session_id, "", stream_name, True)

    def send_message(self, content: str) -> bool:
        """
        Write a message to the subprocess stdin.

        Returns:
            True if the message was written successfully.
        """
        if not self.is_running:
            logger.warning(
                "Cannot send message to stopped session %s", self.session_id
            )
            return False

        try:
            self.process.stdin.write((content + "\n").encode("utf-8"))
            self.process.stdin.flush()
            return True
        except (OSError, BrokenPipeError, AttributeError) as e:
            logger.error(
                "Failed to write to stdin for session %s: %s", self.session_id, e
            )
            return False

    def stop(self, timeout: float = 5.0) -> None:
        """
        Gracefully stop the subprocess.

        Sends SIGTERM, then SIGKILL after the timeout if the process
        does not exit.
        """
        if not self.is_running:
            return

        self._stopped.set()
        logger.info("Stopping session %s (pid %s)", self.session_id, self.pid)

        try:
            self.process.terminate()
        except OSError:
            pass

        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.warning(
                "Session %s did not exit gracefully, killing", self.session_id
            )
            try:
                self.process.kill()
                self.process.wait(timeout=2.0)
            except OSError:
                pass

    def wait_for_exit(self, timeout: float = 1.0) -> Optional[int]:
        """
        Wait briefly for the process to exit and return its return code.
        Returns None if the process is still running.
        """
        try:
            return self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None


class ProcessExecutor:
    """
    Manages CLI subprocess sessions.

    Each session corresponds to one CLI subprocess started with the
    appropriate environment variables so that LLM API calls are routed
    through the Open ACE proxy.  The ProcessExecutor uses the
    cli_adapters registry to obtain tool-specific command-line arguments
    and environment variable mappings.
    """

    def __init__(self, server_url: str, output_callback: Optional[Callable] = None):
        """
        Args:
            server_url: The Open ACE server base URL, used to build the
                LLM proxy URL for subprocess env vars.
            output_callback: Called with (session_id, data, stream, is_complete)
                when output is available.  If None, output is logged.
        """
        self.server_url = server_url
        self._output_callback = output_callback or self._default_output_callback
        self._sessions: Dict[str, SessionProcess] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    @property
    def active_session_count(self) -> int:
        """Number of currently running sessions."""
        with self._lock:
            return sum(1 for s in self._sessions.values() if s.is_running)

    @property
    def active_sessions(self) -> List[str]:
        """List of active session IDs."""
        with self._lock:
            return [sid for sid, s in self._sessions.items() if s.is_running]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _default_output_callback(
        self, session_id: str, data: str, stream: str, is_complete: bool
    ) -> None:
        """Default callback that logs output."""
        if data:
            logger.debug("[%s/%s] %s", session_id[:8], stream, data.rstrip("\n"))
        if is_complete:
            logger.info("Session %s output stream complete", session_id[:8])

    def _build_env(
        self,
        cli_tool: str,
        proxy_token: str,
        model: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        Build environment variables for the CLI subprocess.

        Uses the adapter for the specific CLI tool to determine which
        environment variables to set for proxy routing, then merges in
        some common Open ACE variables.

        Args:
            cli_tool: CLI tool identifier (e.g. 'qwen-code-cli').
            proxy_token: Short-lived proxy token for LLM API authentication.
            model: Optional model name.

        Returns:
            Dict of environment variable name -> value.
        """
        env = dict(os.environ)

        # Build the proxy URL that the CLI should use as its API base
        proxy_url = f"{self.server_url}/api/remote/llm-proxy"

        # Ask the adapter for tool-specific env vars
        adapter = get_adapter(cli_tool)
        adapter_env = adapter.get_env_vars(proxy_url, proxy_token)
        env.update(adapter_env)

        # Also set generic Open ACE env vars that any tool can read
        env["OPENACE_PROXY_URL"] = proxy_url
        env["OPENACE_PROXY_TOKEN"] = proxy_token

        if model:
            env["OPENACE_MODEL"] = model

        return env

    def _find_executable(self, cli_tool: str) -> Optional[str]:
        """
        Locate the CLI tool executable on the system.

        Uses the adapter's executable name first, then falls back to the
        tool identifier itself, then checks common install paths.
        """
        adapter = get_adapter(cli_tool)
        exe_name = adapter.get_executable_name()

        # Try the adapter's preferred executable name
        path = shutil.which(exe_name)
        if path:
            return path

        # Try the raw tool name
        if exe_name != cli_tool:
            path = shutil.which(cli_tool)
            if path:
                return path

        # Check common locations
        candidates = [
            Path.home() / ".local" / "bin" / exe_name,
            Path("/usr/local/bin") / exe_name,
            Path.home() / ".npm-global" / "bin" / exe_name,
            Path.home() / "node_modules" / ".bin" / exe_name,
        ]

        for candidate in candidates:
            if candidate.exists() and os.access(str(candidate), os.X_OK):
                return str(candidate)

        return None

    def _build_command(
        self,
        executable: str,
        cli_tool: str,
        session_id: str,
        project_path: str,
        model: Optional[str] = None,
    ) -> List[str]:
        """
        Build the command line using the adapter's build_start_args().
        """
        adapter = get_adapter(cli_tool)
        return adapter.build_start_args(session_id, project_path, model)

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def start_session(
        self,
        session_id: str,
        project_path: str,
        cli_tool: str,
        proxy_token: str,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Start a CLI subprocess for a remote session.

        Args:
            session_id: Unique session identifier assigned by the server.
            project_path: Working directory on this machine.
            cli_tool: Name of the CLI tool to run.
            proxy_token: Proxy token for authenticating LLM API calls.
            model: Optional model name.

        Returns:
            Dict with 'success', 'pid', and optionally 'error'.
        """
        with self._lock:
            if session_id in self._sessions and self._sessions[session_id].is_running:
                return {"success": False, "error": "Session already running"}

        # Find the executable via the adapter
        executable = self._find_executable(cli_tool)
        if not executable:
            msg = f"CLI tool '{cli_tool}' not found on this machine"
            logger.error(msg)
            return {"success": False, "error": msg}

        # Ensure project path exists
        try:
            Path(project_path).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            msg = f"Cannot create project path '{project_path}': {e}"
            logger.error(msg)
            return {"success": False, "error": msg}

        # Build environment using adapter
        env = self._build_env(cli_tool, proxy_token, model)

        # Build command using adapter
        cmd = self._build_command(executable, cli_tool, session_id, project_path, model)

        logger.info(
            "Starting session %s: %s in %s (pid pending)",
            session_id[:8],
            " ".join(cmd),
            project_path,
        )

        try:
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=project_path,
                env=env,
                # Create a new process group so we can terminate the tree
                start_new_session=True,
            )
        except (OSError, subprocess.SubprocessError) as e:
            msg = f"Failed to start CLI process: {e}"
            logger.error(msg)
            return {"success": False, "error": msg}

        session_proc = SessionProcess(
            session_id=session_id,
            process=process,
            project_path=project_path,
            cli_tool=cli_tool,
            output_callback=self._output_callback,
        )

        with self._lock:
            self._sessions[session_id] = session_proc

        # Start background output reader threads
        session_proc.start_readers()

        logger.info(
            "Session %s started (pid %d): %s",
            session_id[:8],
            process.pid,
            cli_tool,
        )

        return {"success": True, "pid": process.pid}

    def send_message(self, session_id: str, content: str) -> Dict[str, Any]:
        """
        Send a message to a running session's stdin.

        Args:
            session_id: Session identifier.
            content: Text to send.

        Returns:
            Dict with 'success' and optionally 'error'.
        """
        with self._lock:
            session = self._sessions.get(session_id)

        if not session:
            return {"success": False, "error": "Session not found"}

        if not session.is_running:
            return {"success": False, "error": "Session process is not running"}

        ok = session.send_message(content)
        if ok:
            return {"success": True}
        return {"success": False, "error": "Failed to write to process stdin"}

    def stop_session(self, session_id: str) -> Dict[str, Any]:
        """
        Stop a running session.

        Args:
            session_id: Session identifier.

        Returns:
            Dict with 'success' and optionally 'error'.
        """
        with self._lock:
            session = self._sessions.pop(session_id, None)

        if not session:
            return {"success": False, "error": "Session not found"}

        session.stop()
        logger.info("Session %s stopped", session_id[:8])
        return {"success": True}

    def get_session_info(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get information about a session."""
        with self._lock:
            session = self._sessions.get(session_id)

        if not session:
            return None

        return {
            "session_id": session_id,
            "cli_tool": session.cli_tool,
            "project_path": session.project_path,
            "pid": session.pid,
            "is_running": session.is_running,
        }

    def cleanup_stopped(self) -> List[str]:
        """
        Remove sessions whose processes have exited.

        Returns:
            List of cleaned-up session IDs.
        """
        cleaned = []
        with self._lock:
            to_remove = []
            for sid, session in self._sessions.items():
                if not session.is_running:
                    to_remove.append(sid)

            for sid in to_remove:
                self._sessions.pop(sid, None)
                cleaned.append(sid)
                logger.info("Cleaned up stopped session %s", sid[:8])

        return cleaned

    def stop_all(self) -> None:
        """Stop all running sessions."""
        with self._lock:
            sessions = list(self._sessions.values())

        for session in sessions:
            session.stop()

        with self._lock:
            self._sessions.clear()

        logger.info("All sessions stopped")
