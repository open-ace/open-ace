"""Schema compatibility service using Alembic revision graph.

Issue: #2330 - Replace string-based schema checking with Alembic graph validation
"""

from __future__ import annotations

import hashlib
import logging
import os
import signal
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.repositories.schema_guard import get_environment_mode
from app.services.schema_compatibility_types import (
    BypassState,
    CompatibilityPolicy,
    CompatibilityResult,
    SchemaErrorCategory,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)

# Default timeout for graph traversal (seconds)
DEFAULT_GRAPH_TRAVERSAL_TIMEOUT = 5

# Cache for ScriptDirectory instances (process lifetime)
_script_directory_cache: dict[str, ScriptDirectory] = {}

# Bypass state tracking (process lifetime)
_bypass_states: dict[str, BypassState] = {}


class SchemaCompatibilityService:
    """Service for checking database schema compatibility using Alembic revision graph."""

    def __init__(self, alembic_config_path: str = "alembic.ini"):
        """Initialize service with Alembic configuration.

        Args:
            alembic_config_path: Path to alembic.ini file
        """
        self.alembic_config_path = alembic_config_path
        self._script_directory: ScriptDirectory | None = None

    def check_database_compatibility(
        self,
        connection: Connection,
        policy: CompatibilityPolicy | None = None,
    ) -> CompatibilityResult:
        """Check if database schema is compatible with application.

        Args:
            connection: Database connection
            policy: Compatibility policy (defaults to REQUIRE_HEAD)

        Returns:
            CompatibilityResult with detailed diagnostic information
        """
        start_time = time.time()

        if policy is None:
            policy = self._get_policy_from_config()

        try:
            # Check for emergency bypass first
            bypass_result = self._check_bypass(connection)
            if bypass_result.is_active:
                logger.warning(
                    "Schema compatibility check BYPASSED (emergency mode). "
                    f"Reason: {bypass_result.reason}"
                )
                return CompatibilityResult(
                    is_compatible=True,
                    bypass_active=True,
                    bypass_reason=bypass_result.reason,
                    check_duration_ms=(time.time() - start_time) * 1000,
                    diagnostic_message="Schema compatibility check bypassed for emergency. Database may be incompatible.",
                )

            # Load ScriptDirectory
            try:
                script_dir = self._load_script_directory()
            except Exception as e:
                return self._handle_script_directory_error(e, start_time)

            # Get current database revisions (all rows, not just first)
            current_heads = self._get_current_database_heads(connection)

            # Handle empty database states
            if current_heads is None:
                # No alembic_version table - fresh database
                return self._handle_fresh_database(connection, start_time)

            if len(current_heads) == 0:
                # Table exists but empty
                return CompatibilityResult(
                    is_compatible=False,
                    current_heads=[],
                    error_category=SchemaErrorCategory.EMPTY_VERSION_TABLE,
                    check_duration_ms=(time.time() - start_time) * 1000,
                    diagnostic_message="alembic_version table exists but has no revision rows.",
                )

            # Get expected head from migration files
            expected_heads = script_dir.get_heads()

            # Check for multiple heads in database
            if len(current_heads) > 1:
                return self._handle_multiple_heads(
                    current_heads, expected_heads, start_time
                )

            current_revision = current_heads[0]

            # Check for multiple heads in migration files
            if len(expected_heads) > 1:
                logger.warning(
                    f"Multiple heads in migration files: {expected_heads}. "
                    "This indicates a forked migration chain."
                )

            expected_head = expected_heads[0] if expected_heads else None

            # Validate revision is known (exists in migration files)
            revision_script = script_dir.get_revision(current_revision)
            if revision_script is None:
                return CompatibilityResult(
                    is_compatible=False,
                    current_heads=current_heads,
                    expected_head=expected_head,
                    error_category=SchemaErrorCategory.UNKNOWN_REVISION,
                    check_duration_ms=(time.time() - start_time) * 1000,
                    diagnostic_message=f"Database revision '{current_revision}' is not found in migration files.",
                )

            # Check if at expected head
            if policy == CompatibilityPolicy.REQUIRE_HEAD:
                if current_revision == expected_head:
                    return CompatibilityResult(
                        is_compatible=True,
                        current_heads=current_heads,
                        expected_head=expected_head,
                        check_duration_ms=(time.time() - start_time) * 1000,
                    )
                else:
                    # Check if behind head
                    missing = self._get_missing_migrations(
                        script_dir, current_revision, expected_head
                    )
                    return CompatibilityResult(
                        is_compatible=False,
                        current_heads=current_heads,
                        expected_head=expected_head,
                        missing_migrations=missing,
                        error_category=SchemaErrorCategory.BEHIND_HEAD,
                        check_duration_ms=(time.time() - start_time) * 1000,
                        diagnostic_message=self._format_behind_head_message(
                            current_revision, expected_head, missing
                        ),
                    )

            # Check lineage for SUPPORT_ANCESTRY policy
            if policy == CompatibilityPolicy.SUPPORT_ANCESTRY:
                baseline_revision = self._get_baseline_revision(script_dir)
                if baseline_revision and self._is_in_lineage(
                    script_dir, current_revision, baseline_revision
                ):
                    return CompatibilityResult(
                        is_compatible=True,
                        current_heads=current_heads,
                        expected_head=expected_head,
                        check_duration_ms=(time.time() - start_time) * 1000,
                    )

            # For SUPPORT_N_1, check if within one migration of head
            if policy == CompatibilityPolicy.SUPPORT_N_1:
                missing = self._get_missing_migrations(
                    script_dir, current_revision, expected_head
                )
                if len(missing) <= 1:
                    return CompatibilityResult(
                        is_compatible=True,
                        current_heads=current_heads,
                        expected_head=expected_head,
                        missing_migrations=missing,
                        check_duration_ms=(time.time() - start_time) * 1000,
                    )
                else:
                    return CompatibilityResult(
                        is_compatible=False,
                        current_heads=current_heads,
                        expected_head=expected_head,
                        missing_migrations=missing,
                        error_category=SchemaErrorCategory.BEHIND_HEAD,
                        check_duration_ms=(time.time() - start_time) * 1000,
                        diagnostic_message=self._format_behind_head_message(
                            current_revision, expected_head, missing
                        ),
                    )

            # Default: require head
            return CompatibilityResult(
                is_compatible=False,
                current_heads=current_heads,
                expected_head=expected_head,
                error_category=SchemaErrorCategory.BEHIND_HEAD,
                check_duration_ms=(time.time() - start_time) * 1000,
            )

        except TimeoutError as e:
            logger.error(f"Schema compatibility check timed out: {e}")
            return CompatibilityResult(
                is_compatible=False,
                error_category=SchemaErrorCategory.SCRIPT_DIRECTORY_ERROR,
                check_duration_ms=(time.time() - start_time) * 1000,
                diagnostic_message=f"Schema compatibility check timed out: {e}",
            )
        except Exception as e:
            logger.error(f"Unexpected error during schema compatibility check: {e}")

            # Check if this is a revision-not-found error from Alembic
            error_str = str(e).lower()
            if "can't locate revision" in error_str or "unknown revision" in error_str:
                # This is a schema incompatibility, not a system error
                # Fail closed even in development mode
                return CompatibilityResult(
                    is_compatible=False,
                    error_category=SchemaErrorCategory.UNKNOWN_REVISION,
                    check_duration_ms=(time.time() - start_time) * 1000,
                    diagnostic_message=f"Database schema revision is not recognized: {e}",
                )

            # Fail closed in production
            if get_environment_mode() == "production":
                return CompatibilityResult(
                    is_compatible=False,
                    error_category=SchemaErrorCategory.SCRIPT_DIRECTORY_ERROR,
                    check_duration_ms=(time.time() - start_time) * 1000,
                    diagnostic_message=f"Schema compatibility check failed: {e}",
                )
            else:
                # Fail open in development for system errors (not schema incompatibility)
                logger.warning(f"Allowing startup in development mode despite error: {e}")
                return CompatibilityResult(
                    is_compatible=True,
                    check_duration_ms=(time.time() - start_time) * 1000,
                    diagnostic_message=f"Schema check bypassed in development mode due to error: {e}",
                )

    def _load_script_directory(self) -> ScriptDirectory:
        """Load Alembic ScriptDirectory with caching."""
        cache_key = self.alembic_config_path

        if cache_key in _script_directory_cache:
            return _script_directory_cache[cache_key]

        config_path = Path(self.alembic_config_path)
        if not config_path.exists():
            if get_environment_mode() == "production":
                raise FileNotFoundError(
                    f"alembic.ini not found at {self.alembic_config_path} in production"
                )
            else:
                # Development mode - this shouldn't happen but we handle gracefully
                logger.warning(
                    f"alembic.ini not found at {self.alembic_config_path}. "
                    "This should not happen in a proper deployment."
                )
                # Return a minimal ScriptDirectory if possible
                # This will likely fail later, but gives better error message
                raise FileNotFoundError(
                    f"alembic.ini not found at {self.alembic_config_path}"
                )

        cfg = Config(str(config_path))
        script_dir = ScriptDirectory.from_config(cfg)
        _script_directory_cache[cache_key] = script_dir
        return script_dir

    def _get_current_database_heads(self, connection: Connection) -> list[str] | None:
        """Get all revision IDs from alembic_version table.

        Returns:
            List of revision IDs, or None if table doesn't exist
        """
        # Check if alembic_version table exists
        try:
            # Use information_schema for PostgreSQL, sqlite_master for SQLite
            if hasattr(connection, "dialect") and connection.dialect.name == "postgresql":
                table_exists_query = (
                    "SELECT EXISTS ("
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = 'alembic_version'"
                    ")"
                )
            else:
                table_exists_query = (
                    "SELECT EXISTS ("
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='alembic_version'"
                    ")"
                )

            result = connection.execute(sa.text(table_exists_query))
            if not result.scalar():
                return None

            # Get all revisions (not just first row)
            result = connection.execute(sa.text("SELECT version_num FROM alembic_version"))
            rows = result.fetchall()
            return [str(row[0]) for row in rows] if rows else []

        except Exception as e:
            logger.error(f"Error querying alembic_version: {e}")
            raise

    def _get_expected_head(self, script_dir: ScriptDirectory) -> str | None:
        """Get expected head revision from migration files."""
        heads = script_dir.get_heads()
        return heads[0] if heads else None

    def _is_in_lineage(
        self, script_dir: ScriptDirectory, revision: str, ancestor: str
    ) -> bool:
        """Check if revision is descended from ancestor using graph traversal.

        Args:
            script_dir: Alembic ScriptDirectory
            revision: Revision to check
            ancestor: Expected ancestor revision

        Returns:
            True if ancestor is in revision's ancestry
        """
        try:
            with timeout_context(DEFAULT_GRAPH_TRAVERSAL_TIMEOUT):
                ancestors = self._get_ancestors(script_dir, revision)
                return ancestor in ancestors
        except TimeoutError:
            logger.error(f"Graph traversal timed out for revision {revision}")
            raise

    def _get_ancestors(self, script_dir: ScriptDirectory, revision: str) -> set[str]:
        """Walk down_revision chain to collect all ancestors.

        Handles merge migrations with multiple parents (tuple down_revision).
        """
        ancestors = set()
        to_visit = [revision]

        while to_visit:
            current = to_visit.pop()
            if current in ancestors:
                continue
            ancestors.add(current)

            script = script_dir.get_revision(current)
            if script is None:
                # Missing migration file
                logger.warning(f"Missing migration file for revision: {current}")
                continue

            # Handle both scalar and tuple down_revision
            if script.down_revision:
                if isinstance(script.down_revision, tuple):
                    to_visit.extend(script.down_revision)
                else:
                    to_visit.append(script.down_revision)

        return ancestors

    def _get_missing_migrations(
        self, script_dir: ScriptDirectory, current: str, target: str
    ) -> list[str]:
        """Get list of migrations between current and target.

        Returns ordered list of migration IDs needed to reach target.
        """
        if current == target:
            return []

        # Walk from target backwards to current
        path = []
        visited = set()
        current_in_path = False

        def walk_to_current(rev: str) -> bool:
            nonlocal current_in_path
            if rev in visited:
                return current_in_path
            visited.add(rev)

            if rev == current:
                current_in_path = True
                return True

            script = script_dir.get_revision(rev)
            if script is None:
                return False

            if script.down_revision:
                if isinstance(script.down_revision, tuple):
                    for parent in script.down_revision:
                        if walk_to_current(parent):
                            path.append(rev)
                            current_in_path = True
                            return True
                else:
                    if walk_to_current(script.down_revision):
                        path.append(rev)
                        current_in_path = True
                        return True

            return current_in_path

        walk_to_current(target)
        return list(reversed(path))

    def _get_baseline_revision(self, script_dir: ScriptDirectory) -> str | None:
        """Get baseline revision from migration graph."""
        # Import here to avoid circular dependency
        try:
            from migrations.baseline import BASELINE_REVISION

            return BASELINE_REVISION
        except ImportError:
            # Fallback: find revision with no down_revision
            for revision in script_dir.walk_revisions():
                if revision.down_revision is None:
                    return revision.revision
            return None

    def _check_bypass(self, connection: Connection) -> BypassState:
        """Check if emergency bypass is authorized."""
        bypass_env = os.environ.get("OPENACE_EMERGENCY_SCHEMA_BYPASS", "").lower()

        if bypass_env != "true":
            return BypassState(is_active=False)

        # Check security mode
        security_mode = os.environ.get("OPENACE_SECURITY_MODE", "").lower()
        if security_mode != "production":
            logger.warning(
                "Emergency bypass enabled but OPENACE_SECURITY_MODE != production. "
                "Bypass denied."
            )
            return BypassState(is_active=False)

        # Get database hash for rate limiting
        db_url = self._get_database_url_safe(connection)
        db_hash = hashlib.sha256(db_url.encode()).hexdigest()

        # Check rate limiting (1 bypass per hour per database)
        current_time = time.time()
        if db_hash in _bypass_states:
            last_bypass = _bypass_states[db_hash]

            # Check if bypass has expired
            if last_bypass.expires_at and current_time > last_bypass.expires_at:
                logger.warning(
                    f"Emergency bypass has expired. "
                    f"Expired at: {last_bypass.expires_at}, Current: {current_time}"
                )
                return BypassState(is_active=False)

            # Check rate limiting
            if last_bypass.enabled_at and (current_time - last_bypass.enabled_at) < 3600:
                logger.warning(
                    f"Emergency bypass rate limited for database. "
                    f"Last bypass: {last_bypass.enabled_at}, "
                    f"Current: {current_time}"
                )
                return BypassState(is_active=False)

        # Check expiry
        expiry_hours = int(os.environ.get("OPENACE_EMERGENCY_BYPASS_EXPIRY_HOURS", "24"))
        expires_at = current_time + (expiry_hours * 3600)

        # Create bypass state
        bypass_state = BypassState(
            is_active=True,
            enabled_at=current_time,
            expires_at=expires_at,
            database_hash=db_hash,
            reason="operator_initiated",
        )

        # Store bypass state
        _bypass_states[db_hash] = bypass_state

        # Log audit event
        logger.critical(
            "EMERGENCY_SCHEMA_BYPASS_ENABLED",
            extra={
                "event": "emergency_schema_bypass_enabled",
                "database_hash": db_hash,
                "enabled_at": current_time,
                "expires_at": expires_at,
                "expiry_hours": expiry_hours,
                "reason": "operator_initiated",
            },
        )

        return bypass_state

    def _get_database_url_safe(self, connection: Connection) -> str:
        """Get database URL without password for hashing."""
        try:
            # Try to get URL from environment
            url = os.environ.get("DATABASE_URL", "")
            if url:
                # Remove password component
                if "@" in url:
                    # Format: postgresql://user:pass@host/db
                    parts = url.split("@")
                    if len(parts) == 2:
                        credentials = parts[0].split("//")[1] if "://" in parts[0] else parts[0]
                        host_part = parts[1]
                        return f"postgresql://{credentials.split(':')[0]}@{host_part}"
                return url
            return "unknown"
        except Exception:
            return "unknown"

    def _get_policy_from_config(self) -> CompatibilityPolicy:
        """Get compatibility policy from environment configuration."""
        policy_str = os.environ.get("OPENACE_COMPATIBILITY_POLICY", "require_head").lower()

        try:
            return CompatibilityPolicy(policy_str)
        except ValueError:
            logger.warning(f"Unknown policy '{policy_str}', defaulting to REQUIRE_HEAD")
            return CompatibilityPolicy.REQUIRE_HEAD

    def _handle_script_directory_error(
        self, error: Exception, start_time: float
    ) -> CompatibilityResult:
        """Handle ScriptDirectory loading failures."""
        logger.error(f"Failed to load ScriptDirectory: {error}")

        # Fail closed in production
        if get_environment_mode() == "production":
            return CompatibilityResult(
                is_compatible=False,
                error_category=SchemaErrorCategory.SCRIPT_DIRECTORY_ERROR,
                check_duration_ms=(time.time() - start_time) * 1000,
                diagnostic_message=f"Failed to load Alembic configuration: {error}",
            )
        else:
            # Fail open in development
            logger.warning(
                f"Allowing startup in development mode despite ScriptDirectory error: {error}"
            )
            return CompatibilityResult(
                is_compatible=True,
                check_duration_ms=(time.time() - start_time) * 1000,
                diagnostic_message=f"ScriptDirectory error bypassed in development: {error}",
            )

    def _handle_fresh_database(
        self, connection: Connection, start_time: float
    ) -> CompatibilityResult:
        """Handle fresh database (no alembic_version table)."""
        # Check database-level guard in production
        if get_environment_mode() == "production":
            # Check for schema_metadata table
            if self._check_schema_metadata_exists(connection):
                # Database has been initialized, but alembic_version missing
                # This is a corrupted state
                return CompatibilityResult(
                    is_compatible=False,
                    error_category=SchemaErrorCategory.FRESH_DATABASE,
                    check_duration_ms=(time.time() - start_time) * 1000,
                    diagnostic_message=(
                        "Database appears initialized (schema_metadata exists) "
                        "but alembic_version table missing. This indicates a corrupted state."
                    ),
                )
            else:
                # Truly fresh database - fail in production
                return CompatibilityResult(
                    is_compatible=False,
                    error_category=SchemaErrorCategory.FRESH_DATABASE,
                    check_duration_ms=(time.time() - start_time) * 1000,
                    diagnostic_message=(
                        "Fresh database detected in production. "
                        "Web/scheduler workers must not start on uninitialized database. "
                        "Run migration job first: alembic upgrade head"
                    ),
                )
        else:
            # Development mode - allow fresh database
            logger.info("Fresh database detected in development mode - allowing startup")
            return CompatibilityResult(
                is_compatible=True,
                check_duration_ms=(time.time() - start_time) * 1000,
                diagnostic_message="Fresh database allowed in development mode",
            )

    def _check_schema_metadata_exists(self, connection: Connection) -> bool:
        """Check if schema_metadata table exists (database-level guard)."""
        try:
            if hasattr(connection, "dialect") and connection.dialect.name == "postgresql":
                query = (
                    "SELECT EXISTS ("
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = 'schema_metadata'"
                    ")"
                )
            else:
                query = (
                    "SELECT EXISTS ("
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='schema_metadata'"
                    ")"
                )

            result = connection.execute(sa.text(query))
            return bool(result.scalar())
        except Exception as e:
            logger.debug(f"Error checking schema_metadata: {e}")
            return False

    def _handle_multiple_heads(
        self, current_heads: list[str], expected_heads: list[str], start_time: float
    ) -> CompatibilityResult:
        """Handle multiple heads detected in database."""
        heads_str = ", ".join(current_heads)
        expected_str = ", ".join(expected_heads) if expected_heads else "none"

        return CompatibilityResult(
            is_compatible=False,
            current_heads=current_heads,
            expected_head=expected_heads[0] if expected_heads else None,
            error_category=SchemaErrorCategory.MULTIPLE_HEADS,
            check_duration_ms=(time.time() - start_time) * 1000,
            diagnostic_message=(
                f"Multiple heads detected in database: {heads_str}\n"
                f"Expected: single head ({expected_str})\n"
                f"Recovery:\n"
                f"  1. Create merge migration: alembic merge -m 'merge_heads' {heads_str.replace(', ', ' ')}\n"
                f"  2. Apply merge: alembic upgrade head\n"
                f"Documentation: https://alembic.sqlalchemy.org/en/latest/branches.html"
            ),
        )

    def _format_behind_head_message(
        self, current: str, expected: str, missing: list[str]
    ) -> str:
        """Format diagnostic message for behind_head error."""
        missing_count = len(missing)
        missing_list = "\n  - ".join(missing) if missing else "none"

        return (
            f"Database schema revision '{current}' is behind expected head '{expected}'\n"
            f"Missing migrations ({missing_count}):\n  - {missing_list}\n"
            f"Recovery:\n"
            f"  Run: alembic upgrade head\n"
            f"  Estimated downtime: <5 seconds\n"
            f"  Backup recommended before upgrade"
        )


@contextmanager
def timeout_context(seconds: int):
    """Context manager for timeout using signal."""

    def timeout_handler(signum, frame):
        raise TimeoutError(f"Operation timed out after {seconds} seconds")

    # Only works on Unix systems
    try:
        old_handler = signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(seconds)
        try:
            yield
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
    except (OSError, ValueError):
        # Signal not available (e.g., Windows), just yield
        yield


# Singleton instance
_service_instance: SchemaCompatibilityService | None = None


def get_schema_compatibility_service() -> SchemaCompatibilityService:
    """Get singleton instance of SchemaCompatibilityService."""
    global _service_instance
    if _service_instance is None:
        _service_instance = SchemaCompatibilityService()
    return _service_instance
