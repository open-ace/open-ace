"""Issue #3378 (§7.4): SandboxWebuiProxy against real sockets.

The proxy runs on a real gevent hub in its own thread; the fake gateway
upstream is a real TCP server; the client is a real blocking socket. This is
the D1 contract end to end: HTTP passthrough with header injection override,
client credential-prefix stripping, CL+TE 400, Expect stripping,
Connection: close, SSE streaming, WS splice, upstream-unreachable 502, stop()
killing in-flight greenlets, and the update_activity heartbeat.

The gevent-thread scenarios execute in SUBPROCESSES (scripts under
tests/integration/subprocess/): under pytest-xdist workers, killing or
splicing greenlets over native sockets is the #2457 worker-crash class, which
``--timeout-method thread`` cannot interrupt (two CI crashes on this file
before the conversion). This module keeps the helpers the scripts import and
maps every scenario to a thin runner test.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading

import pytest

from app.services.webui_sandbox import SandboxedWebuiLauncher, SandboxWebuiProxy

pytestmark = [pytest.mark.issue(3378)]

_INJECT = {
    "OpenSandbox-Secure-Access": "tok-upstream",
    "OpenSandbox-Ingress-To": "sb-1-3100",
}


class _Gateway:
    """A tiny real TCP 'gateway' that records requests and answers scripted."""

    def __init__(self):
        self.requests: list[tuple[str, dict[str, str], bytes]] = []
        self.mode = "http"  # http | sse | ws | hang | slow-stream | garbage | unauthorized
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while True:
            try:
                conn, _addr = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket):
        conn.settimeout(10)
        try:
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                data += chunk
            head, _, rest = data.partition(b"\r\n\r\n")
            lines = head.decode().split("\r\n")
            headers = {}
            for line in lines[1:]:
                name, _, value = line.partition(":")
                headers[name.strip().lower()] = value.strip()
            if "content-length" in headers:
                need = int(headers["content-length"])
                while len(rest) < need:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    rest += chunk
            self.requests.append((lines[0], headers, rest))
            if self.mode == "ws":
                conn.sendall(
                    b"HTTP/1.1 101 Switching Protocols\r\n"
                    b"Upgrade: websocket\r\n"
                    b"Connection: Upgrade\r\n"
                    b"Sec-WebSocket-Accept: fixed\r\n"
                    b"OpenSandbox-Leak: nope\r\n"
                    b"\r\n"
                )
                # Echo server for the splice test.
                while True:
                    chunk = conn.recv(65536)
                    if not chunk:
                        return
                    conn.sendall(b"up:" + chunk)
            elif self.mode == "sse":
                conn.sendall(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: text/event-stream\r\n"
                    b"Connection: close\r\n"
                    b"\r\n"
                )
                for index in range(3):
                    conn.sendall(f"data: chunk-{index}\n\n".encode())
                conn.close()
                return
            elif self.mode == "slow-stream":
                # M2: body chunks with a mid-stream idle gap LONGER than the
                # proxy's connect/head budget — the stream must survive it.
                import time

                conn.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n"
                )
                conn.sendall(b"data-1\n")
                time.sleep(0.3)
                conn.sendall(b"data-2\n")
                time.sleep(1.5)  # > the 0.5s budgets the test configures
                conn.sendall(b"data-3\n")
                conn.close()
                return
            elif self.mode == "garbage":
                # m2: an upstream head that does not parse (no colon).
                conn.sendall(b"HTTP/1.1 200 OK\r\nno-colon-here\r\n\r\n")
                conn.close()
                return
            elif self.mode == "lf-header":
                # T-A: a header VALUE carrying a bare LF. The proxy parses the
                # head strictly (CRLF-only framing); on re-serialization a
                # lenient parser would read the second line as its own
                # (attacker-chosen) header — so the whole head must be refused
                # (502), never relayed.
                conn.sendall(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: text/plain\r\n"
                    b"Set-Cookie: a=1\nSet-Cookie: session=attacker\r\n"
                    b"Content-Length: 2\r\n"
                    b"Connection: close\r\n\r\n"
                    b"ok"
                )
                conn.close()
                return
            elif self.mode == "unauthorized":
                # m6a: the webui answers, but the token does not validate.
                conn.sendall(
                    b"HTTP/1.1 401 Unauthorized\r\n"
                    b"Content-Type: application/json\r\n"
                    b"Content-Length: 0\r\n"
                    b"Connection: close\r\n\r\n"
                )
                conn.close()
                return
            elif self.mode == "hang":
                # Never answer; holds the client greenlet open for stop().
                import time

                time.sleep(30)
                return
            else:
                body = b'{"version":"ok"}'
                conn.sendall(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: application/json\r\n"
                    + f"Content-Length: {len(body)}\r\n".encode()
                    + b"OpenSandbox-Leak: nope\r\n"
                    + b"OpenSandbox-Secure-Access: keepme\r\n"
                    + b"Connection: close\r\n\r\n"
                    + body
                )
                conn.close()
                return
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def close(self):
        self._sock.close()


class _ProxyThread:
    """Run the gevent proxy on a dedicated thread (its own hub)."""

    def __init__(self, gateway: _Gateway, *, resolver=None, on_activity=None, timeouts=None):
        self.activity = 0
        self.proxy = SandboxWebuiProxy(
            sandbox_id="sb-1",
            upstream_resolver=resolver
            or (lambda: (f"http://127.0.0.1:{gateway.port}", dict(_INJECT))),
            on_activity=on_activity or self._bump,
            **(timeouts or {}),
        )
        self.port = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _bump(self):
        self.activity += 1

    def _run(self):
        import gevent

        self.port = self.proxy.start(0)
        while self.proxy.port is not None:
            gevent.sleep(0.05)

    def start(self):
        import gevent

        self._thread.start()
        for _ in range(100):
            gevent.sleep(0)  # let other hubs run if any
            if self.port:
                return self.port
            import time

            time.sleep(0.02)
        raise AssertionError("proxy did not start")

    def stop(self):
        import gevent

        self.proxy.stop()
        gevent.sleep(0)
        self._thread.join(timeout=5)


def _request(port: int, raw: bytes, read_timeout: float = 5.0) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=read_timeout) as sock:
        sock.sendall(raw)
        out = bytearray()
        try:
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                out.extend(chunk)
        except TimeoutError:
            pass
        return bytes(out)


def _get(port: int, path: str, extra_headers: str = "", body: bytes = b"", **kwargs) -> bytes:
    raw = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: localhost:{port}\r\n"
        "User-Agent: test\r\n"
        "Accept: */*\r\n"
        "Expect: 100-continue\r\n"
        "OpenSandbox-Secure-Access: evil-client-token\r\n"
        "OpenSandbox-Metadata-Inject: x\r\n"
        "X-EXECD-Debug: 1\r\n"
        "OPENSANDBOX-Foo: bar\r\n"
        f"{extra_headers}"
        "\r\n"
    ).encode() + body
    return _request(port, raw, **kwargs)


def _ws_upgrade(port: int, connection: str = "Upgrade") -> tuple[socket.socket, bytes, bytes]:
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    sock.sendall(
        (
            f"GET /ws HTTP/1.1\r\n"
            f"Host: localhost:{port}\r\n"
            "Upgrade: websocket\r\n"
            f"Connection: {connection}\r\n"
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        ).encode()
    )
    data = bytearray()
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(65536)
        if not chunk:
            break
        data.extend(chunk)
    head, _, rest = bytes(data).partition(b"\r\n\r\n")
    return sock, head, rest


def _real_launcher() -> SandboxedWebuiLauncher:
    """A launcher wired only for health_check (sockets only — no backend)."""
    return SandboxedWebuiLauncher(
        api_factory=lambda endpoint: None,
        proxy_service_factory=lambda: None,
    )


# ── subprocess runners (#2457 class: gevent+threads crash xdist workers) ─


_SUITE_RESULTS: dict[str, subprocess.CompletedProcess] = {}


def _run_script(script_name: str) -> subprocess.CompletedProcess:
    if script_name not in _SUITE_RESULTS:
        script = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "subprocess",
            script_name,
        )
        _SUITE_RESULTS[script_name] = subprocess.run(
            [sys.executable, script],
            capture_output=True,
            text=True,
            timeout=300,
        )
    return _SUITE_RESULTS[script_name]


def _assert_scenario(script_name: str, scenario: str) -> bool:
    result = _run_script(script_name)
    assert f"SCENARIO {scenario} OK" in result.stdout, (
        f"scenario {scenario!r} did not pass in {script_name}\n"
        f"exit: {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    return True


_SCENARIOS = "webui_sandbox_proxy_scenarios_3378.py"
_WS = "webui_sandbox_proxy_ws_3378.py"


# ── HTTP passthrough + header rules ────────────────────────────────────


@pytest.mark.security
def test_http_passthrough_injects_headers_and_overrides_client():
    assert _assert_scenario(_SCENARIOS, "http_passthrough_injects_headers_and_overrides_client")


def test_post_body_forwarded_with_content_length():
    assert _assert_scenario(_SCENARIOS, "post_body_forwarded_with_content_length")


@pytest.mark.security
def test_content_length_and_transfer_encoding_coexist_is_400():
    assert _assert_scenario(_SCENARIOS, "content_length_and_transfer_encoding_coexist_is_400")


@pytest.mark.security
def test_duplicate_injection_header_is_400():
    assert _assert_scenario(_SCENARIOS, "duplicate_injection_header_is_400")


def test_sse_streams_through_until_upstream_closes():
    assert _assert_scenario(_SCENARIOS, "sse_streams_through_until_upstream_closes")


# ── WebSocket ──────────────────────────────────────────────────────────


def test_websocket_upgrade_and_bidirectional_splice():
    assert _assert_scenario(_WS, "websocket_upgrade_and_bidirectional_splice")


def test_websocket_upgrade_recognizes_comma_list_without_spaces():
    assert _assert_scenario(_WS, "websocket_upgrade_recognizes_comma_list_without_spaces")


# ── failure and lifecycle paths ────────────────────────────────────────


def test_unreachable_upstream_is_502():
    assert _assert_scenario(_SCENARIOS, "unreachable_upstream_is_502")


def test_stop_closes_port_and_terminates_greenlets():
    assert _assert_scenario(_SCENARIOS, "stop_closes_port_and_terminates_greenlets")


# ── M2: the connect budget must not leak into the stream ───────────────


def test_stream_survives_idle_gap_longer_than_connect_budget():
    assert _assert_scenario(_SCENARIOS, "stream_survives_idle_gap_longer_than_connect_budget")


def test_upstream_that_never_answers_is_502_within_head_budget():
    assert _assert_scenario(_SCENARIOS, "upstream_that_never_answers_is_502_within_head_budget")


# ── m2: request-parse errors are 400, upstream garbage is 502 ──────────


def test_malformed_request_head_line_is_400():
    assert _assert_scenario(_SCENARIOS, "malformed_request_head_line_is_400")


@pytest.mark.security
def test_duplicate_content_length_is_400():
    assert _assert_scenario(_SCENARIOS, "duplicate_content_length_is_400")


def test_malformed_upstream_response_head_is_502():
    assert _assert_scenario(_SCENARIOS, "malformed_upstream_response_head_is_502")


# ── m3: chunked request bodies ─────────────────────────────────────────


def test_chunked_request_body_is_dechunked_and_forwarded():
    assert _assert_scenario(_SCENARIOS, "chunked_request_body_is_dechunked_and_forwarded")


def test_negative_chunk_size_is_400():
    assert _assert_scenario(_SCENARIOS, "negative_chunk_size_is_400")


# ── m6a: the REAL launcher.health_check through the real proxy ────────


def test_launcher_health_check_true_through_real_proxy():
    assert _assert_scenario(_SCENARIOS, "launcher_health_check_true_through_real_proxy")


def test_launcher_health_check_false_on_401_from_pod():
    assert _assert_scenario(_SCENARIOS, "launcher_health_check_false_on_401_from_pod")


# ── T-A: bare CR/LF/NUL heads, RFC token names, CL digits ─────────────


@pytest.mark.security
def test_upstream_header_value_bare_lf_is_502():
    """T-A: an upstream header VALUE with a bare LF (injected Set-Cookie)
    must be refused with 502, never relayed to the browser."""
    assert _assert_scenario(_SCENARIOS, "upstream_header_value_bare_lf_is_502")


@pytest.mark.security
def test_request_header_value_bare_lf_is_400():
    """T-A: a client header VALUE with a bare LF attempting to inject an
    OpenSandbox-*/X-EXECD-* header is a 400 before any upstream contact."""
    assert _assert_scenario(_SCENARIOS, "request_header_value_bare_lf_is_400")


@pytest.mark.security
def test_request_line_bare_lf_injection_is_400():
    """T-A: the request LINE is the same injection surface — a bare LF in
    the start line re-serializes into an injected header for a lenient
    upstream, so the whole head is refused (400)."""
    assert _assert_scenario(_SCENARIOS, "request_line_bare_lf_injection_is_400")


@pytest.mark.security
def test_request_header_nul_and_bad_name_are_400():
    """T-A: NUL inside a value, and header names that are not RFC 7230
    tokens, are refused client-side (400)."""
    assert _assert_scenario(_SCENARIOS, "request_header_nul_and_bad_name_are_400")


@pytest.mark.security
def test_content_length_digit_variants_are_400():
    """T-A: Content-Length must be a plain digit run — int()-parseable
    smuggles (-1 / 5_0 / +5 / internal space / empty) are all 400."""
    assert _assert_scenario(_SCENARIOS, "content_length_digit_variants_are_400")


def test_launcher_health_check_false_on_connection_refused():
    """m6a: nothing listening on the proxy port (proxy crashed/stopped) →
    False, not an exception. No proxy is spawned, so this stays in-process."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
    launcher = _real_launcher()
    assert launcher.health_check(proxy_port=dead_port, token="v2:3:1:2:3:4:5") is False
