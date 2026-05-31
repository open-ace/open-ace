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
from http.cookies import SimpleCookie
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from gevent.pywsgi import WSGIHandler

import app.ws_frame as ws_frame

logger = logging.getLogger(__name__)

_WS_PATH_RE = re.compile(
    r"^/api/remote/terminal/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"/ws$",
    re.IGNORECASE,
)

_VSCODE_LEGACY_WS_PATH_RE = re.compile(
    r"^/api/remote/vscode/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"/ws$",
    re.IGNORECASE,
)

_VSCODE_PROXY_WS_PATH_RE = re.compile(
    r"^/api/remote/vscode/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"/proxy(?P<path>/.*)?$",
    re.IGNORECASE,
)


def _match_vscode_ws_path(path: str) -> tuple[str, str] | None:
    """Return (vscode_id, upstream_path) for VSCode websocket proxy paths."""
    legacy_match = _VSCODE_LEGACY_WS_PATH_RE.match(path)
    if legacy_match:
        return legacy_match.group(1), "/"

    proxy_match = _VSCODE_PROXY_WS_PATH_RE.match(path)
    if proxy_match:
        return proxy_match.group(1), proxy_match.group("path") or "/"

    return None


def _query_token_and_upstream_query(query: str) -> tuple[str, str]:
    """Extract Open ACE token and remove it from the upstream query string."""
    token = ""
    upstream_pairs = []
    for key, value in parse_qsl(query, keep_blank_values=True):
        if key == "token":
            if not token:
                token = value
            continue
        upstream_pairs.append((key, value))
    return token, urlencode(upstream_pairs)


def _cookie_value(cookie_header: str, name: str) -> str:
    if not cookie_header:
        return ""
    try:
        cookies = SimpleCookie()
        cookies.load(cookie_header)
        morsel = cookies.get(name)
        return morsel.value if morsel else ""
    except Exception:
        return ""


def _build_vscode_remote_ws_url(original_http_url: str, upstream_path: str, query: str) -> str:
    """Build the remote code-server websocket URL for a proxied browser path."""
    parsed = urlparse(original_http_url)
    ws_scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse(
        parsed._replace(
            scheme=ws_scheme,
            path=upstream_path or "/",
            params="",
            query=query,
            fragment="",
        )
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
        return _match_vscode_ws_path(path) is not None

    def _handle_vscode_ws(self) -> None:
        path = self.environ.get("PATH_INFO", "")
        matched = _match_vscode_ws_path(path)
        assert matched is not None
        vscode_id, upstream_path = matched

        # WebSocket handshake directly on the raw socket.
        try:
            ws_frame.perform_handshake(self.environ, self.socket)
        except Exception:
            logger.exception("VSCode WS handshake failed for %s", vscode_id[:8])
            self.close_connection = True
            return

        # Parse token from query string.
        query = self.environ.get("QUERY_STRING", "")
        token, upstream_query = _query_token_and_upstream_query(query)

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
        if not token:
            token = _cookie_value(
                self.environ.get("HTTP_COOKIE", ""),
                f"vscode_token_{vscode_id}",
            )
        if not token and info.get("status") == "running":
            token = stored_token
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

        remote_ws_url = _build_vscode_remote_ws_url(
            original_http_url,
            upstream_path,
            upstream_query,
        )

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
