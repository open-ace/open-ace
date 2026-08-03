"""Backfill session_messages columns missing from historical migrations

Revision ID: 20260802_002_backfill_session_messages_columns
Revises: 20260802_001_backfill_agent_sessions_columns
Create Date: 2026-08-02

Issue: #2190

These columns were added to the schema at various points in history but never
received formal Alembic migrations. This revision fills that gap by adding them
with idempotent checks, ensuring the migration can run safely on databases that
already have these columns.

Columns added:
- source: TEXT, default ''
- source_timestamp: TIMESTAMP, nullable
- external_message_id: TEXT, default ''
- content_blocks: TEXT, nullable
- milestone_id: TEXT, default ''
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260802_002_backfill_session_messages_columns"
down_revision: str | None = "20260802_001_backfill_agent_sessions_columns"
branch_labels: str | None = None
depends_on: str | None = None


def _column_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    """Get existing column names for a table."""
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    """Add missing columns to session_messages table (idempotent)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Only add columns if session_messages table exists
    if "session_messages" not in inspector.get_table_names():
        return

    existing_columns = _column_names(inspector, "session_messages")

    # Add source if missing (with default)
    if "source" not in existing_columns:
        op.add_column(
            "session_messages",
            sa.Column("source", sa.Text(), nullable=False, server_default=""),
        )

    # Add source_timestamp if missing
    if "source_timestamp" not in existing_columns:
        op.add_column(
            "session_messages",
            sa.Column("source_timestamp", sa.DateTime(), nullable=True),
        )

    # Add external_message_id if missing (with default)
    if "external_message_id" not in existing_columns:
        op.add_column(
            "session_messages",
            sa.Column("external_message_id", sa.Text(), nullable=False, server_default=""),
        )

    # Add content_blocks if missing
    if "content_blocks" not in existing_columns:
        op.add_column(
            "session_messages",
            sa.Column("content_blocks", sa.Text(), nullable=True),
        )

    # Add milestone_id if missing (with default)
    if "milestone_id" not in existing_columns:
        op.add_column(
            "session_messages",
            sa.Column("milestone_id", sa.Text(), nullable=False, server_default=""),
        )


def downgrade() -> None:
    """No-op downgrade.

    All five columns (source, source_timestamp, external_message_id,
    content_blocks, milestone_id) are defined by the baseline schema
    (baseline_2026_06_23) and were therefore present on every database before
    this migration existed. The upgrade only conditionally adds them to repair
    databases that pre-date the baseline (idempotent backfill). Removing them on
    downgrade would break symmetry with the baseline: these columns and the
    indexes idx_session_messages_external_message_id /
    idx_session_messages_source are baseline-owned, so dropping the columns
    here would make a subsequent re-upgrade (or downstream index recreation)
    fail.

    Because the columns are baseline-owned, the downgrade leaves the schema
    unchanged and only rewinds the alembic revision pointer.
    """
