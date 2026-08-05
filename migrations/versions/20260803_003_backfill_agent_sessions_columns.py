"""Backfill agent_sessions columns missing from historical migrations

Revision ID: 20260803_003_backfill_agent_sessions_columns
Revises: 20260803_002_create_retention_tables
Create Date: 2026-08-03

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

revision: str = "20260803_003_backfill_agent_sessions_columns"
down_revision: str | None = "20260803_002_create_retention_tables"
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
    """No-op downgrade.

    All six columns (project_id, project_path, request_count, workspace_type,
    remote_machine_id, paused_at) are defined by the baseline schema
    (baseline_2026_06_23) and were therefore present on every database before
    this migration existed. The upgrade only conditionally adds them to repair
    databases that pre-date the baseline (idempotent backfill). Removing them on
    downgrade would break symmetry with the baseline: a downstream migration
    (20260721_002) creates an index on remote_machine_id, so dropping the column
    here makes a subsequent re-upgrade fail with "no such column: remote_machine_id".

    Because the columns are baseline-owned, the downgrade leaves the schema
    unchanged and only rewinds the alembic revision pointer.
    """
