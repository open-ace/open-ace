"""Add tenant_id to Analysis tables

Revision ID: 20260719_002_add_tenant_id_to_analysis_tables
Revises: 20260719_001_add_sso_auth_states_ttl
Create Date: 2026-07-19

Issue: #1852

Add tenant_id column to daily_messages, daily_stats, and hourly_stats tables
for proper tenant isolation in Analysis functionality.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_002_add_tenant_id_to_analysis_tables"
down_revision: str | None = "20260719_001_add_sso_auth_states_ttl"
branch_labels: str | None = None
depends_on: str | None = None


def _column_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table_name)}


def _index_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    """Add tenant_id to daily_messages, daily_stats, and hourly_stats."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    is_postgres = conn.dialect.name == "postgresql"

    # daily_messages
    dm_columns = _column_names(inspector, "daily_messages")
    if "tenant_id" not in dm_columns:
        op.add_column(
            "daily_messages",
            sa.Column("tenant_id", sa.Integer(), nullable=True),
        )

    dm_indexes = _index_names(inspector, "daily_messages")
    if "idx_daily_messages_tenant_date" not in dm_indexes:
        if is_postgres:
            op.create_index(
                "idx_daily_messages_tenant_date",
                "daily_messages",
                ["tenant_id", "date"],
                postgresql_concurrently=True,
            )
        else:
            op.create_index(
                "idx_daily_messages_tenant_date",
                "daily_messages",
                ["tenant_id", "date"],
            )

    if "idx_daily_messages_orphan" not in dm_indexes:
        if is_postgres:
            op.execute(
                "CREATE INDEX idx_daily_messages_orphan ON daily_messages(date) "
                "WHERE tenant_id IS NULL"
            )
        else:
            op.execute(
                "CREATE INDEX idx_daily_messages_orphan ON daily_messages(date) "
                "WHERE tenant_id IS NULL"
            )

    # daily_stats
    ds_columns = _column_names(inspector, "daily_stats")
    if "tenant_id" not in ds_columns:
        op.add_column(
            "daily_stats",
            sa.Column("tenant_id", sa.Integer(), nullable=True),
        )

    ds_indexes = _index_names(inspector, "daily_stats")
    if "idx_daily_stats_tenant_date" not in ds_indexes:
        if is_postgres:
            op.create_index(
                "idx_daily_stats_tenant_date",
                "daily_stats",
                ["tenant_id", "date"],
                postgresql_concurrently=True,
            )
        else:
            op.create_index(
                "idx_daily_stats_tenant_date",
                "daily_stats",
                ["tenant_id", "date"],
            )

    if "idx_daily_stats_orphan" not in ds_indexes:
        if is_postgres:
            op.execute(
                "CREATE INDEX idx_daily_stats_orphan ON daily_stats(date) "
                "WHERE tenant_id IS NULL"
            )
        else:
            op.execute(
                "CREATE INDEX idx_daily_stats_orphan ON daily_stats(date) "
                "WHERE tenant_id IS NULL"
            )

    # hourly_stats
    hs_columns = _column_names(inspector, "hourly_stats")
    if "tenant_id" not in hs_columns:
        op.add_column(
            "hourly_stats",
            sa.Column("tenant_id", sa.Integer(), nullable=True),
        )

    hs_indexes = _index_names(inspector, "hourly_stats")
    if "idx_hourly_stats_tenant_date" not in hs_indexes:
        if is_postgres:
            op.create_index(
                "idx_hourly_stats_tenant_date",
                "hourly_stats",
                ["tenant_id", "date"],
                postgresql_concurrently=True,
            )
        else:
            op.create_index(
                "idx_hourly_stats_tenant_date",
                "hourly_stats",
                ["tenant_id", "date"],
            )

    if "idx_hourly_stats_orphan" not in hs_indexes:
        if is_postgres:
            op.execute(
                "CREATE INDEX idx_hourly_stats_orphan ON hourly_stats(date) "
                "WHERE tenant_id IS NULL"
            )
        else:
            op.execute(
                "CREATE INDEX idx_hourly_stats_orphan ON hourly_stats(date) "
                "WHERE tenant_id IS NULL"
            )

    # Backfill tenant_id from user_id for daily_messages
    conn.execute(
        sa.text(
            """
            UPDATE daily_messages
            SET tenant_id = (
                SELECT users.tenant_id
                FROM users
                WHERE users.id = daily_messages.user_id
            )
            WHERE tenant_id IS NULL AND user_id IS NOT NULL
            """
        )
    )

    # Backfill tenant_id from project_path for daily_messages
    conn.execute(
        sa.text(
            """
            UPDATE daily_messages
            SET tenant_id = (
                SELECT projects.tenant_id
                FROM projects
                WHERE projects.path = daily_messages.project_path
            )
            WHERE tenant_id IS NULL AND project_path IS NOT NULL
            """
        )
    )


def downgrade() -> None:
    """Remove tenant_id columns and indexes."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # hourly_stats
    hs_indexes = _index_names(inspector, "hourly_stats")
    if "idx_hourly_stats_orphan" in hs_indexes:
        op.drop_index("idx_hourly_stats_orphan", table_name="hourly_stats")
    if "idx_hourly_stats_tenant_date" in hs_indexes:
        op.drop_index("idx_hourly_stats_tenant_date", table_name="hourly_stats")
    hs_columns = _column_names(inspector, "hourly_stats")
    if "tenant_id" in hs_columns:
        op.drop_column("hourly_stats", "tenant_id")

    # daily_stats
    ds_indexes = _index_names(inspector, "daily_stats")
    if "idx_daily_stats_orphan" in ds_indexes:
        op.drop_index("idx_daily_stats_orphan", table_name="daily_stats")
    if "idx_daily_stats_tenant_date" in ds_indexes:
        op.drop_index("idx_daily_stats_tenant_date", table_name="daily_stats")
    ds_columns = _column_names(inspector, "daily_stats")
    if "tenant_id" in ds_columns:
        op.drop_column("daily_stats", "tenant_id")

    # daily_messages
    dm_indexes = _index_names(inspector, "daily_messages")
    if "idx_daily_messages_orphan" in dm_indexes:
        op.drop_index("idx_daily_messages_orphan", table_name="daily_messages")
    if "idx_daily_messages_tenant_date" in dm_indexes:
        op.drop_index("idx_daily_messages_tenant_date", table_name="daily_messages")
    dm_columns = _column_names(inspector, "daily_messages")
    if "tenant_id" in dm_columns:
        op.drop_column("daily_messages", "tenant_id")