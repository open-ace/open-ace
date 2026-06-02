"""Bridge browser WebSockets from Open ACE to remote terminal WebSockets."""

from __future__ import annotations

import logging
import threading
import urllib.parse
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

import gevent
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
    jobs: list[Any] = field(default_factory=list)


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
        for job in state.jobs:
            with suppress(Exception):
                job.kill(block=False)


def get_active_bridge_count() -> int:
    """Return active bridge count for diagnostics and tests."""
    with _bridge_lock:
        return sum(len(bridges) for bridges in _active_bridges.values())


def bridge_terminal_websocket(
    terminal_id: str, browser_ws, remote_ws_url: str, remote_token: str
) -> None:
    """Forward messages bidirectionally between browser and remote terminal."""
    parsed = urllib.parse.urlsplit(remote_ws_url)
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    query["token"] = remote_token
    upstream_url = urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))
    state = TerminalBridgeConnection(terminal_id=terminal_id, browser_ws=browser_ws)
    _register_bridge(state)

    try:
        with connect(
            upstream_url,
            subprotocols=["binary"],
            close_timeout=5,
            proxy=None,
        ) as remote_ws:
            state.remote_ws = remote_ws
            logger.info("Connected backend bridge to remote terminal: %s", remote_ws_url)

            def browser_to_remote() -> None:
                try:
                    while True:
                        message = browser_ws.receive()
                        if message is None:
                            logger.info("Bridge B→R: browser_ws.receive() returned None, closing")
                            break
                        if isinstance(message, (str, bytes, bytearray, memoryview)):
                            remote_ws.send(message)
                        else:
                            logger.debug(
                                "Ignoring unsupported browser terminal message type: %s",
                                type(message).__name__,
                            )
                except ConnectionClosed as e:
                    logger.info("Bridge B→R: connection error: %s", e)
                except Exception as e:
                    logger.info("Browser to remote terminal bridge error: %s", e)
                finally:
                    with suppress(Exception):
                        remote_ws.close()

            def remote_to_browser() -> None:
                try:
                    while True:
                        message = remote_ws.recv()
                        browser_ws.send(message)
                except ConnectionClosed as e:
                    logger.info("Bridge R→B: connection error: %s", e)
                except Exception as e:
                    logger.debug("Remote terminal to browser bridge error: %s", e)
                finally:
                    with suppress(Exception):
                        browser_ws.close()

            state.jobs = [gevent.spawn(browser_to_remote), gevent.spawn(remote_to_browser)]
            gevent.joinall(state.jobs)
    finally:
        _unregister_bridge(state)


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

            def browser_to_remote() -> None:
                try:
                    while True:
                        message = ws_frame.recv_message(browser_sock)
                        if message is None:
                            logger.info("Raw bridge B→R: browser closed")
                            break
                        remote_ws.send(message)
                except ConnectionClosed as e:
                    logger.info("Raw bridge B→R: remote closed: %s", e)
                except Exception as e:
                    logger.info("Raw bridge B→R error: %s", e)
                finally:
                    with suppress(Exception):
                        remote_ws.close()

            def remote_to_browser() -> None:
                try:
                    while True:
                        message = remote_ws.recv()
                        ws_frame.send_message(browser_sock, message)
                except ConnectionClosed as e:
                    logger.info("Raw bridge R→B: remote closed: %s", e)
                except Exception as e:
                    logger.debug("Raw bridge R→B error: %s", e)
                finally:
                    with suppress(Exception):
                        ws_frame.send_close(browser_sock)

            state.jobs = [gevent.spawn(browser_to_remote), gevent.spawn(remote_to_browser)]
            gevent.joinall(state.jobs)
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

        def browser_to_relay() -> None:
            try:
                while True:
                    message = ws_frame.recv_message(browser_sock)
                    if message is None:
                        logger.info("Relay bridge B→R: browser closed")
                        break
                    relay_ws.send(message)
            except ConnectionClosed as e:
                logger.info("Relay bridge B→R: relay closed: %s", e)
            except Exception as e:
                logger.info("Relay bridge B→R error: %s", e)
            finally:
                with suppress(Exception):
                    relay_ws.close()

        def relay_to_browser() -> None:
            try:
                while True:
                    message = relay_ws.recv()
                    if message is None:
                        logger.info("Relay bridge R→B: relay closed")
                        break
                    ws_frame.send_message(browser_sock, message)
            except ConnectionClosed as e:
                logger.info("Relay bridge R→B: relay closed: %s", e)
            except Exception as e:
                logger.debug("Relay bridge R→B error: %s", e)
            finally:
                with suppress(Exception):
                    ws_frame.send_close(browser_sock)

        state.jobs = [gevent.spawn(browser_to_relay), gevent.spawn(relay_to_browser)]
        gevent.joinall(state.jobs)
    finally:
        _unregister_bridge(state)
