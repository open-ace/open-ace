#!/usr/bin/env python3
"""Issue #3378 (§7.4): SandboxWebuiProxy scenarios, run in a subprocess.

Every scenario here spawns the proxy's gevent hub on a dedicated thread and
several drive greenlet kills or splices — under pytest-xdist workers that
combination is the #2457 crash class (the worker dies in ways --timeout
cannot interrupt; observed on CI for the WS splice and the stop() test). The
repo's established pattern is a standalone script executed by thin pytest
runners (see test_terminal_ws_handler_process.py) — same treatment here.

Each scenario prints ``SCENARIO <name> OK``; a failure aborts the run with a
traceback and a nonzero exit, and the missing markers localize the crash.

Run standalone:
    python tests/integration/subprocess/webui_sandbox_proxy_scenarios_3378.py
"""

import os
import socket
import sys
import traceback

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def _load_test_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "proxy_3378_test_module",
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "test_webui_sandbox_proxy_3378.py",
        ),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _with_fresh_gateway(mod, body):
    """Pytest gave every test a fresh gateway fixture; mirror that here so
    request-recording assertions are not polluted by earlier scenarios."""

    def run():
        gateway = mod._Gateway()
        try:
            body(gateway)
        finally:
            gateway.close()

    return run


def main() -> int:
    mod = _load_test_module()
    scenarios = {name: _with_fresh_gateway(mod, fn) for name, fn in _build_scenarios(mod).items()}
    for name, fn in scenarios.items():
        try:
            fn()
        except Exception:
            print(f"SCENARIO {name} FAILED", flush=True)
            traceback.print_exc()
            return 1
        print(f"SCENARIO {name} OK", flush=True)
    return 0


