#!/usr/bin/env python3
"""
AI Token Usage - Database connection helper (upload-to-central bundle).

This is a deliberately minimal module. ``upload_to_server.py`` -- the only
consumer of this bundle-local ``shared`` package -- needs just
:func:`get_connection`; it drives raw cursors itself and never calls the
richer helpers (``save_message``, ``save_usage``, ``init_database``, ...).

The full database module lives at ``scripts/shared/db.py`` and is the one that
actually runs the heavy lifting:

* ``deploy-remote.sh`` stages ``../../scripts/shared/*.py`` (the canonical
  package) onto remote hosts -- not this file.
* The in-repo fetch scripts (``fetch_openclaw.py`` etc.) import
  ``scripts/shared/db.py`` via ``from shared import db`` as well.

Keeping a ~4000-line verbatim copy of ``scripts/shared/db.py`` here only
invited drift -- this file's ``_get_db_url`` had already fallen behind the
canonical sudo/``gssencmode`` crash fix -- so it was reduced to the connection
helper the bundle actually uses. ``_get_db_url`` / ``ensure_db_dir`` /
``get_connection`` below are copied verbatim from ``scripts/shared/db.py``;
keep them in sync if that file's connection logic changes.
"""

import os
import sqlite3
import sys
from typing import Any, cast

# Ensure scripts directory is in path for standalone script execution
_script_dir = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(_script_dir)
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

# Use standard import after path setup
from shared import config

DB_DIR = config.DB_DIR
DB_PATH = config.DB_PATH

# Cache for database URL
_db_url_cache = None


def _get_db_url() -> str:
    """Get database URL from config.

    Automatically adds gssencmode=disable for PostgreSQL when running under sudo
    to prevent GSSAPI/Kerberos crash (SIGSEGV) in sudo environment.
    """
    global _db_url_cache
    if _db_url_cache is None:
        url = config.get_database_url()
        # Detect sudo environment: SUDO_USER is set when running under sudo
        if url.startswith("postgresql") and os.environ.get("SUDO_USER"):
            # Add gssencmode=disable to prevent GSSAPI crash in sudo environment
            if "?" in url:
                url = url + "&gssencmode=disable"
            else:
                url = url + "?gssencmode=disable"
        _db_url_cache = url
    return cast(str, _db_url_cache)


def ensure_db_dir() -> None:
    """Ensure the database directory exists (for SQLite)."""
    os.makedirs(DB_DIR, exist_ok=True)


def get_connection() -> sqlite3.Connection | Any:
    """Get a database connection (SQLite or PostgreSQL)."""
    url = _get_db_url()
    if url.startswith("postgresql"):
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor

            conn = psycopg2.connect(url)
            conn.cursor_factory = RealDictCursor
            return conn
        except ImportError:
            raise ImportError(
                "psycopg2 is required for PostgreSQL. "
                "Install it with: pip install psycopg2-binary"
            )
    else:
        ensure_db_dir()
        # Extract path from sqlite:/// URL or use default
        if url.startswith("sqlite:///"):
            db_path = url[10:]
        else:
            db_path = DB_PATH
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn
