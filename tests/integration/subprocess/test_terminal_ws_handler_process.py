#!/usr/bin/env python3
"""E2E test: browser WS client -> gevent WSGIServer (RemoteWSHandler) -> upstream terminal.

Verifies the full WebSocket path through the custom handler without
geventwebsocket: handshake, bidirectional bridging, and clean close.

Run:
    python tests/integration/subprocess/test_terminal_ws_handler_process.py
"""

import asyncio
import os
import sys
import time
import uuid

# Project root must be on sys.path before gevent monkey-patch triggers any app imports
# Script is at tests/integration/subprocess/test_terminal_ws_handler_process.py
# -> tests/integration/subprocess is 3 dirnames below the repo root.
import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(559)]

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# gevent monkey-patch is needed only when this file runs as a standalone script
# (``python tests/integration/subprocess/test_terminal_ws_handler_process.py``). Under pytest the
# module is *imported* during collection, and a process-wide monkey-patch at
# import time corrupts every subsequently-collected test in the shard: gevent
# greenlets + native threading/asyncio deadlock in ways ``--timeout`` cannot
# interrupt, hanging the whole shard until the job is cancelled (see #2457).
# Guarding on ``__main__`` keeps collection side-effect-free; ``main()``
# applies the patch before the script's own server/asyncio work runs.
from gevent import monkey

if __name__ == "__main__":
    monkey.patch_all()


def log(stage, msg):
    print(f"  [{stage}] {msg}", flush=True)


# ═══════════════════════════════════════════════════════════
# 1. Mock upstream terminal (echo server)
# ═══════════════════════════════════════════════════════════


def start_mock_upstream():
    """Start a WebSocket echo server on a random port.

    Returns the port number.
    """
    import threading

    import websockets

    port_holder = [None]

    async def handle(websocket):
        async for message in websocket:
            await websocket.send(message)

    async def run():
        async with websockets.serve(handle, "127.0.0.1", 0) as server:
            port_holder[0] = server.sockets[0].getsockname()[1]
            await asyncio.Future()  # run forever

    thread = threading.Thread(target=asyncio.run, args=(run(),), daemon=True)
    thread.start()

    for _ in range(50):
        if port_holder[0] is not None:
            break
        time.sleep(0.05)

    assert port_holder[0] is not None, "Mock upstream failed to start"
    return port_holder[0]


# ═══════════════════════════════════════════════════════════
# 2. Minimal WSGI app (handler intercepts WS before Flask)
# ═══════════════════════════════════════════════════════════


def simple_app(environ, start_response):
    """Minimal WSGI app — non-terminal requests get a plain 200."""
    start_response("200 OK", [("Content-Type", "text/plain")])
    return [b"ok"]


# ═══════════════════════════════════════════════════════════
# 3. Start gevent WSGIServer with RemoteWSHandler
# ═══════════════════════════════════════════════════════════


def start_gevent_server(app):
    """Start a gevent WSGIServer with RemoteWSHandler on a random port."""
    import socket

    from gevent.pywsgi import WSGIServer

    from app.remote_ws_handler import RemoteWSHandler

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]

    server = WSGIServer(sock, app, handler_class=RemoteWSHandler)

    import gevent

    greenlet = gevent.spawn(server.start_accepting)
    # Give the server a moment to start
    time.sleep(0.1)

    return server, greenlet, port


# ═══════════════════════════════════════════════════════════
# Checks (script-internal steps, called from main())
#
# These are NOT pytest tests: the bridge relay (websockets.sync inside
# gevent greenlets) only works under gevent monkey-patching, which must
# never run in the shared pytest process (see the guard at the top).
# pytest runs the whole script in a subprocess below and requires a
# full pass; the child's monkey-patch is fully isolated.
# ═══════════════════════════════════════════════════════════


