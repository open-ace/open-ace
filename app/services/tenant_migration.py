"""
Tenant Migration Service for Issue #2163

Implements cascade update mechanism when user tenant_id changes,
including session invalidation and progress tracking.
"""

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.repositories.database import Database

logger = logging.getLogger(__name__)


@dataclass
class MigrationResult:
    """Result of a tenant migration operation."""

    success: bool
    user_id: int
    old_tenant_id: int
    new_tenant_id: int
    affected_sessions: int = 0
    affected_projects: int = 0
    error: str | None = None
    batch_number: int | None = None


class TenantMigrationService:
    """Service for migrating users between tenants with cascade updates."""

    def __init__(self, db: Database | None = None):
        """Initialize migration service."""
        self.db = db or Database()
        self._lock = threading.Lock()

    def _get_database_type(self) -> str:
        """
        Detect database type for compatibility handling.

        Returns:
            'postgresql', 'sqlite', or 'unknown'
        """
        try:
            # Try to detect database type from connection
            if hasattr(self.db, '_connection'):
                conn = self.db._connection
                if hasattr(conn, 'dialect'):
                    return str(conn.dialect.name)
                # Try detecting from connection string
                if hasattr(conn, 'url'):
                    url = str(conn.url)
                    if 'postgresql' in url:
                        return 'postgresql'
                    elif 'sqlite' in url:
                        return 'sqlite'

            # Try executing PostgreSQL-specific query
            try:
                self.db.fetch_one("SELECT version()")
                return 'postgresql'
            except Exception:
                pass

            # Try SQLite-specific query
            try:
                self.db.fetch_one("SELECT sqlite_version()")
                return 'sqlite'
            except Exception:
                pass

            return 'unknown'
        except Exception:
            return 'unknown'

    def migrate_user_tenant(
        self,
        user_id: int,
        new_tenant_id: int,
        migrated_by: int,
        dry_run: bool = False,
    ) -> MigrationResult:
        """
        Migrate a user to a new tenant with cascade updates.

        Args:
            user_id: ID of user to migrate
            new_tenant_id: Target tenant ID
            migrated_by: ID of admin performing migration
            dry_run: If True, only validate without executing

        Returns:
            MigrationResult with details of the operation
        """
        try:
            # Get current user info
            user_row = self.db.fetch_one(
                "SELECT tenant_id, tenant_version FROM users WHERE id = ?",
                (user_id,)
            )
            if not user_row:
                return MigrationResult(
                    success=False,
                    user_id=user_id,
                    old_tenant_id=0,
                    new_tenant_id=new_tenant_id,
                    error="User not found"
                )

            old_tenant_id = user_row.get("tenant_id", 1)
            current_version = user_row.get("tenant_version", 1)

            if old_tenant_id == new_tenant_id:
                return MigrationResult(
                    success=True,
                    user_id=user_id,
                    old_tenant_id=old_tenant_id,
                    new_tenant_id=new_tenant_id,
                    affected_sessions=0,
                    affected_projects=0
                )

            if dry_run:
                # Count affected records
                sessions_count = self.db.fetch_one(
                    "SELECT COUNT(*) as count FROM agent_sessions WHERE user_id = ?",
                    (user_id,)
                )
                projects_count = self.db.fetch_one(
                    "SELECT COUNT(*) as count FROM projects WHERE created_by = ?",
                    (user_id,)
                )
                return MigrationResult(
                    success=True,
                    user_id=user_id,
                    old_tenant_id=old_tenant_id,
                    new_tenant_id=new_tenant_id,
                    affected_sessions=sessions_count.get("count", 0) if sessions_count else 0,
                    affected_projects=projects_count.get("count", 0) if projects_count else 0
                )

            # Execute migration in transaction
            affected_sessions = 0
            affected_projects = 0

            with self.db.transaction():
                # Get advisory lock for this user (PostgreSQL only)
                # For SQLite/other databases, rely on transaction isolation
                db_type = self._get_database_type()
                if db_type == "postgresql":
                    try:
                        self.db.execute(
                            "SELECT pg_advisory_xact_lock(1000000 + ?)",
                            (user_id,)
                        )
                    except Exception as e:
                        # Advisory lock not available, continue with transaction isolation
                        logger.warning(f"Advisory lock not available: {e}")

                # Update sessions
                self.db.execute(
                    """UPDATE agent_sessions
                       SET tenant_id = ?, tenant_version = tenant_version + 1
                       WHERE user_id = ?""",
                    (new_tenant_id, user_id)
                )

                # Query affected sessions count (more reliable than rowcount)
                sessions_result = self.db.fetch_one(
                    "SELECT COUNT(*) as count FROM agent_sessions WHERE user_id = ? AND tenant_id = ?",
                    (user_id, new_tenant_id)
                )
                affected_sessions = sessions_result.get("count", 0) if sessions_result else 0

                # Update projects
                self.db.execute(
                    """UPDATE projects
                       SET tenant_id = ?
                       WHERE created_by = ? AND tenant_id = ?""",
                    (new_tenant_id, user_id, old_tenant_id)
                )

                # Query affected projects count
                projects_result = self.db.fetch_one(
                    "SELECT COUNT(*) as count FROM projects WHERE created_by = ? AND tenant_id = ?",
                    (user_id, new_tenant_id)
                )
                affected_projects = projects_result.get("count", 0) if projects_result else 0

                # Update user tenant_id and version
                self.db.execute(
                    """UPDATE users
                       SET tenant_id = ?, tenant_version = tenant_version + 1
                       WHERE id = ?""",
                    (new_tenant_id, user_id)
                )

                # Log migration
                self.db.execute(
                    """INSERT INTO tenant_migrations
                       (user_id, old_tenant_id, new_tenant_id, migrated_by, migrated_at,
                        affected_sessions, affected_projects, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'completed')""",
                    (user_id, old_tenant_id, new_tenant_id, migrated_by,
                     datetime.now(timezone.utc), affected_sessions, affected_projects)
                )

            logger.info(
                f"Tenant migration completed: user={user_id}, "
                f"old_tenant={old_tenant_id}, new_tenant={new_tenant_id}, "
                f"sessions={affected_sessions}, projects={affected_projects}"
            )

            return MigrationResult(
                success=True,
                user_id=user_id,
                old_tenant_id=old_tenant_id,
                new_tenant_id=new_tenant_id,
                affected_sessions=affected_sessions,
                affected_projects=affected_projects
            )

        except Exception as e:
            logger.error(f"Tenant migration failed: user={user_id}, error={e}")
            return MigrationResult(
                success=False,
                user_id=user_id,
                old_tenant_id=0,
                new_tenant_id=new_tenant_id,
                error=str(e)
            )

    def migrate_users_batch(
        self,
        user_ids: list[int],
        new_tenant_id: int,
        migrated_by: int,
        batch_size: int = 10,
    ) -> list[MigrationResult]:
        """
        Migrate multiple users to a new tenant with batch processing.

        Args:
            user_ids: List of user IDs to migrate
            new_tenant_id: Target tenant ID
            migrated_by: ID of admin performing migration
            batch_size: Number of users per batch

        Returns:
            List of MigrationResult for each user
        """
        results = []
        total_batches = (len(user_ids) + batch_size - 1) // batch_size

        for batch_num in range(total_batches):
            start_idx = batch_num * batch_size
            end_idx = min(start_idx + batch_size, len(user_ids))
            batch_users = user_ids[start_idx:end_idx]

            for user_id in batch_users:
                result = self.migrate_user_tenant(
                    user_id, new_tenant_id, migrated_by
                )
                result.batch_number = batch_num + 1
                results.append(result)

                if not result.success:
                    logger.warning(
                        f"Batch migration stopped at batch {batch_num + 1} "
                        f"due to failure for user {user_id}"
                    )
                    # Mark all remaining users (including those in current batch after failure) as failed
                    remaining_start_idx = start_idx + batch_users.index(user_id) + 1
                    remaining_users = user_ids[remaining_start_idx:]
                    for remaining_id in remaining_users:
                        results.append(MigrationResult(
                            success=False,
                            user_id=remaining_id,
                            old_tenant_id=0,
                            new_tenant_id=new_tenant_id,
                            error="Batch migration stopped due to previous failure"
                        ))
                    return results

        return results

    def get_migration_progress(self, migration_id: int) -> dict[str, Any] | None:
        """
        Get progress of a migration operation.

        Args:
            migration_id: ID of the migration record

        Returns:
            Dictionary with migration progress or None if not found
        """
        row = self.db.fetch_one(
            """SELECT id, user_id, old_tenant_id, new_tenant_id,
                      migrated_by, migrated_at, affected_sessions, affected_projects,
                      batch_number, total_batches, status
               FROM tenant_migrations WHERE id = ?""",
            (migration_id,)
        )
        if not row:
            return None

        return dict(row)

    def validate_migration_possible(
        self,
        user_id: int,
        new_tenant_id: int
    ) -> tuple[bool, str]:
        """
        Validate if migration is possible.

        Args:
            user_id: ID of user to migrate
            new_tenant_id: Target tenant ID

        Returns:
            Tuple of (is_possible, error_message)
        """
        # Check user exists
        user_row = self.db.fetch_one(
            "SELECT id, tenant_id FROM users WHERE id = ?",
            (user_id,)
        )
        if not user_row:
            return False, "User not found"

        # Check tenant exists
        tenant_row = self.db.fetch_one(
            "SELECT id FROM tenants WHERE id = ?",
            (new_tenant_id,)
        )
        if not tenant_row:
            return False, "Target tenant not found"

        return True, ""

    def rollback_migration(self, migration_id: int) -> bool:
        """
        Rollback a completed migration.

        Args:
            migration_id: ID of the migration to rollback

        Returns:
            True if rollback successful, False otherwise
        """
        try:
            migration = self.get_migration_progress(migration_id)
            if not migration:
                logger.error(f"Migration {migration_id} not found for rollback")
                return False

            if migration.get("status") != "completed":
                logger.error(f"Migration {migration_id} is not in completed state")
                return False

            user_id = migration.get("user_id")
            old_tenant_id = migration.get("old_tenant_id")
            new_tenant_id = migration.get("new_tenant_id")

            # Reverse the migration
            result = self.migrate_user_tenant(
                user_id=user_id,
                new_tenant_id=old_tenant_id,
                migrated_by=migration.get("migrated_by")
            )

            if result.success:
                # Mark original migration as rolled back
                self.db.execute(
                    "UPDATE tenant_migrations SET status = 'rolled_back' WHERE id = ?",
                    (migration_id,)
                )
                logger.info(f"Migration {migration_id} rolled back successfully")
                return True
            else:
                logger.error(f"Rollback failed for migration {migration_id}: {result.error}")
                return False

        except Exception as e:
            logger.error(f"Rollback failed for migration {migration_id}: {e}")
            return False
