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
            sa.Column("source", sa.Text(), nullable=False, server_default="''"),
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
            sa.Column("external_message_id", sa.Text(), nullable=False, server_default="''"),
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
            sa.Column("milestone_id", sa.Text(), nullable=False, server_default="''"),
        )


def downgrade() -> None:
    """Remove backfilled columns from session_messages table (idempotent)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Only remove columns if session_messages table exists
    if "session_messages" not in inspector.get_table_names():
        return

    existing_columns = _column_names(inspector, "session_messages")

    # Drop indexes that depend on the columns we're about to remove
    # These indexes are defined in schema.sql but created by baseline
    if conn.dialect.name == "postgresql":
        # PostgreSQL: drop indexes directly
        try:
            op.execute("DROP INDEX IF EXISTS idx_session_messages_external_message_id")
        except Exception:
            pass  # Index may not exist
        try:
            op.execute("DROP INDEX IF EXISTS idx_session_messages_source")
        except Exception:
            pass  # Index may not exist
    else:
        # SQLite: use batch_alter_table for safety
        try:
            with op.batch_alter_table("session_messages") as batch_op:
                batch_op.drop_index("idx_session_messages_external_message_id")
        except Exception:
            pass  # Index may not exist
        try:
            with op.batch_alter_table("session_messages") as batch_op:
                batch_op.drop_index("idx_session_messages_source")
        except Exception:
            pass  # Index may not exist

    # Drop columns in reverse order
    if "milestone_id" in existing_columns:
        if conn.dialect.name == "postgresql":
            op.drop_column("session_messages", "milestone_id")
        else:
            with op.batch_alter_table("session_messages") as batch_op:
                batch_op.drop_column("milestone_id")

    if "content_blocks" in existing_columns:
        if conn.dialect.name == "postgresql":
            op.drop_column("session_messages", "content_blocks")
        else:
            with op.batch_alter_table("session_messages") as batch_op:
                batch_op.drop_column("content_blocks")

    if "external_message_id" in existing_columns:
        if conn.dialect.name == "postgresql":
            op.drop_column("session_messages", "external_message_id")
        else:
            with op.batch_alter_table("session_messages") as batch_op:
                batch_op.drop_column("external_message_id")

    if "source_timestamp" in existing_columns:
        if conn.dialect.name == "postgresql":
            op.drop_column("session_messages", "source_timestamp")
        else:
            with op.batch_alter_table("session_messages") as batch_op:
                batch_op.drop_column("source_timestamp")

    if "source" in existing_columns:
        if conn.dialect.name == "postgresql":
            op.drop_column("session_messages", "source")
        else:
            with op.batch_alter_table("session_messages") as batch_op:
                batch_op.drop_column("source")