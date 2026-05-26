"""Tests for TerminalWebSocketMiddleware (issue #557).

After issue #559, the middleware was simplified to a passthrough — terminal
WebSocket handling moved to TerminalWSHandler at the WSGI handler level.
These tests verify the passthrough behavior.
"""

from app.terminal_ws_middleware import TerminalWebSocketMiddleware


class TestPassthrough:
    def test_delegates_to_inner_app(self):
        """All requests are forwarded to the inner app unchanged."""
        called = []

        def inner_app(environ, start_response):
            called.append(environ)
            start_response("200 OK", [("Content-Type", "text/plain")])
            return [b"ok"]

        mw = TerminalWebSocketMiddleware(inner_app)
        environ = {"PATH_INFO": "/api/remote/terminal/irrelevant/ws", "QUERY_STRING": ""}
        result = mw(environ, lambda s, h, e=None: [])
        assert len(called) == 1
        assert result == [b"ok"]

    def test_preserves_environ(self):
        """Environ dict is passed through without modification."""
        received = {}

        def inner_app(environ, start_response):
            received.update(environ)
            start_response("200 OK", [])
            return [b""]

        mw = TerminalWebSocketMiddleware(inner_app)
        environ = {
            "PATH_INFO": "/some/path",
            "QUERY_STRING": "a=1",
            "REQUEST_METHOD": "GET",
        }
        mw(environ, lambda s, h, e=None: [])
        assert received["PATH_INFO"] == "/some/path"
        assert received["QUERY_STRING"] == "a=1"
