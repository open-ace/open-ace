"""Custom gevent WSGI handler that processes remote WebSocket upgrades.

Handles WebSocket upgrades for both remote terminals and remote VSCode
(code-server) sessions. Bypasses geventwebsocket entirely for these
paths, handling the WebSocket handshake and framing directly on the raw
socket.

For all other requests the handler delegates to the normal WSGIHandler
flow (including geventwebsocket if it is configured).
"""

from __future__ import annotations

import hmac
import logging
import re
from urllib.parse import urlparse, urlunparse

from gevent.pywsgi import WSGIHandler

import app.ws_frame as ws_frame

logger = logging.getLogger(__name__)

_WS_PATH_RE = re.compile(
    r"^/api/remote/terminal/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"/ws$",
    re.IGNORECASE,
)

_VSCODE_WS_PATH_RE = re.compile(
    r"^/api/remote/vscode/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"/ws$",
    re.IGNORECASE,
)


class RemoteWSHandler(WSGIHandler):
    """WSGI handler that intercepts remote terminal and VSCode WebSocket upgrades."""

    def run_application(self) -> None:
        if self._is_terminal_ws_request():
            self._handle_terminal_ws()
            return
        if self._is_vscode_ws_request():
            self._handle_vscode_ws()
            return
        # Non-terminal: fall through to normal WSGI handling.
        super().run_application()

    # ------------------------------------------------------------------

    def _is_terminal_ws_request(self) -> bool:
        if self.command != "GET":
            return False
        upgrade = self.environ.get("HTTP_UPGRADE", "").lower()
        if upgrade != "websocket":
            return False
        path = self.environ.get("PATH_INFO", "")
        return _WS_PATH_RE.match(path) is not None

    def _handle_terminal_ws(self) -> None:
        path = self.environ.get("PATH_INFO", "")
        m = _WS_PATH_RE.match(path)
        assert m is not None
        terminal_id = m.group(1)

        # WebSocket handshake directly on the raw socket.
        try:
            ws_frame.perform_handshake(self.environ, self.socket)
        except Exception:
            logger.exception("Terminal WS handshake failed for %s", terminal_id[:8])
            self.close_connection = True
            return

        # Parse token from query string.
        token = ""
        query = self.environ.get("QUERY_STRING", "")
        for part in query.split("&"):
            if part.startswith("token="):
                token = part[6:]
                break

        # Look up terminal info.
        from app.modules.workspace.terminal_store import terminal_info_store

        found = terminal_info_store.find_by_terminal_id(terminal_id)
        if not found:
            logger.warning("Terminal WS handler: unknown terminal %s", terminal_id[:8])
            ws_frame.send_close(self.socket, 1011)
            self.close_connection = True
            return

        machine_id, info = found

        # Validate token.
        stored_token = info.get("token", "")
        if not token or not stored_token or not hmac.compare_digest(token, stored_token):
            logger.warning("Terminal WS handler: invalid token for terminal %s", terminal_id[:8])
            ws_frame.send_close(self.socket, 4001)
            self.close_connection = True
            return

        remote_ws_url = info.get("original_ws_url") or info.get("ws_url", "")
        remote_token = info.get("original_token", "")
        if not remote_ws_url or remote_ws_url.startswith("/"):
            logger.error("Terminal WS handler: missing remote URL for terminal %s", terminal_id[:8])
            ws_frame.send_close(self.socket, 1011)
            self.close_connection = True
            return

        # Bridge browser socket to remote terminal.
        try:
            from app.modules.workspace.terminal_ws_bridge import bridge_terminal_websocket_raw

            logger.info(
                "Terminal WS handler: bridging %s for machine %s",
                terminal_id[:8],
                machine_id[:8],
            )
            bridge_terminal_websocket_raw(terminal_id, self.socket, remote_ws_url, remote_token)
        except Exception:
            logger.exception("Terminal WS handler: bridge failed for terminal %s", terminal_id[:8])
            try:
                ws_frame.send_close(self.socket, 1011)
            except Exception:
                pass

        self.close_connection = True

    # ------------------------------------------------------------------
    # VSCode (code-server) WebSocket handling
    # ------------------------------------------------------------------

    def _is_vscode_ws_request(self) -> bool:
        if self.command != "GET":
            return False
        upgrade = self.environ.get("HTTP_UPGRADE", "").lower()
        if upgrade != "websocket":
            return False
        path = self.environ.get("PATH_INFO", "")
        return _VSCODE_WS_PATH_RE.match(path) is not None

    def _handle_vscode_ws(self) -> None:
        path = self.environ.get("PATH_INFO", "")
        m = _VSCODE_WS_PATH_RE.match(path)
        assert m is not None
        vscode_id = m.group(1)

        # WebSocket handshake directly on the raw socket.
        try:
            ws_frame.perform_handshake(self.environ, self.socket)
        except Exception:
            logger.exception("VSCode WS handshake failed for %s", vscode_id[:8])
            self.close_connection = True
            return

        # Parse token from query string.
        token = ""
        query = self.environ.get("QUERY_STRING", "")
        for part in query.split("&"):
            if part.startswith("token="):
                token = part[6:]
                break

        # Look up VSCode info.
        from app.modules.workspace.vscode_store import vscode_info_store

        found = vscode_info_store.find_by_vscode_id(vscode_id)
        if not found:
            logger.warning("VSCode WS handler: unknown vscode %s", vscode_id[:8])
            ws_frame.send_close(self.socket, 1011)
            self.close_connection = True
            return

        machine_id, info = found

        # Validate token.
        stored_token = info.get("token", "")
        if not token or not stored_token or not hmac.compare_digest(token, stored_token):
            logger.warning("VSCode WS handler: invalid token for vscode %s", vscode_id[:8])
            ws_frame.send_close(self.socket, 4001)
            self.close_connection = True
            return

        original_http_url = info.get("original_http_url", "")
        if not original_http_url:
            logger.error("VSCode WS handler: missing remote URL for vscode %s", vscode_id[:8])
            ws_frame.send_close(self.socket, 1011)
            self.close_connection = True
            return

        # Convert http URL to ws URL using urllib.parse for safety
        parsed = urlparse(original_http_url)
        ws_scheme = "wss" if parsed.scheme == "https" else "ws"
        remote_ws_url = urlunparse(parsed._replace(scheme=ws_scheme))

        # Bridge browser socket to remote code-server.
        try:
            from app.modules.workspace.vscode_ws_bridge import bridge_vscode_ws_raw

            logger.info(
                "VSCode WS handler: bridging %s for machine %s",
                vscode_id[:8],
                machine_id[:8],
            )
            bridge_vscode_ws_raw(vscode_id, self.socket, remote_ws_url)
        except Exception:
            logger.exception("VSCode WS handler: bridge failed for vscode %s", vscode_id[:8])
            try:
                ws_frame.send_close(self.socket, 1011)
            except Exception:
                pass

        self.close_connection = True
