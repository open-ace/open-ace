"""Schema version guard for production deployments.

This module provides version checking and compatibility validation for database
schema, ensuring that production deployments only run against properly migrated
databases.

Issue: #2190, #2330
"""

from __future__ import annotations

import logging
import os
import warnings
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)

# DEPRECATED: Minimum supported schema revision (baseline)
# This constant is kept for backward compatibility but should not be used.
# Use SchemaCompatibilityService instead.
MIN_SUPPORTED_REVISION = "baseline_2026_06_23"

# DEPRECATED: Compatibility window constant
# Use OPENACE_COMPATIBILITY_POLICY environment variable instead.
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


def _execute_all(connection: Any, query: str) -> list[Any]:
    """Execute a query and return all rows, compatible with all connection types.

    Like :func:`_execute_scalar` but returns every row instead of just the first
    value.  Normalises ``RealDictCursor`` dict rows to tuples so that positional
    indexing (``row[0]``) works consistently across backends.

    Supports:
    - SQLAlchemy ``Connection`` (has ``.execute()``, no ``.cursor()``)
    - ``PgConnectionWrapper`` / psycopg2 (has ``.cursor()``, no ``.execute()``)
    - ``sqlite3.Connection`` (has both ``.cursor()`` and ``.execute()``)

    Args:
        connection: Database connection object.
        query: SQL query to execute.

    Returns:
        List of rows.  Dict rows (from ``RealDictCursor``) are converted to
        tuples so that ``row[0]`` always returns the first column.
    """
    if hasattr(connection, "cursor") and not hasattr(connection, "execute"):
        # Raw psycopg2 / PgConnectionWrapper (has .cursor(), no .execute())
        cursor = connection.cursor()
        try:
            cursor.execute(query)
            rows = cursor.fetchall()
        finally:
            cursor.close()
        # Normalise RealDictCursor dict rows to tuples for positional indexing
        if rows and isinstance(rows[0], dict):
            return [tuple(row.values()) for row in rows]
        return rows
    elif hasattr(connection, "cursor") and hasattr(connection, "execute"):
        # sqlite3 raw connection (has both .cursor() and .execute())
        # Use .execute() directly for consistency with SQLAlchemy path
        pass  # fall through to the SQLAlchemy-like path below

    # SQLAlchemy Connection (or sqlite3 via .execute())
    result = connection.execute(sa.text(query))
    return result.fetchall()


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

    DEPRECATED: This function only returns a single revision and cannot detect
    multiple heads. Use SchemaCompatibilityService._get_current_database_heads()
    instead.

    Args:
        connection: Database connection (SQLAlchemy Connection or PgConnectionWrapper)

    Returns:
        Current revision string, or None if alembic_version table doesn't exist
        (fresh database) or has no rows.
    """
    warnings.warn(
        "get_database_revision() is deprecated. "
        "Use SchemaCompatibilityService._get_current_database_heads() instead.",
        DeprecationWarning,
        stacklevel=2,
    )

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

    DEPRECATED: This function is deprecated. Use SchemaCompatibilityService instead.
    This wrapper maintains backward compatibility during the transition period.

    Args:
        connection: SQLAlchemy database connection
        min_revision: Minimum required schema revision (DEPRECATED, ignored)
        skip_check: If True, skip the check (for emergency scenarios)

    Raises:
        SchemaCompatibilityError: If schema is not compatible
        RuntimeError: If alembic_version table is in corrupted state

    Issue: #2330 - Refactored to use SchemaCompatibilityService
    """
    # Emergency bypass - must be explicitly enabled
    if skip_check or os.environ.get("OPENACE_SKIP_SCHEMA_CHECK") == "true":
        logger.warning(
            "Schema compatibility check SKIPPED (OPENACE_SKIP_SCHEMA_CHECK=true). "
            "This should only be used in emergency scenarios! "
            "Use OPENACE_EMERGENCY_SCHEMA_BYPASS instead."
        )
        return

    # Import here to avoid circular dependency
    from app.services.schema_compatibility_service import get_schema_compatibility_service
    from app.services.schema_compatibility_types import CompatibilityPolicy

    # Use new service
    service = get_schema_compatibility_service()

    # For backward compatibility, use SUPPORT_ANCESTRY policy in development mode
    # This allows any revision in the baseline lineage (not just head)
    # But use REQUIRE_HEAD in production for strict checking
    env_mode = get_environment_mode()
    if env_mode == "production":
        policy = CompatibilityPolicy.REQUIRE_HEAD
    else:
        # Development mode: use SUPPORT_ANCESTRY for backward compatibility
        # This allows any revision descended from baseline, matching old string-based heuristic
        policy = CompatibilityPolicy.SUPPORT_ANCESTRY

    result = service.check_database_compatibility(connection, policy)

    if not result.is_compatible:
        # Convert to SchemaCompatibilityError for backward compatibility
        error_msg = result.diagnostic_message or "Schema compatibility check failed"

        # Try to get current revision from various sources
        current_rev = None
        if result.current_heads and len(result.current_heads) > 0:
            current_rev = result.current_heads[0]

        # If we don't have revision info but error message contains it, extract it
        if current_rev is None and result.diagnostic_message:
            # Try to extract revision from error message
            # Pattern: "identified by 'revision_id'" or "revision 'revision_id'"
            import re

            match = re.search(r"identified by '([^']+)'", result.diagnostic_message)
            if match:
                current_rev = match.group(1)
            else:
                # Alternative pattern: "revision 'revision_id'"
                match = re.search(
                    r"revision[^']*'([^']+)'", result.diagnostic_message, re.IGNORECASE
                )
                if match:
                    current_rev = match.group(1)

        raise SchemaCompatibilityError(
            error_msg,
            current_revision=current_rev,
            min_revision=MIN_SUPPORTED_REVISION,
        )

    # Log success
    if result.current_heads:
        logger.info(f"Database schema version check passed: {result.current_heads[0]}")
    else:
        logger.info("Database schema version check passed")


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
    from app.utils.security_mode import get_security_mode, reset_security_mode_cache

    try:
        mode = get_security_mode()
        return mode.value  # Return string for backward compatibility
    except RuntimeError:
        # If security mode detection fails, reset cache and try with safe defaults
        # This can happen during test execution if environment is not fully set up
        # Issue #2331: Defensive fallback for edge cases
        reset_security_mode_cache()
        # Force development mode for safety
        import os

        current_mode = os.environ.get("OPENACE_SECURITY_MODE", "").strip()
        if not current_mode:
            os.environ["OPENACE_SECURITY_MODE"] = "development"
        mode = get_security_mode()
        return mode.value


def is_production_environment() -> bool:
    """Check if running in production environment (deprecated).

    Issue #2331: Use security_mode.is_production() instead.

    Returns:
        True if production, False otherwise
    """
    from app.utils.security_mode import is_production

    return is_production()
