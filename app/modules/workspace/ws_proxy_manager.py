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

import hmac
import logging
import os
import subprocess as stdlib_subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

import gevent

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
        import socket

        with self._lock:
            # Find next available port (both in internal tracking and actually free)
            while self._next_port <= PROXY_PORT_END:
                # Check if port is in internal allocations
                if self._next_port in self._port_allocations:
                    self._next_port += 1
                    continue

                # Check if port is actually free (not used by orphaned processes)
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as test_socket:
                        test_socket.settimeout(1)
                        result = test_socket.connect_ex(("127.0.0.1", self._next_port))
                        if result == 0:
                            # Port is in use by some process (orphaned proxy)
                            logger.info(
                                "Port %d is in use (orphaned proxy), skipping", self._next_port
                            )
                            self._next_port += 1
                            continue
                except Exception as e:
                    logger.debug("Port check error: %s", e)

                # Port is free
                port = self._next_port
                self._next_port += 1
                return port

            # Wrap around if we hit the end
            self._next_port = PROXY_PORT_START
            while self._next_port <= PROXY_PORT_END:
                if self._next_port in self._port_allocations:
                    self._next_port += 1
                    continue

                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as test_socket:
                        test_socket.settimeout(1)
                        result = test_socket.connect_ex(("127.0.0.1", self._next_port))
                        if result == 0:
                            self._next_port += 1
                            continue
                except Exception:
                    pass

                port = self._next_port
                self._next_port += 1
                return port

            raise RuntimeError("No available ports for WebSocket proxy")

    def start_proxy(
        self,
        terminal_id: str,
        machine_id: str,
        auth_token: str,
        backend_url: str,
    ) -> tuple[int, str, str]:
        """
        Start a WebSocket proxy for a terminal session.

        Args:
            terminal_id: Terminal session ID
            machine_id: Remote machine ID
            auth_token: Authentication token for browser connections
            backend_url: Open ACE backend URL (for fetching remote WS info)

        Returns:
            Tuple of (proxy_port, proxy_ws_url, auth_token)
            If proxy already exists, returns existing auth_token instead of provided one.
        """
        with self._lock:
            # Check if proxy already exists
            if terminal_id in self._proxies:
                proxy = self._proxies[terminal_id]
                if proxy.process and proxy.process.poll() is None:
                    # Return existing proxy port, URL, and token
                    return proxy.port, f"ws://localhost:{proxy.port}", proxy.auth_token
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

            # Start proxy process - pass token via env var to avoid ps aux exposure
            cmd = [
                "python3",
                proxy_script,
                "--backend-url",
                backend_url,
                "--machine-id",
                machine_id,
                "--terminal-id",
                terminal_id,
                "--port",
                str(port),
            ]
            env = os.environ.copy()
            env["OPEN_ACE_PROXY_TOKEN"] = auth_token

            try:
                # Redirect proxy output to log file for debugging
                proxy_log_path = f"/tmp/ws_proxy_{terminal_id[:8]}.log"
                log_file = open(proxy_log_path, "a")
                process = stdlib_subprocess.Popen(
                    cmd,
                    stdout=log_file,
                    stderr=log_file,
                    env=env,
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

                return port, proxy_ws_url, auth_token

            except Exception as e:
                logger.error("Failed to start WebSocket proxy: %s", e)
                raise

    def _wait_for_ready(self, process, terminal_id: str) -> bool:
        """Wait for proxy to signal it's ready (gevent-compatible)."""
        timeout = 10  # seconds
        start = time.time()
        ready_line = ""

        while time.time() - start < timeout:
            poll_result = process.poll()
            if poll_result is not None:
                # Process exited
                try:
                    stderr = process.stderr.read().decode() if process.stderr else ""
                    stdout = process.stdout.read().decode() if process.stdout else ""
                    logger.error(
                        "Proxy process exited early (code=%s): stderr=%s stdout=%s",
                        poll_result,
                        stderr[:500],
                        stdout[:200],
                    )
                except Exception as read_err:
                    logger.error("Proxy process exited, read error: %s", read_err)
                return False

            # Check stdout for READY signal (non-blocking read)
            try:
                if process.stdout:
                    # Use select to check if data is available (avoid blocking readline)
                    import select as _select

                    readable, _, _ = _select.select([process.stdout], [], [], 0.5)
                    if readable:
                        line = process.stdout.readline()
                        if line:
                            line_str = line.decode().strip()
                            logger.debug("Read from stdout: %s", line_str)
                            if line_str.startswith("READY:"):
                                logger.info("Proxy READY signal received: %s", line_str)
                                return True
                            ready_line += line_str + "\n"
            except Exception as e:
                logger.debug("Read error: %s", e)

            gevent.sleep(0.1)  # Use gevent sleep instead of blocking time.sleep

        logger.warning(
            "Timeout waiting for proxy READY signal (terminal %s). Accumulated output: %s",
            terminal_id[:8],
            ready_line[:200],
        )
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
                return hmac.compare_digest(proxy.auth_token, token)
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
