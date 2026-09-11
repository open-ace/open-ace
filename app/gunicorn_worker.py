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


def _verify_gevent_patch() -> None:
    """Verify gevent monkey-patch was applied correctly.

    Issue #2237: Check that gunicorn_entry.py successfully applied gevent
    monkey-patch before any urllib3 imports. If not, log a warning that
    RecursionError may occur in urllib3 during LLM proxy requests.
    """
    import ssl

    if not hasattr(ssl, "_gevent_patched"):
        logger.warning(
            "gevent monkey-patch verification failed: ssl._gevent_patched not set. "
            "This could cause RecursionError in urllib3 during LLM proxy requests. "
            "Ensure gunicorn_entry.py is used as the entry point."
        )
    else:
        logger.info("gevent monkey-patch verification passed: ssl._gevent_patched is set")


# Issue #2237: Verify gevent patch at worker initialization
_verify_gevent_patch()

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
    """Gunicorn pywsgi worker with remote terminal and VSCode WebSocket handler.

    This worker class:
    1. Inherits from GeventPyWSGIWorker (gevent-based worker)
    2. Uses RemoteWSHandler for WebSocket upgrade handling
    3. Applies psycogreen patch for gevent compatibility (Issue #2187)
    4. Spawns the webui-pod orphan reconcile at worker start (Issue #3378)

    The reconcile is env-gated (OPENACE_WEBUI_ORPHAN_RECONCILE=1, set only by
    the web service entrypoint) and runs on its OWN greenlet — the sweep must
    never run inline in a request-serving greenlet (design #3378 FEAS-R4-3).
    """

    wsgi_handler = RemoteWSHandler

    def run(self) -> None:
        """Spawn the (gated) orphan reconcile, THEN enter the serving loop.

        Issue #3378 review (B1): gunicorn's ``Worker.init_process`` ends with
        ``self.run()`` and blocks there for the worker's entire life, so a hook
        placed AFTER ``super().init_process()`` would only execute at shutdown,
        when no greenlet hub drives it anymore. Overriding ``run()`` instead
        puts the spawn right before the service loop starts (the reconcile
        itself only spawns a greenlet — serving is never delayed by the sweep).
        """
        try:
            from app.services.webui_sandbox import maybe_spawn_webui_orphan_reconcile

            maybe_spawn_webui_orphan_reconcile()
        except Exception:  # noqa: BLE001 - fail-soft: boot must not fail
            logger.exception("webui orphan reconcile spawn failed (fail-soft)")
        super().run()
