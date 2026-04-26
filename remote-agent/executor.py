#!/usr/bin/env python3
"""
Open ACE Remote Agent - CLI Subprocess Executor

Manages the lifecycle of CLI subprocesses (qwen-code-cli, claude-code, etc.)
for remote sessions. Handles starting, feeding input, reading output via
non-blocking I/O, and stopping processes.

Uses CLI adapters from cli_adapters to build per-tool command lines and
environment variables.
"""

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
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
        permission_callback: Optional[Callable[[str, dict], None]] = None,
        usage_callback: Optional[Callable[[str, Dict[str, int]], None]] = None,
        env: Optional[Dict[str, str]] = None,
        model: Optional[str] = None,
        permission_mode: Optional[str] = None,
        allowed_tools: Optional[List[str]] = None,
    ):
        self.session_id = session_id
        self.process = process
        self.project_path = project_path
        self.cli_tool = cli_tool
        self.output_callback = output_callback
        self.permission_callback = permission_callback
        self.usage_callback = usage_callback
        self.env = env
        self.model = model
        self.permission_mode = permission_mode
        self.allowed_tools: List[str] = list(allowed_tools) if allowed_tools else []

        self._stdout_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._stopped = threading.Event()
        self._restart_lock = threading.Lock()  # Prevents concurrent restarts
        self._paused = False
        self._pause_lock = threading.Lock()

        # SDK mode initialization tracking
        self._sdk_initialized = threading.Event()
        self._init_request_id: Optional[str] = None

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
        via the output callback.  Detects ``control_request`` messages from
        the CLI (e.g. permission prompts) and invokes the optional
        ``permission_callback`` so the agent can forward them to the server.
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

                text_stripped = text.strip()
                if text_stripped and stream_name == "stdout":
                    try:
                        parsed = json.loads(text_stripped)
                        msg_type = parsed.get("type")

                        # Handle SDK initialization response
                        if msg_type == "control_response" and self._init_request_id:
                            resp = parsed.get("response", {})
                            req_id = resp.get("request_id", "")
                            if req_id == self._init_request_id:
                                subtype = resp.get("subtype")
                                if subtype == "success":
                                    logger.info(
                                        "SDK initialization complete for session %s",
                                        self.session_id[:8],
                                    )
                                    self._sdk_initialized.set()
                                else:
                                    logger.error(
                                        "SDK initialization failed for session %s: %s",
                                        self.session_id[:8],
                                        resp.get("error", "unknown"),
                                    )
                                    self._sdk_initialized.set()  # Unblock even on failure
                                self._init_request_id = None
                                continue

                        # Handle permission/control requests from CLI
                        if msg_type == "control_request" and self.permission_callback:
                            self.permission_callback(self.session_id, parsed)
                            continue

                        # Handle result messages — extract token usage
                        if msg_type == "result" and self.usage_callback:
                            data = parsed.get("data", {})
                            usage = data.get("usage")
                            if not usage:
                                msg = data.get("message", {})
                                usage = msg.get("usage")
                            if usage and isinstance(usage, dict):
                                tokens = {
                                    "input": usage.get("input_tokens", usage.get("input", 0)),
                                    "output": usage.get("output_tokens", usage.get("output", 0)),
                                }
                                if tokens["input"] or tokens["output"]:
                                    self.usage_callback(self.session_id, tokens)
                    except (json.JSONDecodeError, ValueError):
                        pass

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

    def send_permission_response(self, request_id: str, behavior: str, message: Optional[str] = None) -> bool:
        """
        Write a control_response to the subprocess stdin to approve/deny
        a pending permission request from the CLI.

        Args:
            request_id: The request_id from the original control_request.
            behavior: "allow" or "deny".
            message: Optional message (used for deny).

        Returns:
            True if the response was written successfully.
        """
        if not self.is_running:
            logger.warning(
                "Cannot send permission response to stopped session %s",
                self.session_id,
            )
            return False

        response_inner: Dict[str, Any] = {"behavior": behavior}
        if message:
            response_inner["message"] = message

        response = {
            "type": "control_response",
            "response": {
                "subtype": "success",
                "request_id": request_id,
                "response": response_inner,
            },
        }

        try:
            payload = json.dumps(response) + "\n"
            self.process.stdin.write(payload.encode("utf-8"))
            self.process.stdin.flush()
            logger.info(
                "Sent permission response for session %s, request %s: %s",
                self.session_id[:8],
                request_id[:8],
                behavior,
            )
            return True
        except (OSError, BrokenPipeError, AttributeError) as e:
            logger.error(
                "Failed to send permission response for session %s: %s",
                self.session_id[:8],
                e,
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

        # Resume the process first if paused so it can handle SIGTERM
        if self._paused:
            try:
                if os.name != "nt":
                    os.killpg(os.getpgid(self.process.pid), signal.SIGCONT)
            except OSError:
                pass
            self._paused = False

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

    def pause(self) -> bool:
        """Suspend the subprocess using SIGSTOP (Unix) or psutil (Windows)."""
        with self._pause_lock:
            if self._paused:
                return True
            if not self.is_running:
                return False
            try:
                if os.name != "nt":
                    pgid = os.getpgid(self.process.pid)
                    os.killpg(pgid, signal.SIGSTOP)
                    self._paused = True
                    logger.info(
                        "Paused session %s (pid %d)", self.session_id[:8], self.process.pid
                    )
                    return True
                else:
                    return self._pause_windows()
            except (ProcessLookupError, PermissionError, OSError) as e:
                logger.error(
                    "Failed to pause session %s: %s", self.session_id[:8], e
                )
                return False

    def resume(self) -> bool:
        """Resume a paused subprocess using SIGCONT (Unix) or psutil (Windows)."""
        with self._pause_lock:
            if not self._paused:
                return True  # Already running, nothing to do
            try:
                if os.name != "nt":
                    pgid = os.getpgid(self.process.pid)
                    os.killpg(pgid, signal.SIGCONT)
                    self._paused = False
                    logger.info(
                        "Resumed session %s (pid %d)", self.session_id[:8], self.process.pid
                    )
                    return True
                else:
                    return self._resume_windows()
            except (ProcessLookupError, PermissionError, OSError) as e:
                logger.error(
                    "Failed to resume session %s: %s", self.session_id[:8], e
                )
                self._paused = False
                return False

    def _pause_windows(self) -> bool:
        """Windows fallback: suspend via psutil or stop process entirely."""
        try:
            import psutil
            psutil.Process(self.process.pid).suspend()
            self._paused = True
            logger.info(
                "Paused session %s (pid %d) via psutil", self.session_id[:8], self.process.pid
            )
            return True
        except ImportError:
            logger.warning(
                "psutil not available, stopping process for pause on session %s",
                self.session_id[:8],
            )
            self.stop()
            self._stopped_for_pause = True
            self._paused = True
            return True

    def _resume_windows(self) -> bool:
        """Windows fallback: resume via psutil or signal restart needed."""
        if getattr(self, "_stopped_for_pause", False):
            self._stopped_for_pause = False
            self._paused = False
            return False  # Caller should restart with --resume
        try:
            import psutil
            psutil.Process(self.process.pid).resume()
            self._paused = False
            logger.info(
                "Resumed session %s (pid %d) via psutil", self.session_id[:8], self.process.pid
            )
            return True
        except ImportError:
            return False

    def wait_for_exit(self, timeout: float = 1.0) -> Optional[int]:
        """
        Wait briefly for the process to exit and return its return code.
        Returns None if the process is still running.
        """
        try:
            return self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def send_sdk_init(self) -> bool:
        """
        Send an initialize control_request to the CLI to activate SDK mode.
        This must be the first message sent to the CLI process; it enables
        the control plane (PermissionController, etc.) so that permission
        prompts are properly handled instead of causing the CLI to hang.

        Returns:
            True if the initialize message was written successfully.
        """
        if not self.is_running:
            logger.warning(
                "Cannot send SDK init to stopped session %s", self.session_id
            )
            return False

        init_request_id = str(uuid.uuid4())
        self._init_request_id = init_request_id
        self._sdk_initialized.clear()

        init_msg = {
            "type": "control_request",
            "request_id": init_request_id,
            "request": {
                "subtype": "initialize",
            },
        }

        try:
            payload = json.dumps(init_msg) + "\n"
            self.process.stdin.write(payload.encode("utf-8"))
            self.process.stdin.flush()
            logger.info(
                "Sent SDK initialize for session %s (request_id=%s)",
                self.session_id[:8],
                init_request_id[:8],
            )
            return True
        except (OSError, BrokenPipeError, AttributeError) as e:
            logger.error(
                "Failed to send SDK init for session %s: %s",
                self.session_id[:8],
                e,
            )
            self._init_request_id = None
            return False

    def wait_sdk_initialized(self, timeout: float = 15.0) -> bool:
        """
        Wait for the SDK initialization control_response from the CLI.

        Returns:
            True if initialized successfully within the timeout.
        """
        result = self._sdk_initialized.wait(timeout=timeout)
        if not result:
            logger.warning(
                "SDK initialization timed out for session %s after %.1fs",
                self.session_id[:8],
                timeout,
            )
        return result


class ProcessExecutor:
    """
    Manages CLI subprocess sessions.

    Each session corresponds to one CLI subprocess started with the
    appropriate environment variables so that LLM API calls are routed
    through the Open ACE proxy.  The ProcessExecutor uses the
    cli_adapters registry to obtain tool-specific command-line arguments
    and environment variable mappings.
    """

    def __init__(self, server_url: str, output_callback: Optional[Callable] = None, permission_callback: Optional[Callable] = None, usage_callback: Optional[Callable] = None):
        """
        Args:
            server_url: The Open ACE server base URL, used to build the
                LLM proxy URL for subprocess env vars.
            output_callback: Called with (session_id, data, stream, is_complete)
                when output is available.  If None, output is logged.
            permission_callback: Called with (session_id, control_request_dict)
                when the CLI outputs a control_request message (e.g. permission
                prompt).  If None, control_requests are forwarded as regular output.
            usage_callback: Called with (session_id, tokens_dict) when a result
                message contains token usage data.
        """
        self.server_url = server_url
        self._output_callback = output_callback or self._default_output_callback
        self._permission_callback = permission_callback
        self._usage_callback = usage_callback
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

        # Force UTF-8 encoding for subprocess I/O (important on Windows)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"

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

        # Windows npm global bin path and other common locations
        if os.name == "nt":
            appdata = os.environ.get("APPDATA", "")
            if appdata:
                npm_bin = Path(appdata) / "npm"
                candidates.extend([
                    npm_bin / exe_name,
                    npm_bin / f"{exe_name}.cmd",
                    npm_bin / f"{exe_name}.bat",
                    npm_bin / f"{exe_name}.ps1",
                ])

            # Also check NVM/npm installation paths on Windows
            program_files = os.environ.get("PROGRAMFILES", "C:\\Program Files")
            candidates.extend([
                Path(program_files) / "nodejs" / exe_name,
                Path(program_files) / "nodejs" / f"{exe_name}.cmd",
                Path(program_files) / "nodejs" / f"{exe_name}.bat",
            ])

            # Check user-local npm prefix
            localappdata = os.environ.get("LOCALAPPDATA", "")
            if localappdata:
                candidates.extend([
                    Path(localappdata) / "npm" / exe_name,
                    Path(localappdata) / "npm" / f"{exe_name}.cmd",
                    Path(localappdata) / "npm" / f"{exe_name}.bat",
                ])

        for candidate in candidates:
            if candidate.exists():
                # On Windows, os.access with X_OK doesn't work reliably,
                # so we just check if the file exists
                if os.name == "nt" or os.access(str(candidate), os.X_OK):
                    return str(candidate)

        return None

    def _build_command(
        self,
        executable: str,
        cli_tool: str,
        session_id: str,
        project_path: str,
        model: Optional[str] = None,
        permission_mode: Optional[str] = None,
        allowed_tools: Optional[List[str]] = None,
        resume: bool = False,
    ) -> List[str]:
        adapter = get_adapter(cli_tool)
        # Use the resolved executable path (may have .cmd/.bat on Windows)
        adapter_args = adapter.build_start_args(
            session_id, project_path, model,
            permission_mode=permission_mode,
            allowed_tools=allowed_tools,
            resume=resume,
        )
        return [executable] + adapter_args[1:]

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
        permission_mode: Optional[str] = None,
        allowed_tools: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            if session_id in self._sessions and self._sessions[session_id].is_running:
                return {"success": False, "error": "Session already running"}

        # Find the executable via the adapter
        executable = self._find_executable(cli_tool)
        if not executable:
            adapter = get_adapter(cli_tool)
            display_name = adapter.get_display_name()
            install_cmd = adapter.get_install_command()

            # Build a helpful error message with installation instructions
            msg = (
                f"CLI tool '{cli_tool}' ({display_name}) not found on this machine.\n"
                f"Please install it first by running: {install_cmd}"
            )

            # Add Windows-specific hints if applicable
            if os.name == "nt":
                msg += (
                    f"\n\nOn Windows, npm global binaries are typically installed in:\n"
                    f"  %APPDATA%\\npm (e.g., C:\\Users\\<username>\\AppData\\Roaming\\npm)\n"
                    f"Ensure this directory is in your system PATH."
                )

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
        cmd = self._build_command(executable, cli_tool, session_id, project_path, model, permission_mode or "default", allowed_tools)

        logger.info(
            "Starting session %s: %s in %s (pid pending)",
            session_id[:8],
            " ".join(cmd),
            project_path,
        )

        # On Windows, .cmd/.bat/.ps1 files need to be executed via shell
        use_shell = os.name == "nt" and executable.lower().endswith((".cmd", ".bat", ".ps1"))

        # When shell=True on Windows, cmd should be a string, not a list
        cmd_to_use = " ".join(cmd) if use_shell else cmd

        try:
            process = subprocess.Popen(
                cmd_to_use,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=project_path,
                env=env,
                shell=use_shell,
                # Create a new process group so we can terminate the tree
                start_new_session=not use_shell and os.name != "nt",
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" and not use_shell else 0,
            )
        except (OSError, subprocess.SubprocessError) as e:
            # Use error code for platform-independent messages
            errno = getattr(e, 'errno', None)
            winerror = getattr(e, 'winerror', None)

            # Map common Windows error codes to English messages
            if os.name == "nt" and winerror is not None:
                error_map = {
                    2: "The system cannot find the file specified",
                    3: "The system cannot find the path specified",
                    5: "Access is denied",
                    15: "The system cannot find the drive specified",
                    87: "The parameter is incorrect",
                    123: "The filename, directory name, or volume label syntax is incorrect",
                    193: "Not a valid Win32 application",
                    267: "The directory name is invalid",
                }
                err_msg = error_map.get(winerror, f"Windows error {winerror}")
            else:
                err_msg = str(e)

            # Build a helpful diagnostic message
            adapter = get_adapter(cli_tool)
            display_name = adapter.get_display_name()
            install_cmd = adapter.get_install_command()

            msg = (
                f"Failed to start {display_name} process: [WinError {winerror or errno}] {err_msg}\n"
                f"The executable '{executable}' was found but failed to launch.\n"
                f"This may indicate a corrupted installation. Try reinstalling with: {install_cmd}"
            )
            logger.error(msg)
            return {"success": False, "error": msg}

        session_proc = SessionProcess(
            session_id=session_id,
            process=process,
            project_path=project_path,
            cli_tool=cli_tool,
            output_callback=self._output_callback,
            permission_callback=self._permission_callback,
            usage_callback=self._usage_callback,
            env=env,
            model=model,
            permission_mode=permission_mode,
            allowed_tools=allowed_tools,
        )

        with self._lock:
            self._sessions[session_id] = session_proc

        # Start background output reader threads
        session_proc.start_readers()

        # Send SDK initialize to activate control plane (permission handling)
        adapter = get_adapter(cli_tool)
        if adapter.supports_stdin_input():
            if not session_proc.send_sdk_init():
                logger.warning(
                    "Failed to send SDK init for session %s, continuing anyway",
                    session_id[:8],
                )
            elif not session_proc.wait_sdk_initialized(timeout=15.0):
                logger.warning(
                    "SDK init timed out for session %s, continuing in direct mode",
                    session_id[:8],
                )

        logger.info(
            "Session %s started (pid %d): %s",
            session_id[:8],
            process.pid,
            cli_tool,
        )

        self._save_sessions_meta()
        return {"success": True, "pid": process.pid}

    def send_message(self, session_id: str, content: str) -> Dict[str, Any]:
        """
        Send a message to a running session.

        For CLI tools that support stdin input, writes a stream-json formatted
        user message to the process stdin.  If the process has exited since the
        last message, it is automatically restarted first.

        For tools that don't, runs a single-shot subprocess.

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

        # Check if the CLI tool supports stdin input
        adapter = get_adapter(session.cli_tool)
        if not adapter.supports_stdin_input():
            return self._run_single_shot(session, content, adapter)

        if not session.is_running:
            logger.info(
                "Session %s process exited, restarting for new message",
                session_id[:8],
            )
            restart_result = self._restart_session(session_id)
            if not restart_result["success"]:
                return restart_result

        # Format as stream-json user message
        message = json.dumps({
            "type": "user",
            "session_id": session_id,
            "message": {
                "role": "user",
                "content": content,
            },
            "parent_tool_use_id": None,
        }) + "\n"

        with self._lock:
            session = self._sessions.get(session_id)

        ok = session.send_message(message)
        if ok:
            return {"success": True}
        return {"success": False, "error": "Failed to write to process stdin"}

    def _run_single_shot(
        self, session: "SessionProcess", content: str, adapter: BaseCLIAdapter
    ) -> Dict[str, Any]:
        """Run a single-shot CLI command for tools that don't support stdin."""
        args = adapter.build_single_shot_args(content, session.project_path)
        logger.info("Single-shot %s: %s", session.session_id[:8], " ".join(args))

        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                cwd=session.project_path,
                env=session.env,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Command timed out"}
        except (OSError, subprocess.SubprocessError) as e:
            return {"success": False, "error": f"Failed to run command: {e}"}

        # Feed stdout lines to the output callback
        if proc.stdout:
            for line in proc.stdout.splitlines():
                if line.strip():
                    session.output_callback(session.session_id, line, "stdout", False)
            session.output_callback(session.session_id, "", "stdout", True)

        if proc.stderr:
            for line in proc.stderr.splitlines():
                if line.strip():
                    session.output_callback(session.session_id, line, "stderr", False)

        return {"success": True}

    def _restart_session(self, session_id: str) -> Dict[str, Any]:
        """
        Restart the CLI subprocess for a session that has exited.

        Uses a per-session restart lock to prevent concurrent restarts from
        creating orphaned processes.

        Args:
            session_id: Session identifier.

        Returns:
            Dict with 'success' and optionally 'error' / 'pid'.
        """
        with self._lock:
            old_session = self._sessions.get(session_id)

        if not old_session:
            return {"success": False, "error": "Session not found"}

        # Per-session lock: only one restart at a time for this session
        with old_session._restart_lock:
            # Re-fetch after acquiring restart lock (another thread may
            # have already restarted while we waited)
            with self._lock:
                current_session = self._sessions.get(session_id)
            if current_session is not old_session:
                # Another thread already restarted — nothing to do
                return {"success": True, "pid": current_session.pid}

            # Stop old process if still lingering
            if old_session.is_running:
                old_session.stop()

            executable = self._find_executable(old_session.cli_tool)
            if not executable:
                return {
                    "success": False,
                    "error": f"CLI tool '{old_session.cli_tool}' not found",
                }

            cmd = self._build_command(
                executable,
                old_session.cli_tool,
                session_id,
                old_session.project_path,
                old_session.model,
                old_session.permission_mode,
                old_session.allowed_tools,
                resume=True,
            )

            logger.info(
                "Restarting session %s: %s", session_id[:8], " ".join(cmd)
            )

            # On Windows, .cmd/.bat/.ps1 files need to be executed via shell
            use_shell = os.name == "nt" and cmd[0].lower().endswith((".cmd", ".bat", ".ps1"))

            # When shell=True on Windows, cmd should be a string, not a list
            cmd_to_use = " ".join(cmd) if use_shell else cmd

            try:
                process = subprocess.Popen(
                    cmd_to_use,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=old_session.project_path,
                    env=old_session.env,
                    shell=use_shell,
                    start_new_session=not use_shell and os.name != "nt",
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" and not use_shell else 0,
                )
            except (OSError, subprocess.SubprocessError) as e:
                # Use error code for platform-independent English messages
                errno = getattr(e, 'errno', None)
                winerror = getattr(e, 'winerror', None)
                if os.name == "nt" and winerror is not None:
                    error_map = {
                        2: "The system cannot find the file specified",
                        3: "The system cannot find the path specified",
                        5: "Access is denied",
                    }
                    err_msg = error_map.get(winerror, f"Windows error {winerror}")
                else:
                    err_msg = str(e)
                return {"success": False, "error": f"Failed to restart CLI: [WinError {winerror or errno}] {err_msg}"}

            new_session = SessionProcess(
                session_id=session_id,
                process=process,
                project_path=old_session.project_path,
                cli_tool=old_session.cli_tool,
                output_callback=self._output_callback,
                permission_callback=self._permission_callback,
                usage_callback=self._usage_callback,
                env=old_session.env,
                model=old_session.model,
                permission_mode=old_session.permission_mode,
                allowed_tools=old_session.allowed_tools,
            )

            with self._lock:
                self._sessions[session_id] = new_session

            new_session.start_readers()

            # Re-send SDK initialize after restart to activate control plane
            adapter = get_adapter(old_session.cli_tool)
            if adapter.supports_stdin_input():
                if not new_session.send_sdk_init():
                    logger.warning(
                        "Failed to send SDK init after restart for session %s",
                        session_id[:8],
                    )
                elif not new_session.wait_sdk_initialized(timeout=15.0):
                    logger.warning(
                        "SDK init timed out after restart for session %s",
                        session_id[:8],
                    )

            logger.info(
                "Restarted session %s (pid %d)", session_id[:8], process.pid
            )
            return {"success": True, "pid": process.pid}

    def add_allowed_tool_and_restart(
        self, session_id: str, tool_name: str
    ) -> Dict[str, Any]:
        """Add a tool to the allowed list and restart the CLI process."""
        with self._lock:
            session = self._sessions.get(session_id)
        if not session:
            return {"success": False, "error": "Session not found"}

        if tool_name not in session.allowed_tools:
            session.allowed_tools.append(tool_name)
        logger.info(
            "Adding allowed tool %s to session %s, restarting",
            tool_name, session_id[:8],
        )
        result = self._restart_session(session_id)
        if not result["success"]:
            return result

        # Send continue message after restart
        with self._lock:
            session = self._sessions.get(session_id)
        if session and session.is_running:
            continue_msg = json.dumps({
                "type": "user",
                "session_id": session_id,
                "message": {"role": "user", "content": "The user approved the tool use. Please continue."},
                "parent_tool_use_id": None,
            }) + "\n"
            try:
                session.process.stdin.write(continue_msg.encode("utf-8"))
                session.process.stdin.flush()
            except (OSError, BrokenPipeError) as e:
                logger.error("Failed to send continue message: %s", e)
                return {"success": False, "error": str(e)}

        return {"success": True}

    def update_permission_mode(
        self, session_id: str, permission_mode: str
    ) -> Dict[str, Any]:
        """Update the permission mode and restart the CLI process if changed."""
        with self._lock:
            session = self._sessions.get(session_id)
        if not session:
            return {"success": False, "error": "Session not found"}

        if session.permission_mode == permission_mode:
            return {"success": True}  # No change needed

        session.permission_mode = permission_mode
        logger.info(
            "Updating permission mode to %s for session %s, restarting",
            permission_mode, session_id[:8],
        )
        return self._restart_session(session_id)

    def update_model(
        self, session_id: str, model: str
    ) -> Dict[str, Any]:
        """Update the model and restart the CLI process."""
        with self._lock:
            session = self._sessions.get(session_id)
        if not session:
            return {"success": False, "error": "Session not found"}

        if session.model == model:
            return {"success": True}  # No change needed

        session.model = model
        logger.info(
            "Updating model to %s for session %s, restarting",
            model, session_id[:8],
        )
        return self._restart_session(session_id)

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
        self._save_sessions_meta()
        return {"success": True}

    def send_permission_response(self, session_id: str, request_id: str, behavior: str, message: Optional[str] = None) -> Dict[str, Any]:
        """
        Send a permission response to a CLI subprocess.

        Args:
            session_id: Session identifier.
            request_id: The request_id from the original control_request.
            behavior: "allow" or "deny".
            message: Optional message (used for deny).

        Returns:
            Dict with 'success' and optionally 'error'.
        """
        with self._lock:
            session = self._sessions.get(session_id)

        if not session:
            return {"success": False, "error": "Session not found"}

        ok = session.send_permission_response(request_id, behavior, message)
        if ok:
            return {"success": True}
        return {"success": False, "error": "Failed to write permission response to stdin"}

    def pause_session(self, session_id: str) -> Dict[str, Any]:
        """Pause a session by suspending its subprocess."""
        with self._lock:
            session = self._sessions.get(session_id)
        if not session:
            return {"success": False, "error": "Session not found"}
        ok = session.pause()
        self._save_sessions_meta()
        return {"success": ok, "paused": session._paused, "pid": session.pid}

    def resume_session(self, session_id: str) -> Dict[str, Any]:
        """Resume a paused session. If the process died while paused, restart with --resume."""
        with self._lock:
            session = self._sessions.get(session_id)
        if not session:
            return {"success": False, "error": "Session not found"}
        ok = session.resume()
        if not ok:
            # Process stopped while paused (Windows fallback or process died)
            restart_ok = self._restart_session(session_id)
            if restart_ok.get("success"):
                self._save_sessions_meta()
                return {"success": True, "restarted": True}
            return restart_ok
        self._save_sessions_meta()
        return {"success": True, "paused": session._paused, "pid": session.pid}

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
            "paused": session._paused,
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

        self._save_sessions_meta()
        logger.info("All sessions stopped")

    # ------------------------------------------------------------------
    # Crash recovery: session metadata persistence
    # ------------------------------------------------------------------

    _META_DIR = Path.home() / ".open-ace-agent"
    _META_FILE = Path.home() / ".open-ace-agent" / "sessions.json"

    def _save_sessions_meta(self) -> None:
        """Persist active session metadata to disk for crash recovery."""
        try:
            self._META_DIR.mkdir(parents=True, exist_ok=True)
            with self._lock:
                meta = {}
                for sid, s in self._sessions.items():
                    meta[sid] = {
                        "cli_tool": s.cli_tool,
                        "project_path": s.project_path,
                        "model": s.model,
                        "permission_mode": s.permission_mode,
                        "allowed_tools": s.allowed_tools,
                        "paused": s._paused,
                        "env": {k: v for k, v in s.env.items() if not k.endswith("TOKEN")} if s.env else {},
                    }
            self._META_FILE.write_text(json.dumps(meta, indent=2), encoding="utf-8")
            logger.debug("Saved %d session(s) metadata", len(meta))
        except Exception as e:
            logger.warning("Failed to save session metadata: %s", e)

    def restore_sessions(self) -> List[str]:
        """
        Restore sessions from persisted metadata after a crash.

        Returns list of successfully restored session IDs.
        """
        if not self._META_FILE.exists():
            return []

        try:
            meta = json.loads(self._META_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load session metadata: %s", e)
            return []

        if not meta:
            return []

        logger.info("Restoring %d session(s) from metadata", len(meta))
        restored = []

        for sid, info in meta.items():
            executable = self._find_executable(info["cli_tool"])
            if not executable:
                logger.warning(
                    "Cannot restore session %s: CLI tool '%s' not found",
                    sid[:8], info["cli_tool"],
                )
                continue

            # Rebuild env without saved proxy token — the server will
            # provide a fresh one on the next start_session command.
            # For now, build minimal env from adapter defaults.
            env = self._build_env(info["cli_tool"], "", info.get("model"))
            # Merge any saved non-token env vars
            if info.get("env"):
                env.update(info["env"])

            cmd = self._build_command(
                executable,
                info["cli_tool"],
                sid,
                info["project_path"],
                info.get("model"),
                info.get("permission_mode"),
                info.get("allowed_tools"),
                resume=True,
            )

            use_shell = os.name == "nt" and executable.lower().endswith((".cmd", ".bat", ".ps1"))
            cmd_to_use = " ".join(cmd) if use_shell else cmd

            try:
                process = subprocess.Popen(
                    cmd_to_use,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=info["project_path"],
                    env=env,
                    shell=use_shell,
                    start_new_session=not use_shell and os.name != "nt",
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" and not use_shell else 0,
                )
            except (OSError, subprocess.SubprocessError) as e:
                logger.error("Failed to restore session %s: %s", sid[:8], e)
                continue

            session_proc = SessionProcess(
                session_id=sid,
                process=process,
                project_path=info["project_path"],
                cli_tool=info["cli_tool"],
                output_callback=self._output_callback,
                permission_callback=self._permission_callback,
                usage_callback=self._usage_callback,
                env=env,
                model=info.get("model"),
                permission_mode=info.get("permission_mode"),
                allowed_tools=info.get("allowed_tools"),
            )

            with self._lock:
                self._sessions[sid] = session_proc

            session_proc.start_readers()

            # Send SDK init
            adapter = get_adapter(info["cli_tool"])
            if adapter.supports_stdin_input():
                session_proc.send_sdk_init()
                session_proc.wait_sdk_initialized(timeout=15.0)

            restored.append(sid)
            logger.info(
                "Restored session %s (pid %d) with --resume",
                sid[:8], process.pid,
            )

        # Clear metadata file after successful restore
        try:
            self._META_FILE.write_text("{}", encoding="utf-8")
        except OSError:
            pass

        self._save_sessions_meta()
        return restored
