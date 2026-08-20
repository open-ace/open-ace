"""Add daily_usage_synced field to agent_sessions table

Revision ID: 20260820_003
Revises: 20260820_002
Create Date: 2026-08-20

Issue: #2585
Add daily_usage_synced field to agent_sessions table for idempotent
synchronization of autonomous development usage to daily_usage table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_003"
down_revision: str | None = "20260820_002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [col["name"] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    """Add daily_usage_synced column to agent_sessions table."""
    # Add daily_usage_synced column if not exists
    if not column_exists("agent_sessions", "daily_usage_synced"):
        op.add_column(
            "agent_sessions",
            sa.Column(
                "daily_usage_synced",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
                comment="Whether usage has been synced to daily_usage table",
            ),
        )

    # Create partial index for unsynced sessions
    # This helps quickly find sessions that need syncing
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    indexes = [idx["name"] for idx in inspector.get_indexes("agent_sessions")]

    # SQLite doesn't support partial indexes in older versions
    # Check dialect and create appropriate index
    dialect = conn.dialect.name

    if dialect == "postgresql":
        # PostgreSQL supports partial indexes
        if "idx_agent_sessions_daily_usage_synced" not in indexes:
            op.create_index(
                "idx_agent_sessions_daily_usage_synced",
                "agent_sessions",
                ["daily_usage_synced"],
                unique=False,
                postgresql_where=sa.text("daily_usage_synced = FALSE"),
            )
    else:
        # For SQLite, create regular index
        if "idx_agent_sessions_daily_usage_synced" not in indexes:
            op.create_index(
                "idx_agent_sessions_daily_usage_synced",
                "agent_sessions",
                ["daily_usage_synced"],
                unique=False,
            )


def downgrade() -> None:
    """Remove daily_usage_synced column from agent_sessions table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    indexes = [idx["name"] for idx in inspector.get_indexes("agent_sessions")]

    if "idx_agent_sessions_daily_usage_synced" in indexes:
        op.drop_index("idx_agent_sessions_daily_usage_synced", table_name="agent_sessions")

    if column_exists("agent_sessions", "daily_usage_synced"):
        # SQLite requires batch_alter_table for DROP COLUMN
        if conn.dialect.name == "postgresql":
            op.drop_column("agent_sessions", "daily_usage_synced")
        else:
            with op.batch_alter_table("agent_sessions") as batch_op:
                batch_op.drop_column("daily_usage_synced")
