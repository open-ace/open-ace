#!/usr/bin/env python3
"""
Open ACE Remote Agent - Main Daemon

Connects to the Open ACE server via WebSocket (with HTTP fallback),
handles commands (start_session, send_message, stop_session), manages
CLI subprocesses through the executor module, and sends heartbeats.
"""

import json
import logging
import os
import signal
import sys
import threading
import time
from typing import Any, Callable, Dict, Optional

import requests

from config import AgentConfig
from executor import ProcessExecutor
from system_info import get_capabilities

logger = logging.getLogger("openace-agent")


class RemoteAgent:
    """
    Main remote agent daemon.

    Connects to the Open ACE server, receives commands, manages CLI
    subprocesses, and reports output and status back to the server.
    """

    def __init__(self, config: Optional[AgentConfig] = None):
        self.config = config or AgentConfig()
        self._executor = ProcessExecutor(
            server_url=self.config.server_url,
            output_callback=self._on_session_output,
        )
        self._capabilities = get_capabilities()
        self._ws = None
        self._ws_connected = threading.Event()
        self._ws_lock = threading.Lock()
        self._running = False
        self._reconnect_delay = self.config.reconnect_base_delay
        self._heartbeat_timer: Optional[threading.Timer] = None

        # Thread-safe queue for messages that need to be sent when
        # the connection is available
        self._outbound_lock = threading.Lock()
        self._outbound_queue: list = []

    # ----------------------------------------------------------------
    # Connection lifecycle
    # ----------------------------------------------------------------

    def start(self) -> None:
        """
        Main entry point. Runs the agent loop with reconnection logic.

        Blocks until shutdown is requested via signal or unhandled error.
        """
        self._running = True

        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        logger.info(
            "Open ACE Remote Agent starting (machine_id=%s, server=%s)",
            self.config.machine_id[:8],
            self.config.server_url,
        )
        logger.info("Capabilities: %s", json.dumps(self._capabilities, indent=2))

        while self._running:
            try:
                self._connect_and_run()
            except Exception as e:
                logger.error("Connection error: %s", e)

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

    def _connect_and_run(self) -> None:
        """
        Attempt to establish a WebSocket connection and run the
        message loop. Falls back to HTTP polling if WebSocket fails.
        """
        try:
            import websocket

            ws_url = self.config.ws_url + "/api/remote/agent/ws"
            logger.info("Connecting to %s", ws_url)

            ws = websocket.WebSocketApp(
                ws_url,
                on_open=self._on_ws_open,
                on_message=self._on_ws_message,
                on_error=self._on_ws_error,
                on_close=self._on_ws_close,
                header={"Authorization": f"Bearer {self.config.agent_token or ''}"},
            )

            with self._ws_lock:
                self._ws = ws

            ws.run_forever(
                ping_interval=30,
                ping_timeout=10,
                skip_utf8_validation=True,
            )

        except ImportError:
            logger.warning(
                "websocket-client not installed, using HTTP polling fallback"
            )
            self._http_poll_loop()
        except Exception as e:
            logger.error("WebSocket connection failed: %s", e)
            logger.info("Falling back to HTTP polling")
            self._http_poll_loop()

    # ----------------------------------------------------------------
    # WebSocket handlers
    # ----------------------------------------------------------------

    def _on_ws_open(self, ws) -> None:
        """Handle WebSocket connection established."""
        logger.info("WebSocket connected")
        self._ws_connected.set()
        self._reconnect_delay = self.config.reconnect_base_delay

        # Send registration message
        self._send_ws_message({
            "type": "register",
            "machine_id": self.config.machine_id,
            "capabilities": self._capabilities,
        })

        # Start heartbeat timer
        self._schedule_heartbeat()

    def _on_ws_message(self, ws, message: str) -> None:
        """Handle an incoming WebSocket message."""
        try:
            data = json.loads(message)
        except json.JSONDecodeError as e:
            logger.warning("Invalid JSON received: %s", e)
            return

        msg_type = data.get("type")

        if msg_type == "command":
            self._handle_command(data)
        elif msg_type == "ping":
            self._send_ws_message({"type": "pong", "machine_id": self.config.machine_id})
        else:
            logger.warning("Unknown message type: %s", msg_type)

    def _on_ws_error(self, ws, error) -> None:
        """Handle WebSocket error."""
        logger.error("WebSocket error: %s", error)

    def _on_ws_close(self, ws, close_status_code, close_msg) -> None:
        """Handle WebSocket connection closed."""
        logger.info(
            "WebSocket closed (code=%s, reason=%s)",
            close_status_code,
            close_msg,
        )
        self._ws_connected.clear()
        self._cancel_heartbeat()

    def _send_ws_message(self, message: Dict[str, Any]) -> bool:
        """Send a JSON message over the WebSocket."""
        with self._ws_lock:
            ws = self._ws

        if ws is None:
            # Queue for HTTP fallback
            with self._outbound_lock:
                self._outbound_queue.append(message)
            return False

        try:
            ws.send(json.dumps(message))
            return True
        except Exception as e:
            logger.error("Failed to send WebSocket message: %s", e)
            with self._outbound_lock:
                self._outbound_queue.append(message)
            return False

    # ----------------------------------------------------------------
    # HTTP fallback
    # ----------------------------------------------------------------

    def _http_poll_loop(self) -> None:
        """
        HTTP long-polling fallback when WebSocket is not available.

        Periodically POSTs heartbeat and queued messages to the server's
        /api/remote/agent/message endpoint.
        """
        logger.info("Starting HTTP polling mode")

        # Register via HTTP first
        self._http_send({
            "type": "register",
            "machine_id": self.config.machine_id,
            "capabilities": self._capabilities,
        })

        poll_interval = self.config.heartbeat_interval
        last_heartbeat = 0.0

        while self._running:
            now = time.time()

            # Send heartbeat if interval has elapsed
            if now - last_heartbeat >= poll_interval:
                self._send_heartbeat_via_http()
                last_heartbeat = now

            # Flush any queued outbound messages
            self._flush_outbound_queue()

            # Brief sleep between polls
            time.sleep(min(5.0, poll_interval / 2))

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
        """Send a heartbeat via HTTP."""
        active = self._executor.active_sessions
        self._http_send({
            "type": "heartbeat",
            "machine_id": self.config.machine_id,
            "status": "busy" if active else "idle",
            "active_sessions": len(active),
        })

    def _flush_outbound_queue(self) -> None:
        """Send all queued outbound messages via HTTP."""
        with self._outbound_lock:
            queue = self._outbound_queue[:]
            self._outbound_queue.clear()

        for msg in queue:
            self._http_send(msg)

    # ----------------------------------------------------------------
    # Heartbeat
    # ----------------------------------------------------------------

    def _schedule_heartbeat(self) -> None:
        """Schedule the next heartbeat message."""
        self._cancel_heartbeat()

        def heartbeat():
            if not self._running:
                return
            active = self._executor.active_sessions
            self._send_ws_message({
                "type": "heartbeat",
                "machine_id": self.config.machine_id,
                "status": "busy" if active else "idle",
                "active_sessions": len(active),
            })
            if self._running:
                self._schedule_heartbeat()

        self._heartbeat_timer = threading.Timer(
            self.config.heartbeat_interval, heartbeat
        )
        self._heartbeat_timer.daemon = True
        self._heartbeat_timer.start()

    def _cancel_heartbeat(self) -> None:
        """Cancel the pending heartbeat timer."""
        if self._heartbeat_timer is not None:
            self._heartbeat_timer.cancel()
            self._heartbeat_timer = None

    # ----------------------------------------------------------------
    # Command handling
    # ----------------------------------------------------------------

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
        elif command == "pause_session":
            # Forward compatibility: pause is not implemented at the
            # subprocess level; send acknowledgement
            self._send_session_status(session_id, "paused")
        elif command == "resume_session":
            self._send_session_status(session_id, "running")
        else:
            logger.warning("Unknown command: %s", command)

    def _cmd_start_session(self, data: Dict[str, Any]) -> None:
        """Handle a start_session command."""
        session_id = data.get("session_id", "")
        project_path = data.get("project_path", os.path.expanduser("~"))
        cli_tool = data.get("cli_tool", "qwen-code-cli")
        proxy_token = data.get("proxy_token", "")
        model = data.get("model")

        logger.info(
            "Starting session %s: cli=%s path=%s model=%s",
            session_id[:8],
            cli_tool,
            project_path,
            model,
        )

        result = self._executor.start_session(
            session_id=session_id,
            project_path=project_path,
            cli_tool=cli_tool,
            proxy_token=proxy_token,
            model=model,
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

    # ----------------------------------------------------------------
    # Outbound message helpers
    # ----------------------------------------------------------------

    def _on_session_output(
        self, session_id: str, data: str, stream: str, is_complete: bool
    ) -> None:
        """
        Callback invoked by the executor when a session produces output.

        Sends session_output message to the server via WebSocket or HTTP.
        """
        self._send_session_output(session_id, data, stream, is_complete)

        # If the output stream completed and the process has exited,
        # send a final status update
        if is_complete:
            info = self._executor.get_session_info(session_id)
            if info and not info["is_running"]:
                self._send_session_status(session_id, "exited")

    def _send_session_output(
        self, session_id: str, data: str, stream: str, is_complete: bool
    ) -> None:
        """Send a session_output message to the server."""
        message = {
            "type": "session_output",
            "session_id": session_id,
            "data": data,
            "stream": stream,
            "is_complete": is_complete,
            "machine_id": self.config.machine_id,
        }
        if not self._send_ws_message(message):
            # WebSocket not available — send via HTTP
            self._http_send(message)

    def _send_session_status(
        self, session_id: str, status: str, pid: Optional[int] = None
    ) -> None:
        """Send a session_status message to the server."""
        message = {
            "type": "session_status",
            "session_id": session_id,
            "status": status,
            "pid": pid,
            "machine_id": self.config.machine_id,
        }
        if not self._send_ws_message(message):
            self._http_send(message)

    def _send_usage_report(
        self, session_id: str, tokens: Dict[str, int], requests: int = 1
    ) -> None:
        """Send a usage_report message to the server."""
        message = {
            "type": "usage_report",
            "session_id": session_id,
            "tokens": tokens,
            "requests": requests,
            "machine_id": self.config.machine_id,
        }
        if not self._send_ws_message(message):
            self._http_send(message)

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

        self._cancel_heartbeat()

        # Stop all running CLI subprocesses
        self._executor.stop_all()

        # Close WebSocket connection
        with self._ws_lock:
            if self._ws is not None:
                try:
                    self._ws.close()
                except Exception:
                    pass
                self._ws = None

        logger.info("Agent shutdown complete")


def setup_logging(level: str = "INFO") -> None:
    """Configure logging for the agent daemon."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )


def main() -> None:
    """Entry point for the remote agent daemon."""
    config = AgentConfig()

    setup_logging(config.log_level)

    logger.info("=" * 60)
    logger.info("Open ACE Remote Agent")
    logger.info("Server:  %s", config.server_url)
    logger.info("Machine: %s", config.machine_id)
    logger.info("=" * 60)

    agent = RemoteAgent(config)
    agent.start()


if __name__ == "__main__":
    main()