def _build_scenarios(mod):
    """Scenario name → zero-arg callable (bodies mirror the pytest tests)."""

    def http_passthrough_injects_headers_and_overrides_client(gateway):
        runner = mod._ProxyThread(gateway)
        port = runner.start()
        try:
            response = mod._get(port, "/api/version?token=v2:1:2:3:4:5")
            assert response.startswith(b"HTTP/1.1 200 OK")
            assert b'{"version":"ok"}' in response
            assert b"Connection: close" in response
            assert b"OpenSandbox-Leak" not in response
            assert b"OpenSandbox-Secure-Access: keepme" in response
            request_line, headers, _body = gateway.requests[-1]
            assert request_line.startswith("GET /api/version?token=v2:1:2:3:4:5")
            assert headers["opensandbox-secure-access"] == "tok-upstream"
            assert headers["opensandbox-ingress-to"] == "sb-1-3100"
            assert "opensandbox-metadata-inject" not in headers
            assert "x-execd-debug" not in headers
            assert "opensandbox-foo" not in headers
            assert "expect" not in headers
            assert runner.activity == 1
        finally:
            runner.stop()

    def post_body_forwarded_with_content_length(gateway):
        runner = mod._ProxyThread(gateway)
        port = runner.start()
        try:
            body = b'{"message":"hello"}'
            raw = (
                f"POST /api/chat HTTP/1.1\r\n"
                f"Host: localhost:{port}\r\n"
                f"Content-Length: {len(body)}\r\n"
                "\r\n"
            ).encode() + body
            response = mod._request(port, raw)
            assert b"HTTP/1.1 200 OK" in response
            request_line, headers, forwarded_body = gateway.requests[-1]
            assert request_line.startswith("POST /api/chat")
            assert headers["content-length"] == str(len(body))
            assert forwarded_body == body
        finally:
            runner.stop()

    def content_length_and_transfer_encoding_coexist_is_400(gateway):
        runner = mod._ProxyThread(gateway)
        port = runner.start()
        try:
            response = mod._get(
                port,
                "/api/version",
                extra_headers="Content-Length: 3\r\nTransfer-Encoding: chunked\r\n",
            )
            assert response.startswith(b"HTTP/1.1 400")
            assert gateway.requests == []
            assert runner.activity == 0
        finally:
            runner.stop()

    def duplicate_injection_header_is_400(gateway):
        runner = mod._ProxyThread(gateway)
        port = runner.start()
        try:
            response = mod._get(
                port,
                "/api/version",
                extra_headers="X-EXECD-ACCESS-TOKEN: a\r\nX-EXECD-ACCESS-TOKEN: b\r\n",
            )
            assert response.startswith(b"HTTP/1.1 400")
            assert gateway.requests == []
        finally:
            runner.stop()

    def sse_streams_through_until_upstream_closes(gateway):
        gateway.mode = "sse"
        runner = mod._ProxyThread(gateway)
        port = runner.start()
        try:
            response = mod._get(port, "/events", read_timeout=10)
            assert response.startswith(b"HTTP/1.1 200 OK")
            assert b"text/event-stream" in response
            for index in range(3):
                assert f"data: chunk-{index}".encode() in response
        finally:
            runner.stop()
            gateway.mode = "http"

    def unreachable_upstream_is_502(gateway):
        def dead_resolver():
            return ("http://127.0.0.1:1", dict(mod._INJECT))

        runner = mod._ProxyThread(gateway, resolver=dead_resolver)
        port = runner.start()
        try:
            response = mod._get(port, "/api/version")
            assert response.startswith(b"HTTP/1.1 502")
            assert runner.activity == 0
        finally:
            runner.stop()

    def stop_closes_port_and_terminates_greenlets(gateway):
        import time

        gateway.mode = "hang"
        runner = mod._ProxyThread(gateway)
        port = runner.start()
        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        client.sendall(b"GET /hang HTTP/1.1\r\nHost: x\r\n\r\n")
        time.sleep(0.3)
        runner.proxy.stop()
        client.settimeout(5)
        try:
            chunk = client.recv(65536)
            assert chunk == b"" or chunk
        except OSError:
            pass
        finally:
            client.close()
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            raise AssertionError("port still open after stop()")
        except OSError:
            pass
        runner._thread.join(timeout=5)
        assert not runner._thread.is_alive()
        gateway.mode = "http"

    def stream_survives_idle_gap_longer_than_connect_budget(gateway):
        gateway.mode = "slow-stream"
        runner = mod._ProxyThread(
            gateway,
            timeouts={
                "upstream_connect_timeout_seconds": 0.5,
                "upstream_head_timeout_seconds": 0.5,
            },
        )
        port = runner.start()
        try:
            response = mod._get(port, "/stream", read_timeout=10)
            assert response.startswith(b"HTTP/1.1 200 OK")
            for marker in (b"data-1", b"data-2", b"data-3"):
                assert marker in response
        finally:
            runner.stop()
            gateway.mode = "http"

    def upstream_that_never_answers_is_502_within_head_budget(gateway):
        gateway.mode = "hang"
        runner = mod._ProxyThread(gateway, timeouts={"upstream_head_timeout_seconds": 0.5})
        port = runner.start()
        try:
            response = mod._get(port, "/hang", read_timeout=5)
            assert response.startswith(b"HTTP/1.1 502")
        finally:
            runner.stop()
            gateway.mode = "http"

    def malformed_request_head_line_is_400(gateway):
        runner = mod._ProxyThread(gateway)
        port = runner.start()
        try:
            raw = b"GET / HTTP/1.1\r\nHost: x\r\nno-colon-line\r\n\r\n"
            response = mod._request(port, raw)
            assert response.startswith(b"HTTP/1.1 400")
            assert gateway.requests == []
        finally:
            runner.stop()

    def duplicate_content_length_is_400(gateway):
        runner = mod._ProxyThread(gateway)
        port = runner.start()
        try:
            response = mod._get(
                port,
                "/api/version",
                extra_headers="Content-Length: 3\r\nContent-Length: 4\r\n",
            )
            assert response.startswith(b"HTTP/1.1 400")
            assert gateway.requests == []
        finally:
            runner.stop()

    def malformed_upstream_response_head_is_502(gateway):
        gateway.mode = "garbage"
        runner = mod._ProxyThread(gateway)
        port = runner.start()
        try:
            response = mod._get(port, "/api/version")
            assert response.startswith(b"HTTP/1.1 502")
        finally:
            runner.stop()
            gateway.mode = "http"

    def chunked_request_body_is_dechunked_and_forwarded(gateway):
        runner = mod._ProxyThread(gateway)
        port = runner.start()
        try:
            chunked = b"5\r\nhello\r\ne\r\n chunked world\r\n0\r\n\r\n"
            raw = (
                f"POST /api/chat HTTP/1.1\r\n"
                f"Host: localhost:{port}\r\n"
                "Transfer-Encoding: chunked\r\n"
                "\r\n"
            ).encode() + chunked
            response = mod._request(port, raw)
            assert b"HTTP/1.1 200 OK" in response
            request_line, headers, forwarded_body = gateway.requests[-1]
            assert request_line.startswith("POST /api/chat")
            assert forwarded_body == b"hello chunked world"
            assert headers["content-length"] == "19"
            assert "transfer-encoding" not in headers
        finally:
            runner.stop()

    def negative_chunk_size_is_400(gateway):
        runner = mod._ProxyThread(gateway)
        port = runner.start()
        try:
            raw = (
                f"POST /api/chat HTTP/1.1\r\n"
                f"Host: localhost:{port}\r\n"
                "Transfer-Encoding: chunked\r\n"
                "\r\n"
            ).encode() + b"-5\r\nhello"
            response = mod._request(port, raw)
            assert response.startswith(b"HTTP/1.1 400")
            assert gateway.requests == []
        finally:
            runner.stop()

    def launcher_health_check_true_through_real_proxy(gateway):
        launcher = mod._real_launcher()
        runner = mod._ProxyThread(gateway)
        port = runner.start()
        try:
            assert launcher.health_check(proxy_port=port, token="v2:3:%d:1:2:abcdef" % port) is True
            request_line, headers, _body = gateway.requests[-1]
            assert request_line.startswith("GET /api/version?token=v2%3A3%3A")
            assert headers["connection"] == "close"
        finally:
            runner.stop()

    def launcher_health_check_false_on_401_from_pod(gateway):
        gateway.mode = "unauthorized"
        launcher = mod._real_launcher()
        runner = mod._ProxyThread(gateway)
        port = runner.start()
        try:
            assert launcher.health_check(proxy_port=port, token="v2:3:1:2:3:4:5") is False
        finally:
            runner.stop()
            gateway.mode = "http"

    return {
        "http_passthrough_injects_headers_and_overrides_client": (
            http_passthrough_injects_headers_and_overrides_client
        ),
        "post_body_forwarded_with_content_length": post_body_forwarded_with_content_length,
        "content_length_and_transfer_encoding_coexist_is_400": (
            content_length_and_transfer_encoding_coexist_is_400
        ),
        "duplicate_injection_header_is_400": duplicate_injection_header_is_400,
        "sse_streams_through_until_upstream_closes": sse_streams_through_until_upstream_closes,
        "unreachable_upstream_is_502": unreachable_upstream_is_502,
        "stop_closes_port_and_terminates_greenlets": stop_closes_port_and_terminates_greenlets,
        "stream_survives_idle_gap_longer_than_connect_budget": (
            stream_survives_idle_gap_longer_than_connect_budget
        ),
        "upstream_that_never_answers_is_502_within_head_budget": (
            upstream_that_never_answers_is_502_within_head_budget
        ),
        "malformed_request_head_line_is_400": malformed_request_head_line_is_400,
        "duplicate_content_length_is_400": duplicate_content_length_is_400,
        "malformed_upstream_response_head_is_502": malformed_upstream_response_head_is_502,
        "chunked_request_body_is_dechunked_and_forwarded": (
            chunked_request_body_is_dechunked_and_forwarded
        ),
        "negative_chunk_size_is_400": negative_chunk_size_is_400,
        "launcher_health_check_true_through_real_proxy": (
            launcher_health_check_true_through_real_proxy
        ),
        "launcher_health_check_false_on_401_from_pod": (
            launcher_health_check_false_on_401_from_pod
        ),
    }


if __name__ == "__main__":
    sys.exit(main())
