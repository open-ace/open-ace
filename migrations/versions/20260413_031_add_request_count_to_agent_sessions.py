"""Add request_count column to agent_sessions table

Revision ID: 031_add_request_count
Revises: 030_add_user_daily_stats_table
Create Date: 2026-04-13

This migration adds a request_count column to the agent_sessions table
to track the number of API requests (assistant messages) per session.

The request_count differs from message_count:
- message_count: total number of messages (user + assistant + system + tool)
- request_count: number of API requests (only assistant messages)

This allows the session list to display accurate request counts
instead of showing message_count which includes all message types.

"""

from typing import Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "031_add_request_count"
down_revision: Union[str, None] = "030_add_user_daily_stats_table"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    """Check if a column exists in the table."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :table_name AND column_name = :column_name"
            ),
            {"table_name": table_name, "column_name": column_name},
        )
    else:
        # SQLite
        result = conn.execute(
            sa.text("SELECT 1 FROM pragma_table_info(:table_name) WHERE name = :column_name"),
            {"table_name": table_name, "column_name": column_name},
        )
    return result.fetchone() is not None


def upgrade() -> None:
    """Upgrade database schema."""
    conn = op.get_bind()

    if not _column_exists(conn, "agent_sessions", "request_count"):
        op.add_column(
            "agent_sessions",
            sa.Column("request_count", sa.Integer(), nullable=True, default=0),
        )

        # Set default value for existing rows
        op.execute(
            sa.text("UPDATE agent_sessions SET request_count = 0 WHERE request_count IS NULL")
        )


def downgrade() -> None:
    """Downgrade database schema."""
    conn = op.get_bind()

    if _column_exists(conn, "agent_sessions", "request_count"):
        op.drop_column("agent_sessions", "request_count")