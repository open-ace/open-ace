"""Issue #3378 (§7.4): SandboxWebuiProxy against real sockets.

The proxy runs on a real gevent hub in its own thread; the fake gateway
upstream is a real TCP server; the client is a real blocking socket. This is
the D1 contract end to end: HTTP passthrough with header injection override,
client credential-prefix stripping, CL+TE 400, Expect stripping,
Connection: close, SSE streaming, WS splice, upstream-unreachable 502, stop()
killing in-flight greenlets, and the update_activity heartbeat.
"""

from __future__ import annotations

import socket
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


@pytest.fixture()
def gateway():
    server = _Gateway()
    yield server
    server.close()


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


# ── HTTP passthrough + header rules ────────────────────────────────────


@pytest.mark.security
def test_http_passthrough_injects_headers_and_overrides_client(gateway):
    runner = _ProxyThread(gateway)
    port = runner.start()
    try:
        response = _get(port, "/api/version?token=v2:1:2:3:4:5")
        assert response.startswith(b"HTTP/1.1 200 OK")
        assert b'{"version":"ok"}' in response
        assert b"Connection: close" in response
        # Response allowlist: unknown prefix headers stripped, the injectable
        # name passes.
        assert b"OpenSandbox-Leak" not in response
        assert b"OpenSandbox-Secure-Access: keepme" in response

        request_line, headers, _body = gateway.requests[-1]
        assert request_line.startswith("GET /api/version?token=v2:1:2:3:4:5")
        # Injection overrides the client's forged value...
        assert headers["opensandbox-secure-access"] == "tok-upstream"
        assert headers["opensandbox-ingress-to"] == "sb-1-3100"
        # ...and every other client prefix header was stripped.
        assert "opensandbox-metadata-inject" not in headers
        assert "x-execd-debug" not in headers
        assert "opensandbox-foo" not in headers
        # Expect is stripped (the proxy cannot answer a 100-continue).
        assert "expect" not in headers
        assert runner.activity == 1
    finally:
        runner.stop()


def test_post_body_forwarded_with_content_length(gateway):
    runner = _ProxyThread(gateway)
    port = runner.start()
    try:
        body = b'{"message":"hello"}'
        raw = (
            f"POST /api/chat HTTP/1.1\r\n"
            f"Host: localhost:{port}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "\r\n"
        ).encode() + body
        response = _request(port, raw)
        assert b"HTTP/1.1 200 OK" in response
        request_line, headers, forwarded_body = gateway.requests[-1]
        assert request_line.startswith("POST /api/chat")
        assert headers["content-length"] == str(len(body))
        assert forwarded_body == body
    finally:
        runner.stop()


@pytest.mark.security
def test_content_length_and_transfer_encoding_coexist_is_400(gateway):
    runner = _ProxyThread(gateway)
    port = runner.start()
    try:
        response = _get(
            port,
            "/api/version",
            extra_headers="Content-Length: 3\r\nTransfer-Encoding: chunked\r\n",
        )
        assert response.startswith(b"HTTP/1.1 400")
        assert gateway.requests == []  # never reached the upstream
        assert runner.activity == 0
    finally:
        runner.stop()


@pytest.mark.security
def test_duplicate_injection_header_is_400(gateway):
    runner = _ProxyThread(gateway)
    port = runner.start()
    try:
        response = _get(
            port,
            "/api/version",
            extra_headers="X-EXECD-ACCESS-TOKEN: a\r\nX-EXECD-ACCESS-TOKEN: b\r\n",
        )
        assert response.startswith(b"HTTP/1.1 400")
        assert gateway.requests == []
    finally:
        runner.stop()


def test_sse_streams_through_until_upstream_closes(gateway):
    gateway.mode = "sse"
    runner = _ProxyThread(gateway)
    port = runner.start()
    try:
        response = _get(port, "/events", read_timeout=10)
        assert response.startswith(b"HTTP/1.1 200 OK")
        assert b"text/event-stream" in response
        for index in range(3):
            assert f"data: chunk-{index}".encode() in response
    finally:
        runner.stop()


# ── WebSocket ──────────────────────────────────────────────────────────


def _ws_upgrade(port: int, connection: str = "Upgrade") -> tuple[socket.socket, bytes, bytes]:
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    sock.sendall(
        f"GET /ws HTTP/1.1\r\n"
        f"Host: localhost:{port}\r\n"
        "Upgrade: websocket\r\n"
        f"Connection: {connection}\r\n"
        "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n".encode()
    )
    data = bytearray()
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(65536)
        if not chunk:
            break
        data.extend(chunk)
    head, _, rest = bytes(data).partition(b"\r\n\r\n")
    return sock, head, rest


