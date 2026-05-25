"""Tests for TerminalWebSocketMiddleware (issue #557).

The middleware intercepts WebSocket requests to
/api/remote/terminal/<uuid>/ws at the WSGI layer, bypassing Flask routing.
"""

import uuid

from app.modules.workspace.terminal_store import terminal_info_store
from app.terminal_ws_middleware import _WS_PATH_RE, TerminalWebSocketMiddleware

# ---------------------------------------------------------------------------
# Path matching
# ---------------------------------------------------------------------------


class TestPathRegex:
    def test_matches_valid_terminal_ws_path(self):
        tid = str(uuid.uuid4())
        m = _WS_PATH_RE.match(f"/api/remote/terminal/{tid}/ws")
        assert m is not None
        assert m.group(1) == tid

    def test_rejects_non_terminal_path(self):
        assert _WS_PATH_RE.match("/api/remote/agent/ws") is None

    def test_rejects_missing_ws_suffix(self):
        tid = str(uuid.uuid4())
        assert _WS_PATH_RE.match(f"/api/remote/terminal/{tid}/status") is None

    def test_rejects_invalid_uuid(self):
        assert _WS_PATH_RE.match("/api/remote/terminal/not-a-uuid/ws") is None


# ---------------------------------------------------------------------------
# Middleware pass-through
# ---------------------------------------------------------------------------


class TestPassthrough:
    def test_non_ws_environ_passes_through(self):
        """Requests without wsgi.websocket are forwarded to the inner app."""
        called = []

        def inner_app(environ, start_response):
            called.append(True)
            start_response("200 OK", [("Content-Type", "text/plain")])
            return [b"ok"]

        mw = TerminalWebSocketMiddleware(inner_app)
        environ = {"PATH_INFO": "/api/remote/terminal/irrelevant/ws", "QUERY_STRING": ""}
        result = mw(environ, lambda s, h, e=None: [])
        assert called == [True]
        assert result == [b"ok"]

    def test_non_terminal_ws_path_passes_through(self):
        """WebSocket requests to other paths are forwarded to the inner app."""
        called = []

        def inner_app(environ, start_response):
            called.append(True)
            start_response("200 OK", [("Content-Type", "text/plain")])
            return [b"ok"]

        class FakeWS:
            pass

        mw = TerminalWebSocketMiddleware(inner_app)
        environ = {
            "PATH_INFO": "/api/remote/agent/ws",
            "QUERY_STRING": "",
            "wsgi.websocket": FakeWS(),
        }
        mw(environ, lambda s, h, e=None: [])
        assert called == [True]

    def test_ws_terminal_path_intercepted(self):
        """WebSocket requests to terminal/<uuid>/ws are NOT forwarded to Flask."""
        called = []

        def inner_app(environ, start_response):
            called.append(True)
            return [b"should not be called"]

        tid = str(uuid.uuid4())
        machine_id = "mw-test-machine"

        closed_with = {}

        class FakeWS:
            def close(self, code=1000, reason=""):
                closed_with["code"] = code
                closed_with["reason"] = reason

        # Store terminal info so the middleware can find it
        terminal_info_store.put(
            machine_id,
            tid,
            {
                "status": "running",
                "token": "test-browser-token",
                "original_ws_url": "ws://1.2.3.4:9999",
                "original_token": "remote-token",
            },
        )

        try:
            mw = TerminalWebSocketMiddleware(inner_app)
            environ = {
                "PATH_INFO": f"/api/remote/terminal/{tid}/ws",
                "QUERY_STRING": "token=test-browser-token",
                "wsgi.websocket": FakeWS(),
            }

            status_holder = {}

            def start_response(status, headers, exc_info=None):
                status_holder["status"] = status

            # The middleware will try to bridge, which will fail to connect
            # to ws://1.2.3.4:9999.  We just verify it does NOT call inner_app.
            mw(environ, start_response)
            # inner_app should not have been called (middleware handled it)
            assert called == []
        finally:
            terminal_info_store.pop(machine_id, tid)

    def test_ws_terminal_unknown_terminal_closes_1011(self):
        """Unknown terminal_id results in close code 1011."""
        called = []

        def inner_app(environ, start_response):
            called.append(True)
            return [b"nope"]

        tid = str(uuid.uuid4())

        closed_with = {}

        class FakeWS:
            def close(self, code=1000, reason=""):
                closed_with["code"] = code
                closed_with["reason"] = reason

        mw = TerminalWebSocketMiddleware(inner_app)
        environ = {
            "PATH_INFO": f"/api/remote/terminal/{tid}/ws",
            "QUERY_STRING": "token=whatever",
            "wsgi.websocket": FakeWS(),
        }

        status_holder = {}

        def start_response(status, headers, exc_info=None):
            status_holder["status"] = status

        mw(environ, start_response)
        assert called == []
        assert closed_with.get("code") == 1011

    def test_ws_terminal_bad_token_closes_4001(self):
        """Invalid token results in close code 4001."""
        tid = str(uuid.uuid4())
        machine_id = "mw-bad-token"

        closed_with = {}

        class FakeWS:
            def close(self, code=1000, reason=""):
                closed_with["code"] = code
                closed_with["reason"] = reason

        terminal_info_store.put(
            machine_id,
            tid,
            {
                "status": "running",
                "token": "correct-token",
                "original_ws_url": "ws://1.2.3.4:9999",
                "original_token": "remote-token",
            },
        )

        try:
            mw = TerminalWebSocketMiddleware(lambda e, sr: [b"nope"])
            environ = {
                "PATH_INFO": f"/api/remote/terminal/{tid}/ws",
                "QUERY_STRING": "token=wrong-token",
                "wsgi.websocket": FakeWS(),
            }

            mw(environ, lambda s, h, e=None: [])
            assert closed_with.get("code") == 4001
        finally:
            terminal_info_store.pop(machine_id, tid)

    def test_ws_terminal_relative_url_closes_1011(self):
        """Relative remote URL results in close code 1011."""
        tid = str(uuid.uuid4())
        machine_id = "mw-rel-url"

        closed_with = {}

        class FakeWS:
            def close(self, code=1000, reason=""):
                closed_with["code"] = code

        terminal_info_store.put(
            machine_id,
            tid,
            {
                "status": "running",
                "token": "tok",
                "original_ws_url": "/relative/path",
                "original_token": "remote-token",
            },
        )

        try:
            mw = TerminalWebSocketMiddleware(lambda e, sr: [b"nope"])
            environ = {
                "PATH_INFO": f"/api/remote/terminal/{tid}/ws",
                "QUERY_STRING": "token=tok",
                "wsgi.websocket": FakeWS(),
            }

            mw(environ, lambda s, h, e=None: [])
            assert closed_with.get("code") == 1011
        finally:
            terminal_info_store.pop(machine_id, tid)
