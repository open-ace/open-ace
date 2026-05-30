"""
Open ACE Remote Agent - Main Daemon

Connects to the Open ACE server via HTTP polling, handles commands
(start_session, send_message, stop_session, start_terminal, stop_terminal),
manages CLI subprocesses through the executor module, and sends heartbeats.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from typing import Any

import requests
from cli_settings import apply_cli_settings
from executor import ProcessExecutor
from session_sync import SessionSyncService
from system_info import get_capabilities

from config import AgentConfig

logger = logging.getLogger("openace-agent")


class RemoteAgent:
    """
    Main remote agent daemon.

    Connects to the Open ACE server via HTTP polling, receives commands,
    manages CLI subprocesses, and reports output and status back.
    """

    def __init__(self, config: AgentConfig | None = None):
        self.config = config or AgentConfig()
        self._executor = ProcessExecutor(
            server_url=self.config.server_url,
            output_callback=self._on_session_output,
            permission_callback=self._on_permission_request,
            usage_callback=self._send_usage_report,
        )
        self._capabilities = get_capabilities()
        self._running = False
        self._reconnect_delay = self.config.reconnect_base_delay
        # Terminal server management
        self._terminal_processes: dict[str, subprocess.Popen] = {}
        self._terminal_tokens: dict[str, str] = {}
        self._terminal_ports: dict[str, int] = {}
        self._terminal_ws_urls: dict[str, str] = {}  # Store ws_url for attach
        # VSCode (code-server) state
        self._vscode_processes: dict[str, subprocess.Popen] = {}
        self._vscode_tokens: dict[str, str] = {}
        self._vscode_ports: dict[str, int] = {}
        # Terminal info persistence directory
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self._terminal_info_dir = os.path.join(script_dir, ".terminal_sessions")
        # Session sync service
        self._session_sync = SessionSyncService(self._http_send, self.config)
        # Restore terminal sessions from files
        self._restore_terminal_sessions()

    def _restore_terminal_sessions(self) -> None:
        """Restore terminal session info from persisted files."""
        if not os.path.exists(self._terminal_info_dir):
            return
        try:
            for filename in os.listdir(self._terminal_info_dir):
                if filename.endswith(".json"):
                    filepath = os.path.join(self._terminal_info_dir, filename)
                    try:
                        with open(filepath) as f:
                            info = json.load(f)
                        terminal_id = info.get("terminal_id")
                        if terminal_id:
                            self._terminal_ports[terminal_id] = info.get("port", 0)
                            self._terminal_tokens[terminal_id] = info.get("token", "")
                            self._terminal_ws_urls[terminal_id] = info.get("ws_url", "")
                            # Check if port is still listening
                            port = info.get("port", 0)
                            if self._check_port_listening(port):
                                logger.info(
                                    "Restored terminal session %s: port=%d",
                                    terminal_id[:8],
                                    port,
                                )
                            else:
                                # Port not listening, remove stale info file
                                logger.info(
                                    "Terminal %s port %d not listening, removing stale file",
                                    terminal_id[:8],
                                    port,
                                )
                                os.remove(filepath)
                    except Exception as e:
                        logger.warning(
                            "Failed to restore terminal session from %s: %s", filename, e
                        )
        except Exception as e:
            logger.warning("Failed to restore terminal sessions: %s", e)

    def _check_port_listening(self, port: int) -> bool:
        """Check if a port is still listening."""
        if port <= 0:
            return False
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                result = s.connect_ex(("127.0.0.1", port))
                return result == 0
        except Exception:
            return False

    def _atomic_write_json(self, filepath: str, data: dict | list) -> None:
        """Write JSON to file atomically using temp file + rename."""
        dir_path = os.path.dirname(filepath)
        fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.rename(tmp_path, filepath)
            os.chmod(filepath, 0o600)
        except Exception:
            os.unlink(tmp_path)
            raise

    def _save_terminal_info(self, terminal_id: str, port: int, token: str, ws_url: str) -> None:
        """Save terminal session info to a file for persistence."""
        os.makedirs(self._terminal_info_dir, exist_ok=True)
        filepath = os.path.join(self._terminal_info_dir, f"{terminal_id}.json")
        try:
            info = {
                "terminal_id": terminal_id,
                "port": port,
                "token": token,
                "ws_url": ws_url,
                "created_at": datetime.utcnow().isoformat(),
            }
            self._atomic_write_json(filepath, info)
            logger.debug("Saved terminal info for %s to %s", terminal_id[:8], filepath)
        except Exception as e:
            logger.warning("Failed to save terminal info: %s", e)

    def _remove_terminal_info(self, terminal_id: str) -> None:
        """Remove terminal session info file."""
        filepath = os.path.join(self._terminal_info_dir, f"{terminal_id}.json")
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception as e:
            logger.warning("Failed to remove terminal info file: %s", e)

    # ----------------------------------------------------------------
    # Connection lifecycle
    # ----------------------------------------------------------------

    def start(self) -> None:
        """
        Main entry point. Runs the HTTP polling loop with reconnection logic.

        Blocks until shutdown is requested via signal or unhandled error.
        """
        self._running = True

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        logger.info(
            "Open ACE Remote Agent starting (machine_id=%s, server=%s)",
            self.config.machine_id[:8],
            self.config.server_url,
        )
        logger.info("Capabilities: %s", json.dumps(self._capabilities, indent=2))

        # Restore sessions from previous run (crash recovery)
        restored = self._executor.restore_sessions()
        if restored:
            logger.info("Restored %d session(s) from crash recovery", len(restored))
            for sid in restored:
                self._send_session_status(sid, "running")

        # Start session sync service
        self._session_sync.start()

        while self._running:
            try:
                self._http_poll_loop()
            except Exception as e:
                logger.error("HTTP poll loop error: %s", e)

            if not self._running:
                break

            # Exponential backoff reconnection
            delay = min(self._reconnect_delay, self.config.reconnect_max_delay)
            logger.info("Reconnecting in %.1f seconds...", delay)
            time.sleep(delay)
            self._reconnect_delay = min(self._reconnect_delay * 2, self.config.reconnect_max_delay)

        self._shutdown()

    # ----------------------------------------------------------------
    # HTTP polling
    # ----------------------------------------------------------------

    def _http_poll_loop(self) -> None:
        """
        HTTP polling loop for agent-server communication.

        Periodically POSTs heartbeat and queued messages to the server's
        /api/remote/agent/message endpoint.
        """
        logger.info("Starting HTTP polling mode")
        self._reconnect_delay = self.config.reconnect_base_delay

        # Register via HTTP first
        resp = self._http_send(
            {
                "type": "register",
                "machine_id": self.config.machine_id,
                "capabilities": self._capabilities,
            }
        )
        if resp and isinstance(resp, dict):
            pending = resp.get("pending_commands", [])
            if pending:
                logger.info("Processing %d pending commands from server", len(pending))
                for cmd in pending:
                    try:
                        self._handle_command(cmd)
                    except Exception as e:
                        logger.error("Error handling command: %s", e)

        heartbeat_interval = self.config.heartbeat_interval
        last_heartbeat = 0.0
        command_poll_interval = 1  # Poll for commands every 1 second
        last_command_poll = 0.0

        while self._running:
            now = time.time()

            # Send full heartbeat (status update) at configured interval
            if now - last_heartbeat >= heartbeat_interval:
                self._send_heartbeat_via_http()
                last_heartbeat = now
                last_command_poll = now
            # Fetch pending commands at shorter interval for low-latency response
            elif now - last_command_poll >= command_poll_interval:
                self._poll_commands_via_http()
                last_command_poll = now

            time.sleep(0.5)

    def _poll_commands_via_http(self) -> None:
        """Fetch pending commands from server without triggering a DB write."""
        resp = self._http_send(
            {
                "type": "poll",
                "machine_id": self.config.machine_id,
            }
        )

        if resp and isinstance(resp, dict):
            pending = resp.get("pending_commands", [])
            for cmd in pending:
                try:
                    self._handle_command(cmd)
                except Exception as e:
                    logger.error("Error handling command: %s", e)

    def _http_send(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """
        POST a message to the server's HTTP fallback endpoint.

        Returns the parsed JSON response or None on failure.
        """
        url = f"{self.config.server_url}/api/remote/agent/message"
        headers = {"Content-Type": "application/json"}

        if self.config.agent_token:
            headers["Authorization"] = f"Bearer {self.config.agent_token}"

        try:
            resp = requests.post(
                url,
                json=message,
                headers=headers,
                timeout=30,
                verify=not self.config.skip_ssl_verify,
            )
            if resp.status_code == 200:
                return resp.json()
            else:
                logger.warning(
                    "HTTP %d from %s: %s",
                    resp.status_code,
                    url,
                    resp.text[:200],
                )
                return None
        except requests.RequestException as e:
            logger.error("HTTP request failed: %s", e)
            return None

    def _send_heartbeat_via_http(self) -> None:
        """Send a heartbeat via HTTP and process any pending commands."""
        active = self._executor.active_sessions
        resp = self._http_send(
            {
                "type": "heartbeat",
                "machine_id": self.config.machine_id,
                "status": "busy" if active else "idle",
                "active_sessions": len(active),
                "active_terminals": len(self._terminal_processes),
                "capabilities": self._capabilities,
            }
        )

        # Process pending commands from the server response
        if resp and isinstance(resp, dict):
            pending = resp.get("pending_commands", [])
            for cmd in pending:
                try:
                    self._handle_command(cmd)
                except Exception as e:
                    logger.error("Error handling command: %s", e)

    # ----------------------------------------------------------------
    # Outbound message helpers
    # ----------------------------------------------------------------

    def _on_session_output(
        self, session_id: str, data: str, stream: str, is_complete: bool
    ) -> None:
        """Callback invoked by the executor when a session produces output."""
        self._send_session_output(session_id, data, stream, is_complete)

        # NOTE: We intentionally do NOT send "exited" status when the CLI
        # process exits after a response.  The session stays "active" so
        # the SSE stream remains open and the user can send follow-up
        # messages.  The executor will restart the CLI process when the
        # next message arrives.

    def _on_permission_request(self, session_id: str, control_request: dict) -> None:
        """Callback invoked by the executor when the CLI outputs a control_request."""
        request_payload = control_request.get("request", {})
        logger.info(
            "Permission request from session %s: %s (tool=%s)",
            session_id[:8],
            request_payload.get("subtype"),
            request_payload.get("tool_name"),
        )
        self._http_send(
            {
                "type": "permission_request",
                "session_id": session_id,
                "machine_id": self.config.machine_id,
                "control_request": control_request,
            }
        )

    def _send_session_output(
        self, session_id: str, data: str, stream: str, is_complete: bool
    ) -> None:
        """Send a session_output message to the server."""
        self._http_send(
            {
                "type": "session_output",
                "session_id": session_id,
                "data": data,
                "stream": stream,
                "is_complete": is_complete,
                "machine_id": self.config.machine_id,
            }
        )

    def _send_session_status(self, session_id: str, status: str, pid: int | None = None) -> None:
        """Send a session_status message to the server."""
        logger.info(
            "Sending session_status: session=%s status=%s pid=%s", session_id[:8], status, pid
        )
        result = self._http_send(
            {
                "type": "session_status",
                "session_id": session_id,
                "status": status,
                "pid": pid,
                "machine_id": self.config.machine_id,
            }
        )
        if result:
            logger.info("session_status sent successfully for session %s", session_id[:8])
        else:
            logger.warning("Failed to send session_status for session %s", session_id[:8])

    def _send_usage_report(
        self, session_id: str, tokens: dict[str, int], requests: int = 1
    ) -> None:
        """Send a usage_report message to the server."""
        self._http_send(
            {
                "type": "usage_report",
                "session_id": session_id,
                "tokens": tokens,
                "requests": requests,
                "machine_id": self.config.machine_id,
            }
        )

    def _handle_command(self, data: dict[str, Any]) -> None:
        """Dispatch a command from the server."""
        command = data.get("command")
        session_id = data.get("session_id")

        if command == "start_session":
            self._cmd_start_session(data)
        elif command == "send_message":
            self._cmd_send_message(data)
        elif command == "stop_session":
            self._cmd_stop_session(data)
        elif command == "permission_response":
            self._cmd_permission_response(data)
        elif command == "update_permission_mode":
            self._cmd_update_permission_mode(data)
        elif command == "update_model":
            self._cmd_update_model(data)
        elif command == "pause_session":
            result = self._executor.pause_session(session_id)
            if result["success"]:
                self._send_session_status(session_id, "paused")
            else:
                self._send_session_output(
                    session_id, f"Pause failed: {result.get('error')}", "stderr", True
                )
        elif command == "resume_session":
            result = self._executor.resume_session(session_id)
            if result["success"]:
                self._send_session_status(session_id, "running")
            else:
                self._send_session_output(
                    session_id, f"Resume failed: {result.get('error')}", "stderr", True
                )
        elif command == "abort_request":
            result = self._executor.interrupt_session(session_id)
            if not result["success"]:
                logger.warning(
                    "Abort failed for session %s: %s",
                    session_id[:8] if session_id else "N/A",
                    result.get("error"),
                )
        elif command == "start_terminal":
            self._cmd_start_terminal(data)
        elif command == "stop_terminal":
            self._cmd_stop_terminal(data)
        elif command == "attach_terminal":
            self._cmd_attach_terminal(data)
        elif command == "browse_directory":
            self._cmd_browse_directory(data)
        elif command == "create_directory":
            self._cmd_create_directory(data)
        elif command == "git_status":
            self._cmd_git_status(data)
        elif command == "git_diff":
            self._cmd_git_diff(data)
        elif command == "git_file":
            self._cmd_git_file(data)
        elif command == "start_vscode":
            self._cmd_start_vscode(data)
        elif command == "stop_vscode":
            self._cmd_stop_vscode(data)
        elif command == "attach_vscode":
            self._cmd_attach_vscode(data)
        else:
            logger.warning("Unknown command: %s", command)

    def _cmd_start_session(self, data: dict[str, Any]) -> None:
        """Handle a start_session command."""
        session_id = data.get("session_id", "")
        project_path = data.get("project_path", os.path.expanduser("~"))
        cli_tool = data.get("cli_tool", "qwen-code-cli")
        proxy_token = data.get("proxy_token", "")
        model = data.get("model")
        permission_mode = data.get("permission_mode")
        cli_settings = data.get("cli_settings", {})

        logger.info(
            "Starting session %s: cli=%s path=%s model=%s mode=%s",
            session_id[:8],
            cli_tool,
            project_path,
            model,
            permission_mode,
        )

        # Apply CLI settings before starting session
        if cli_settings:
            self._apply_cli_settings(cli_settings)

        result = self._executor.start_session(
            session_id=session_id,
            project_path=project_path,
            cli_tool=cli_tool,
            proxy_token=proxy_token,
            model=model,
            permission_mode=permission_mode,
        )

        if result["success"]:
            self._send_session_status(session_id, "running", result.get("pid"))
        else:
            self._send_session_status(session_id, "error")
            self._send_session_output(
                session_id,
                f"Failed to start session: {result.get('error', 'unknown error')}",
                "stderr",
                is_complete=True,
            )

    def _cmd_send_message(self, data: dict[str, Any]) -> None:
        """Handle a send_message command."""
        session_id = data.get("session_id", "")
        content = data.get("content", "")

        logger.info("Sending message to session %s: %s", session_id[:8], content[:80])

        result = self._executor.send_message(session_id, content)
        if not result["success"]:
            error_msg = result.get("error", "unknown error")
            logger.warning(
                "Failed to send message to session %s: %s",
                session_id[:8],
                error_msg,
            )
            self._send_session_status(session_id, "error")
            self._send_session_output(
                session_id,
                f"Failed to send message: {error_msg}",
                "stderr",
                is_complete=True,
            )

    def _cmd_stop_session(self, data: dict[str, Any]) -> None:
        """Handle a stop_session command."""
        session_id = data.get("session_id", "")

        logger.info("Stopping session %s", session_id[:8])
        self._executor.stop_session(session_id)
        self._send_session_status(session_id, "stopped")

    def _cmd_permission_response(self, data: dict[str, Any]) -> None:
        """Handle a permission_response command from the frontend.

        Sends a ``control_response`` to the CLI subprocess stdin so the
        CLI's ControlDispatcher can resolve the pending permission request
        and continue (or abort) the tool call.
        """
        session_id = data.get("session_id", "")
        behavior = data.get("behavior", "deny")
        request_id = data.get("request_id", "")
        tool_name = data.get("tool_name", "")
        message = data.get("message")

        logger.info(
            "Permission response for session %s: %s request_id=%s tool=%s",
            session_id[:8],
            behavior,
            request_id[:8] if request_id else "N/A",
            tool_name,
        )

        result = self._executor.send_permission_response(session_id, request_id, behavior, message)

        if not result["success"]:
            logger.warning(
                "Failed to handle permission response: %s",
                result.get("error"),
            )

    def _cmd_update_permission_mode(self, data: dict[str, Any]) -> None:
        """Handle update_permission_mode command from the frontend."""
        session_id = data.get("session_id", "")
        permission_mode = data.get("permission_mode", "default")

        logger.info(
            "Updating permission mode for session %s: %s",
            session_id[:8],
            permission_mode,
        )

        result = self._executor.update_permission_mode(session_id, permission_mode)
        if not result["success"]:
            logger.warning(
                "Failed to update permission mode: %s",
                result.get("error"),
            )

    def _cmd_update_model(self, data: dict[str, Any]) -> None:
        """Handle update_model command from the frontend."""
        session_id = data.get("session_id", "")
        model = data.get("model", "")

        logger.info(
            "Updating model for session %s: %s",
            session_id[:8],
            model,
        )

        result = self._executor.update_model(session_id, model)
        if not result["success"]:
            logger.warning(
                "Failed to update model: %s",
                result.get("error"),
            )

    # ----------------------------------------------------------------
    # Terminal management
    # ----------------------------------------------------------------

    def _cmd_start_terminal(self, data: dict[str, Any]) -> None:
        """Handle a start_terminal command."""
        terminal_id = data.get("terminal_id", "")
        proxy_url = data.get("proxy_url", "")
        # Support both old single-token format and new multi-token format
        anthropic_token = data.get("anthropic_token", data.get("proxy_token", ""))
        openai_token = data.get("openai_token", "")
        work_dir = data.get("work_dir", os.path.expanduser("~"))
        cli_settings = data.get("cli_settings", {})

        logger.info("Starting terminal %s: work_dir=%s", terminal_id[:8], work_dir)

        # Apply CLI settings before starting terminal
        # (non-sensitive config only; API credentials are set via env vars)
        if cli_settings:
            self._apply_cli_settings(cli_settings)

        # Stop existing terminal with same ID if any
        if terminal_id in self._terminal_processes:
            self._stop_terminal_process(terminal_id)

        # Generate auth token for this terminal
        term_token = secrets.token_hex(32)
        self._terminal_tokens[terminal_id] = term_token

        # Find terminal_server.py script path
        script_dir = os.path.dirname(os.path.abspath(__file__))
        server_script = os.path.join(script_dir, "terminal_server.py")
        terminal_info_dir = os.path.join(script_dir, ".terminal_sessions")
        os.makedirs(terminal_info_dir, exist_ok=True)

        # Build command - pass tokens via environment variables (not CLI args)
        cmd = [
            sys.executable,
            server_script,
            "--terminal-id",
            terminal_id,
            "--port",
            "0",  # Auto-select port
            "--proxy-url",
            proxy_url,
            "--work-dir",
            work_dir,
        ]
        env = os.environ.copy()
        env["OPEN_ACE_TERMINAL_TOKEN"] = term_token
        env["OPEN_ANTHROPIC_TOKEN"] = anthropic_token or ""
        if openai_token:
            env["OPEN_OPENAI_TOKEN"] = openai_token

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                start_new_session=True,
            )
            self._terminal_processes[terminal_id] = proc

            # Wait for READY:port output (with timeout)
            port = self._read_terminal_port(proc, terminal_id)
            # Capture remaining stderr before closing PIPE file descriptors.
            old_stdout, old_stderr = proc.stdout, proc.stderr
            stderr_output = old_stderr.read().decode(errors="replace")[:500]
            # Redirect to DEVNULL to prevent pipe buffer deadlock,
            # then close the old PIPE file descriptors.
            proc.stdout = open(os.devnull, "wb")
            proc.stderr = open(os.devnull, "wb")
            old_stdout.close()
            old_stderr.close()
            if port:
                self._terminal_ports[terminal_id] = port
                hostname = self._get_reachable_hostname()
                ws_url = f"ws://{hostname}:{port}"

                # Save terminal info to file for persistence across agent restarts
                self._save_terminal_info(terminal_id, port, term_token, ws_url)

                self._http_send(
                    {
                        "type": "terminal_status",
                        "terminal_id": terminal_id,
                        "machine_id": self.config.machine_id,
                        "status": "running",
                        "ws_url": ws_url,
                        "token": term_token,
                    }
                )
                logger.info("Terminal %s running on %s", terminal_id[:8], ws_url)
                self._session_sync.notify_terminal_active(terminal_id)
            else:
                logger.error("Terminal server failed to start: %s", stderr_output[:500])
                self._http_send(
                    {
                        "type": "terminal_status",
                        "terminal_id": terminal_id,
                        "machine_id": self.config.machine_id,
                        "status": "error",
                        "error": f"Terminal server failed to start: {stderr_output[:200]}",
                    }
                )

        except Exception as e:
            logger.error("Failed to start terminal: %s", e)
            self._http_send(
                {
                    "type": "terminal_status",
                    "terminal_id": terminal_id,
                    "machine_id": self.config.machine_id,
                    "status": "error",
                    "error": str(e),
                }
            )

    def _cmd_stop_terminal(self, data: dict[str, Any]) -> None:
        """Handle a stop_terminal command."""
        terminal_id = data.get("terminal_id", "")
        logger.info("Stopping terminal %s", terminal_id[:8])
        self._stop_terminal_process(terminal_id)
        self._http_send(
            {
                "type": "terminal_status",
                "terminal_id": terminal_id,
                "machine_id": self.config.machine_id,
                "status": "stopped",
            }
        )

    def _cmd_attach_terminal(self, data: dict[str, Any]) -> None:
        """Handle an attach_terminal command (reconnect to existing terminal).

        Called when user refreshes browser and wants to reconnect to the same
        terminal session without losing PTY state (e.g., Claude Code chat history).

        If terminal_server is not running, restart it with fresh API tokens.
        """
        terminal_id = data.get("terminal_id", "")
        anthropic_token = data.get("anthropic_token", "")
        openai_token = data.get("openai_token", "")
        proxy_url = data.get("proxy_url", "")
        logger.info(
            "Attaching to terminal %s (tokens provided: %s)", terminal_id[:8], bool(anthropic_token)
        )

        # Check if terminal server is still running (from process tracking)
        if terminal_id in self._terminal_processes:
            proc = self._terminal_processes[terminal_id]
            if proc.poll() is None:
                # Terminal server still running - return existing info
                port = self._terminal_ports.get(terminal_id)
                term_token = self._terminal_tokens.get(terminal_id)
                hostname = self._get_reachable_hostname()
                ws_url = self._terminal_ws_urls.get(terminal_id) or f"ws://{hostname}:{port}"

                self._http_send(
                    {
                        "type": "terminal_status",
                        "terminal_id": terminal_id,
                        "machine_id": self.config.machine_id,
                        "status": "running",
                        "ws_url": ws_url,
                        "token": term_token,
                    }
                )
                logger.info("Terminal %s attached (from process): %s", terminal_id[:8], ws_url)
                return

        # Check if we have persisted terminal info (agent restart case)
        if terminal_id in self._terminal_ports:
            port = self._terminal_ports.get(terminal_id)
            term_token = self._terminal_tokens.get(terminal_id)
            ws_url = self._terminal_ws_urls.get(terminal_id)

            # Verify port is still listening
            if port and self._check_port_listening(port):
                self._http_send(
                    {
                        "type": "terminal_status",
                        "terminal_id": terminal_id,
                        "machine_id": self.config.machine_id,
                        "status": "running",
                        "ws_url": ws_url,
                        "token": term_token,
                    }
                )
                logger.info("Terminal %s attached (from persistence): %s", terminal_id[:8], ws_url)
                return
            else:
                # Port not listening, clean up stale data
                logger.info("Terminal %s port %d not listening, cleaning up", terminal_id[:8], port)
                self._remove_terminal_info(terminal_id)
                self._terminal_ports.pop(terminal_id, None)
                self._terminal_tokens.pop(terminal_id, None)
                self._terminal_ws_urls.pop(terminal_id, None)

        # Terminal not found - restart with fresh tokens if provided
        if anthropic_token and proxy_url:
            logger.info("Terminal %s not found, restarting with fresh tokens", terminal_id[:8])

            # Use start_terminal logic to restart
            work_dir = ""  # Default work dir
            term_token = secrets.token_hex(32)

            # Build command - pass tokens via environment variables
            script_dir = os.path.dirname(os.path.abspath(__file__))
            server_script = os.path.join(script_dir, "terminal_server.py")
            cmd = [
                sys.executable,
                server_script,
                "--terminal-id",
                terminal_id,
                "--port",
                "0",
                "--proxy-url",
                proxy_url,
                "--work-dir",
                work_dir,
            ]
            env = os.environ.copy()
            env["OPEN_ACE_TERMINAL_TOKEN"] = term_token
            env["OPEN_ANTHROPIC_TOKEN"] = anthropic_token or ""
            if openai_token:
                env["OPEN_OPENAI_TOKEN"] = openai_token

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                    start_new_session=True,
                )
                self._terminal_processes[terminal_id] = proc

                # Wait for READY:port output
                port = self._read_terminal_port(proc, terminal_id)
                # Close old PIPE file descriptors and redirect to DEVNULL
                # to prevent pipe buffer deadlock.
                old_stdout, old_stderr = proc.stdout, proc.stderr
                proc.stdout = open(os.devnull, "wb")
                proc.stderr = open(os.devnull, "wb")
                old_stdout.close()
                old_stderr.close()
                if port:
                    self._terminal_ports[terminal_id] = port
                    hostname = self._get_reachable_hostname()
                    ws_url = f"ws://{hostname}:{port}"

                    self._terminal_tokens[terminal_id] = term_token
                    self._terminal_ws_urls[terminal_id] = ws_url

                    self._save_terminal_info(terminal_id, port, term_token, ws_url)

                    self._http_send(
                        {
                            "type": "terminal_status",
                            "terminal_id": terminal_id,
                            "machine_id": self.config.machine_id,
                            "status": "running",
                            "ws_url": ws_url,
                            "token": term_token,
                        }
                    )
                    logger.info("Terminal %s restarted: %s", terminal_id[:8], ws_url)
                    self._session_sync.notify_terminal_active(terminal_id)
                    return
            except Exception as e:
                logger.error("Failed to restart terminal %s: %s", terminal_id[:8], e)

        # Terminal not found and no tokens provided
        logger.info("Terminal %s not found and no tokens provided", terminal_id[:8])
        self._http_send(
            {
                "type": "terminal_status",
                "terminal_id": terminal_id,
                "machine_id": self.config.machine_id,
                "status": "not_found",
            }
        )

    def _cmd_browse_directory(self, data: dict[str, Any]) -> None:
        """Handle a browse_directory command.

        Returns a list of directories in the specified path for directory browsing UI.
        """
        import os

        request_id = data.get("request_id", "")
        requested_path = data.get("path", "")
        # Expand ~ in path before checking existence (e.g., ~/workspace -> /home/user/workspace)
        if requested_path:
            path = os.path.expanduser(requested_path)
        else:
            # Use home directory as fallback if no path provided
            path = os.path.expanduser("~")
        logger.info(
            "Browsing directory: %s (request_id=%s)", path, request_id[:8] if request_id else "none"
        )

        try:
            # If requested path doesn't exist, try to find a fallback
            if not os.path.exists(path):
                # Try parent directory first
                parent = os.path.dirname(path)
                if parent and os.path.exists(parent) and os.path.isdir(parent):
                    # Browse parent instead and note the fallback
                    actual_path = parent
                    fallback_note = (
                        f"Requested path '{path}' does not exist. Showing parent directory instead."
                    )
                    logger.info("Path %s not found, falling back to parent %s", path, parent)
                else:
                    # Fall back to home directory
                    home = os.path.expanduser("~")
                    if os.path.exists(home):
                        actual_path = home
                        fallback_note = f"Requested path '{path}' does not exist. Showing home directory instead."
                        logger.info("Path %s not found, falling back to home %s", path, home)
                    else:
                        # No fallback available
                        self._http_send(
                            {
                                "type": "browse_result",
                                "machine_id": self.config.machine_id,
                                "request_id": request_id,
                                "success": False,
                                "error": f"Path does not exist: {path}",
                            }
                        )
                        return
                path = actual_path
            else:
                fallback_note = None

            # Check if it's a directory
            if not os.path.isdir(path):
                self._http_send(
                    {
                        "type": "browse_result",
                        "machine_id": self.config.machine_id,
                        "request_id": request_id,
                        "success": False,
                        "error": f"Path is not a directory: {path}",
                    }
                )
                return

            # List directories in the path
            directories = []
            try:
                for entry in os.listdir(path):
                    full_path = os.path.join(path, entry)
                    if os.path.isdir(full_path):
                        # Check if directory is writable
                        try:
                            is_writable = os.access(full_path, os.W_OK)
                        except OSError:
                            is_writable = False

                        directories.append(
                            {
                                "name": entry,
                                "path": full_path,
                                "is_writable": is_writable,
                            }
                        )
            except PermissionError:
                self._http_send(
                    {
                        "type": "browse_result",
                        "machine_id": self.config.machine_id,
                        "request_id": request_id,
                        "success": False,
                        "error": f"Permission denied: {path}",
                    }
                )
                return

            # Sort directories alphabetically
            directories.sort(key=lambda d: d["name"].lower())

            # Determine parent path
            parent = os.path.dirname(path) if path != os.path.dirname(path) else None

            # Check if current path is writable
            is_writable = os.access(path, os.W_OK)

            self._http_send(
                {
                    "type": "browse_result",
                    "machine_id": self.config.machine_id,
                    "request_id": request_id,
                    "success": True,
                    "result": {
                        "path": path,
                        "name": os.path.basename(path) or path,
                        "directories": directories,
                        "parent": parent,
                        "homePath": os.path.expanduser("~"),
                        "canCreate": is_writable,
                        "is_writable": is_writable,
                        "fallback_note": fallback_note,  # Include note if path was changed
                    },
                }
            )
            logger.info("Browse result for %s: %d directories", path, len(directories))

        except Exception as e:
            logger.error("Failed to browse directory %s: %s", path, e)
            self._http_send(
                {
                    "type": "browse_result",
                    "machine_id": self.config.machine_id,
                    "request_id": request_id,
                    "success": False,
                    "error": str(e),
                }
            )

    def _send_create_result(
        self,
        request_id: str,
        success: bool,
        error: str | None = None,
        result: dict | None = None,
    ) -> None:
        """Send a create_directory result back to the server."""
        response: dict[str, Any] = {
            "type": "browse_result",
            "machine_id": self.config.machine_id,
            "request_id": request_id,
            "success": success,
        }
        if error is not None:
            response["error"] = error
        if result is not None:
            response["result"] = result
        self._http_send(response)

    def _cmd_create_directory(self, data: dict[str, Any]) -> None:
        """Handle a create_directory command."""
        request_id = data.get("request_id", "")
        dir_path = data.get("path", "")

        if not dir_path:
            self._send_create_result(request_id, False, error="No path specified")
            return

        dir_path = os.path.expanduser(dir_path)
        name = os.path.basename(dir_path)

        # Validate name before realpath (realpath fails on null bytes)
        invalid_chars = set("/\0")
        if os.name == "nt":
            invalid_chars.update('\\:*?"<>|')
        if any(c in name for c in invalid_chars) or not name:
            self._send_create_result(request_id, False, error=f"Invalid directory name: {name}")
            return

        dir_path = os.path.realpath(dir_path)
        logger.info("Creating directory: %s", dir_path)

        try:
            parent = os.path.dirname(dir_path)

            if not os.path.exists(parent):
                self._send_create_result(
                    request_id, False, error=f"Parent directory does not exist: {parent}"
                )
                return

            if not os.path.isdir(parent):
                self._send_create_result(
                    request_id, False, error=f"Parent path is not a directory: {parent}"
                )
                return

            if not os.access(parent, os.W_OK):
                self._send_create_result(
                    request_id, False, error=f"Permission denied: cannot write to {parent}"
                )
                return

            os.mkdir(dir_path)

            self._send_create_result(
                request_id,
                True,
                result={"path": dir_path, "message": "Directory created successfully"},
            )
            logger.info("Created directory: %s", dir_path)

        except FileExistsError:
            self._send_create_result(
                request_id, False, error=f"Directory already exists: {dir_path}"
            )
        except PermissionError:
            self._send_create_result(request_id, False, error=f"Permission denied: {dir_path}")
        except OSError as e:
            self._send_create_result(request_id, False, error=f"Failed to create directory: {e}")
        except Exception as e:
            logger.error("Failed to create directory %s: %s", dir_path, e)
            self._send_create_result(request_id, False, error=str(e))

    # ── Git command handlers ──────────────────────────────────────────

    def _run_git(self, args: list[str], cwd: str) -> tuple[str, str, bool]:
        """Run a git subprocess and return (stdout, stderr, success)."""
        try:
            result = subprocess.run(
                ["git", "-C", cwd] + args,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.stdout, result.stderr, result.returncode == 0
        except FileNotFoundError:
            return "", "git is not installed", False
        except subprocess.TimeoutExpired:
            return "", "git command timed out", False
        except Exception as e:
            return "", str(e), False

    def _send_git_result(
        self,
        request_id: str,
        success: bool,
        error: str | None = None,
        result: dict | None = None,
    ) -> None:
        """Send a git_result message back to the server."""
        response: dict[str, Any] = {
            "type": "git_result",
            "machine_id": self.config.machine_id,
            "request_id": request_id,
            "success": success,
        }
        if error is not None:
            response["error"] = error
        if result is not None:
            response["result"] = result
        self._http_send(response)

    def _cmd_git_status(self, data: dict[str, Any]) -> None:
        """Handle a git_status command.

        Runs git status and git diff to produce a list of changed files,
        matching the format of qwen-code-webui/backend/handlers/git.ts.
        """
        request_id = data.get("request_id", "")
        project_path = data.get("project_path", "")

        if not project_path:
            self._send_git_result(request_id, False, error="No project_path specified")
            return

        cwd = os.path.realpath(os.path.expanduser(project_path))
        if not os.path.isdir(cwd):
            self._send_git_result(request_id, False, error=f"Directory does not exist: {cwd}")
            return

        if not os.path.isdir(os.path.join(cwd, ".git")):
            self._send_git_result(request_id, True, result={"files": []})
            return

        try:
            # Check if HEAD exists
            _, _, has_head = self._run_git(["rev-parse", "HEAD"], cwd)

            # Get diff stats
            diff_args = (
                ["diff", "--numstat", "HEAD"] if has_head else ["diff", "--cached", "--numstat"]
            )
            diff_stdout, _, diff_ok = self._run_git(diff_args, cwd)

            # Get porcelain status
            status_stdout, _, status_ok = self._run_git(["status", "--porcelain"], cwd)

            files: dict[str, dict] = {}

            # Parse diff stats
            if diff_ok and diff_stdout.strip():
                for line in diff_stdout.strip().split("\n"):
                    parts = line.split("\t")
                    if len(parts) >= 3:
                        additions = 0 if parts[0] == "-" else (int(parts[0]) or 0)
                        deletions = 0 if parts[1] == "-" else (int(parts[1]) or 0)
                        path = parts[2]
                        files[path] = {
                            "path": path,
                            "status": "added" if additions > 0 and deletions == 0 else "modified",
                            "additions": additions,
                            "deletions": deletions,
                        }

            # Parse porcelain status
            if status_ok and status_stdout.strip():
                for line in status_stdout.strip().split("\n"):
                    if len(line) < 4:
                        continue
                    status_code = line[:2]
                    if "R" in status_code and " -> " in line:
                        file_path = line[3:].split(" -> ")[1]
                    else:
                        file_path = line[3:]

                    if file_path in files:
                        if "D" in status_code:
                            files[file_path]["status"] = "deleted"
                        elif "A" in status_code or status_code == "??":
                            files[file_path]["status"] = "added"
                    elif status_code == "??":
                        files[file_path] = {
                            "path": file_path,
                            "status": "added",
                            "additions": 0,
                            "deletions": 0,
                        }
                    elif "D" in status_code:
                        files[file_path] = {
                            "path": file_path,
                            "status": "deleted",
                            "additions": 0,
                            "deletions": 0,
                        }

            # For added files with 0 additions, count lines
            for path, change in files.items():
                if change["status"] == "added" and change["additions"] == 0:
                    try:
                        full_path = os.path.join(cwd, path)
                        if os.path.isfile(full_path):
                            with open(full_path, errors="replace") as f:
                                change["additions"] = sum(1 for _ in f)
                    except OSError:
                        pass

            sorted_files = sorted(files.values(), key=lambda f: f["path"])
            self._send_git_result(request_id, True, result={"files": sorted_files})
            logger.info("git_status for %s: %d files", cwd, len(sorted_files))

        except Exception as e:
            logger.error("git_status failed for %s: %s", cwd, e)
            self._send_git_result(request_id, False, error=str(e))

    def _cmd_git_diff(self, data: dict[str, Any]) -> None:
        """Handle a git_diff command."""
        request_id = data.get("request_id", "")
        project_path = data.get("project_path", "")
        file = data.get("file", "")

        if not project_path or not file:
            self._send_git_result(request_id, False, error="project_path and file are required")
            return

        cwd = os.path.realpath(os.path.expanduser(project_path))

        try:
            # Validate path traversal
            full_path = os.path.realpath(os.path.join(cwd, file))
            if not full_path.startswith(os.path.realpath(cwd)):
                self._send_git_result(request_id, False, error="Path traversal detected")
                return

            _, _, has_head = self._run_git(["rev-parse", "HEAD"], cwd)

            # Get diff
            diff_args = (
                ["diff", "HEAD", "--", file] if has_head else ["diff", "--cached", "--", file]
            )
            diff_stdout, _, _ = self._run_git(diff_args, cwd)

            # Get original content
            original_content = ""
            if has_head:
                show_stdout, _, show_ok = self._run_git(["show", f"HEAD:{file}"], cwd)
                if show_ok:
                    original_content = show_stdout

            # Get modified content
            modified_content = ""
            try:
                if os.path.isfile(full_path):
                    with open(full_path, errors="replace") as f:
                        modified_content = f.read()
            except OSError:
                pass

            self._send_git_result(
                request_id,
                True,
                result={
                    "file": file,
                    "diff": diff_stdout if has_head else diff_stdout,
                    "originalContent": original_content,
                    "modifiedContent": modified_content,
                },
            )

        except Exception as e:
            logger.error("git_diff failed for %s/%s: %s", cwd, file, e)
            self._send_git_result(request_id, False, error=str(e))

    def _cmd_git_file(self, data: dict[str, Any]) -> None:
        """Handle a git_file command (read file content)."""
        request_id = data.get("request_id", "")
        project_path = data.get("project_path", "")
        file = data.get("file", "")

        if not project_path or not file:
            self._send_git_result(request_id, False, error="project_path and file are required")
            return

        cwd = os.path.realpath(os.path.expanduser(project_path))

        try:
            full_path = os.path.realpath(os.path.join(cwd, file))
            if not full_path.startswith(os.path.realpath(cwd)):
                self._send_git_result(request_id, False, error="Path traversal detected")
                return

            if not os.path.isfile(full_path):
                self._send_git_result(request_id, False, error=f"File not found: {file}")
                return

            with open(full_path, errors="replace") as f:
                content = f.read()

            self._send_git_result(request_id, True, result={"file": file, "content": content})

        except PermissionError:
            self._send_git_result(request_id, False, error=f"Permission denied: {file}")
        except Exception as e:
            logger.error("git_file failed for %s/%s: %s", cwd, file, e)
            self._send_git_result(request_id, False, error=str(e))

    # ── VSCode (code-server) command handlers ─────────────────────────

    def _find_code_server(self) -> str | None:
        """Find code-server binary on the system."""
        # Check PATH first
        cs = shutil.which("code-server")
        if cs:
            return cs
        # Check common install locations
        home = os.path.expanduser("~")
        common_paths = [
            os.path.join(home, ".local", "bin", "code-server"),
            "/usr/local/bin/code-server",
            "/usr/bin/code-server",
        ]
        for path in common_paths:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
        return None

    def _send_vscode_status(
        self,
        vscode_id: str,
        status: str,
        http_url: str | None = None,
        token: str | None = None,
        error: str | None = None,
    ) -> None:
        """Send a vscode_status message back to the server."""
        msg: dict[str, Any] = {
            "type": "vscode_status",
            "vscode_id": vscode_id,
            "machine_id": self.config.machine_id,
            "status": status,
        }
        if http_url is not None:
            msg["http_url"] = http_url
        if token is not None:
            msg["token"] = token
        if error is not None:
            msg["error"] = error
        self._http_send(msg)

    def _cmd_start_vscode(self, data: dict[str, Any]) -> None:
        """Handle a start_vscode command.

        Starts code-server on the remote machine for the given project path.
        """
        vscode_id = data.get("vscode_id", "")
        project_path = data.get("project_path", "")

        if not project_path:
            self._send_vscode_status(vscode_id, "error", error="No project_path specified")
            return

        cwd = os.path.realpath(os.path.expanduser(project_path))
        if not os.path.isdir(cwd):
            self._send_vscode_status(vscode_id, "error", error=f"Directory does not exist: {cwd}")
            return

        # Find code-server
        cs_path = self._find_code_server()
        if not cs_path:
            self._send_vscode_status(
                vscode_id,
                "error",
                error="code-server is not installed. Please install it: https://coder.com/docs/code-server/latest/install",
            )
            return

        # Stop existing code-server with same ID if any
        existing_proc = self._vscode_processes.pop(vscode_id, None)
        if existing_proc:
            try:
                existing_proc.terminate()
                existing_proc.wait(timeout=5)
            except Exception:
                try:
                    existing_proc.kill()
                except Exception:
                    pass

        logger.info("Starting VSCode %s for %s", vscode_id[:8], cwd)

        # Generate auth token
        vscode_token = secrets.token_hex(32)
        self._vscode_tokens[vscode_id] = vscode_token

        try:
            cmd = [
                cs_path,
                "--port",
                "0",  # Auto-select port
                "--auth",
                "none",  # Auth handled by open-ace proxy
                "--disable-telemetry",
                "--disable-workspace-trust",
                "--disable-getting-started-override",
                cwd,
            ]

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            self._vscode_processes[vscode_id] = proc

            # Wait for code-server to print its URL (with timeout)
            port = self._read_vscode_port(proc, vscode_id)

            # Capture remaining stderr before closing PIPE file descriptors.
            old_stdout, old_stderr = proc.stdout, proc.stderr
            stderr_output = old_stderr.read().decode(errors="replace")[:500]

            # Redirect to DEVNULL to prevent pipe buffer deadlock,
            # then close the old PIPE file descriptors.
            proc.stdout = open(os.devnull, "wb")
            proc.stderr = open(os.devnull, "wb")
            old_stdout.close()
            old_stderr.close()

            if port:
                self._vscode_ports[vscode_id] = port
                hostname = self._get_reachable_hostname()
                http_url = f"http://{hostname}:{port}"

                self._send_vscode_status(
                    vscode_id, "running", http_url=http_url, token=vscode_token
                )
                logger.info("VSCode %s running on %s", vscode_id[:8], http_url)
            else:
                logger.error("code-server failed to start for %s: %s", vscode_id[:8], stderr_output)
                self._send_vscode_status(
                    vscode_id,
                    "error",
                    error=f"code-server failed to start: {stderr_output[:200]}",
                )

        except Exception as e:
            logger.error("Failed to start code-server: %s", e)
            self._send_vscode_status(vscode_id, "error", error=str(e))

    def _read_vscode_port(self, proc: subprocess.Popen, vscode_id: str) -> int | None:
        """Read stdout/stderr from code-server process until a URL with port is found."""
        import re

        port_pattern = re.compile(r"https?://[\d.]+:(\d+)")
        deadline = time.time() + 30  # 30s timeout
        try:
            while time.time() < deadline:
                line = proc.stdout.readline()
                if not line:
                    # Check stderr too
                    line = proc.stderr.readline()
                if not line:
                    if proc.poll() is not None:
                        return None
                    time.sleep(0.5)
                    continue
                text = line.decode(errors="replace").strip()
                match = port_pattern.search(text)
                if match:
                    return int(match.group(1))
        except Exception as e:
            logger.warning("Error reading code-server port: %s", e)
        return None

    def _cmd_stop_vscode(self, data: dict[str, Any]) -> None:
        """Handle a stop_vscode command."""
        vscode_id = data.get("vscode_id", "")
        logger.info("Stopping VSCode %s", vscode_id[:8])

        proc = self._vscode_processes.pop(vscode_id, None)
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

        self._vscode_ports.pop(vscode_id, None)
        self._vscode_tokens.pop(vscode_id, None)
        self._send_vscode_status(vscode_id, "stopped")

    def _cmd_attach_vscode(self, data: dict[str, Any]) -> None:
        """Handle an attach_vscode command.

        Checks if a code-server process is still running and returns its info.
        """
        vscode_id = data.get("vscode_id", "")
        proc = self._vscode_processes.get(vscode_id)
        if proc and proc.poll() is None:
            # Still running
            port = self._vscode_ports.get(vscode_id)
            token = self._vscode_tokens.get(vscode_id)
            if port:
                hostname = self._get_reachable_hostname()
                http_url = f"http://{hostname}:{port}"
                self._send_vscode_status(vscode_id, "running", http_url=http_url, token=token)
            else:
                self._send_vscode_status(vscode_id, "error", error="Port info lost")
        else:
            # Not running
            self._vscode_processes.pop(vscode_id, None)
            self._vscode_ports.pop(vscode_id, None)
            self._vscode_tokens.pop(vscode_id, None)
            self._send_vscode_status(vscode_id, "not_found")

    # ── Terminal helpers ───────────────────────────────────────────────

    def _stop_terminal_process(self, terminal_id: str) -> None:
        """Stop a terminal server process."""
        proc = self._terminal_processes.pop(terminal_id, None)
        if proc:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except Exception as e:
                logger.warning("Error stopping terminal process: %s", e)
        self._terminal_tokens.pop(terminal_id, None)
        self._terminal_ports.pop(terminal_id, None)
        self._terminal_ws_urls.pop(terminal_id, None)
        self._remove_terminal_info(terminal_id)

    def _read_terminal_port(self, proc: subprocess.Popen, terminal_id: str) -> int | None:
        """Read the port number from terminal server's READY:port stdout."""
        import select as _select

        try:
            # Use select to avoid blocking indefinitely
            ready, _, _ = _select.select([proc.stdout], [], [], 10.0)
            if not ready:
                logger.warning("Timeout waiting for terminal port from %s", terminal_id[:8])
                return None
            line = proc.stdout.readline().decode().strip()
            if line.startswith("READY:"):
                return int(line.split(":")[1])
        except Exception as e:
            logger.error("Failed to read terminal port: %s", e)
        return None

    def _get_reachable_hostname(self) -> str:
        """Get a hostname/IP that the browser can use to reach this machine."""
        # Prefer IP address (hostname may not be resolvable from browser)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                ip = s.getsockname()[0]
                return ip
        except Exception:
            pass
        # Fall back to configured hostname
        hostname = self.config.hostname
        if hostname and hostname != "localhost":
            return hostname
        return "127.0.0.1"

    def _apply_cli_settings(self, cli_settings: dict[str, Any]) -> None:
        """
        Write config files for configured CLI tools.

        API keys are not persisted. The shared writer injects the Open ACE
        proxy routing for Qwen and Codex while preserving non-sensitive user
        preferences.

        Args:
            cli_settings: Dict with tool_name -> settings mapping
                         e.g., {"claude-code": {...}, "qwen-code": {...}}
        """
        if not cli_settings:
            return

        proxy_base_url = f"{self.config.server_url.rstrip('/')}/api/remote/llm-proxy/v1"
        apply_cli_settings(cli_settings, proxy_base_url=proxy_base_url)

    # ----------------------------------------------------------------
    # Shutdown
    # ----------------------------------------------------------------

    def _signal_handler(self, signum: int, frame) -> None:
        """Handle SIGINT / SIGTERM for graceful shutdown."""
        logger.info("Received signal %d, shutting down...", signum)
        self._running = False

    def _shutdown(self) -> None:
        """Clean up all resources.

        Note: Terminal server processes are NOT killed on shutdown.
        They are started with start_new_session=True and run independently,
        allowing users to reconnect after agent restarts (browser refresh case).
        """
        logger.info("Shutting down agent...")
        self._session_sync.stop()
        self._executor.stop_all()
        # Clear terminal process tracking (but don't kill them - they persist)
        self._terminal_processes.clear()
        logger.info("Agent shutdown complete (terminal servers left running)")


