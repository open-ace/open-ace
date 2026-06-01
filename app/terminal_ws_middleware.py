"""WSGI passthrough middleware for terminal WebSocket requests.

Terminal WebSocket upgrades are now intercepted at the gevent WSGI handler
level by ``RemoteWSHandler`` (see ``remote_ws_handler.py``), which
bypasses geventwebsocket entirely and uses raw socket I/O.

This middleware is kept as a safety net: if a request somehow reaches the
WSGI app with ``wsgi.websocket`` set (e.g. a non-terminal path that uses
geventwebsocket), it passes through to the real Flask app unchanged.
"""

import logging

logger = logging.getLogger(__name__)


class TerminalWebSocketMiddleware:
    """Passthrough WSGI middleware — terminal WS is handled by the handler."""

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        return self.app(environ, start_response)
