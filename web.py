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

# Add the project root to the path
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Initialize database before starting the app
from scripts.shared.db import init_database

init_database()

# Create the Flask application using the factory
from app import create_app

app = create_app()

# Get configuration
from scripts.shared.config import WEB_HOST, WEB_PORT
from app.repositories.database import DB_PATH, is_postgresql, get_database_url

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
    print(f"Config: ~/.open-ace/config.json")
    print("-" * 50)

    # Check if running in production mode
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    if debug_mode:
        print("WARNING: Running in DEBUG mode - not recommended for production!")

    # Disable reloader when stdin is not available (e.g., running in scripts, pipes, or background)
    # This prevents termios.error: (5, 'Input/output error')
    # Check both isatty() and whether stdin is actually usable
    try:
        # Try to access stdin attributes - will fail if stdin is closed or redirected
        if sys.stdin.isatty() and sys.stdin.fileno() >= 0:
            use_reloader = debug_mode
        else:
            use_reloader = False
    except (AttributeError, OSError, ValueError):
        # stdin is not available (closed, redirected, or in background)
        use_reloader = False

    app.run(
        host=WEB_HOST, port=WEB_PORT, debug=debug_mode, threaded=True, use_reloader=use_reloader
    )
