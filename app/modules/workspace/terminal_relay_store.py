"""In-memory store for terminal relay WebSocket connections.

When remote machines are on private networks (not directly reachable from backend),
the agent opens a WebSocket relay connection to the backend. The backend then
bridges browser WebSocket connections through this relay to the remote terminal.

This solves the issue where backend cannot directly connect to ws://192.168.x.x:port
because the remote machine is behind NAT or on a private network.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)

# Relay connections older than this (seconds) without activity are considered stale.
RELAY_TTL_SECONDS = 30 * 60  # 30 minutes
MAX_PENDING_BRIDGES = 100


class TerminalRelayStore:
    """Thread-safe store for terminal relay WebSocket connections.

    Each terminal session can have:
    - An active relay WebSocket from the agent
    - Pending browser connections waiting for relay (stored as (socket, event) tuples)
    """

    def __init__(self):
        self._lock = threading.Lock()
        # relay_websockets[terminal_id] = websocket connection from agent
        self._relay_websockets: dict[str, Any] = {}
        # pending_browsers[terminal_id] = list of (browser_socket, bridge_done_event) tuples
        self._pending_browsers: dict[str, list[tuple[Any, Any]]] = defaultdict(list)
        # relay_last_activity[terminal_id] = timestamp of last data transfer
        self._relay_last_activity: dict[str, float] = {}
        # relay_tokens[terminal_id] = token for relay authentication
        self._relay_tokens: dict[str, str] = {}

    def register_relay(self, terminal_id: str, relay_ws: Any, token: str) -> None:
        """Register an agent relay WebSocket connection.

        Args:
            terminal_id: The terminal session ID
            relay_ws: The WebSocket connection from the agent
            token: Authentication token for this relay
        """
        with self._lock:
            self._relay_websockets[terminal_id] = relay_ws
            self._relay_tokens[terminal_id] = token
            self._relay_last_activity[terminal_id] = time.time()

            # Connect any pending browser connections
            pending = self._pending_browsers.pop(terminal_id, [])
            logger.info(
                "Relay registered for terminal %s, connecting %d pending browsers",
                terminal_id[:8],
                len(pending),
            )

        # Process pending browsers outside the lock to avoid blocking
        for browser_sock, bridge_done_event in pending:
            self._start_bridge(terminal_id, browser_sock, bridge_done_event)

    def unregister_relay(self, terminal_id: str) -> None:
        """Remove a relay WebSocket (agent disconnected)."""
        with self._lock:
            self._relay_websockets.pop(terminal_id, None)
            self._relay_tokens.pop(terminal_id, None)
            self._relay_last_activity.pop(terminal_id, None)
            logger.info("Relay unregistered for terminal %s", terminal_id[:8])

    def get_relay(self, terminal_id: str) -> Any | None:
        """Get the relay WebSocket for a terminal."""
        with self._lock:
            return self._relay_websockets.get(terminal_id)

    def validate_token(self, terminal_id: str, token: str) -> bool:
        """Validate relay authentication token."""
        import hmac

        with self._lock:
            stored = self._relay_tokens.get(terminal_id, "")
            if not stored or not token:
                return False
            return hmac.compare_digest(stored, token)

    def has_relay(self, terminal_id: str) -> bool:
        """Check if a relay connection exists for this terminal."""
        with self._lock:
            return terminal_id in self._relay_websockets

    def add_pending_browser(
        self, terminal_id: str, browser_sock: Any, bridge_done_event: Any = None
    ) -> bool:
        """Add a browser connection to pending queue.

        Args:
            terminal_id: The terminal session ID
            browser_sock: Raw browser socket (not wrapped)
            bridge_done_event: Event to signal when bridge completes

        Returns True if added to pending (no relay available),
        False if relay exists and bridge started immediately.
        """
        with self._lock:
            if terminal_id in self._relay_websockets:
                # Relay exists, start bridge immediately
                logger.info(
                    "Browser connected, relay exists for terminal %s",
                    terminal_id[:8],
                )
                # Bridge will be started outside lock
                return False
            else:
                # No relay, add to pending with bridge_done_event
                if len(self._pending_browsers[terminal_id]) >= MAX_PENDING_BRIDGES:
                    logger.warning(
                        "Too many pending browsers for terminal %s, rejecting",
                        terminal_id[:8],
                    )
                    return False
                self._pending_browsers[terminal_id].append((browser_sock, bridge_done_event))
                logger.info(
                    "Browser added to pending for terminal %s (no relay yet)",
                    terminal_id[:8],
                )
                return True

    def get_pending_browsers(self, terminal_id: str) -> list[tuple[Any, Any]]:
        """Get and clear pending browser connections for a terminal.

        Returns list of (browser_sock, bridge_done_event) tuples.
        """
        with self._lock:
            return self._pending_browsers.pop(terminal_id, [])

    def update_activity(self, terminal_id: str) -> None:
        """Update last activity timestamp for a relay."""
        with self._lock:
            if terminal_id in self._relay_websockets:
                self._relay_last_activity[terminal_id] = time.time()

    def cleanup_stale(self) -> int:
        """Remove stale relay connections. Returns number removed."""
        now = time.time()
        stale_ids: list[str] = []
        with self._lock:
            for terminal_id, last_activity in self._relay_last_activity.items():
                if now - last_activity > RELAY_TTL_SECONDS:
                    stale_ids.append(terminal_id)
            for terminal_id in stale_ids:
                self._relay_websockets.pop(terminal_id, None)
                self._relay_tokens.pop(terminal_id, None)
                self._relay_last_activity.pop(terminal_id, None)
                self._pending_browsers.pop(terminal_id, None)
        for terminal_id in stale_ids:
            logger.info("Cleaned up stale relay for terminal %s", terminal_id[:8])
        return len(stale_ids)

    def _start_bridge(
        self, terminal_id: str, browser_sock: Any, bridge_done_event: Any = None
    ) -> None:
        """Start bridging browser to relay (called outside lock).

        Args:
            terminal_id: The terminal session ID
            browser_sock: Raw browser socket
            bridge_done_event: Event to signal when bridge completes
        """
        relay_ws = self.get_relay(terminal_id)
        if relay_ws:
            import gevent

            from app.modules.workspace.terminal_ws_bridge import bridge_browser_to_relay

            def run_bridge():
                try:
                    bridge_browser_to_relay(terminal_id, browser_sock, relay_ws)
                except Exception as e:
                    logger.error("Failed to bridge browser to relay: %s", e)
                    try:
                        import app.ws_frame as ws_frame

                        ws_frame.send_close(browser_sock, 1011, "Bridge failed")
                    except Exception:
                        pass
                finally:
                    if bridge_done_event:
                        bridge_done_event.set()

            gevent.spawn(run_bridge)


# Module-level singleton
terminal_relay_store = TerminalRelayStore()
