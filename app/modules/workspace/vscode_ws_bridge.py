"""Bridge browser WebSockets from Open ACE to remote code-server WebSockets."""

from __future__ import annotations


import logging
import threading
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import gevent
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

import app.ws_frame as ws_frame

logger = logging.getLogger(__name__)

MAX_ACTIVE_BRIDGES = 100

_bridge_lock = threading.Lock()
_active_bridges: dict[str, list[VSCodeBridgeConnection]] = {}


@dataclass
class VSCodeBridgeConnection:
    """Active browser-to-code-server bridge resources."""

    vscode_id: str
    browser_ws: Any
    remote_ws: Any = None
    jobs: list[Any] = field(default_factory=list)


def _register_bridge(state: VSCodeBridgeConnection) -> None:
    with _bridge_lock:
        total = sum(len(v) for v in _active_bridges.values())
        if total >= MAX_ACTIVE_BRIDGES:
            raise RuntimeError(f"Too many active VSCode bridges ({total}/{MAX_ACTIVE_BRIDGES})")
        _active_bridges.setdefault(state.vscode_id, []).append(state)


def _unregister_bridge(state: VSCodeBridgeConnection) -> None:
    with _bridge_lock:
        bridges = _active_bridges.get(state.vscode_id)
        if not bridges:
            return
        with suppress(ValueError):
            bridges.remove(state)
        if not bridges:
            _active_bridges.pop(state.vscode_id, None)


def close_vscode_bridges(vscode_id: str) -> None:
    """Close active bridge connections for a stopped VSCode session."""
    with _bridge_lock:
        bridges = list(_active_bridges.pop(vscode_id, []))

    for state in bridges:
        for job in state.jobs:
            try:
                job.kill()
            except Exception:
                pass
        if state.remote_ws:
            try:
                state.remote_ws.close()
            except Exception:
                pass
        if state.browser_ws:
            try:
                state.browser_ws.close()
            except Exception:
                pass


def bridge_vscode_ws(browser_ws: Any, remote_ws_url: str, vscode_id: str) -> None:
    """Bridge a browser WebSocket to the remote code-server WebSocket.

    Args:
        browser_ws: The browser's WebSocket connection (gevent-websocket)
        remote_ws_url: The WebSocket URL of the remote code-server
        vscode_id: The VSCode session ID for cleanup tracking
    """
    state = VSCodeBridgeConnection(vscode_id=vscode_id, browser_ws=browser_ws)
    _register_bridge(state)

    try:
        remote_ws = connect(remote_ws_url)
        state.remote_ws = remote_ws
    except Exception as e:
        logger.error("Failed to connect to remote code-server WS %s: %s", remote_ws_url, e)
        _unregister_bridge(state)
        try:
            browser_ws.close()
        except Exception:
            pass
        return

    def browser_to_remote():
        try:
            for raw_frame in browser_ws:
                if isinstance(raw_frame, bytes):
                    remote_ws.send(raw_frame)
                else:
                    remote_ws.send(raw_frame)
        except ConnectionClosed:
            pass
        except Exception as e:
            logger.debug("browser_to_remote error for VSCode %s: %s", vscode_id[:8], e)
        finally:
            try:
                remote_ws.close()
            except Exception:
                pass

    def remote_to_browser():
        try:
            for message in remote_ws:
                if isinstance(message, bytes):
                    browser_ws.send(message, binary=True)
                else:
                    browser_ws.send(message)
        except ConnectionClosed:
            pass
        except Exception as e:
            logger.debug("remote_to_browser error for VSCode %s: %s", vscode_id[:8], e)
        finally:
            try:
                browser_ws.close()
            except Exception:
                pass

    job1 = gevent.spawn(browser_to_remote)
    job2 = gevent.spawn(remote_to_browser)
    state.jobs = [job1, job2]

    try:
        gevent.joinall([job1, job2], raise_error=False)
    finally:
        _unregister_bridge(state)


def bridge_vscode_ws_raw(vscode_id: str, browser_sock, remote_ws_url: str) -> None:
    """Bridge a raw browser socket (via RemoteWSHandler) to a remote code-server.

    Uses ``ws_frame`` for browser-side I/O and ``websockets.sync`` for the
    remote side.  The browser socket has already completed the WS handshake
    before this function is called.
    """
    state = VSCodeBridgeConnection(vscode_id=vscode_id, browser_ws=browser_sock)
    _register_bridge(state)

    parsed = urlparse(remote_ws_url)
    origin_scheme = "https" if parsed.scheme == "wss" else "http"
    origin = f"{origin_scheme}://{parsed.netloc}" if parsed.netloc else None

    try:
        with connect(
            remote_ws_url,
            origin=origin,
            close_timeout=5,
            proxy=None,
        ) as remote_ws:
            state.remote_ws = remote_ws
            logger.info("Connected raw bridge to remote code-server: %s", remote_ws_url)

            def browser_to_remote() -> None:
                try:
                    while True:
                        message = ws_frame.recv_message(browser_sock)
                        if message is None:
                            logger.info("VSCode raw bridge B→R: browser closed")
                            break
                        remote_ws.send(message)
                except ConnectionClosed as e:
                    logger.info("VSCode raw bridge B→R: remote closed: %s", e)
                except Exception as e:
                    logger.info("VSCode raw bridge B→R error: %s", e)
                finally:
                    with suppress(Exception):
                        remote_ws.close()

            def remote_to_browser() -> None:
                try:
                    while True:
                        message = remote_ws.recv()
                        ws_frame.send_message(browser_sock, message)
                except ConnectionClosed as e:
                    logger.info("VSCode raw bridge R→B: remote closed: %s", e)
                except Exception as e:
                    logger.debug("VSCode raw bridge R→B error: %s", e)
                finally:
                    with suppress(Exception):
                        ws_frame.send_close(browser_sock)

            state.jobs = [gevent.spawn(browser_to_remote), gevent.spawn(remote_to_browser)]
            gevent.joinall(state.jobs)
    finally:
        _unregister_bridge(state)
