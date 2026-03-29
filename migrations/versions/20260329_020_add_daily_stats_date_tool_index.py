"""Add composite index for daily_stats(date, tool_name)

Revision ID: 020_stats_date_tool_idx
Revises: 019_add_hourly_stats
Create Date: 2026-03-29

This migration adds a composite index on daily_stats(date, tool_name)
to optimize trend analysis queries that aggregate by date and tool.

Problem:
- get_daily_by_tool() queries daily_stats with GROUP BY date, tool_name
- Existing idx_daily_stats_date_tool_host includes host_name which is
  not always needed for queries without host filter
- A smaller composite index can be more efficient for common queries

Solution:
- Add idx_daily_stats_date_tool composite index
- This index is optimal for queries that aggregate by date and tool_name
  without filtering by host_name

Query pattern optimized:
    SELECT date, tool_name, SUM(total_tokens)
    FROM daily_stats
    WHERE date >= ? AND date <= ?
    GROUP BY date, tool_name

"""

from typing import Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "020_stats_date_tool_idx"
down_revision: Union[str, None] = "019_add_hourly_stats"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def _index_exists(conn, table_name: str, index_name: str) -> bool:
    """Check if an index exists in the database."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text("SELECT 1 FROM pg_indexes WHERE indexname = :index_name"),
            {"index_name": index_name},
        )
    else:
        # SQLite
        result = conn.execute(
            sa.text("SELECT 1 FROM sqlite_master WHERE type='index' AND name = :index_name"),
            {"index_name": index_name},
        )
    return result.fetchone() is not None


def _table_exists(conn, table_name: str) -> bool:
    """Check if a table exists in the database."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = :table_name)"
            ),
            {"table_name": table_name},
        )
        return result.fetchone()[0]
    else:
        result = conn.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='table' AND name = :table_name"),
            {"table_name": table_name},
        )
        return result.fetchone() is not None


def upgrade() -> None:
    """Upgrade database schema."""
    conn = op.get_bind()

    # Only add index if daily_stats table exists
    if _table_exists(conn, "daily_stats"):
        # Add composite index for date + tool_name queries
        if not _index_exists(conn, "daily_stats", "idx_daily_stats_date_tool"):
            op.create_index("idx_daily_stats_date_tool", "daily_stats", ["date", "tool_name"])


def downgrade() -> None:
    """Downgrade database schema."""
    conn = op.get_bind()

    if _table_exists(conn, "daily_stats"):
        if _index_exists(conn, "daily_stats", "idx_daily_stats_date_tool"):
            op.drop_index("idx_daily_stats_date_tool", "daily_stats")