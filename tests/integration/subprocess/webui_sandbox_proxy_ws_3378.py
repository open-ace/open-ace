#!/usr/bin/env python3
"""Issue #3378 (§7.4): SandboxWebuiProxy WebSocket splice, run in a subprocess.

The bidirectional WS splice test drives gevent greenlets and native threads in
one process; under pytest-xdist workers that combination is exactly the #2457
failure class (the worker dies in ways --timeout cannot interrupt). The repo's
established pattern is a standalone script executed by a thin pytest runner
(see test_terminal_ws_handler_process.py) — same treatment here.

Run standalone:
    python tests/integration/subprocess/webui_sandbox_proxy_ws_3378.py
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def main() -> int:
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

    scenarios = {
        "websocket_upgrade_and_bidirectional_splice": _scenario_splice,
        "websocket_upgrade_recognizes_comma_list_without_spaces": _scenario_comma_list,
    }
    for name, fn in scenarios.items():
        fn(mod)
        print(f"SCENARIO {name} OK")
    return 0


def _scenario_splice(mod):
    gateway = mod._Gateway()
    gateway.mode = "ws"
    runner = mod._ProxyThread(gateway)
    port = runner.start()
    try:
        sock, head, _rest = mod._ws_upgrade(port)
        with sock:
            assert head.startswith(b"HTTP/1.1 101"), head
            assert b"Sec-WebSocket-Accept: fixed" in head
            assert b"OpenSandbox-Leak" not in head, "unknown prefix header leaked in 101"
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
            assert b"up:ping" in bytes(echoed), "upstream echo did not reach the client"
        assert runner.activity >= 1, "WS upgrade did not feed the activity heartbeat"
    finally:
        runner.stop()
        gateway.close()


def _scenario_comma_list(mod):
    """m7: ``Connection: Upgrade,keep-alive`` (comma, no space) is still an
    upgrade request — tokens are stripped individually."""
    gateway = mod._Gateway()
    gateway.mode = "ws"
    runner = mod._ProxyThread(gateway)
    port = runner.start()
    try:
        sock, head, _rest = mod._ws_upgrade(port, connection="Upgrade,keep-alive")
        with sock:
            assert head.startswith(b"HTTP/1.1 101")
            request_line, headers, _body = gateway.requests[-1]
            assert headers["upgrade"] == "websocket"
            assert headers["connection"].lower() == "upgrade"
    finally:
        runner.stop()
        gateway.close()


if __name__ == "__main__":
    sys.exit(main())
