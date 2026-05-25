"""WSGI middleware that handles terminal WebSocket upgrades.

Flask/Werkzeug cannot reliably route WebSocket requests to view functions
because Werkzeug intercepts or mishandles the upgraded connection.
Issue #147 first discovered this; PR #556 re-introduced the Flask-route
approach which fails in the same way.  This middleware intercepts terminal
WebSocket requests at the WSGI layer, before Flask sees them.
"""

import hmac
import logging
import re

logger = logging.getLogger(__name__)

# Pre-compiled pattern: /api/remote/terminal/<uuid>/ws
_WS_PATH_RE = re.compile(
    r"^/api/remote/terminal/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/ws$",
    re.IGNORECASE,
)


class TerminalWebSocketMiddleware:
    """WSGI middleware that bridges browser WebSocket connections to remote
    terminal servers, bypassing Flask routing entirely.
    """

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        ws = environ.get("wsgi.websocket")
        if ws is None:
            return self.app(environ, start_response)

        path = environ.get("PATH_INFO", "")
        m = _WS_PATH_RE.match(path)
        if m is None:
            return self.app(environ, start_response)

        terminal_id = m.group(1)

        # Parse query string to extract token
        query = environ.get("QUERY_STRING", "")
        token = ""
        for part in query.split("&"):
            if part.startswith("token="):
                token = part[6:]
                break

        self._handle_bridge(terminal_id, token, ws)
        # The bridge consumed the connection; return empty response body
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b""]

    # ------------------------------------------------------------------
    @staticmethod
    def _handle_bridge(terminal_id: str, token: str, browser_ws) -> None:
        from app.modules.workspace.terminal_store import terminal_info_store

        found = terminal_info_store.find_by_terminal_id(terminal_id)
        if not found:
            logger.warning(
                "Terminal WS (middleware) requested for unknown terminal %s",
                terminal_id[:8],
            )
            try:
                browser_ws.close(1011, "Terminal not available")
            except Exception:
                pass
            return

        machine_id, info = found
        stored_token = info.get("token", "")
        if not token or not stored_token or not hmac.compare_digest(token, stored_token):
            logger.warning(
                "Terminal WS (middleware) rejected invalid token for terminal %s",
                terminal_id[:8],
            )
            try:
                browser_ws.close(4001, "Authentication failed")
            except Exception:
                pass
            return

        remote_ws_url = info.get("original_ws_url") or info.get("ws_url", "")
        remote_token = info.get("original_token", "")
        if not remote_ws_url or remote_ws_url.startswith("/"):
            logger.error(
                "Terminal WS (middleware) missing remote URL for terminal %s",
                terminal_id[:8],
            )
            try:
                browser_ws.close(1011, "Remote terminal not available")
            except Exception:
                pass
            return

        try:
            from app.modules.workspace.terminal_ws_bridge import bridge_terminal_websocket

            logger.info(
                "Bridging terminal %s for machine %s (middleware)",
                terminal_id[:8],
                machine_id[:8],
            )
            bridge_terminal_websocket(terminal_id, browser_ws, remote_ws_url, remote_token)
        except Exception as e:
            logger.error(
                "Terminal WS (middleware) bridge failed for terminal %s: %s",
                terminal_id[:8],
                e,
            )
            try:
                browser_ws.close(1011, "Remote terminal connection failed")
            except Exception:
                pass
