"""Schema version guard for production deployments.

This module provides version checking and compatibility validation for database
schema, ensuring that production deployments only run against properly migrated
databases.

Issue: #2190
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)

# Minimum supported schema revision (baseline)
MIN_SUPPORTED_REVISION = "baseline_2026_06_23"

# Compatibility window: number of revisions back we can support
SCHEMA_COMPATIBILITY_WINDOW = 10


class SchemaCompatibilityError(Exception):
    """Raised when database schema version is incompatible with application."""

    def __init__(
        self,
        message: str,
        current_revision: str | None = None,
        min_revision: str = MIN_SUPPORTED_REVISION,
    ):
        super().__init__(message)
        self.current_revision = current_revision
        self.min_revision = min_revision


def _is_sqlite(connection: Any) -> bool:
    """Detect if a connection is SQLite, supporting both SQLAlchemy and raw connections."""
    # SQLAlchemy Connection: check dialect
    if hasattr(connection, "dialect"):
        return bool(connection.dialect.name == "sqlite")
    # sqlite3 raw connection
    return hasattr(connection, "execute") and not hasattr(connection, "_conn")


def _execute_scalar(connection: Any, query: str) -> Any:
    """Execute a scalar query, compatible with both SQLAlchemy and raw connections.

    Supports:
    - SQLAlchemy ``Connection`` (has ``.execute()``, no ``.cursor()``)
    - ``PgConnectionWrapper`` / psycopg2 (has ``.cursor()``)
    - ``sqlite3.Connection`` (has both ``.cursor()`` and ``.execute()``)

    Args:
        connection: Database connection object.
        query: SQL query to execute.

    Returns:
        The first column of the first row, or None.
    """
    if hasattr(connection, "cursor") and not hasattr(connection, "execute"):
        # Raw psycopg2 / PgConnectionWrapper (has .cursor(), no .execute())
        cursor = connection.cursor()
        try:
            cursor.execute(query)
            row = cursor.fetchone()
        finally:
            cursor.close()
        if row is None:
            return None
        # psycopg2 RealDictCursor returns dict-like rows; plain cursor returns tuples
        if isinstance(row, dict):
            return next(iter(row.values()))
        return row[0]
    elif hasattr(connection, "cursor") and hasattr(connection, "execute"):
        # sqlite3 raw connection (has both .cursor() and .execute())
        # Use .execute() directly for consistency with SQLAlchemy path
        pass  # fall through to the SQLAlchemy-like path below

    # SQLAlchemy Connection (or sqlite3 via .execute())
    result = connection.execute(sa.text(query))
    return result.scalar()


def _table_exists(connection: Any, table_name: str) -> bool:
    """Check if a table exists, compatible with both SQLAlchemy and raw connections.

    Uses dialect-appropriate SQL:
    - PostgreSQL: information_schema.tables
    - SQLite: sqlite_master

    Note: table_name is always a hardcoded constant (e.g. "alembic_version"),
    so string interpolation is safe here and avoids parameterization complexity
    across heterogeneous connection types.
    """
    # table_name is always a hardcoded constant ("alembic_version"), so
    # string interpolation is safe — no user input involved.
    if _is_sqlite(connection):
        query = (
            "SELECT EXISTS (SELECT 1 FROM sqlite_master WHERE type='table' AND name='"
            + table_name
            + "')"
        )  # nosec: B608
    else:
        query = (
            "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = '"
            + table_name
            + "')"
        )  # nosec: B608
    return bool(_execute_scalar(connection, query))


def get_database_revision(connection: Connection) -> str | None:
    """Get the current Alembic revision from the database.

    Args:
        connection: Database connection (SQLAlchemy Connection or PgConnectionWrapper)

    Returns:
        Current revision string, or None if alembic_version table doesn't exist
        (fresh database) or has no rows.
    """
    # Check if alembic_version table exists.
    # We cannot use sa.inspect() here because the caller may pass a raw
    # psycopg2 connection wrapped in PgConnectionWrapper, which is not
    # recognised by SQLAlchemy's inspection system.  Use portable SQL instead.
    try:
        if not _table_exists(connection, "alembic_version"):
            logger.debug("alembic_version table does not exist - fresh database")
            return None
    except Exception as e:
        logger.error(f"Error checking alembic_version table existence: {e}")
        raise

    # Query current revision
    try:
        result = _execute_scalar(
            connection,
            "SELECT version_num FROM alembic_version ORDER BY version_num LIMIT 1",
        )

        if result:
            logger.debug(f"Current database revision: {result}")
            return str(result)

        # Table exists but has no rows - corrupted state
        logger.warning("alembic_version table exists but has no revision row")
        return None

    except Exception as e:
        logger.error(f"Error querying alembic_version: {e}")
        raise


def check_schema_compatibility(
    connection: Connection,
    min_revision: str = MIN_SUPPORTED_REVISION,
    skip_check: bool = False,
) -> None:
    """Check if database schema is compatible with application requirements.

    Args:
        connection: SQLAlchemy database connection
        min_revision: Minimum required schema revision
        skip_check: If True, skip the check (for emergency scenarios)

    Raises:
        SchemaCompatibilityError: If schema is not compatible
        RuntimeError: If alembic_version table is in corrupted state
    """
    # Emergency bypass - must be explicitly enabled
    if skip_check or os.environ.get("OPENACE_SKIP_SCHEMA_CHECK") == "true":
        logger.warning(
            "Schema compatibility check SKIPPED (OPENACE_SKIP_SCHEMA_CHECK=true). "
            "This should only be used in emergency scenarios!"
        )
        return

    current_revision = get_database_revision(connection)

    # Fresh database (no alembic_version table) - allow through
    # The install path will create schema from baseline and stamp version
    if current_revision is None:
        logger.info(
            "Fresh database detected (no alembic_version). "
            "Schema will be initialized from baseline."
        )
        return

    # Check if current revision meets minimum requirement
    # We use a simple heuristic: if the revision is not at least baseline,
    # it's considered incompatible. Revision IDs are timestamps, so we can
    # compare them lexicographically. However, we need to handle edge cases
    # where the revision ID doesn't follow the expected pattern.
    #
    # A revision is considered compatible if:
    # 1. It matches the min_revision exactly, OR
    # 2. It's a known valid revision (starts with a timestamp pattern)
    #    and is lexicographically >= min_revision, OR
    # 3. It's a custom/unknown revision (in which case we reject it)
    if current_revision == min_revision:
        logger.info(f"Database schema version check passed: {current_revision}")
        return

    # Heuristic: valid revisions typically start with a date pattern (YYYYMMDD)
    # or are the baseline. Unknown revisions are rejected.
    if not (current_revision.startswith("20") or current_revision == "baseline_2026_06_23"):
        # Unknown revision format - reject it
        raise SchemaCompatibilityError(
            f"Database schema revision '{current_revision}' is not recognized. "
            f"Minimum supported revision is '{min_revision}'. "
            f"Run 'alembic upgrade head' to migrate database.",
            current_revision=current_revision,
            min_revision=min_revision,
        )

    # Known revision format - check if it's at least baseline
    # SPECIAL CASE: baseline starts with 'b', timestamp revisions start with '20'
    # Lexicographical comparison fails here: '2' < 'b', so "20260802" < "baseline"
    # We handle this explicitly: any timestamp revision (starts with '20') is
    # assumed to be >= baseline because baseline is the starting point.
    if current_revision.startswith("20") and min_revision.startswith("baseline"):
        # Timestamp revision (starts with '20') is always >= baseline
        # because baseline is the starting point and timestamp revisions come after
        logger.info(f"Database schema version check passed: {current_revision}")
        return

    # For other cases, use lexicographical comparison
    # (e.g., comparing two timestamp revisions)
    if current_revision < min_revision:
        raise SchemaCompatibilityError(
            f"Database schema revision '{current_revision}' is below minimum "
            f"supported revision '{min_revision}'. "
            f"Run 'alembic upgrade head' to migrate database.",
            current_revision=current_revision,
            min_revision=min_revision,
        )

    logger.info(f"Database schema version check passed: {current_revision}")


# Track whether deprecation warning has been logged
_deprecation_warned = False


def get_environment_mode() -> str:
    """Get environment mode (deprecated - remove in v2.1.0).

    Issue #2331: Compatibility layer maintaining backward compatibility.

    Migration: Use app.utils.security_mode.get_security_mode() instead.
    This wrapper ensures backward compatibility during migration period.

    Returns:
        "production" or "development"
    """
    global _deprecation_warned

    # Log deprecation warning once per process
    if not _deprecation_warned:
        logger.warning(
            "get_environment_mode() is deprecated and will be removed in v2.1.0. "
            "Use app.utils.security_mode.get_security_mode() instead. "
            "Migration guide: https://github.com/open-ace/open-ace/issues/2331"
        )
        _deprecation_warned = True

    # Delegate to unified security mode API
    from app.utils.security_mode import get_security_mode

    mode = get_security_mode()
    return mode.value  # Return string for backward compatibility


def is_production_environment() -> bool:
    """Check if running in production environment (deprecated).

    Issue #2331: Use security_mode.is_production() instead.

    Returns:
        True if production, False otherwise
    """
    from app.utils.security_mode import is_production

    return is_production()
