"""Add index for user request trend query optimization

Revision ID: 023_add_user_request_trend_index
Revises: 022_add_request_stats_indexes
Create Date: 2026-04-03

This migration adds an optimized index for the get_user_request_trend query
which was causing slow page loads on /work/usage.

The query:
    SELECT date, COUNT(*) as requests, SUM(tokens_used) as tokens
    FROM daily_messages
    WHERE date >= ? AND date <= ? AND role = 'assistant' AND sender_name = ?
    GROUP BY date
    ORDER BY date ASC

Before index: ~6.7s (Parallel Seq Scan)
After index: ~0.2ms (Index Scan)

"""

from typing import Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "023_add_user_request_trend_index"
down_revision: Union[str, None] = "022_add_request_stats_indexes"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def _index_exists(conn, table_name: str, index_name: str) -> bool:
    """Check if an index exists."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text("SELECT 1 FROM pg_indexes WHERE indexname = :index_name"),
            {"index_name": index_name},
        )
    else:
        result = conn.execute(
            sa.text("SELECT 1 FROM sqlite_master WHERE type='index' AND name = :index_name"),
            {"index_name": index_name},
        )
    return result.fetchone() is not None


def upgrade() -> None:
    """Add index for user request trend query."""
    conn = op.get_bind()

    # Create index for user request trend query
    # This index optimizes queries that filter by sender_name, date range, and role
    # Note: CONCURRENTLY is not used because alembic runs in transaction block
    # For fresh installation, locking is not a concern
    if not _index_exists(conn, "daily_messages", "idx_messages_sender_date_role"):
        op.execute(
            """
            CREATE INDEX idx_messages_sender_date_role
            ON daily_messages (sender_name, date, role)
            """
        )


def downgrade() -> None:
    """Remove index for user request trend query."""
    conn = op.get_bind()

    if _index_exists(conn, "daily_messages", "idx_messages_sender_date_role"):
        op.execute("DROP INDEX idx_messages_sender_date_role")