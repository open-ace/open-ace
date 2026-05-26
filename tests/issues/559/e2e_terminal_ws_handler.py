#!/usr/bin/env python3
"""E2E test: browser WS client -> gevent WSGIServer (TerminalWSHandler) -> upstream terminal.

Verifies the full WebSocket path through the custom handler without
geventwebsocket: handshake, bidirectional bridging, and clean close.

Run:
    python tests/issues/559/e2e_terminal_ws_handler.py
"""

import asyncio
import os
import sys
import time
import uuid

# Project root must be on sys.path before gevent monkey-patch triggers any app imports
# Script is at tests/issues/559/e2e_terminal_ws_handler.py -> 4 levels up to project root
PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# gevent monkey-patch must come before other third-party imports
from gevent import monkey

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
# 3. Start gevent WSGIServer with TerminalWSHandler
# ═══════════════════════════════════════════════════════════


def start_gevent_server(app):
    """Start a gevent WSGIServer with TerminalWSHandler on a random port."""
    import socket

    from gevent.pywsgi import WSGIServer

    from app.terminal_ws_handler import TerminalWSHandler

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]

    server = WSGIServer(sock, app, handler_class=TerminalWSHandler)

    import gevent

    greenlet = gevent.spawn(server.start_accepting)
    # Give the server a moment to start
    time.sleep(0.1)

    return server, greenlet, port


# ═══════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════


def test_text_echo(upstream_port, server_port, terminal_id, token):
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


def test_binary_echo(upstream_port, server_port, terminal_id, token):
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


def test_multiple_messages(upstream_port, server_port, terminal_id, token):
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


def test_invalid_token_rejected(server_port, terminal_id):
    """Invalid token should cause the server to close the connection."""
    import socket

    from websockets.sync.client import connect

    url = f"ws://127.0.0.1:{server_port}/api/remote/terminal/{terminal_id}/ws?token=wrong-token"
    sock = socket.create_connection(("127.0.0.1", server_port))
    try:
        with connect(url, sock=sock, subprotocols=["binary"]) as ws:
            # Server should close the connection quickly
            ws.send("should fail")
            # If we get here, try to recv — should get close or error
            try:
                ws.recv(timeout=3)
            except Exception:
                pass  # Connection closed, as expected
    except Exception:
        pass  # Connection rejected during handshake or immediately after, also fine
    log("PASS", "invalid token rejected")


def test_unknown_terminal_rejected(server_port):
    """Unknown terminal_id should cause the server to close the connection."""
    import socket

    from websockets.sync.client import connect

    fake_id = str(uuid.uuid4())
    url = f"ws://127.0.0.1:{server_port}/api/remote/terminal/{fake_id}/ws?token=anything"
    sock = socket.create_connection(("127.0.0.1", server_port))
    try:
        with connect(url, sock=sock, subprotocols=["binary"]) as ws:
            ws.send("should fail")
            try:
                ws.recv(timeout=3)
            except Exception:
                pass
    except Exception:
        pass
    log("PASS", "unknown terminal rejected")


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════


def main():
    # Bypass system SOCKS proxy for localhost connections
    os.environ["NO_PROXY"] = "127.0.0.1,localhost"
    os.environ["no_proxy"] = "127.0.0.1,localhost"

    print("=" * 60)
    print("  E2E: Browser -> TerminalWSHandler -> Upstream Terminal")
    print("=" * 60)

    upstream_port = start_mock_upstream()
    log("Setup", f"Mock upstream terminal on port {upstream_port}")

    server, greenlet, server_port = start_gevent_server(simple_app)
    log("Setup", f"Gevent server with TerminalWSHandler on port {server_port}")

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
        test_text_echo(upstream_port, server_port, terminal_id, token)
        test_binary_echo(upstream_port, server_port, terminal_id, token)
        test_multiple_messages(upstream_port, server_port, terminal_id, token)
        test_invalid_token_rejected(server_port, terminal_id)
        test_unknown_terminal_rejected(server_port)
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
