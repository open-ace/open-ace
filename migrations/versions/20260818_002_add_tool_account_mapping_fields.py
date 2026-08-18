"""Add mapping source/status fields and conflict/backfill tables

Revision ID: 20260818_002_add_tool_account_mapping_fields
Revises: 20260818_001_repair_pending_revoke_drift
Create Date: 2026-08-18

Issue: #2761

This migration adds support for distinguishing discovered tool accounts
from predeclared ones, with conflict tracking and backfill logging.

Tables added:
- tool_account_conflicts: Conflict event logging
- backfill_logs: Message backfill operation logging
- mapping_migration_status: Migration progress tracking

Columns added to user_tool_accounts:
- mapping_source: Origin of the mapping (manual/auto/predeclared/import)
- mapping_status: Current status (pending/active/stale/conflict_*)
- discovered_at: First discovery timestamp
- last_activity_at: Last activity timestamp
- observed_message_count: Number of observed messages
- created_by: User who created this mapping
- tenant_id: Tenant ID (denormalized for query performance)
- version: Optimistic lock version number
"""

import logging

import sqlalchemy as sa
from alembic import op

log = logging.getLogger(__name__)

revision: str = "20260818_002_add_tool_account_mapping_fields"
down_revision: str | None = "20260818_001"
branch_labels: str | None = None
depends_on: str | None = None


def _is_postgresql() -> bool:
    """Check if the database is PostgreSQL."""
    bind = op.get_bind()
    return bind.dialect.name == "postgresql"