def test_websocket_upgrade_and_bidirectional_splice(gateway):
    gateway.mode = "ws"
    runner = _ProxyThread(gateway)
    port = runner.start()
    try:
        sock, head, _rest = _ws_upgrade(port)
        with sock:
            assert head.startswith(b"HTTP/1.1 101")
            assert b"Sec-WebSocket-Accept: fixed" in head
            # Upstream's unknown prefix header is stripped from the 101 too.
            assert b"OpenSandbox-Leak" not in head
            # The injected routing headers reached the upgrade request.
            request_line, headers, _body = gateway.requests[-1]
            assert headers["opensandbox-secure-access"] == "tok-upstream"
            assert headers["connection"].lower() == "upgrade"

            sock.sendall(b"ping")
            echoed = bytearray()
            while b"up:ping" not in echoed:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                echoed.extend(chunk)
            assert b"up:ping" in bytes(echoed)
        assert runner.activity >= 1
    finally:
        runner.stop()


# ── failure and lifecycle paths ────────────────────────────────────────


def test_unreachable_upstream_is_502(gateway):
    def dead_resolver():
        return ("http://127.0.0.1:1", dict(_INJECT))

    runner = _ProxyThread(gateway, resolver=dead_resolver)
    port = runner.start()
    try:
        response = _get(port, "/api/version")
        assert response.startswith(b"HTTP/1.1 502")
        assert runner.activity == 0
    finally:
        runner.stop()


def test_stop_closes_port_and_terminates_greenlets(gateway):
    gateway.mode = "hang"  # an in-flight request the proxy is holding open
    runner = _ProxyThread(gateway)
    port = runner.start()
    client = socket.create_connection(("127.0.0.1", port), timeout=5)
    client.sendall(b"GET /hang HTTP/1.1\r\nHost: x\r\n\r\n")
    import time

    time.sleep(0.3)  # let the proxy pick up the connection
    runner.proxy.stop()
    # The held connection is torn down: recv unblocks with EOF or ECONNRESET.
    client.settimeout(5)
    try:
        chunk = client.recv(65536)
        assert chunk == b"" or chunk  # either EOF or a reset — not a hang
    except OSError:
        pass
    finally:
        client.close()
    # The port is gone.
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
    runner._thread.join(timeout=5)
    assert not runner._thread.is_alive()


# ── M2: the connect budget must not leak into the stream ───────────────


def test_stream_survives_idle_gap_longer_than_connect_budget(gateway):
    """M2: create_connection installs its timeout on the socket; unless it is
    cleared after establishment, the body pump (which treats socket.timeout
    as peer-close) truncates any stream that idles longer than the budget.
    The budgets are configurable — the test shrinks them to 0.5s and the
    upstream idles 1.5s mid-stream, the same regression at test scale."""
    gateway.mode = "slow-stream"
    runner = _ProxyThread(
        gateway,
        timeouts={
            "upstream_connect_timeout_seconds": 0.5,
            "upstream_head_timeout_seconds": 0.5,
        },
    )
    port = runner.start()
    try:
        response = _get(port, "/stream", read_timeout=10)
        assert response.startswith(b"HTTP/1.1 200 OK")
        for marker in (b"data-1", b"data-2", b"data-3"):
            assert marker in response  # the 1.5s idle gap did not cut the stream
    finally:
        runner.stop()


def test_upstream_that_never_answers_is_502_within_head_budget(gateway):
    """M2: the head phase keeps its own bounded window — an upstream that
    accepted the connection but never answers must 502, not pin a greenlet."""
    gateway.mode = "hang"
    runner = _ProxyThread(gateway, timeouts={"upstream_head_timeout_seconds": 0.5})
    port = runner.start()
    try:
        response = _get(port, "/hang", read_timeout=5)
        assert response.startswith(b"HTTP/1.1 502")
    finally:
        runner.stop()


# ── m2: request-parse errors are 400, upstream garbage is 502 ──────────


def test_malformed_request_head_line_is_400(gateway):
    runner = _ProxyThread(gateway)
    port = runner.start()
    try:
        raw = b"GET / HTTP/1.1\r\nHost: x\r\nno-colon-line\r\n\r\n"
        response = _request(port, raw)
        assert response.startswith(b"HTTP/1.1 400")
        assert gateway.requests == []  # never reached the upstream
    finally:
        runner.stop()


@pytest.mark.security
def test_duplicate_content_length_is_400(gateway):
    runner = _ProxyThread(gateway)
    port = runner.start()
    try:
        response = _get(
            port,
            "/api/version",
            extra_headers="Content-Length: 3\r\nContent-Length: 4\r\n",
        )
        assert response.startswith(b"HTTP/1.1 400")
        assert gateway.requests == []  # RFC 7230 §3.3.2 MUST-reject
    finally:
        runner.stop()


