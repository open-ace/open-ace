"""Add indexes for user mapping optimization

Revision ID: 20260715_002_add_users_mapping_indexes
Revises: 20260715_001_add_last_ci_failure_head_sha
Create Date: 2026-07-15

Issue: #1574
PR #1572 introduced subqueries to resolve user_id from sender_name in both
get_batch_aggregates and refresh_stats methods. These subqueries match against
users.username and users.system_account columns.

Without indexes, these subqueries can cause full table scans on the users table,
especially problematic in refresh_stats which executes the subquery for every row
in daily_messages during aggregation.

This migration adds indexes to optimize the subquery performance:
- idx_users_username: Index on username for exact match lookups
- idx_users_system_account: Index on system_account for pattern matching

PostgreSQL: Uses CONCURRENTLY to avoid locking the table during creation
SQLite: Uses regular CREATE INDEX (no CONCURRENTLY support)
"""

import logging
from typing import Union

import sqlalchemy as sa
from alembic import op

log = logging.getLogger(__name__)

revision: str = "20260715_002_add_users_mapping_indexes"
down_revision: Union[str, None] = "20260715_001_add_last_ci_failure_head_sha"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def _is_postgresql() -> bool:
    """Check if the database is PostgreSQL."""
    bind = op.get_bind()
    return bind.dialect.name == "postgresql"


def upgrade() -> None:
    """Add indexes for username and system_account columns.

    Uses the approved MIG002 pattern for PostgreSQL CONCURRENTLY operations:
    wrapped in autocommit_block() with postgresql_concurrently=True.
    """
    connection = op.get_bind()

    # Check if indexes already exist
    inspector = sa.inspect(connection)
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("users")}

    if _is_postgresql():
        # PostgreSQL: Use CONCURRENTLY to avoid locking the table
        # This allows the index to be built without blocking writes
        # MIG002 compliant: autocommit_block() + postgresql_concurrently=True

        if "idx_users_username" not in existing_indexes:
            log.info("Creating idx_users_username index on users table (PostgreSQL)")
            with op.get_context().autocommit_block():
                op.create_index(
                    "idx_users_username",
                    "users",
                    ["username"],
                    postgresql_concurrently=True,
                    postgresql_where=sa.text("deleted_at IS NULL AND is_active = true"),
                )
        else:
            log.info("idx_users_username already exists, skipping")

        if "idx_users_system_account" not in existing_indexes:
            log.info("Creating idx_users_system_account index on users table (PostgreSQL)")
            with op.get_context().autocommit_block():
                op.create_index(
                    "idx_users_system_account",
                    "users",
                    ["system_account"],
                    postgresql_concurrently=True,
                    postgresql_where=sa.text(
                        "deleted_at IS NULL AND is_active = true AND system_account IS NOT NULL"
                    ),
                )
        else:
            log.info("idx_users_system_account already exists, skipping")
    else:
        # SQLite: Regular CREATE INDEX (no CONCURRENTLY support)
        # SQLite supports partial indexes since 3.8.0

        if "idx_users_username" not in existing_indexes:
            log.info("Creating idx_users_username index on users table (SQLite)")
            op.create_index(
                "idx_users_username",
                "users",
                ["username"],
                unique=False,
                sqlite_where=sa.text("deleted_at IS NULL AND is_active = true"),
            )
        else:
            log.info("idx_users_username already exists, skipping")

        if "idx_users_system_account" not in existing_indexes:
            log.info("Creating idx_users_system_account index on users table (SQLite)")
            op.create_index(
                "idx_users_system_account",
                "users",
                ["system_account"],
                unique=False,
                sqlite_where=sa.text(
                    "deleted_at IS NULL AND is_active = true AND system_account IS NOT NULL"
                ),
            )
        else:
            log.info("idx_users_system_account already exists, skipping")


def downgrade() -> None:
    """Remove indexes for username and system_account columns.

    Uses the approved MIG002 pattern for PostgreSQL CONCURRENTLY operations:
    wrapped in autocommit_block() with postgresql_concurrently=True.
    """
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("users")}

    if _is_postgresql():
        # PostgreSQL: Use CONCURRENTLY to avoid locking the table
        # MIG002 compliant: autocommit_block() + postgresql_concurrently=True
        if "idx_users_username" in existing_indexes:
            log.info("Dropping idx_users_username index")
            with op.get_context().autocommit_block():
                op.drop_index(
                    "idx_users_username",
                    table_name="users",
                    postgresql_concurrently=True,
                )

        if "idx_users_system_account" in existing_indexes:
            log.info("Dropping idx_users_system_account index")
            with op.get_context().autocommit_block():
                op.drop_index(
                    "idx_users_system_account",
                    table_name="users",
                    postgresql_concurrently=True,
                )
    else:
        # SQLite: Regular DROP INDEX (no CONCURRENTLY support)
        if "idx_users_username" in existing_indexes:
            log.info("Dropping idx_users_username index")
            op.drop_index("idx_users_username", table_name="users")

        if "idx_users_system_account" in existing_indexes:
            log.info("Dropping idx_users_system_account index")
            op.drop_index("idx_users_system_account", table_name="users")
