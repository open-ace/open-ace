"""Unit tests for app.remote_ws_handler — custom gevent WSGI handler."""

import re
from unittest.mock import MagicMock, patch

import pytest

from app.remote_ws_handler import (
    _WS_PATH_RE,
    RemoteWSHandler,
    _build_vscode_remote_ws_url,
    _match_vscode_ws_path,
    _query_token_and_upstream_query,
)

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


class TestVSCodeWSPathMatching:
    def test_legacy_ws_path_matches_root_upstream_path(self):
        assert _match_vscode_ws_path(
            "/api/remote/vscode/12345678-1234-1234-1234-123456789abc/ws"
        ) == ("12345678-1234-1234-1234-123456789abc", "/")

    def test_proxy_root_matches_root_upstream_path(self):
        assert _match_vscode_ws_path(
            "/api/remote/vscode/12345678-1234-1234-1234-123456789abc/proxy/"
        ) == ("12345678-1234-1234-1234-123456789abc", "/")

    def test_proxy_nested_path_preserved(self):
        assert _match_vscode_ws_path(
            "/api/remote/vscode/12345678-1234-1234-1234-123456789abc/proxy/stable/ws"
        ) == ("12345678-1234-1234-1234-123456789abc", "/stable/ws")

    def test_non_vscode_proxy_path_rejected(self):
        assert _match_vscode_ws_path("/api/remote/vscode/not-a-uuid/proxy/stable/ws") is None


class TestVSCodeWSUrlBuilding:
    def test_token_removed_from_upstream_query(self):
        token, query = _query_token_and_upstream_query(
            "token=openace-token&folder=%2Froot%2Fworkspace&reconnectionToken=abc"
        )

        assert token == "openace-token"
        assert "token=" not in query
        assert "folder=%2Froot%2Fworkspace" in query
        assert "reconnectionToken=abc" in query

    def test_remote_ws_url_preserves_proxy_path_and_query(self):
        assert (
            _build_vscode_remote_ws_url(
                "http://192.168.64.3:45678",
                "/stable/ws",
                "reconnectionToken=abc",
            )
            == "ws://192.168.64.3:45678/stable/ws?reconnectionToken=abc"
        )


# ---------------------------------------------------------------------------
# Tests: _is_terminal_ws_request
# ---------------------------------------------------------------------------


class TestIsTerminalWsRequest:
    def _make_handler(self, command="GET", path="", upgrade="websocket"):
        handler = MagicMock(spec=RemoteWSHandler)
        handler.command = command
        handler.environ = {
            "PATH_INFO": path,
            "HTTP_UPGRADE": upgrade,
        }
        return RemoteWSHandler._is_terminal_ws_request(handler)

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
        handler = MagicMock(spec=RemoteWSHandler)
        handler.command = "GET"
        handler.environ = {
            "PATH_INFO": "/api/remote/terminal/12345678-1234-1234-1234-123456789abc/ws",
            "HTTP_UPGRADE": "websocket",
        }
        handler._handle_terminal_ws = MagicMock()

        RemoteWSHandler.run_application(handler)

        handler._handle_terminal_ws.assert_called_once()

    def test_non_terminal_delegates_to_super(self):
        """Non-terminal and non-vscode requests should fall through to the parent WSGIHandler."""
        handler = MagicMock(spec=RemoteWSHandler)
        handler._is_terminal_ws_request.return_value = False
        handler._is_vscode_ws_request.return_value = False
        handler._handle_terminal_ws = MagicMock()

        with patch.object(RemoteWSHandler.__bases__[0], "run_application") as mock_super:
            RemoteWSHandler.run_application(handler)
            mock_super.assert_called_once()
        handler._handle_terminal_ws.assert_not_called()

    def test_vscode_ws_intercepted(self):
        handler = MagicMock(spec=RemoteWSHandler)
        handler._is_terminal_ws_request.return_value = False
        handler._is_vscode_ws_request.return_value = True
        handler._handle_vscode_ws = MagicMock()

        RemoteWSHandler.run_application(handler)

        handler._handle_vscode_ws.assert_called_once()
        handler._handle_terminal_ws.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: _handle_terminal_ws
# ---------------------------------------------------------------------------