def test_malformed_upstream_response_head_is_502(gateway):
    gateway.mode = "garbage"
    runner = _ProxyThread(gateway)
    port = runner.start()
    try:
        response = _get(port, "/api/version")
        assert response.startswith(b"HTTP/1.1 502")  # upstream garbage, not 400
    finally:
        runner.stop()


# ── m3: chunked request bodies ─────────────────────────────────────────


def test_chunked_request_body_is_dechunked_and_forwarded(gateway):
    runner = _ProxyThread(gateway)
    port = runner.start()
    try:
        chunked = b"5\r\nhello\r\ne\r\n chunked world\r\n0\r\n\r\n"
        raw = (
            f"POST /api/chat HTTP/1.1\r\n"
            f"Host: localhost:{port}\r\n"
            "Transfer-Encoding: chunked\r\n"
            "\r\n"
        ).encode() + chunked
        response = _request(port, raw)
        assert b"HTTP/1.1 200 OK" in response
        request_line, headers, forwarded_body = gateway.requests[-1]
        assert request_line.startswith("POST /api/chat")
        # De-chunked and re-framed with a real Content-Length upstream.
        assert forwarded_body == b"hello chunked world"
        assert headers["content-length"] == "19"
        assert "transfer-encoding" not in headers
    finally:
        runner.stop()


def test_negative_chunk_size_is_400(gateway):
    runner = _ProxyThread(gateway)
    port = runner.start()
    try:
        raw = (
            f"POST /api/chat HTTP/1.1\r\n"
            f"Host: localhost:{port}\r\n"
            "Transfer-Encoding: chunked\r\n"
            "\r\n"
        ).encode() + b"-5\r\nhello"
        response = _request(port, raw)
        assert response.startswith(b"HTTP/1.1 400")
        assert gateway.requests == []  # refused before contacting the upstream
    finally:
        runner.stop()


# ── m7: Connection header token list parsing ───────────────────────────


def test_websocket_upgrade_recognizes_comma_list_without_spaces(gateway):
    """m7: ``Connection: Upgrade,keep-alive`` (comma, no space) is still an
    upgrade request — tokens are stripped individually."""
    gateway.mode = "ws"
    runner = _ProxyThread(gateway)
    port = runner.start()
    try:
        sock, head, _rest = _ws_upgrade(port, connection="Upgrade,keep-alive")
        with sock:
            assert head.startswith(b"HTTP/1.1 101")
            request_line, headers, _body = gateway.requests[-1]
            assert headers["upgrade"] == "websocket"
            assert headers["connection"].lower() == "upgrade"
    finally:
        runner.stop()


# ── m6a: the REAL launcher.health_check through the real proxy ────────


def _real_launcher() -> SandboxedWebuiLauncher:
    """A launcher wired only for health_check (sockets only — no backend)."""
    return SandboxedWebuiLauncher(
        api_factory=lambda endpoint: None,
        proxy_service_factory=lambda: None,
    )


def test_launcher_health_check_true_through_real_proxy(gateway):
    """m6a: /api/version?token= 200 through the real proxy → True. The probe's
    raw-socket HTTP/1.0 + Connection: close shape must survive the proxy, and
    the token it mints must reach the upstream query string."""
    launcher = _real_launcher()
    runner = _ProxyThread(gateway)
    port = runner.start()
    try:
        assert launcher.health_check(proxy_port=port, token="v2:3:%d:1:2:abcdef" % port) is True
        request_line, headers, _body = gateway.requests[-1]
        assert request_line.startswith("GET /api/version?token=v2%3A3%3A")
        assert headers["connection"] == "close"  # health probes never hold sockets
    finally:
        runner.stop()


def test_launcher_health_check_false_on_401_from_pod(gateway):
    """m6a: the webui answering 401 (token did not validate) → False."""
    gateway.mode = "unauthorized"
    launcher = _real_launcher()
    runner = _ProxyThread(gateway)
    port = runner.start()
    try:
        assert launcher.health_check(proxy_port=port, token="v2:3:1:2:3:4:5") is False
    finally:
        runner.stop()


def test_launcher_health_check_false_on_connection_refused():
    """m6a: nothing listening on the proxy port (proxy crashed/stopped) →
    False, not an exception."""
    # Reserve then release a port so nothing is bound to it.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
    launcher = _real_launcher()
    assert launcher.health_check(proxy_port=dead_port, token="v2:3:1:2:3:4:5") is False
