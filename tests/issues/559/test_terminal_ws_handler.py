"""Unit tests for app.terminal_ws_handler — custom gevent WSGI handler."""

import re
from unittest.mock import MagicMock, patch

import pytest

from app.terminal_ws_handler import _WS_PATH_RE, TerminalWSHandler

# ---------------------------------------------------------------------------
# Tests: _WS_PATH_RE
# ---------------------------------------------------------------------------


class TestWSPathRegex:
    def test_valid_terminal_ws_path(self):
        m = _WS_PATH_RE.match("/api/remote/terminal/12345678-1234-1234-1234-123456789abc/ws")
        assert m is not None
        assert m.group(1) == "12345678-1234-1234-1234-123456789abc"

    def test_uppercase_uuid(self):
        m = _WS_PATH_RE.match("/api/remote/terminal/ABCDEF12-1234-1234-1234-ABCDEF123456/WS")
        assert m is not None

    def test_mixed_case_uuid(self):
        m = _WS_PATH_RE.match("/api/remote/terminal/aBcDeF12-1234-1234-1234-123456789AbC/ws")
        assert m is not None

    @pytest.mark.parametrize(
        "path",
        [
            "/api/remote/terminal/not-a-uuid/ws",
            "/api/remote/terminal/12345678-1234-1234-1234-123456789abc",  # no /ws
            "/api/remote/terminal/12345678-1234-1234-1234-123456789abc/ws/extra",
            "/api/remote/terminal/",  # empty uuid
            "/other/path",
            "/api/remote/terminal/12345678-1234-1234-1234/ws",  # too short
        ],
    )
    def test_invalid_paths(self, path):
        assert _WS_PATH_RE.match(path) is None


# ---------------------------------------------------------------------------
# Tests: _is_terminal_ws_request
# ---------------------------------------------------------------------------


class TestIsTerminalWsRequest:
    def _make_handler(self, command="GET", path="", upgrade="websocket"):
        handler = MagicMock(spec=TerminalWSHandler)
        handler.command = command
        handler.environ = {
            "PATH_INFO": path,
            "HTTP_UPGRADE": upgrade,
        }
        return TerminalWSHandler._is_terminal_ws_request(handler)

    def test_valid_terminal_ws(self):
        assert self._make_handler(
            command="GET",
            path="/api/remote/terminal/12345678-1234-1234-1234-123456789abc/ws",
            upgrade="websocket",
        )

    def test_post_method_rejected(self):
        assert not self._make_handler(
            command="POST",
            path="/api/remote/terminal/12345678-1234-1234-1234-123456789abc/ws",
            upgrade="websocket",
        )

    def test_no_upgrade_header_rejected(self):
        assert not self._make_handler(
            command="GET",
            path="/api/remote/terminal/12345678-1234-1234-1234-123456789abc/ws",
            upgrade="",
        )

    def test_wrong_upgrade_rejected(self):
        assert not self._make_handler(
            command="GET",
            path="/api/remote/terminal/12345678-1234-1234-1234-123456789abc/ws",
            upgrade="h2c",
        )

    def test_wrong_path_rejected(self):
        assert not self._make_handler(
            command="GET",
            path="/api/something/else",
            upgrade="websocket",
        )

    def test_case_insensitive_upgrade(self):
        assert self._make_handler(
            command="GET",
            path="/api/remote/terminal/12345678-1234-1234-1234-123456789abc/ws",
            upgrade="WebSocket",
        )


# ---------------------------------------------------------------------------
# Tests: run_application dispatch
# ---------------------------------------------------------------------------


class TestRunApplication:
    def test_terminal_ws_intercepted(self):
        handler = MagicMock(spec=TerminalWSHandler)
        handler.command = "GET"
        handler.environ = {
            "PATH_INFO": "/api/remote/terminal/12345678-1234-1234-1234-123456789abc/ws",
            "HTTP_UPGRADE": "websocket",
        }
        handler._handle_terminal_ws = MagicMock()

        TerminalWSHandler.run_application(handler)

        handler._handle_terminal_ws.assert_called_once()

    def test_non_terminal_delegates_to_super(self):
        """Non-terminal requests should fall through to the parent WSGIHandler."""
        handler = MagicMock(spec=TerminalWSHandler)
        handler._is_terminal_ws_request.return_value = False
        handler._handle_terminal_ws = MagicMock()

        with patch.object(TerminalWSHandler.__bases__[0], "run_application") as mock_super:
            TerminalWSHandler.run_application(handler)
            mock_super.assert_called_once()
        handler._handle_terminal_ws.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: _handle_terminal_ws
# ---------------------------------------------------------------------------


