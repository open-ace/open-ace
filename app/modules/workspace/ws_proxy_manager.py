"""
WebSocket Terminal Proxy Manager

Manages WebSocket proxy processes that bridge browser connections to remote
terminal servers. The browser cannot directly connect to remote private IPs
(e.g., ws://192.168.64.3:port), so we run a proxy on the Open ACE server.

Architecture:
  Browser → ws://localhost:5001 (proxy) → ws://192.168.64.3:port (remote)

Each terminal session gets its own proxy process on a dynamically allocated port.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

import gevent
from gevent import subprocess as gevent_subprocess

if TYPE_CHECKING:
    import subprocess

logger = logging.getLogger(__name__)

# Port range for WebSocket proxies
PROXY_PORT_START = 42000
PROXY_PORT_END = 42999


@dataclass
class ProxyInstance:
    """Represents a running WebSocket proxy process."""

    terminal_id: str
    machine_id: str
    port: int
    pid: int | None = None
    process: subprocess.Popen | None = None  # type: ignore[valid-type]
    started_at: datetime = field(default_factory=datetime.now)
    auth_token: str = ""
    original_ws_url: str = ""  # Remote terminal's original ws_url


class WebSocketProxyManager:
    """
    Manages WebSocket proxy processes.

    Each proxy:
    1. Runs websocket_proxy.py as a subprocess
    2. Listens on a dynamically allocated port
    3. Accepts browser connections with auth token
    4. Connects to the remote terminal WebSocket
    5. Forwards messages bidirectionally
    """

    def __init__(self):
        self._lock = threading.RLock()  # Use RLock to allow re-entry
        self._proxies: dict[str, ProxyInstance] = {}  # terminal_id -> ProxyInstance
        self._port_allocations: dict[int, str] = {}  # port -> terminal_id
        self._next_port = PROXY_PORT_START

    def _allocate_port(self) -> int:
        """Allocate an available port for a new proxy."""
        with self._lock:
            # Find next available port
            while self._next_port <= PROXY_PORT_END:
                if self._next_port not in self._port_allocations:
                    port = self._next_port
                    self._next_port += 1
                    return port
                self._next_port += 1

            # Wrap around if we hit the end
            self._next_port = PROXY_PORT_START
            while self._next_port <= PROXY_PORT_END:
                if self._next_port not in self._port_allocations:
                    port = self._next_port
                    self._next_port += 1
                    return port
                self._next_port += 1

            raise RuntimeError("No available ports for WebSocket proxy")

    def start_proxy(
        self,
        terminal_id: str,
        machine_id: str,
        auth_token: str,
        backend_url: str,
    ) -> tuple[int, str]:
        """
        Start a WebSocket proxy for a terminal session.

        Args:
            terminal_id: Terminal session ID
            machine_id: Remote machine ID
            auth_token: Authentication token for browser connections
            backend_url: Open ACE backend URL (for fetching remote WS info)

        Returns:
            Tuple of (proxy_port, proxy_ws_url)
        """
        with self._lock:
            # Check if proxy already exists
            if terminal_id in self._proxies:
                proxy = self._proxies[terminal_id]
                if proxy.process and proxy.process.poll() is None:
                    return proxy.port, f"ws://localhost:{proxy.port}"
                else:
                    # Process died, clean up
                    self._cleanup_proxy(terminal_id)

            # Allocate port
            port = self._allocate_port()

            # Find websocket_proxy.py script (in remote-agent directory at project root)
            # ws_proxy_manager.py is in app/modules/workspace/
            # websocket_proxy.py is in remote-agent/ at project root
            script_dir = os.path.dirname(os.path.abspath(__file__))
            # Go up 3 levels: workspace -> modules -> app -> project_root
            project_root = os.path.normpath(os.path.join(script_dir, "..", "..", ".."))
            proxy_script = os.path.join(project_root, "remote-agent", "websocket_proxy.py")

            logger.info("Looking for proxy script at: %s", proxy_script)
            if not os.path.exists(proxy_script):
                logger.error("WebSocket proxy script not found: %s", proxy_script)
                raise RuntimeError("WebSocket proxy script not found")

            # Start proxy process (use gevent subprocess to avoid blocking)
            cmd = [
                "python3",
                proxy_script,
                "--token",
                auth_token,
                "--backend-url",
                backend_url,
                "--machine-id",
                machine_id,
                "--terminal-id",
                terminal_id,
                "--port",
                str(port),
            ]

            try:
                # Redirect proxy output to log file for debugging
                proxy_log_path = f"/tmp/ws_proxy_{terminal_id[:8]}.log"
                process = gevent_subprocess.Popen(
                    cmd,
                    stdout=gevent_subprocess.PIPE,
                    stderr=gevent_subprocess.PIPE,
                )
                logger.info(
                    "Proxy process started with PID %d, logs at %s", process.pid, proxy_log_path
                )

                proxy = ProxyInstance(
                    terminal_id=terminal_id,
                    machine_id=machine_id,
                    port=port,
                    pid=process.pid,
                    process=process,
                    auth_token=auth_token,
                )

                self._proxies[terminal_id] = proxy
                self._port_allocations[port] = terminal_id

                # Wait for READY signal
                logger.info(
                    "Waiting for WebSocket proxy to be ready (terminal %s, port %d)",
                    terminal_id[:8],
                    port,
                )
                ready = self._wait_for_ready(process, terminal_id)
                if not ready:
                    self._cleanup_proxy(terminal_id)
                    raise RuntimeError("WebSocket proxy failed to start")

                proxy_ws_url = f"ws://localhost:{port}"
                logger.info(
                    "WebSocket proxy started for terminal %s on port %d",
                    terminal_id[:8],
                    port,
                )

                return port, proxy_ws_url

            except Exception as e:
                logger.error("Failed to start WebSocket proxy: %s", e)
                raise

    def _wait_for_ready(self, process, terminal_id: str) -> bool:
        """Wait for proxy to signal it's ready (gevent-compatible)."""
        timeout = 10  # seconds
        start = time.time()

        while time.time() - start < timeout:
            if process.poll() is not None:
                # Process exited
                try:
                    stderr = process.stderr.read().decode() if process.stderr else ""
                    logger.error("Proxy process exited early: %s", stderr[:500])
                except Exception:
                    pass
                return False

            # Check stdout for READY signal (non-blocking read)
            try:
                # Use gevent's communicate with timeout to avoid blocking
                line = process.stdout.readline()
                if line:
                    line_str = line.decode().strip()
                    if line_str.startswith("READY:"):
                        logger.info("Proxy READY signal received: %s", line_str)
                        return True
            except Exception as e:
                logger.debug("Read error: %s", e)

            gevent.sleep(0.1)  # Use gevent sleep instead of blocking time.sleep

        logger.warning("Timeout waiting for proxy READY signal (terminal %s)", terminal_id[:8])
        return False

    def stop_proxy(self, terminal_id: str) -> bool:
        """Stop a WebSocket proxy process."""
        with self._lock:
            if terminal_id not in self._proxies:
                return False

            proxy = self._proxies[terminal_id]
            if proxy.process and proxy.process.poll() is None:
                try:
                    proxy.process.terminate()
                    proxy.process.wait(timeout=5)
                except Exception:
                    proxy.process.kill()

            self._cleanup_proxy(terminal_id)
            logger.info("WebSocket proxy stopped for terminal %s", terminal_id[:8])
            return True

    def _cleanup_proxy(self, terminal_id: str) -> None:
        """Clean up proxy state (assumes lock is held)."""
        if terminal_id in self._proxies:
            proxy = self._proxies.pop(terminal_id)
            if proxy.port in self._port_allocations:
                self._port_allocations.pop(proxy.port)

    def get_proxy_url(self, terminal_id: str) -> str | None:
        """Get the proxy WebSocket URL for a terminal."""
        with self._lock:
            if terminal_id in self._proxies:
                proxy = self._proxies[terminal_id]
                if proxy.process and proxy.process.poll() is None:
                    return f"ws://localhost:{proxy.port}"
        return None

    def validate_proxy_token(self, terminal_id: str, token: str) -> bool:
        """Validate if a token matches the proxy token for a given terminal.

        Used by the API endpoint to authenticate WebSocket proxy requests.
        """
        with self._lock:
            if terminal_id in self._proxies:
                proxy = self._proxies[terminal_id]
                return proxy.auth_token == token
        return False

    def stop_all(self) -> None:
        """Stop all proxy processes."""
        with self._lock:
            for terminal_id in list(self._proxies.keys()):
                proxy = self._proxies[terminal_id]
                if proxy.process and proxy.process.poll() is None:
                    try:
                        proxy.process.terminate()
                        proxy.process.wait(timeout=5)
                    except Exception:
                        proxy.process.kill()
                self._cleanup_proxy(terminal_id)


# Module-level singleton
_ws_proxy_manager: WebSocketProxyManager | None = None


def get_ws_proxy_manager() -> WebSocketProxyManager:
    """Get the global WebSocket proxy manager instance."""
    global _ws_proxy_manager
    if _ws_proxy_manager is None:
        _ws_proxy_manager = WebSocketProxyManager()
    return _ws_proxy_manager