def fix_stdin_for_service() -> None:
    """Fix stdin state when running under a service manager.

    When running as a service (launchd on macOS, systemd on Linux,
    Task Scheduler on Windows), stdin may be closed or have an invalid
    file descriptor. This can affect subprocess stdin pipe creation and
    cause "Broken pipe" errors when writing to subprocess stdin.

    This function detects and fixes stdin issues by reopening stdin
    to the platform-appropriate null device if necessary.
    """
    null_dev = os.devnull  # '/dev/null' on Unix, 'nul' on Windows

    if sys.stdin is None or sys.stdin.closed:
        sys.stdin = open(null_dev)
        logger.info("Fixed stdin for service environment: stdin was None/closed")
    else:
        try:
            fd = sys.stdin.fileno()
            if fd < 0:
                sys.stdin = open(null_dev)
                logger.info("Fixed stdin for service environment: invalid fd (%d)", fd)
        except (ValueError, OSError):
            sys.stdin = open(null_dev)
            logger.info("Fixed stdin for service environment: no valid fileno")


class _FlushFileHandler(logging.FileHandler):
    """FileHandler that flushes after each record to prevent data loss on crash."""

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()


def setup_logging(level: str = "INFO") -> None:
    """Configure logging for the agent daemon."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent.log")

    # Determine if running under a *nix service manager that redirects
    # stdout/stderr to its own log files (launchd, systemd).
    # On these platforms we skip the FileHandler to avoid duplicate logs.
    # On Windows, we ALWAYS write a log file because stdout is typically
    # invisible when running under Task Scheduler or Start-Process -Hidden.
    is_unix_service = os.name != "nt" and (
        "INVOCATION_ID" in os.environ  # systemd sets this
        or os.path.exists("/proc/self/cgroup")  # Linux container/systemd
        or (
            os.environ.get("TERM") is None
            and os.environ.get("_LAUNCHD_SOCKET") is not None  # macOS launchd
        )
    )

    # On Windows, force stdout to UTF-8 to prevent garbled non-ASCII text.
    # Windows console defaults to code pages like CP936 (GBK) for Chinese,
    # which causes UTF-8 strings to be mis-decoded.
    if os.name == "nt" and sys.stdout and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass  # Ignore if reconfigure fails

    if is_unix_service:
        # Service manager handles log redirection - use stdout only
        logging.basicConfig(
            level=numeric_level,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            handlers=[
                logging.StreamHandler(sys.stdout),
            ],
        )
    else:
        # Running manually or on Windows - always write to both stdout and file.
        # Uses _FlushFileHandler to ensure every log line is persisted immediately,
        # so crash diagnostics are never lost to an unflushed buffer.
        logging.basicConfig(
            level=numeric_level,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            handlers=[
                logging.StreamHandler(sys.stdout),
                _FlushFileHandler(log_file, encoding="utf-8"),
            ],
        )


def main() -> None:
    """Entry point for the remote agent daemon."""
    config = AgentConfig()

    setup_logging(config.log_level)

    # Fix stdin for service environments (launchd/systemd/Task Scheduler)
    # This prevents "Broken pipe" errors when writing to subprocess stdin
    try:
        fix_stdin_for_service()
    except Exception as e:
        logger.warning("fix_stdin_for_service failed (non-fatal): %s", e)

    logger.info("=" * 60)
    logger.info("Open ACE Remote Agent")
    logger.info("Server:  %s", config.server_url)
    logger.info("Machine: %s", config.machine_id)
    logger.info("=" * 60)

    try:
        agent = RemoteAgent(config)
        agent.start()
    except Exception:
        logger.exception("Agent crashed with unhandled exception")
        raise


if __name__ == "__main__":
    main()
