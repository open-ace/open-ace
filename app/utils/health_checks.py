"""Health check utilities for Kubernetes probes.

Issue #2186: Provides functions for liveness and readiness checks.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Global initialization status tracker
_init_status: dict[str, Any] = {
    "completed": False,
    "error": None,
    "error_category": None,
}


def set_init_error(error: str, category: str = "unknown") -> None:
    """Record initialization failure during startup.

    Args:
        error: Error message describing the failure.
        category: Error category for classification.
    """
    _init_status["error"] = error
    _init_status["error_category"] = category
    logger.error(f"Initialization failed [{category}]: {error}")


def mark_init_completed() -> None:
    """Mark initialization as completed successfully."""
    _init_status["completed"] = True
    logger.info("Application initialization completed")


def check_initialization_status() -> dict[str, Any]:
    """Check if initialization completed successfully.

    Returns:
        Dict with status and error info if failed.
    """
    if _init_status["error"]:
        return {
            "status": "error",
            "error": _init_status["error"],
            "category": _init_status["error_category"],
        }
    elif not _init_status["completed"]:
        return {"status": "pending"}
    else:
        return {"status": "ok"}


def check_database_connection(timeout: float = 2.0) -> dict[str, Any]:
    """Check database connection with timeout.

    Uses a dedicated probe connection (not from pool) to avoid
    connection pool exhaustion.

    Args:
        timeout: Connection timeout in seconds.

    Returns:
        Dict with status and optional error message.
    """
    try:
        from app.repositories.database import is_postgresql

        if is_postgresql():
            # Use probe-specific connection
            conn = _get_postgresql_probe_connection(timeout)
            if conn is None:
                return {"status": "error", "error": "connection_failed"}
            try:
                # PgConnectionWrapper delegates to a psycopg2 connection, which
                # exposes cursor() but not execute(); run the probe through a
                # cursor instead of conn.execute() (would raise AttributeError
                # and make /readyz report connection_failed forever).
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                return {"status": "ok"}
            finally:
                conn.close()
        else:
            # SQLite connection
            from app.repositories.database import Database

            db = Database()
            conn = db.get_connection()
            try:
                conn.execute("SELECT 1")
                conn.close()
                return {"status": "ok"}
            except Exception:
                conn.close()
                raise

    except Exception as e:
        error_msg = _sanitize_error_message(e)
        logger.warning(f"Database health check failed: {error_msg}")
        return {"status": "error", "error": error_msg}


def _get_postgresql_probe_connection(timeout: float):
    """Get PostgreSQL connection for probe (not from pool).

    Args:
        timeout: Connection timeout in seconds.

    Returns:
        Connection or None if failed.
    """
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor

        from app.repositories.database import get_database_url

        url = get_database_url()

        # Add connect_timeout to URL
        if "?" in url:
            url_with_timeout = f"{url}&connect_timeout={int(timeout)}"
        else:
            url_with_timeout = f"{url}?connect_timeout={int(timeout)}"

        conn = psycopg2.connect(url_with_timeout)

        # Set statement timeout
        with conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = '{int(timeout * 1000)}'")

        # Wrap for compatibility
        from app.repositories.database import PgConnectionWrapper

        return PgConnectionWrapper(conn, cursor_factory=RealDictCursor, from_pool=False)

    except Exception as e:
        logger.debug(f"Probe connection failed: {e}")
        return None


def check_config_directory() -> dict[str, Any]:
    """Check if config directory exists and is writable.

    Returns:
        Dict with status and optional error message.
    """
    config_dir = os.environ.get("OPENACE_CONFIG_DIR")

    if not config_dir:
        # Default config directory
        config_dir = os.path.join(os.path.expanduser("~"), ".open-ace")

    try:
        # Check if directory exists
        if not os.path.exists(config_dir):
            return {"status": "error", "error": "not_found", "path": config_dir}

        # Check if writable
        if not os.access(config_dir, os.W_OK):
            return {"status": "error", "error": "not_writable", "path": config_dir}

        return {"status": "ok", "path": config_dir}

    except Exception as e:
        error_msg = _sanitize_error_message(e)
        return {"status": "error", "error": error_msg}


def check_workspace_directory() -> dict[str, Any]:
    """Check if workspace directory is accessible.

    Returns:
        Dict with status and optional error message.
    """
    try:
        from app.utils.workspace import get_workspace_base_dir

        workspace_dir = get_workspace_base_dir()

        if not os.path.exists(workspace_dir):
            return {"status": "error", "error": "not_found", "path": workspace_dir}

        if not os.access(workspace_dir, os.R_OK):
            return {"status": "error", "error": "not_readable", "path": workspace_dir}

        return {"status": "ok", "path": workspace_dir}

    except Exception as e:
        error_msg = _sanitize_error_message(e)
        return {"status": "error", "error": error_msg}


def check_encryption_registry() -> dict[str, Any]:
    """Check if encryption key registry is functional.

    Returns:
        Dict with status and optional error message.
    """
    try:
        from app.utils.encryption_key_registry import get_registry

        registry = get_registry()

        # Quick validation - just check if registry is initialized
        if registry.get_key_count() == 0:
            return {"status": "error", "error": "no_keys_configured"}

        return {"status": "ok", "key_count": registry.get_key_count()}

    except ImportError:
        # Encryption not available - may be optional in dev mode
        return {"status": "skipped", "reason": "not_configured"}
    except Exception as e:
        error_msg = _sanitize_error_message(e)
        return {"status": "error", "error": error_msg}


def _sanitize_error_message(error: Exception) -> str:
    """Convert exception to safe error message without sensitive info.

    Args:
        error: The exception to sanitize.

    Returns:
        Safe error message string.
    """
    error_str = str(error).lower()

    # Keyword-based classification to avoid leaking sensitive info
    sensitive_keywords = [
        "password",
        "secret",
        "key",
        "token",
        "credential",
        "api_key",
        "private",
    ]

    for keyword in sensitive_keywords:
        if keyword in error_str:
            return "authentication_failed"

    # Map common error patterns to safe messages
    if "connection" in error_str or "connect" in error_str:
        return "connection_failed"
    elif "timeout" in error_str or "timed out" in error_str:
        return "timeout"
    elif "permission" in error_str or "access" in error_str:
        return "permission_denied"
    elif "not found" in error_str:
        return "not_found"
    elif "refused" in error_str:
        return "connection_refused"
    else:
        return "internal_error"


def get_current_timestamp() -> str:
    """Get current UTC timestamp in ISO format.

    Returns:
        ISO format timestamp string.
    """
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run_check_with_timeout(check_func, timeout_seconds: float = 1.0) -> dict[str, Any]:
    """Run a health check function with timeout.

    Uses ThreadPoolExecutor for cross-platform compatibility
    (works on Windows, Linux, and with gevent).

    Args:
        check_func: The check function to run.
        timeout_seconds: Timeout in seconds.

    Returns:
        Dict with status and optional error message.
    """
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as FuturesTimeoutError

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(check_func)
            return future.result(timeout=timeout_seconds)
    except FuturesTimeoutError:
        logger.warning(f"Health check timed out after {timeout_seconds}s")
        return {"status": "error", "error": "timeout"}
    except Exception as e:
        error_msg = _sanitize_error_message(e)
        logger.warning(f"Health check failed: {error_msg}")
        return {"status": "error", "error": error_msg}


def check_ssh_sync_failure() -> dict[str, Any]:
    """Check for SSH sync failure warning file (Issue #2328).

    The secure SSH sync creates a warning file when it fails.
    This check ensures the health check fails when SSH sync has failed,
    providing operational visibility.

    Returns:
        Dict with status. If SSH sync failed, returns error status.
    """
    warning_file = "/var/log/openace/ssh-sync-failure.warning"

    try:
        if os.path.exists(warning_file):
            # Read the warning file for details
            try:
                with open(warning_file, "r") as f:
                    content = f.read()
                # Extract first line (timestamp) for logging
                first_line = content.split("\n")[0] if content else "Unknown error"
                logger.error(f"SSH sync failure detected: {first_line}")
                return {
                    "status": "error",
                    "error": "ssh_sync_failure",
                    "details": first_line,
                    "warning_file": warning_file,
                }
            except Exception as e:
                logger.error(f"SSH sync failure detected but could not read warning file: {e}")
                return {
                    "status": "error",
                    "error": "ssh_sync_failure",
                    "warning_file": warning_file,
                }
        else:
            return {"status": "ok"}
    except Exception as e:
        error_msg = _sanitize_error_message(e)
        logger.warning(f"SSH sync check failed: {error_msg}")
        return {"status": "error", "error": error_msg}
