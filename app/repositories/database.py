#!/usr/bin/env python3
"""
Open ACE - Database Connection

Provides database connection management for the Open ACE application.
Supports both SQLite (default) and PostgreSQL databases.
"""

import logging
import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

# Database configuration
CONFIG_DIR = os.path.expanduser("~/.open-ace")
DEFAULT_SQLITE_PATH = os.path.join(CONFIG_DIR, "ace.db")
# Legacy alias for backwards compatibility
DB_PATH = DEFAULT_SQLITE_PATH


def get_database_url() -> str:
    """
    Get database URL with priority: environment variable > config file > default SQLite.

    Returns:
        str: Database URL.
    """
    # Import here to avoid circular import
    from scripts.shared.config import get_database_url as _get_database_url
    return _get_database_url()


def is_postgresql() -> bool:
    """
    Check if using PostgreSQL.

    Returns:
        bool: True if using PostgreSQL.
    """
    return get_database_url().startswith('postgresql')


def get_param_placeholder() -> str:
    """
    Get the parameter placeholder for the current database.

    Returns:
        str: Parameter placeholder ('%s' for PostgreSQL, '?' for SQLite).
    """
    return '%s' if is_postgresql() else '?'


def adapt_sql(query: str) -> str:
    """
    Adapt SQL query for the current database.
    Converts SQLite-style placeholders (?) to PostgreSQL-style (%s) if needed.

    Args:
        query: SQL query with ? placeholders.

    Returns:
        str: Adapted SQL query.
    """
    if is_postgresql():
        # Replace ? with %s, but be careful with string literals
        # Simple approach: just replace all ? with %s
        return query.replace('?', '%s')
    return query


def ensure_db_dir() -> None:
    """Ensure the database directory exists (for SQLite)."""
    os.makedirs(CONFIG_DIR, exist_ok=True)


def get_connection() -> Union[sqlite3.Connection, Any]:
    """
    Get a database connection.

    Returns:
        Connection: Database connection with row factory.
    """
    if is_postgresql():
        return get_postgresql_connection()
    else:
        return get_sqlite_connection()


def get_sqlite_connection() -> sqlite3.Connection:
    """
    Get a SQLite database connection.

    Returns:
        sqlite3.Connection: SQLite connection with row factory.
    """
    ensure_db_dir()
    conn = sqlite3.connect(DEFAULT_SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    # Enable foreign keys
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_postgresql_connection() -> Any:
    """
    Get a PostgreSQL database connection.

    Returns:
        psycopg2.connection: PostgreSQL connection.
    """
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor

        url = os.environ['DATABASE_URL']
        conn = psycopg2.connect(url)
        # Store cursor factory for later use
        conn._cursor_factory = RealDictCursor
        return conn
    except ImportError:
        raise ImportError(
            "psycopg2 is required for PostgreSQL. "
            "Install it with: pip install psycopg2-binary"
        )


@contextmanager
def get_db_connection():
    """
    Database connection context manager.

    Usage:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(...)
            conn.commit()
    """
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


class Database:
    """Database manager class for dependency injection."""

    def __init__(self, db_url: Optional[str] = None):
        """
        Initialize database manager.

        Args:
            db_url: Optional custom database URL.
        """
        self.db_url = db_url or get_database_url()
        self._is_postgresql = self.db_url.startswith('postgresql')

    @property
    def is_postgresql(self) -> bool:
        """Check if using PostgreSQL."""
        return self._is_postgresql

    def get_connection(self) -> Union[sqlite3.Connection, Any]:
        """Get a database connection."""
        if self._is_postgresql:
            return self._get_postgresql_connection()
        else:
            return self._get_sqlite_connection()

    def _get_sqlite_connection(self) -> sqlite3.Connection:
        """Get SQLite connection."""
        ensure_db_dir()
        # Extract path from URL
        if self.db_url.startswith('sqlite:///'):
            db_path = self.db_url[10:]
        else:
            db_path = DEFAULT_SQLITE_PATH

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _get_postgresql_connection(self) -> Any:
        """Get PostgreSQL connection."""
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor

            conn = psycopg2.connect(self.db_url)
            return conn
        except ImportError:
            raise ImportError(
                "psycopg2 is required for PostgreSQL. "
                "Install it with: pip install psycopg2-binary"
            )

    @contextmanager
    def connection(self):
        """Database connection context manager."""
        conn = self.get_connection()
        try:
            yield conn
        finally:
            conn.close()

    def execute(self, query: str, params: tuple = ()) -> Any:
        """
        Execute a query and return the cursor.

        Args:
            query: SQL query string.
            params: Query parameters.

        Returns:
            Cursor: Query cursor.
        """
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(adapt_sql(query), params)
            conn.commit()
            return cursor

    def fetch_one(self, query: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        """
        Execute a query and return a single row.

        Args:
            query: SQL query string.
            params: Query parameters.

        Returns:
            Optional[dict]: Single row as dictionary or None.
        """
        with self.connection() as conn:
            # Use RealDictCursor for PostgreSQL
            if is_postgresql():
                from psycopg2.extras import RealDictCursor
                cursor = conn.cursor(cursor_factory=RealDictCursor)
            else:
                cursor = conn.cursor()
            cursor.execute(adapt_sql(query), params)
            row = cursor.fetchone()

            if row is None:
                return None

            # Handle both SQLite Row and psycopg2 RealDictRow
            if isinstance(row, dict):
                return row
            return dict(row)

    def fetch_all(self, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
        """
        Execute a query and return all rows.

        Args:
            query: SQL query string.
            params: Query parameters.

        Returns:
            list: List of rows as dictionaries.
        """
        with self.connection() as conn:
            # Use RealDictCursor for PostgreSQL
            if is_postgresql():
                from psycopg2.extras import RealDictCursor
                cursor = conn.cursor(cursor_factory=RealDictCursor)
            else:
                cursor = conn.cursor()
            cursor.execute(adapt_sql(query), params)
            rows = cursor.fetchall()

            # Handle both SQLite Row and psycopg2 RealDictRow
            if rows and isinstance(rows[0], dict):
                return rows
            return [dict(row) for row in rows]

    def executemany(self, query: str, params_list: List[tuple]) -> Any:
        """
        Execute a query with multiple parameter sets.

        Args:
            query: SQL query string.
            params_list: List of parameter tuples.

        Returns:
            Cursor: Query cursor.
        """
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.executemany(query, params_list)
            conn.commit()
            return cursor

    def table_exists(self, table_name: str) -> bool:
        """
        Check if a table exists.

        Args:
            table_name: Name of the table.

        Returns:
            bool: True if table exists.
        """
        if self._is_postgresql:
            result = self.fetch_one(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s)",
                (table_name,)
            )
            return result.get('exists', False) if result else False
        else:
            result = self.fetch_one(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,)
            )
            return result is not None