def test_e2e_terminal_ws_handler_script():
    """Run the manual e2e script in a subprocess and require a full pass."""
    import subprocess

    proc = subprocess.run(
        [sys.executable, os.path.abspath(__file__)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    tail = f"stdout tail:\n{proc.stdout[-1500:]}\nstderr tail:\n{proc.stderr[-1500:]}"
    assert proc.returncode == 0, f"e2e script failed:\n{tail}"
    assert "All E2E tests passed!" in proc.stdout, f"e2e script incomplete:\n{tail}"


def _check_text_echo(upstream_port, server_port, terminal_id, token):
    """Browser sends text -> upstream echoes -> browser receives text."""
    import socket

    from websockets.sync.client import connect

    url = f"ws://127.0.0.1:{server_port}/api/remote/terminal/{terminal_id}/ws?token={token}"
    sock = socket.create_connection(("127.0.0.1", server_port))
    with connect(url, sock=sock, subprotocols=["binary"]) as ws:
        ws.send("hello terminal")
        result = ws.recv(timeout=5)
        assert result == "hello terminal", f"Expected 'hello terminal', got {result!r}"
    log("PASS", "text echo")


def _check_binary_echo(upstream_port, server_port, terminal_id, token):
    """Browser sends binary -> upstream echoes -> browser receives binary."""
    import socket

    from websockets.sync.client import connect

    url = f"ws://127.0.0.1:{server_port}/api/remote/terminal/{terminal_id}/ws?token={token}"
    sock = socket.create_connection(("127.0.0.1", server_port))
    with connect(url, sock=sock, subprotocols=["binary"]) as ws:
        payload = bytes(range(256))
        ws.send(payload)
        result = ws.recv(timeout=5)
        assert (
            result == payload
        ), f"Binary mismatch: got {len(result)} bytes, expected {len(payload)}"
    log("PASS", "binary echo")


def _check_multiple_messages(upstream_port, server_port, terminal_id, token):
    """Send multiple messages in sequence, verify order."""
    import socket

    from websockets.sync.client import connect

    url = f"ws://127.0.0.1:{server_port}/api/remote/terminal/{terminal_id}/ws?token={token}"
    sock = socket.create_connection(("127.0.0.1", server_port))
    with connect(url, sock=sock, subprotocols=["binary"]) as ws:
        messages = ["msg1", "msg2", "msg3"]
        for m in messages:
            ws.send(m)
        for expected in messages:
            result = ws.recv(timeout=5)
            assert result == expected, f"Expected {expected!r}, got {result!r}"
    log("PASS", "multiple messages in order")


def _check_invalid_token_rejected(server_port, terminal_id):
    """Invalid token must terminate the session — never normal traffic.

    Either outcome is correct: rejection at the upgrade handshake, or a close
    right after. Receiving an echoed/normal frame would mean the token check
    is missing, and fails the check.
    """
    import socket

    from websockets.exceptions import WebSocketException
    from websockets.sync.client import connect

    url = f"ws://127.0.0.1:{server_port}/api/remote/terminal/{terminal_id}/ws?token=wrong-token"
    sock = socket.create_connection(("127.0.0.1", server_port))
    outcome = "received-data"
    try:
        with connect(url, sock=sock, subprotocols=["binary"]) as ws:
            ws.send("should fail")
            try:
                ws.recv(timeout=3)
            except WebSocketException:
                outcome = "closed-after-connect"
    except (OSError, WebSocketException):
        outcome = "handshake-rejected"
    assert (
        outcome != "received-data"
    ), f"invalid token must close/reject the session, but the client got normal traffic ({outcome})"
    log("PASS", "invalid token rejected")


def _check_unknown_terminal_rejected(server_port):
    """Unknown terminal_id must terminate the session — never normal traffic.

    Same contract as the invalid-token check: rejection at the handshake or a
    close right after both pass; an echoed/normal frame fails.
    """
    import socket

    from websockets.exceptions import WebSocketException
    from websockets.sync.client import connect

    fake_id = str(uuid.uuid4())
    url = f"ws://127.0.0.1:{server_port}/api/remote/terminal/{fake_id}/ws?token=anything"
    sock = socket.create_connection(("127.0.0.1", server_port))
    outcome = "received-data"
    try:
        with connect(url, sock=sock, subprotocols=["binary"]) as ws:
            ws.send("should fail")
            try:
                ws.recv(timeout=3)
            except WebSocketException:
                outcome = "closed-after-connect"
    except (OSError, WebSocketException):
        outcome = "handshake-rejected"
    assert (
        outcome != "received-data"
    ), f"unknown terminal must close/reject the session, but the client got normal traffic ({outcome})"
    log("PASS", "unknown terminal rejected")


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════


def main():
    # Bypass system SOCKS proxy for localhost connections
    os.environ["NO_PROXY"] = "127.0.0.1,localhost"
    os.environ["no_proxy"] = "127.0.0.1,localhost"

    print("=" * 60)
    print("  E2E: Browser -> RemoteWSHandler -> Upstream Terminal")
    print("=" * 60)

    upstream_port = start_mock_upstream()
    log("Setup", f"Mock upstream terminal on port {upstream_port}")

    server, greenlet, server_port = start_gevent_server(simple_app)
    log("Setup", f"Gevent server with RemoteWSHandler on port {server_port}")

    terminal_id = str(uuid.uuid4())
    machine_id = f"e2e-machine-{terminal_id[:8]}"
    token = f"e2e-token-{uuid.uuid4().hex[:16]}"

    from app.modules.workspace.terminal_store import terminal_info_store

    upstream_url = f"ws://127.0.0.1:{upstream_port}/ws"
    terminal_info_store.put(
        machine_id,
        terminal_id,
        {
            "status": "running",
            "token": token,
            "ws_url": upstream_url,
            "original_ws_url": upstream_url,
            "original_token": "upstream-token",
        },
    )
    log("Setup", f"Registered terminal {terminal_id[:8]} -> upstream {upstream_url}")

    try:
        _check_text_echo(upstream_port, server_port, terminal_id, token)
        _check_binary_echo(upstream_port, server_port, terminal_id, token)
        _check_multiple_messages(upstream_port, server_port, terminal_id, token)
        _check_invalid_token_rejected(server_port, terminal_id)
        _check_unknown_terminal_rejected(server_port)
    finally:
        terminal_info_store.pop(machine_id, terminal_id)
        server.stop()
        greenlet.kill()
        log("Cleanup", "Stopped server and unregistered terminal")

    print()
    print("=" * 60)
    print("  All E2E tests passed!")
    print("=" * 60)


if __name__ == "__main__":
    main()
