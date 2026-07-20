"""Bridge browser WebSockets from Open ACE to remote terminal WebSockets."""

from __future__ import annotations


import logging
import threading
import urllib.parse
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

import gevent
from gevent.event import Event
from gevent.lock import Semaphore
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

import app.ws_frame as ws_frame

logger = logging.getLogger(__name__)

MAX_ACTIVE_BRIDGES = 1000

_bridge_lock = threading.Lock()
_active_bridges: dict[str, list[TerminalBridgeConnection]] = {}


@dataclass
class TerminalBridgeConnection:
    """Active browser-to-terminal bridge resources."""

    terminal_id: str
    browser_ws: Any
    remote_ws: Any = None


def _register_bridge(state: TerminalBridgeConnection) -> None:
    with _bridge_lock:
        _active_bridges.setdefault(state.terminal_id, []).append(state)
        active_count = sum(len(bridges) for bridges in _active_bridges.values())
    if active_count > MAX_ACTIVE_BRIDGES:
        logger.warning("Active terminal bridge count is high: %d", active_count)


def _unregister_bridge(state: TerminalBridgeConnection) -> None:
    with _bridge_lock:
        bridges = _active_bridges.get(state.terminal_id)
        if not bridges:
            return
        with suppress(ValueError):
            bridges.remove(state)
        if not bridges:
            _active_bridges.pop(state.terminal_id, None)


def close_terminal_bridges(terminal_id: str) -> None:
    """Close active bridge connections for a stopped terminal."""
    with _bridge_lock:
        bridges = list(_active_bridges.pop(terminal_id, []))

    for state in bridges:
        with suppress(Exception):
            if hasattr(state.browser_ws, "close"):
                state.browser_ws.close()
        if state.remote_ws is not None:
            with suppress(Exception):
                state.remote_ws.close()


def get_active_bridge_count() -> int:
    """Return active bridge count for diagnostics and tests."""
    with _bridge_lock:
        return sum(len(bridges) for bridges in _active_bridges.values())


def bridge_terminal_websocket_raw(
    terminal_id: str, browser_sock, remote_ws_url: str, remote_token: str
) -> None:
    """Bridge a raw browser socket (via RemoteWSHandler) to a remote terminal.

    Uses ``ws_frame`` for browser-side I/O and ``websockets.sync`` for the
    remote side.  The browser socket has already completed the WS handshake
    before this function is called.
    """
    parsed = urllib.parse.urlsplit(remote_ws_url)
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    query["token"] = remote_token
    upstream_url = urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))
    state = TerminalBridgeConnection(terminal_id=terminal_id, browser_ws=browser_sock)
    _register_bridge(state)

    try:
        with connect(
            upstream_url,
            subprotocols=["binary"],
            close_timeout=5,
            proxy=None,
        ) as remote_ws:
            state.remote_ws = remote_ws
            logger.info("Connected raw bridge to remote terminal: %s", remote_ws_url)
            _run_raw_bridge(browser_sock, remote_ws, "Raw")
    finally:
        _unregister_bridge(state)


def bridge_browser_to_relay(terminal_id: str, browser_sock, relay_ws: Any) -> None:
    """Bridge a raw browser socket to an agent relay WebSocket.

    This is used when the backend cannot directly reach the remote terminal
    (e.g., remote machine is on a private network). The agent has established
    a relay WebSocket connection to the backend, and we bridge through it.

    Args:
        terminal_id: The terminal session ID
        browser_sock: Raw browser socket (via RemoteWSHandler, already handshaked)
        relay_ws: WebSocket connection from agent (websockets.sync.client connection)
    """
    state = TerminalBridgeConnection(
        terminal_id=terminal_id, browser_ws=browser_sock, remote_ws=relay_ws
    )
    _register_bridge(state)

    try:
        logger.info("Starting browser-to-relay bridge for terminal %s", terminal_id[:8])
        _run_raw_bridge(browser_sock, relay_ws, "Relay")
    finally:
        _unregister_bridge(state)


def _run_raw_bridge(browser_sock: Any, remote_ws: Any, label: str) -> None:
    """Run bidirectional bridge between a raw browser socket and a remote WS.

    Shared by both direct (bridge_terminal_websocket_raw) and relay
    (bridge_browser_to_relay) paths to avoid duplicating try/except logic.
    """
    tag_b2r = f"{label} bridge B→R"
    tag_r2b = f"{label} bridge R→B"
    write_lock = Semaphore(1)

    def browser_to_remote() -> None:
        try:
            while True:
                message = ws_frame.recv_message(browser_sock)
                if message is None:
                    logger.info("%s: browser closed", tag_b2r)
                    break
                remote_ws.send(message)
        except ConnectionClosed as e:
            logger.info("%s: remote closed: %s", tag_b2r, e)
        except Exception as e:
            logger.info("%s error: %s", tag_b2r, e)
        finally:
            with suppress(Exception):
                remote_ws.close()

    def remote_to_browser() -> None:
        try:
            while True:
                message = remote_ws.recv()
                if message is None:
                    logger.info("%s: remote closed", tag_r2b)
                    break
                with write_lock:
                    ws_frame.send_message(browser_sock, message)
        except ConnectionClosed as e:
            logger.info("%s: remote closed: %s", tag_r2b, e)
        except Exception as e:
            logger.debug("%s error: %s", tag_r2b, e)
        finally:
            with suppress(Exception):
                with write_lock:
                    ws_frame.send_close(browser_sock)

    shutdown = Event()

    def browser_keepalive() -> None:
        """Send periodic WebSocket pings to browser to prevent nginx timeout."""
        try:
            while not shutdown.is_set():
                shutdown.wait(30)
                if not shutdown.is_set():
                    with write_lock:
                        ws_frame.send_ping(browser_sock)
        except Exception as e:
            logger.debug("Keepalive greenlet exiting: %s", e)

    jobs = [
        gevent.spawn(browser_to_remote),
        gevent.spawn(remote_to_browser),
        gevent.spawn(browser_keepalive),
    ]
    gevent.joinall(jobs)
    shutdown.set()