class TestHandleTerminalWs:
    UUID = "12345678-1234-1234-1234-123456789abc"

    def _make_handler(self, query_string="", token="valid-token"):
        handler = MagicMock(spec=TerminalWSHandler)
        handler.environ = {
            "PATH_INFO": f"/api/remote/terminal/{self.UUID}/ws",
            "QUERY_STRING": query_string or f"token={token}",
            "HTTP_SEC_WEBSOCKET_KEY": "dGhlIHNhbXBsZSBub25jZQ==",
        }
        handler.socket = MagicMock()
        handler.close_connection = False
        return handler

    @patch("app.ws_frame.send_close")
    @patch("app.ws_frame.perform_handshake")
    @patch("app.modules.workspace.terminal_store.terminal_info_store")
    def test_unknown_terminal_closes(self, mock_store, mock_handshake, mock_send_close):
        handler = self._make_handler()
        mock_store.find_by_terminal_id.return_value = None

        TerminalWSHandler._handle_terminal_ws(handler)

        mock_send_close.assert_called_once_with(handler.socket, 1011)
        assert handler.close_connection is True

    @patch("app.ws_frame.send_close")
    @patch("app.ws_frame.perform_handshake")
    @patch("app.modules.workspace.terminal_store.terminal_info_store")
    def test_invalid_token_closes(self, mock_store, mock_handshake, mock_send_close):
        handler = self._make_handler(query_string="token=wrong-token")
        mock_store.find_by_terminal_id.return_value = (
            "machine-123",
            {"token": "correct-token"},
        )

        TerminalWSHandler._handle_terminal_ws(handler)

        mock_send_close.assert_called_once_with(handler.socket, 4001)
        assert handler.close_connection is True

    @patch("app.ws_frame.send_close")
    @patch("app.ws_frame.perform_handshake")
    @patch("app.modules.workspace.terminal_store.terminal_info_store")
    def test_missing_remote_url_closes(self, mock_store, mock_handshake, mock_send_close):
        handler = self._make_handler(query_string="token=my-token")
        mock_store.find_by_terminal_id.return_value = (
            "machine-123",
            {
                "token": "my-token",
                "ws_url": "/relative/path",
                "original_ws_url": "",
                "original_token": "",
            },
        )

        TerminalWSHandler._handle_terminal_ws(handler)

        mock_send_close.assert_called_once_with(handler.socket, 1011)
        assert handler.close_connection is True

    @patch("app.ws_frame.send_close")
    @patch("app.ws_frame.perform_handshake")
    @patch("app.modules.workspace.terminal_ws_bridge.bridge_terminal_websocket_raw")
    @patch("app.modules.workspace.terminal_store.terminal_info_store")
    def test_successful_bridge(self, mock_store, mock_bridge, mock_handshake, mock_send_close):
        handler = self._make_handler(query_string="token=my-token")
        mock_store.find_by_terminal_id.return_value = (
            "machine-123",
            {
                "token": "my-token",
                "original_ws_url": "ws://remote:42000/terminal/abc/ws",
                "original_token": "remote-token",
            },
        )

        TerminalWSHandler._handle_terminal_ws(handler)

        mock_handshake.assert_called_once_with(handler.environ, handler.socket)
        mock_bridge.assert_called_once_with(
            self.UUID,
            handler.socket,
            "ws://remote:42000/terminal/abc/ws",
            "remote-token",
        )
        assert handler.close_connection is True
        mock_send_close.assert_not_called()

    @patch("app.ws_frame.perform_handshake")
    def test_handshake_failure_closes(self, mock_handshake):
        mock_handshake.side_effect = Exception("handshake error")
        handler = self._make_handler()

        TerminalWSHandler._handle_terminal_ws(handler)
        assert handler.close_connection is True

    @patch("app.ws_frame.send_close")
    @patch("app.ws_frame.perform_handshake")
    @patch("app.modules.workspace.terminal_ws_bridge.bridge_terminal_websocket_raw")
    @patch("app.modules.workspace.terminal_store.terminal_info_store")
    def test_bridge_exception_sends_close(
        self, mock_store, mock_bridge, mock_handshake, mock_send_close
    ):
        mock_bridge.side_effect = Exception("bridge crash")
        handler = self._make_handler(query_string="token=my-token")
        mock_store.find_by_terminal_id.return_value = (
            "machine-123",
            {
                "token": "my-token",
                "original_ws_url": "ws://remote:42000/ws",
                "original_token": "rt",
            },
        )

        TerminalWSHandler._handle_terminal_ws(handler)

        mock_send_close.assert_called_once_with(handler.socket, 1011)
