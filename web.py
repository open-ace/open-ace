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

# Create the Flask application using the factory
from app import create_app

app = create_app()

from app.repositories.database import DB_PATH, get_database_url, is_postgresql

# Get configuration
from scripts.shared.config import WEB_HOST, WEB_PORT

if __name__ == "__main__":
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

    from gevent.pywsgi import WSGIServer

    server = WSGIServer((WEB_HOST, WEB_PORT), app)
    print(f"Starting Open ACE on {WEB_HOST}:{WEB_PORT} (gevent)")
    server.serve_forever()
