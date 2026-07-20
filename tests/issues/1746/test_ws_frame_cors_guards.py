"""Regression tests for issue #1746: WS frame caps and dev CORS narrowing."""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

import pytest

project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import app.ws_frame as ws_frame
from app import register_error_handlers
from app.__init__ import _reset_cors_origins_cache


class FakeSocket:
    """Minimal socket double for frame parser tests."""

    def __init__(self, recv_data: bytes = b""):
        self._recv_buf = bytearray(recv_data)
        self.sent: list[bytes] = []

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def recv(self, n: int) -> bytes:
        if not self._recv_buf:
            return b""
        chunk = bytes(self._recv_buf[:n])
        self._recv_buf = self._recv_buf[n:]
        return chunk

    @property
    def all_sent(self) -> bytes:
        return b"".join(self.sent)


def _mask(mask_key: bytes, payload: bytes) -> bytes:
    return bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))


def _build_client_frame(payload: bytes, opcode: int = ws_frame.OP_TEXT, fin: bool = True) -> bytes:
    first = (0x80 if fin else 0x00) | opcode
    mask_key = b"\x37\xfa\x21\x3d"
    length = len(payload)

    if length < 126:
        header = struct.pack("!BB", first, 0x80 | length)
    elif length < 65536:
        header = struct.pack("!BBH", first, 0x80 | 126, length)
    else:
        header = struct.pack("!BBQ", first, 0x80 | 127, length)

    return header + mask_key + _mask(mask_key, payload)


@pytest.fixture
def clean_cors_env(monkeypatch):
    """Clear CORS environment before each test.

    Using monkeypatch ensures the environment is restored after the test,
    preventing cross-test pollution.
    """
    monkeypatch.delenv("OPENACE_CORS_ALLOWED_ORIGINS", raising=False)
    # Reset cache to ensure clean state
    _reset_cors_origins_cache()
    # Yield to let test run
    yield
    # Clean up after test
    _reset_cors_origins_cache()


@pytest.fixture
def cors_app(clean_cors_env):
    from flask import Flask, jsonify

    app = Flask(__name__)
    app.config["TESTING"] = True
    register_error_handlers(app)

    @app.route("/api/ping", methods=["GET"])
    def ping():
        return jsonify({"ok": True})

    return app


class TestWebSocketFrameCap:
    def test_oversized_64bit_frame_is_rejected_before_payload_read(self, monkeypatch):
        monkeypatch.setenv("OPENACE_WS_MAX_MESSAGE_BYTES", "1024")
        # Build a properly masked oversized frame
        mask_key = b"\x37\xfa\x21\x3d"
        oversized_payload = b"x" * 2048
        masked_payload = _mask(mask_key, oversized_payload)
        frame = struct.pack("!BBQ", 0x82, 0x80 | 127, 2048) + mask_key + masked_payload
        sock = FakeSocket(frame)

        with pytest.raises(ws_frame.WebSocketMessageTooLarge, match="frame length 2048"):
            ws_frame.recv_message(sock)

        close_frame = sock.all_sent
        assert close_frame[0] == 0x88
        assert struct.unpack("!H", close_frame[2:4])[0] == 1009

    def test_fragmented_message_over_limit_is_rejected(self, monkeypatch):
        monkeypatch.setenv("OPENACE_WS_MAX_MESSAGE_BYTES", "5")
        frame = _build_client_frame(
            b"abc", opcode=ws_frame.OP_TEXT, fin=False
        ) + _build_client_frame(b"def", opcode=ws_frame.OP_CONT, fin=True)
        sock = FakeSocket(frame)

        with pytest.raises(ws_frame.WebSocketMessageTooLarge, match="message length 6"):
            ws_frame.recv_message(sock)

        close_frame = sock.all_sent
        assert close_frame[0] == 0x88
        assert struct.unpack("!H", close_frame[2:4])[0] == 1009


