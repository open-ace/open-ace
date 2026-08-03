"""Custom Gunicorn worker class that supports remote WebSocket upgrades.

Uses ``RemoteWSHandler`` as the WSGI handler class so that remote terminal
and VSCode WebSocket connections are intercepted at the handler level and
bridged using raw socket I/O, bypassing the incompatible geventwebsocket
library.

Issue #2187: Also applies psycogreen patch for gevent-compatible psycopg2.

Usage in production::

    gunicorn --worker-class app.gunicorn_worker.TerminalGeventWorker ...
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Issue #2187: Apply psycogreen patch for gevent compatibility.
# This MUST be done before any psycopg2 connections are created.
# The import order is:
# 1. gevent monkey patching (done by GeventPyWSGIWorker parent)
# 2. psycogreen patch (done here)
# 3. Application code imports (happens when worker loads app)
try:
    import psycogreen.gevent

    psycogreen.gevent.patch_psycopg()
    logger.info("psycogreen patch applied successfully for gevent worker")
except ImportError:
    logger.warning(
        "psycogreen not available - psycopg2 connections may block gevent event loop. "
        "Install with: pip install psycogreen"
    )
except Exception as e:
    logger.error(f"Failed to apply psycogreen patch: {e}")

from gunicorn.workers.ggevent import GeventPyWSGIWorker

from app.remote_ws_handler import RemoteWSHandler


class TerminalGeventWorker(GeventPyWSGIWorker):
    """Gevent pywsgi worker with remote terminal and VSCode WebSocket handler.

    This worker class:
    1. Inherits from GeventPyWSGIWorker (gevent-based worker)
    2. Uses RemoteWSHandler for WebSocket upgrade handling
    3. Applies psycogreen patch for psycopg2 gevent compatibility (Issue #2187)
    """

    wsgi_handler = RemoteWSHandler