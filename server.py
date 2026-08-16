#!/usr/bin/env python3
"""
Open ACE - Web Application Entry Point

This is the main entry point for the Open ACE web application.
The application logic has been refactored into the app/ module with:
- routes/ - API endpoint definitions
- services/ - Business logic layer
- repositories/ - Data access layer
- models/ - Data models
- utils/ - Utility functions

For the legacy implementation, see web_legacy.py
"""

import os
import sys

# 确保 scheduler 在 gevent 环境下正常运行（Issue #1481）
# gevent patch 后 threading.Thread 变为协程，在 serve_forever 循环中无法调度
# 使用 APScheduler 作为默认后端，创建真实线程不受 gevent 影响
os.environ.setdefault("SCHEDULER_IMPLEMENTATION", "apscheduler")

# gevent monkey-patch must be applied before any other imports
from gevent import monkey

monkey.patch_all()

# Make psycopg2 cooperative with gevent (prevents blocking the event loop)
import psycogreen.gevent

psycogreen.gevent.patch_psycopg()

# Add the project root to the path
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Load secret_key from config.json before creating app
# This ensures APIKeyProxyService can find the encryption key
from scripts.shared.config import get_secret_key

secret_key = get_secret_key()
if secret_key:
    os.environ["SECRET_KEY"] = secret_key

# Issue #2331/#2654: Set OPENACE_SECURITY_MODE fallback at module level (before
# create_app) so it applies in both gunicorn and __main__ paths.  In production-
# capable environments (systemd, Docker, K8s) the service unit or entrypoint
# must set OPENACE_SECURITY_MODE explicitly; the development fallback only
# triggers when none of those production indicators are present.
_security_mode = os.environ.get("OPENACE_SECURITY_MODE", "").strip()
if not _security_mode:
    _is_prod_capable = (
        os.path.isdir("/run/systemd/system")  # systemd
        or bool(os.environ.get("KUBERNETES_SERVICE_HOST"))  # K8s
        or os.environ.get("FLASK_ENV") == "production"
    )
    if not _is_prod_capable:
        print("=" * 60)
        print("  LOCAL DEVELOPMENT MODE")
        print("  Setting OPENACE_SECURITY_MODE=development")
        print("  For production: set OPENACE_SECURITY_MODE explicitly")
        print("=" * 60)
        os.environ["OPENACE_SECURITY_MODE"] = "development"

# Issue #2667: non-Docker bootstrap for OPENACE_ENCRYPTION_KEY. docker-
# entrypoint.sh generates and persists this key so `docker compose up` works
# zero-config, but `python3 server.py` (and gunicorn on a bare host) had no
# equivalent — APIKeyProxyService raised RuntimeError at first use while
# /api/autonomous/models masked it as "no models configured". No-op when the
# key is already set or the security mode is production.
from app.utils.security_env import ensure_generated_encryption_key

ensure_generated_encryption_key()

# Create the Flask application using the factory
from app import create_app

app = create_app()

from app.repositories.database import DB_PATH, get_database_url, is_postgresql

# Get configuration
from scripts.shared.config import WEB_HOST, WEB_PORT

if __name__ == "__main__":
    # OPENACE_SECURITY_MODE fallback is now set at module level (before
    # create_app) to cover both gunicorn and direct execution paths.
    # See Issue #2654 for details.

    print(f"Starting Open ACE on {WEB_HOST}:{WEB_PORT}")
    if is_postgresql():
        # Hide password in URL for display
        db_url = get_database_url()
        if "@" in db_url:
            # Mask password: postgresql://user:password@host/db -> postgresql://user:***@host/db
            parts = db_url.split("@")
            prefix = parts[0].rsplit(":", 1)[0] + ":***"
            display_url = prefix + "@" + parts[1]
            print(f"Database: {display_url}")
        else:
            print(f"Database: {db_url}")
    else:
        print(f"Database: {DB_PATH}")
    print("Config: ~/.open-ace/config.json")
    print("-" * 50)

    # Check if running in production mode
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    if debug_mode:
        print("WARNING: Running in DEBUG mode - not recommended for production!")

    # Disable reloader when stdin is not available (e.g., running in scripts, pipes, or background)
    # This prevents termios.error: (5, 'Input/output error')
    # Reloader is problematic in many environments, so we disable it by default
    # unless explicitly running in an interactive terminal with TTY support
    use_reloader = False
    if debug_mode:
        try:
            # Check if we have a proper TTY that supports termios operations
            import termios

            if sys.stdin.isatty() and sys.stdin.fileno() >= 0:
                # Try to get terminal attributes - will fail if TTY is not fully functional
                termios.tcgetattr(sys.stdin.fileno())
                use_reloader = True
        except (AttributeError, OSError, ValueError, termios.error):
            # stdin is not available or termios not supported
            use_reloader = False

    from gevent.pywsgi import WSGIHandler, WSGIServer

    server_kwargs = {}
    try:
        from app.remote_ws_handler import RemoteWSHandler

        server_kwargs["handler_class"] = RemoteWSHandler
    except ImportError:
        server_kwargs["handler_class"] = WSGIHandler
        print("WARNING: remote_ws_handler unavailable; remote WebSocket is disabled")

    server = WSGIServer((WEB_HOST, WEB_PORT), app, **server_kwargs)

    # Graceful shutdown: stop webui instances on SIGTERM/SIGINT
    import signal

    import gevent

    def _shutdown_and_stop():
        try:
            from app.services.autonomous_scheduler import AutonomousScheduler

            AutonomousScheduler.instance().stop()
        except Exception as e:
            print(f"Error stopping autonomous scheduler: {e}")
        try:
            from app.services.webui_manager import shutdown_webui_manager

            shutdown_webui_manager()
        except Exception as e:
            print(f"Error during shutdown: {e}")
        server.stop()

    def handle_shutdown(signum, frame):
        print(f"\nReceived signal {signum}, shutting down gracefully...")
        gevent.spawn(_shutdown_and_stop)

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    print(f"Starting Open ACE on {WEB_HOST}:{WEB_PORT} (gevent)")
    server.serve_forever()
