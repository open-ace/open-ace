"""Backfill agent_sessions columns missing from historical migrations

Revision ID: 20260802_001_backfill_agent_sessions_columns
Revises: 20260801_001_add_platform_tenant_admin_roles
Create Date: 2026-08-02

Issue: #2190

These columns were added to the schema at various points in history but never
received formal Alembic migrations. This revision fills that gap by adding them
with idempotent checks, ensuring the migration can run safely on databases that
already have these columns (e.g., those bootstrapped from baseline or those that
had runtime DDL backfill them).

Columns added:
- project_id: INTEGER, nullable
- project_path: TEXT, nullable
- request_count: INTEGER, default 0
- workspace_type: TEXT, default 'local'
- remote_machine_id: TEXT, nullable
- paused_at: TIMESTAMP, nullable
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260802_001_backfill_agent_sessions_columns"
down_revision: str | None = "20260801_001_add_platform_tenant_admin_roles"
branch_labels: str | None = None
depends_on: str | None = None


def _column_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    """Get existing column names for a table."""
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    """Add missing columns to agent_sessions table (idempotent)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Only add columns if agent_sessions table exists
    if "agent_sessions" not in inspector.get_table_names():
        return

    existing_columns = _column_names(inspector, "agent_sessions")

    # Add project_id if missing
    if "project_id" not in existing_columns:
        op.add_column(
            "agent_sessions",
            sa.Column("project_id", sa.Integer(), nullable=True),
        )

    # Add project_path if missing
    if "project_path" not in existing_columns:
        op.add_column(
            "agent_sessions",
            sa.Column("project_path", sa.String(length=500), nullable=True),
        )

    # Add request_count if missing (with default)
    if "request_count" not in existing_columns:
        op.add_column(
            "agent_sessions",
            sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        )

    # Add workspace_type if missing (with default)
    if "workspace_type" not in existing_columns:
        op.add_column(
            "agent_sessions",
            sa.Column("workspace_type", sa.Text(), nullable=False, server_default="local"),
        )

    # Add remote_machine_id if missing
    if "remote_machine_id" not in existing_columns:
        op.add_column(
            "agent_sessions",
            sa.Column("remote_machine_id", sa.Text(), nullable=True),
        )

    # Add paused_at if missing
    if "paused_at" not in existing_columns:
        op.add_column(
            "agent_sessions",
            sa.Column("paused_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    """Remove backfilled columns from agent_sessions table (idempotent)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Only remove columns if agent_sessions table exists
    if "agent_sessions" not in inspector.get_table_names():
        return

    existing_columns = _column_names(inspector, "agent_sessions")

    # Drop indexes that depend on the columns we're about to remove
    # These indexes are defined in schema.sql but created by baseline
    if conn.dialect.name == "postgresql":
        # PostgreSQL: drop indexes directly
        try:
            op.execute("DROP INDEX IF EXISTS idx_agent_sessions_project")
        except Exception:
            pass  # Index may not exist
        try:
            op.execute("DROP INDEX IF EXISTS idx_agent_sessions_remote_machine_id")
        except Exception:
            pass  # Index may not exist
    else:
        # SQLite: use batch_alter_table for safety
        try:
            with op.batch_alter_table("agent_sessions") as batch_op:
                batch_op.drop_index("idx_agent_sessions_project")
        except Exception:
            pass  # Index may not exist
        try:
            with op.batch_alter_table("agent_sessions") as batch_op:
                batch_op.drop_index("idx_agent_sessions_remote_machine_id")
        except Exception:
            pass  # Index may not exist

    # Drop columns in reverse order
    if "paused_at" in existing_columns:
        if conn.dialect.name == "postgresql":
            op.drop_column("agent_sessions", "paused_at")
        else:
            with op.batch_alter_table("agent_sessions") as batch_op:
                batch_op.drop_column("paused_at")

    if "remote_machine_id" in existing_columns:
        if conn.dialect.name == "postgresql":
            op.drop_column("agent_sessions", "remote_machine_id")
        else:
            with op.batch_alter_table("agent_sessions") as batch_op:
                batch_op.drop_column("remote_machine_id")

    if "workspace_type" in existing_columns:
        if conn.dialect.name == "postgresql":
            op.drop_column("agent_sessions", "workspace_type")
        else:
            with op.batch_alter_table("agent_sessions") as batch_op:
                batch_op.drop_column("workspace_type")

    if "request_count" in existing_columns:
        if conn.dialect.name == "postgresql":
            op.drop_column("agent_sessions", "request_count")
        else:
            with op.batch_alter_table("agent_sessions") as batch_op:
                batch_op.drop_column("request_count")

    if "project_path" in existing_columns:
        if conn.dialect.name == "postgresql":
            op.drop_column("agent_sessions", "project_path")
        else:
            with op.batch_alter_table("agent_sessions") as batch_op:
                batch_op.drop_column("project_path")

    if "project_id" in existing_columns:
        if conn.dialect.name == "postgresql":
            op.drop_column("agent_sessions", "project_id")
        else:
            with op.batch_alter_table("agent_sessions") as batch_op:
                batch_op.drop_column("project_id")
