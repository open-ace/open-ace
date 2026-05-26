"""Custom Gunicorn worker class that supports terminal WebSocket upgrades.

Uses ``TerminalWSHandler`` as the WSGI handler class so that terminal
WebSocket connections are intercepted at the handler level and bridged
using raw socket I/O, bypassing the incompatible geventwebsocket library.

Usage in production::

    gunicorn --worker-class app.gunicorn_worker.TerminalGeventWorker ...
"""

from __future__ import annotations

from gunicorn.workers.ggevent import GeventPyWSGIWorker

from app.terminal_ws_handler import TerminalWSHandler


class TerminalGeventWorker(GeventPyWSGIWorker):
    """Gevent pywsgi worker with terminal WebSocket handler."""

    wsgi_handler = TerminalWSHandler
