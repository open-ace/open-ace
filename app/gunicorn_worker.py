"""Custom Gunicorn worker class that supports remote WebSocket upgrades.

Uses ``RemoteWSHandler`` as the WSGI handler class so that remote terminal
and VSCode WebSocket connections are intercepted at the handler level and
bridged using raw socket I/O, bypassing the incompatible geventwebsocket
library.

Usage in production::

    gunicorn --worker-class app.gunicorn_worker.TerminalGeventWorker ...
"""

from __future__ import annotations




from gunicorn.workers.ggevent import GeventPyWSGIWorker

from app.remote_ws_handler import RemoteWSHandler


class TerminalGeventWorker(GeventPyWSGIWorker):
    """Gevent pywsgi worker with remote terminal and VSCode WebSocket handler."""

    wsgi_handler = RemoteWSHandler
