#!/usr/bin/env python3
"""
Open ACE Remote Agent - Main Daemon

Connects to the Open ACE server via HTTP polling, handles commands
(start_session, send_message, stop_session), manages CLI subprocesses
through the executor module, and sends heartbeats.
"""

import json
import logging
import os
import signal
import sys
import time
from typing import Any, Dict, Optional

import requests

from config import AgentConfig
from executor import ProcessExecutor
from system_info import get_capabilities

logger = logging.getLogger("openace-agent")


class RemoteAgent:
    """
    Main remote agent daemon.

    Connects to the Open ACE server via HTTP polling, receives commands,
    manages CLI subprocesses, and reports output and status back.
    """

    def __init__(self, config: Optional[AgentConfig] = None):
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
            self._reconnect_delay = min(
                self._reconnect_delay * 2, self.config.reconnect_max_delay
            )

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
        resp = self._http_send({
            "type": "register",
            "machine_id": self.config.machine_id,
            "capabilities": self._capabilities,
        })
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
        """Fetch pending commands from server without sending a full heartbeat."""
        active = self._executor.active_sessions
        resp = self._http_send({
            "type": "heartbeat",
            "machine_id": self.config.machine_id,
            "status": "busy" if active else "idle",
            "active_sessions": len(active),
        })

        if resp and isinstance(resp, dict):
            pending = resp.get("pending_commands", [])
            for cmd in pending:
                try:
                    self._handle_command(cmd)
                except Exception as e:
                    logger.error("Error handling command: %s", e)

    def _http_send(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
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
        resp = self._http_send({
            "type": "heartbeat",
            "machine_id": self.config.machine_id,
            "status": "busy" if active else "idle",
            "active_sessions": len(active),
        })

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
        self._http_send({
            "type": "permission_request",
            "session_id": session_id,
            "machine_id": self.config.machine_id,
            "control_request": control_request,
        })

    def _send_session_output(
        self, session_id: str, data: str, stream: str, is_complete: bool
    ) -> None:
        """Send a session_output message to the server."""
        self._http_send({
            "type": "session_output",
            "session_id": session_id,
            "data": data,
            "stream": stream,
            "is_complete": is_complete,
            "machine_id": self.config.machine_id,
        })

    def _send_session_status(
        self, session_id: str, status: str, pid: Optional[int] = None
    ) -> None:
        """Send a session_status message to the server."""
        logger.info(
            "Sending session_status: session=%s status=%s pid=%s",
            session_id[:8], status, pid
        )
        result = self._http_send({
            "type": "session_status",
            "session_id": session_id,
            "status": status,
            "pid": pid,
            "machine_id": self.config.machine_id,
        })
        if result:
            logger.info("session_status sent successfully for session %s", session_id[:8])
        else:
            logger.warning("Failed to send session_status for session %s", session_id[:8])

    def _send_usage_report(
        self, session_id: str, tokens: Dict[str, int], requests: int = 1
    ) -> None:
        """Send a usage_report message to the server."""
        self._http_send({
            "type": "usage_report",
            "session_id": session_id,
            "tokens": tokens,
            "requests": requests,
            "machine_id": self.config.machine_id,
        })

    def _handle_command(self, data: Dict[str, Any]) -> None:
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
        else:
            logger.warning("Unknown command: %s", command)

    def _cmd_start_session(self, data: Dict[str, Any]) -> None:
        """Handle a start_session command."""
        session_id = data.get("session_id", "")
        project_path = data.get("project_path", os.path.expanduser("~"))
        cli_tool = data.get("cli_tool", "qwen-code-cli")
        proxy_token = data.get("proxy_token", "")
        model = data.get("model")
        permission_mode = data.get("permission_mode")

        logger.info(
            "Starting session %s: cli=%s path=%s model=%s mode=%s",
            session_id[:8],
            cli_tool,
            project_path,
            model,
            permission_mode,
        )

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

    def _cmd_send_message(self, data: Dict[str, Any]) -> None:
        """Handle a send_message command."""
        session_id = data.get("session_id", "")
        content = data.get("content", "")

        logger.info("Sending message to session %s: %s", session_id[:8], content[:80])

        result = self._executor.send_message(session_id, content)
        if not result["success"]:
            logger.warning(
                "Failed to send message to session %s: %s",
                session_id[:8],
                result.get("error"),
            )

    def _cmd_stop_session(self, data: Dict[str, Any]) -> None:
        """Handle a stop_session command."""
        session_id = data.get("session_id", "")

        logger.info("Stopping session %s", session_id[:8])
        self._executor.stop_session(session_id)
        self._send_session_status(session_id, "stopped")

    def _cmd_permission_response(self, data: Dict[str, Any]) -> None:
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

        result = self._executor.send_permission_response(
            session_id, request_id, behavior, message
        )

        if not result["success"]:
            logger.warning(
                "Failed to handle permission response: %s",
                result.get("error"),
            )

    def _cmd_update_permission_mode(self, data: Dict[str, Any]) -> None:
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

    def _cmd_update_model(self, data: Dict[str, Any]) -> None:
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
    # Shutdown
    # ----------------------------------------------------------------

    def _signal_handler(self, signum: int, frame) -> None:
        """Handle SIGINT / SIGTERM for graceful shutdown."""
        logger.info("Received signal %d, shutting down...", signum)
        self._running = False

    def _shutdown(self) -> None:
        """Clean up all resources."""
        logger.info("Shutting down agent...")
        self._executor.stop_all()
        logger.info("Agent shutdown complete")


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
        sys.stdin = open(null_dev, "r")
        logger.info("Fixed stdin for service environment: stdin was None/closed")
    else:
        try:
            fd = sys.stdin.fileno()
            if fd < 0:
                sys.stdin = open(null_dev, "r")
                logger.info("Fixed stdin for service environment: invalid fd (%d)", fd)
        except (ValueError, OSError):
            sys.stdin = open(null_dev, "r")
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
    is_unix_service = (
        os.name != "nt"
        and (
            "INVOCATION_ID" in os.environ  # systemd sets this
            or os.path.exists("/proc/self/cgroup")  # Linux container/systemd
            or (
                os.environ.get("TERM") is None
                and os.environ.get("_LAUNCHD_SOCKET") is not None  # macOS launchd
            )
        )
    )

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
