"""In-memory store for terminal relay WebSocket connections.

When remote machines are on private networks (not directly reachable from backend),
the agent opens a WebSocket relay connection to the backend. The backend then
bridges browser WebSocket connections through this relay to the remote terminal.

This solves the issue where backend cannot directly connect to ws://192.168.x.x:port
because the remote machine is behind NAT or on a private network.

HA Support (Issue #1851):
- Relay state is registered in Redis for cross-Pod awareness
- Close frame (code=1012) sent to browser when relay disconnects
- Graceful degradation to in-memory mode when Redis is unavailable

The relay WebSocket objects are process-local. HTTP remote-session control
state is persisted, but an active browser-to-agent terminal bridge must still
use the web pod that owns the live relay socket or reconnect after failover.
"""

from __future__ import annotations


import logging
import threading
from typing import Any

from app.modules.workspace.relay_distributed_store import (
    KEY_TTL,
    RelayDistributedStore,
    RelayType,
    get_relay_distributed_store,
)

logger = logging.getLogger(__name__)


class TerminalRelayStore:
    """Thread-safe store for terminal relay WebSocket connections.

    Each terminal session can have:
    - At most one active relay WebSocket from the agent
    - One pending browser connection waiting for relay (stored as (socket, event) tuple)

    Only one browser bridge is allowed per relay to avoid concurrent socket access.
    Additional browser connections are rejected while a bridge is active.

    HA Support:
    - Relay state is registered in Redis for cross-Pod awareness
    - Close frame sent to browser when relay disconnects
    - Graceful degradation when Redis is unavailable
    """

    def __init__(self, distributed_store: RelayDistributedStore | None = None):
        self._lock = threading.Lock()
        # relay_websockets[terminal_id] = websocket connection from agent
        self._relay_websockets: dict[str, Any] = {}
        # pending_browsers[terminal_id] = (browser_socket, bridge_done_event)
        # Only one pending browser per terminal to avoid concurrent relay access.
        self._pending_browsers: dict[str, tuple[Any, Any]] = {}
        # active_bridges[terminal_id] = (browser_socket, heartbeat_greenlet)
        # Tracks active browser bridges for disconnect notification
        self._active_bridges: dict[str, tuple[Any, Any]] = {}
        # relay_tokens[terminal_id] = token for relay authentication
        self._relay_tokens: dict[str, str] = {}
        # Distributed store for cross-Pod state
        self._distributed_store = distributed_store

    def register_relay(self, terminal_id: str, relay_ws: Any, token: str) -> None:
        """Register an agent relay WebSocket connection.

        If a relay already exists for this terminal, closes the old relay
        to prevent relay hijacking via silent overwrite.

        Also registers the relay state in Redis for cross-Pod awareness.

        Args:
            terminal_id: The terminal session ID
            relay_ws: The WebSocket connection from the agent
            token: Authentication token for this relay
        """
        # Register in Redis first (if available)
        distributed = self._get_distributed_store()
        if distributed and distributed.is_redis_available():
            if not distributed.register_relay(RelayType.TERMINAL, terminal_id):
                logger.warning(
                    "Failed to register relay %s in Redis (already registered or Redis unavailable)",
                    terminal_id[:8],
                )
                # Continue with local registration anyway

        with self._lock:
            # Close existing relay to prevent hijacking
            existing = self._relay_websockets.get(terminal_id)
            if existing is not None:
                logger.warning(
                    "Relay already exists for terminal %s, closing old relay",
                    terminal_id[:8],
                )
                if hasattr(existing, "_close_event") and existing._close_event:
                    existing._close_event.set()
                if hasattr(existing, "close"):
                    try:
                        existing.close()
                    except Exception:
                        pass

            self._relay_websockets[terminal_id] = relay_ws
            self._relay_tokens[terminal_id] = token

            # Pick up one pending browser if available and no bridge active
            pending = self._pending_browsers.pop(terminal_id, None)
            if pending is not None and terminal_id not in self._active_bridges:
                browser_sock, bridge_done_event = pending
                logger.info(
                    "Relay registered for terminal %s, bridging pending browser",
                    terminal_id[:8],
                )
                # Start bridge outside lock
                self._spawn_bridge(terminal_id, browser_sock, bridge_done_event)
            else:
                logger.info("Relay registered for terminal %s", terminal_id[:8])

    def unregister_relay(self, terminal_id: str) -> None:
        """Remove a relay WebSocket (agent disconnected).

        Sends close frame to connected browser before cleaning up.
        Also unregisters from Redis for cross-Pod awareness.
        """
        # Unregister from Redis
        distributed = self._get_distributed_store()
        if distributed and distributed.is_redis_available():
            distributed.unregister_relay(RelayType.TERMINAL, terminal_id)

        with self._lock:
            # Notify connected browser before cleanup
            bridge_info = self._active_bridges.get(terminal_id)
            if bridge_info is not None:
                browser_sock, _heartbeat_greenlet = bridge_info
                self._send_relay_disconnected_close(browser_sock)

            self._relay_websockets.pop(terminal_id, None)
            self._relay_tokens.pop(terminal_id, None)
            self._active_bridges.pop(terminal_id, None)
            logger.info("Relay unregistered for terminal %s", terminal_id[:8])

    def get_relay(self, terminal_id: str) -> Any | None:
        """Get the relay WebSocket for a terminal."""
        with self._lock:
            return self._relay_websockets.get(terminal_id)

    def has_relay(self, terminal_id: str) -> bool:
        """Check if a relay connection exists for this terminal."""
        with self._lock:
            return terminal_id in self._relay_websockets

    def is_active_bridge(self, terminal_id: str) -> bool:
        """Check if a bridge is active for this terminal."""
        with self._lock:
            return terminal_id in self._active_bridges

    def add_pending_browser(
        self, terminal_id: str, browser_sock: Any, bridge_done_event: Any = None
    ) -> bool:
        """Add a browser connection to pending queue.

        Only one pending browser is allowed per terminal. If a bridge is already
        active, or a browser is already pending, additional connections are rejected.

        Args:
            terminal_id: The terminal session ID
            browser_sock: Raw browser socket (not wrapped)
            bridge_done_event: Event to signal when bridge completes

        Returns True if added to pending (no relay available),
        False if relay exists and bridge started immediately,
        or the browser was rejected (bridge already active).
        """
        with self._lock:
            if terminal_id in self._active_bridges:
                logger.warning(
                    "Bridge already active for terminal %s, rejecting browser",
                    terminal_id[:8],
                )
                return False

            if terminal_id in self._relay_websockets:
                # Relay exists and no bridge active - start bridge immediately
                logger.info(
                    "Browser connected, relay exists for terminal %s",
                    terminal_id[:8],
                )
                # Start bridge outside lock
                self._spawn_bridge(terminal_id, browser_sock, bridge_done_event)
                return False

            # No relay - add to pending if slot available
            if terminal_id in self._pending_browsers:
                logger.warning(
                    "Pending browser already exists for terminal %s, rejecting",
                    terminal_id[:8],
                )
                return False

            self._pending_browsers[terminal_id] = (browser_sock, bridge_done_event)
            logger.info(
                "Browser added to pending for terminal %s (no relay yet)",
                terminal_id[:8],
            )
            return True

    def remove_pending_browser(self, terminal_id: str, browser_sock: Any) -> None:
        """Remove a pending browser from the queue (e.g., on timeout).

        This prevents bridging already-closed sockets.
        """
        with self._lock:
            pending = self._pending_browsers.get(terminal_id)
            if pending is not None and pending[0] is browser_sock:
                del self._pending_browsers[terminal_id]
                logger.info(
                    "Removed pending browser for terminal %s (timeout)",
                    terminal_id[:8],
                )

    def _spawn_bridge(
        self, terminal_id: str, browser_sock: Any, bridge_done_event: Any = None
    ) -> None:
        """Start bridging browser to relay (must be called inside lock).

        The actual gevent.spawn happens outside the lock context.
        """
        relay_ws = self._relay_websockets.get(terminal_id)

        def run_bridge():
            heartbeat_greenlet = None
            try:
                if relay_ws is None:
                    logger.error("No relay for terminal %s in bridge", terminal_id[:8])
                    return
                from app.modules.workspace.terminal_ws_bridge import bridge_browser_to_relay

                # Start heartbeat greenlet for Redis
                heartbeat_greenlet = self._start_heartbeat_greenlet(terminal_id)

                # Track active bridge
                with self._lock:
                    self._active_bridges[terminal_id] = (browser_sock, heartbeat_greenlet)

                bridge_browser_to_relay(terminal_id, browser_sock, relay_ws)
            except Exception as e:
                logger.error("Failed to bridge browser to relay: %s", e)
                try:
                    import app.ws_frame as ws_frame

                    ws_frame.send_close(browser_sock, 1011, "Bridge failed")
                except Exception:
                    pass
            finally:
                # Stop heartbeat
                if heartbeat_greenlet is not None:
                    try:
                        heartbeat_greenlet.kill()
                    except Exception:
                        pass

                with self._lock:
                    self._active_bridges.pop(terminal_id, None)
                if bridge_done_event:
                    bridge_done_event.set()

        # Import here to avoid circular imports at module level
        import gevent

        gevent.spawn(run_bridge)

    def _get_distributed_store(self) -> RelayDistributedStore | None:
        """Get the distributed store instance (lazy initialization)."""
        if self._distributed_store is None:
            try:
                self._distributed_store = get_relay_distributed_store()
            except Exception as e:
                logger.warning("Failed to get distributed store: %s", e)
        return self._distributed_store

    def _start_heartbeat_greenlet(self, terminal_id: str):
        """Start a heartbeat greenlet for a relay.

        The greenlet sends periodic heartbeats to Redis to keep the relay
        state alive and detect stale entries.
        """
        import gevent

        stop_event = gevent.event.Event()

        def heartbeat_loop():
            distributed = self._get_distributed_store()
            if distributed is None:
                return

            while not stop_event.is_set():
                stop_event.wait(KEY_TTL // 2)  # Heartbeat at half TTL
                if stop_event.is_set():
                    break
                try:
                    distributed.heartbeat(RelayType.TERMINAL, terminal_id)
                except Exception as e:
                    logger.debug(
                        "Heartbeat failed for terminal %s: %s",
                        terminal_id[:8],
                        e,
                    )

        greenlet = gevent.spawn(heartbeat_loop)
        greenlet._stop_event = stop_event  # type: ignore[attr-defined]
        return greenlet

    @staticmethod
    def _send_relay_disconnected_close(browser_sock: Any) -> None:
        """Send close frame to browser when relay disconnects.

        Args:
            browser_sock: Browser socket to notify
        """
        try:
            import app.ws_frame as ws_frame

            ws_frame.send_close(browser_sock, 1012, "Relay disconnected")
            logger.info("Sent relay disconnected close frame to browser")
        except Exception as e:
            logger.debug("Failed to send relay disconnected close frame: %s", e)


# Module-level singleton
terminal_relay_store = TerminalRelayStore()
