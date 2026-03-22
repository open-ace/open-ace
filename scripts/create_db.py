#!/usr/bin/env python3
"""Initialize database on remote machine.

Supports both SQLite (default) and PostgreSQL databases.
For PostgreSQL, set the DATABASE_URL environment variable.
"""

import os
import sys

# Add shared directory to path
script_dir = os.path.dirname(os.path.abspath(__file__))
shared_dir = os.path.join(script_dir)
if shared_dir not in sys.path:
    sys.path.insert(0, shared_dir)

from shared.config import DB_DIR, DB_PATH


def is_postgresql() -> bool:
    """Check if using PostgreSQL database."""
    return os.environ.get('DATABASE_URL', '').startswith('postgresql')


def get_connection():
    """Get a database connection (SQLite or PostgreSQL)."""
    if is_postgresql():
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor
            url = os.environ['DATABASE_URL']
            conn = psycopg2.connect(url)
            conn.cursor_factory = RealDictCursor
            return conn
        except ImportError:
            raise ImportError(
                "psycopg2 is required for PostgreSQL. "
                "Install it with: pip install psycopg2-binary"
            )
    else:
        import sqlite3
        # Ensure directory exists (for SQLite)
        os.makedirs(DB_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn


def get_id_type() -> str:
    """Get the appropriate ID type for the current database."""
    if is_postgresql():
        return "SERIAL PRIMARY KEY"
    else:
        return "INTEGER PRIMARY KEY AUTOINCREMENT"


# Create database
conn = get_connection()
cursor = conn.cursor()

id_type = get_id_type()

cursor.execute(f'''
    CREATE TABLE IF NOT EXISTS daily_usage (
        id {id_type},
        date TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        host_name TEXT NOT NULL DEFAULT 'localhost',
        tokens_used INTEGER DEFAULT 0,
        input_tokens INTEGER DEFAULT 0,
        output_tokens INTEGER DEFAULT 0,
        cache_tokens INTEGER DEFAULT 0,
        request_count INTEGER DEFAULT 0,
        models_used TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(date, tool_name, host_name)
    )
''')

cursor.execute(f'''
    CREATE TABLE IF NOT EXISTS daily_messages (
        id {id_type},
        date TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        host_name TEXT NOT NULL DEFAULT 'localhost',
        message_id TEXT NOT NULL,
        parent_id TEXT,
        role TEXT NOT NULL,
        content TEXT,
        full_entry TEXT,
        tokens_used INTEGER DEFAULT 0,
        input_tokens INTEGER DEFAULT 0,
        output_tokens INTEGER DEFAULT 0,
        model TEXT,
        timestamp TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(date, tool_name, message_id, host_name)
    )
''')

conn.commit()
conn.close()

if is_postgresql():
    print(f"Database created at PostgreSQL: {os.environ.get('DATABASE_URL', '').split('@')[-1]}")
else:
    print(f"Database created at {DB_PATH}")