class TestCorsPolicy:
    def test_loopback_webui_origin_is_allowed(self, cors_app):
        client = cors_app.test_client()

        resp = client.get("/api/ping", headers={"Origin": "http://localhost:3100"})

        assert resp.status_code == 200
        assert resp.headers["Access-Control-Allow-Origin"] == "http://localhost:3100"
        assert resp.headers["Access-Control-Allow-Credentials"] == "true"

    def test_arbitrary_same_port_non_loopback_origin_is_not_reflected(self, cors_app):
        client = cors_app.test_client()

        resp = client.get("/api/ping", headers={"Origin": "http://192.168.1.55:3100"})

        assert resp.status_code == 200
        assert "Access-Control-Allow-Origin" not in resp.headers
        assert "Access-Control-Allow-Credentials" not in resp.headers

    def test_same_host_lan_ip_webui_origin_is_allowed(self, cors_app):
        """Issue #1859: LAN IP access must allow the WebUI iframe origin.

        When the browser reaches the backend via a non-loopback address (e.g.
        ``http://198.51.100.1:5000``), the per-user qwen-code-webui iframe
        is served on a webui port using the same hostname
        (``http://198.51.100.1:3101``). The backend must emit CORS headers
        for that origin so the iframe's credentialed API calls are not blocked
        by the browser ("NetworkError when attempting to fetch resource.").

        Uses RFC 5737 TEST-NET-2 documentation address to avoid embedding
        real infrastructure IPs in the repo.
        """
        client = cors_app.test_client()

        resp = client.get(
            "/api/ping",
            headers={"Origin": "http://198.51.100.1:3101"},
            base_url="http://198.51.100.1:5000",
        )

        assert resp.status_code == 200
        assert resp.headers["Access-Control-Allow-Origin"] == "http://198.51.100.1:3101"
        assert resp.headers["Access-Control-Allow-Credentials"] == "true"

    def test_same_host_ipv6_webui_origin_is_allowed(self, cors_app):
        """Issue #1859 review follow-up: IPv6 same-host access must work.

        ``Host: [2001:db8::1]:5000`` must be parsed to ``2001:db8::1`` and
        match the origin hostname from ``http://[2001:db8::1]:3101``. A naive
        ``host.split(":", 1)[0]`` would yield ``"[2001"`` and fail closed.

        Uses RFC 3849 documentation IPv6 prefix ``2001:db8::/32``.
        """
        client = cors_app.test_client()

        resp = client.get(
            "/api/ping",
            headers={"Origin": "http://[2001:db8::1]:3101"},
            base_url="http://[2001:db8::1]:5000",
        )

        assert resp.status_code == 200
        assert resp.headers["Access-Control-Allow-Origin"] == "http://[2001:db8::1]:3101"
        assert resp.headers["Access-Control-Allow-Credentials"] == "true"

    def test_mismatched_host_webui_origin_is_not_reflected(self, cors_app):
        """Issue #1859: a WebUI origin whose host differs from the backend
        Host header must still be rejected to prevent credential reflection
        onto an attacker-controlled host."""
        client = cors_app.test_client()

        resp = client.get(
            "/api/ping",
            headers={"Origin": "http://evil.example:3101"},
            base_url="http://198.51.100.1:5000",
        )

        assert resp.status_code == 200
        assert "Access-Control-Allow-Origin" not in resp.headers
        assert "Access-Control-Allow-Credentials" not in resp.headers

    def test_preflight_honors_explicit_allowlist(self, monkeypatch, request):
        # Use monkeypatch to set env - this happens AFTER autouse fixture clears it
        # Create a fresh app for this test to avoid state pollution

        # Set the environment variable using monkeypatch
        monkeypatch.setenv(
            "OPENACE_CORS_ALLOWED_ORIGINS",
            "https://workspace.internal.example, https://ops.example",
        )

        # Force cache rebuild immediately
        from app.__init__ import _get_allowed_cors_origins

        cache = _get_allowed_cors_origins()
        print(f"Cache after setting env: {cache}")

        from flask import Flask, jsonify

        app = Flask(__name__)
        app.config["TESTING"] = True

        # Register error handlers first (includes OPTIONS handler)
        register_error_handlers(app)

        # Then register the actual route
        @app.route("/api/ping", methods=["GET"])
        def ping():
            return jsonify({"ok": True})

        client = app.test_client()

        resp = client.options(
            "/api/ping",
            headers={"Origin": "https://workspace.internal.example"},
        )

        assert resp.status_code == 200
        assert (
            "Access-Control-Allow-Origin" in resp.headers
        ), f"Expected CORS headers, got: {dict(resp.headers)}"
        assert resp.headers["Access-Control-Allow-Origin"] == "https://workspace.internal.example"
        assert resp.headers["Access-Control-Allow-Credentials"] == "true"