class TestHandleTerminalWs:
    UUID = "12345678-1234-1234-1234-123456789abc"

    def _make_handler(self, query_string="", token="valid-token"):
        handler = MagicMock(spec=RemoteWSHandler)
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

        RemoteWSHandler._handle_terminal_ws(handler)

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

        RemoteWSHandler._handle_terminal_ws(handler)

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

        RemoteWSHandler._handle_terminal_ws(handler)

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

        RemoteWSHandler._handle_terminal_ws(handler)

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

        RemoteWSHandler._handle_terminal_ws(handler)
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

        RemoteWSHandler._handle_terminal_ws(handler)

        mock_send_close.assert_called_once_with(handler.socket, 1011)


class TestHandleVSCodeWs:
    UUID = "12345678-1234-1234-1234-123456789abc"

    def _make_handler(self, path=None, query_string="", cookie=""):
        handler = MagicMock(spec=RemoteWSHandler)
        handler.environ = {
            "PATH_INFO": path or f"/api/remote/vscode/{self.UUID}/proxy/stable/ws",
            "QUERY_STRING": query_string or "token=my-token&folder=%2Froot%2Fworkspace",
            "HTTP_COOKIE": cookie,
            "HTTP_SEC_WEBSOCKET_KEY": "dGhlIHNhbXBsZSBub25jZQ==",
        }
        handler.socket = MagicMock()
        handler.close_connection = False
        return handler

    @patch("app.ws_frame.send_close")
    @patch("app.ws_frame.perform_handshake")
    @patch("app.modules.workspace.vscode_ws_bridge.bridge_vscode_ws_raw")
    @patch("app.modules.workspace.vscode_store.vscode_info_store")
    def test_proxy_path_bridges_to_matching_remote_path(
        self, mock_store, mock_bridge, mock_handshake, mock_send_close
    ):
        handler = self._make_handler()
        mock_store.find_by_vscode_id.return_value = (
            "machine-123",
            {
                "status": "running",
                "token": "my-token",
                "original_http_url": "http://remote:45678",
            },
        )

        RemoteWSHandler._handle_vscode_ws(handler)

        mock_handshake.assert_called_once_with(handler.environ, handler.socket)
        mock_bridge.assert_called_once_with(
            self.UUID,
            handler.socket,
            "ws://remote:45678/stable/ws?folder=%2Froot%2Fworkspace",
        )
        mock_send_close.assert_not_called()
        assert handler.close_connection is True

    @patch("app.ws_frame.send_close")
    @patch("app.ws_frame.perform_handshake")
    @patch("app.modules.workspace.vscode_ws_bridge.bridge_vscode_ws_raw")
    @patch("app.modules.workspace.vscode_store.vscode_info_store")
    def test_cookie_token_can_authenticate_proxy_ws(
        self, mock_store, mock_bridge, mock_handshake, mock_send_close
    ):
        handler = self._make_handler(
            query_string="reconnectionToken=abc",
            cookie=f"vscode_token_{self.UUID}=cookie-token",
        )
        mock_store.find_by_vscode_id.return_value = (
            "machine-123",
            {
                "status": "running",
                "token": "cookie-token",
                "original_http_url": "http://remote:45678",
            },
        )

        RemoteWSHandler._handle_vscode_ws(handler)

        mock_bridge.assert_called_once_with(
            self.UUID,
            handler.socket,
            "ws://remote:45678/stable/ws?reconnectionToken=abc",
        )
        mock_send_close.assert_not_called()

    @patch("app.ws_frame.send_close")
    @patch("app.ws_frame.perform_handshake")
    @patch("app.modules.workspace.vscode_store.vscode_info_store")
    def test_invalid_proxy_ws_token_closes(self, mock_store, mock_handshake, mock_send_close):
        handler = self._make_handler(query_string="token=wrong")
        mock_store.find_by_vscode_id.return_value = (
            "machine-123",
            {
                "status": "running",
                "token": "correct",
                "original_http_url": "http://remote:45678",
            },
        )

        RemoteWSHandler._handle_vscode_ws(handler)

        mock_send_close.assert_called_once_with(handler.socket, 4001)
        assert handler.close_connection is True