def upgrade() -> None:
    """Add mapping fields, conflict table, backfill logs, and migration status."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    # =========================================================================
    # 1. Add columns to user_tool_accounts
    # =========================================================================
    uta_columns = {col["name"] for col in inspector.get_columns("user_tool_accounts")}

    if "mapping_source" not in uta_columns:
        log.info("Adding mapping_source column to user_tool_accounts")
        op.add_column(
            "user_tool_accounts",
            sa.Column("mapping_source", sa.String(20), nullable=True, default="manual"),
        )

    if "mapping_status" not in uta_columns:
        log.info("Adding mapping_status column to user_tool_accounts")
        op.add_column(
            "user_tool_accounts",
            sa.Column("mapping_status", sa.String(20), nullable=True, default="active"),
        )

    if "discovered_at" not in uta_columns:
        log.info("Adding discovered_at column to user_tool_accounts")
        op.add_column(
            "user_tool_accounts",
            sa.Column("discovered_at", sa.TIMESTAMP, nullable=True),
        )

    if "last_activity_at" not in uta_columns:
        log.info("Adding last_activity_at column to user_tool_accounts")
        op.add_column(
            "user_tool_accounts",
            sa.Column("last_activity_at", sa.TIMESTAMP, nullable=True),
        )

    if "observed_message_count" not in uta_columns:
        log.info("Adding observed_message_count column to user_tool_accounts")
        op.add_column(
            "user_tool_accounts",
            sa.Column("observed_message_count", sa.Integer, nullable=True, default=0),
        )

    if "created_by" not in uta_columns:
        log.info("Adding created_by column to user_tool_accounts")
        op.add_column(
            "user_tool_accounts",
            sa.Column("created_by", sa.Integer, nullable=True),
        )

    if "tenant_id" not in uta_columns:
        log.info("Adding tenant_id column to user_tool_accounts")
        op.add_column(
            "user_tool_accounts",
            sa.Column("tenant_id", sa.Integer, nullable=True),
        )

    if "version" not in uta_columns:
        log.info("Adding version column to user_tool_accounts")
        op.add_column(
            "user_tool_accounts",
            sa.Column("version", sa.Integer, nullable=True, default=1),
        )

    # =========================================================================
    # 2. Add indexes to user_tool_accounts
    # =========================================================================
    uta_indexes = {idx["name"] for idx in inspector.get_indexes("user_tool_accounts")}

    if "idx_uta_status_account" not in uta_indexes:
        log.info("Creating idx_uta_status_account index")
        op.create_index(
            "idx_uta_status_account",
            "user_tool_accounts",
            ["mapping_status", "tool_account"],
            unique=False,
        )

    if "idx_uta_last_activity" not in uta_indexes:
        log.info("Creating idx_uta_last_activity index")
        if _is_postgresql():
            with op.get_context().autocommit_block():
                op.create_index(
                    "idx_uta_last_activity",
                    "user_tool_accounts",
                    ["last_activity_at"],
                    postgresql_concurrently=True,
                    postgresql_where=sa.text("mapping_status = 'active'"),
                )
        else:
            op.create_index(
                "idx_uta_last_activity",
                "user_tool_accounts",
                ["last_activity_at"],
                unique=False,
                sqlite_where=sa.text("mapping_status = 'active'"),
            )

    # =========================================================================
    # 3. Create tool_account_conflicts table
    # =========================================================================
    if "tool_account_conflicts" not in inspector.get_table_names():
        log.info("Creating tool_account_conflicts table")
        op.create_table(
            "tool_account_conflicts",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("mapping_id", sa.Integer, sa.ForeignKey("user_tool_accounts.id"), nullable=False),
            sa.Column("conflict_type", sa.String(20), nullable=False),
            sa.Column("expected_value", sa.Text, nullable=True),
            sa.Column("actual_value", sa.Text, nullable=True),
            sa.Column("detected_at", sa.TIMESTAMP, nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("resolved_at", sa.TIMESTAMP, nullable=True),
            sa.Column("resolved_by", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
            sa.Column("resolution_action", sa.String(20), nullable=True),
            sa.Column("details", sa.Text, nullable=True),
        )

        op.create_index(
            "idx_tac_mapping",
            "tool_account_conflicts",
            ["mapping_id"],
            unique=False,
        )

        if _is_postgresql():
            with op.get_context().autocommit_block():
                op.create_index(
                    "idx_tac_unresolved",
                    "tool_account_conflicts",
                    ["detected_at"],
                    postgresql_concurrently=True,
                    postgresql_where=sa.text("resolved_at IS NULL"),
                )
        else:
            op.create_index(
                "idx_tac_unresolved",
                "tool_account_conflicts",
                ["detected_at"],
                unique=False,
                sqlite_where=sa.text("resolved_at IS NULL"),
            )

    # =========================================================================
    # 4. Create backfill_logs table
    # =========================================================================
    if "backfill_logs" not in inspector.get_table_names():
        log.info("Creating backfill_logs table")
        op.create_table(
            "backfill_logs",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("mapping_id", sa.Integer, sa.ForeignKey("user_tool_accounts.id"), nullable=False),
            sa.Column("backfilled_count", sa.Integer, nullable=False),
            sa.Column("first_date", sa.Date, nullable=True),
            sa.Column("last_date", sa.Date, nullable=True),
            sa.Column("started_at", sa.TIMESTAMP, nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("completed_at", sa.TIMESTAMP, nullable=True),
            sa.Column("status", sa.String(20), nullable=True, server_default="completed"),
        )

        op.create_index(
            "idx_bl_mapping",
            "backfill_logs",
            ["mapping_id"],
            unique=False,
        )

    # =========================================================================
    # 5. Create mapping_migration_status table
    # =========================================================================
    if "mapping_migration_status" not in inspector.get_table_names():
        log.info("Creating mapping_migration_status table")
        op.create_table(
            "mapping_migration_status",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("migration_name", sa.String(100), nullable=False),
            sa.Column("status", sa.String(20), nullable=True, server_default="pending"),
            sa.Column("last_processed_id", sa.Integer, nullable=True),
            sa.Column("total_count", sa.Integer, nullable=True),
            sa.Column("processed_count", sa.Integer, nullable=True, default=0),
            sa.Column("started_at", sa.TIMESTAMP, nullable=True),
            sa.Column("completed_at", sa.TIMESTAMP, nullable=True),
            sa.Column("error_message", sa.Text, nullable=True),
        )

    # =========================================================================
    # 6. Backfill existing data
    # =========================================================================
    # Set default values for existing records
    if _is_postgresql():
        op.execute("""
            UPDATE user_tool_accounts
            SET mapping_source = COALESCE(mapping_source, 'manual'),
                mapping_status = COALESCE(mapping_status, 'active'),
                version = COALESCE(version, 1),
                observed_message_count = COALESCE(observed_message_count, 0)
            WHERE mapping_source IS NULL OR mapping_status IS NULL OR version IS NULL
        """)
    else:
        op.execute("""
            UPDATE user_tool_accounts
            SET mapping_source = COALESCE(mapping_source, 'manual'),
                mapping_status = COALESCE(mapping_status, 'active'),
                version = COALESCE(version, 1),
                observed_message_count = COALESCE(observed_message_count, 0)
            WHERE mapping_source IS NULL OR mapping_status IS NULL OR version IS NULL
        """)

    # Backfill tenant_id from users table
    if _is_postgresql():
        op.execute("""
            UPDATE user_tool_accounts uta
            SET tenant_id = u.tenant_id
            FROM users u
            WHERE uta.user_id = u.id
              AND uta.tenant_id IS NULL
        """)
    else:
        op.execute("""
            UPDATE user_tool_accounts
            SET tenant_id = (
                SELECT u.tenant_id FROM users u WHERE u.id = user_tool_accounts.user_id
            )
            WHERE tenant_id IS NULL
        """)


def downgrade() -> None:
    """Remove mapping fields and related tables."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    # Drop tables in reverse order
    if "mapping_migration_status" in inspector.get_table_names():
        log.info("Dropping mapping_migration_status table")
        op.drop_table("mapping_migration_status")

    if "backfill_logs" in inspector.get_table_names():
        log.info("Dropping backfill_logs table")
        op.drop_table("backfill_logs")

    if "tool_account_conflicts" in inspector.get_table_names():
        log.info("Dropping tool_account_conflicts table")
        op.drop_table("tool_account_conflicts")

    # Drop indexes
    uta_indexes = {idx["name"] for idx in inspector.get_indexes("user_tool_accounts")}

    if "idx_uta_last_activity" in uta_indexes:
        log.info("Dropping idx_uta_last_activity index")
        if _is_postgresql():
            with op.get_context().autocommit_block():
                op.drop_index("idx_uta_last_activity", table_name="user_tool_accounts", postgresql_concurrently=True)
        else:
            op.drop_index("idx_uta_last_activity", table_name="user_tool_accounts")

    if "idx_uta_status_account" in uta_indexes:
        log.info("Dropping idx_uta_status_account index")
        op.drop_index("idx_uta_status_account", table_name="user_tool_accounts")

    # Drop columns
    uta_columns = {col["name"] for col in inspector.get_columns("user_tool_accounts")}
    columns_to_drop = ["version", "tenant_id", "created_by", "observed_message_count",
                       "last_activity_at", "discovered_at", "mapping_status", "mapping_source"]

    if _is_postgresql():
        for col_name in columns_to_drop:
            if col_name in uta_columns:
                log.info(f"Dropping {col_name} column from user_tool_accounts")
                op.drop_column("user_tool_accounts", col_name)
    else:
        # SQLite requires batch_alter_table for DROP COLUMN
        with op.batch_alter_table("user_tool_accounts") as batch_op:
            for col_name in columns_to_drop:
                if col_name in uta_columns:
                    log.info(f"Dropping {col_name} column from user_tool_accounts")
                    batch_op.drop_column(col_name)
