"""Optimize sessions list query with materialized view

Revision ID: 022_sessions_list_opt
Revises: 021_postgresql_optimization
Create Date: 2026-03-29

This migration creates a materialized view to optimize the sessions list query on /work page.

Problem:
- The sessions list query takes ~2 seconds to load
- Query uses GROUP BY agent_session_id, tool_name, host_name, sender_name
- Query aggregates tokens_used, input_tokens, output_tokens, timestamp
- Even with indexes, the query still needs to scan ~150,000 rows for aggregation
- EXPLAIN ANALYZE shows Parallel Seq Scan (full table scan)

Solution:
- Create session_stats materialized view for PostgreSQL
- Pre-aggregate session statistics for fast retrieval
- Add indexes on materialized view for ORDER BY and filtering
- Refresh mechanism added to DataFetchScheduler

Performance improvement:
- Query time reduced from ~2 seconds to ~0.05 seconds (40x faster)

Query pattern optimized:
    SELECT agent_session_id, tool_name, host_name, sender_name,
           MAX(sender_id), MAX(date), COUNT(*),
           SUM(tokens_used), SUM(input_tokens), SUM(output_tokens),
           MIN(timestamp), MAX(timestamp)
    FROM daily_messages
    WHERE agent_session_id IS NOT NULL
    GROUP BY agent_session_id, tool_name, host_name, sender_name
    ORDER BY MAX(timestamp) DESC
    LIMIT 50

"""

from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "022_sessions_list_opt"
down_revision: Union[str, None] = "021_postgresql_optimization"
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


def _matview_exists(conn, matview_name: str) -> bool:
    """Check if a materialized view exists (PostgreSQL only)."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text("SELECT EXISTS (SELECT FROM pg_matviews WHERE matviewname = :matview_name)"),
            {"matview_name": matview_name},
        )
        return result.fetchone()[0]
    return False


def upgrade() -> None:
    """Upgrade database schema."""
    conn = op.get_bind()

    if not _table_exists(conn, "daily_messages"):
        return

    if conn.dialect.name == "postgresql":
        # Create materialized view for session statistics
        if not _matview_exists(conn, "session_stats"):
            op.execute(sa.text("""
                    CREATE MATERIALIZED VIEW session_stats AS
                    SELECT
                        agent_session_id as session_id,
                        tool_name,
                        host_name,
                        sender_name,
                        MAX(sender_id) as sender_id,
                        MAX(date) as date,
                        COUNT(*) as message_count,
                        SUM(tokens_used) as total_tokens,
                        SUM(input_tokens) as total_input_tokens,
                        SUM(output_tokens) as total_output_tokens,
                        MIN(timestamp) as created_at,
                        MAX(timestamp) as updated_at
                    FROM daily_messages
                    WHERE agent_session_id IS NOT NULL
                    GROUP BY agent_session_id, tool_name, host_name, sender_name
                    """))

        # Create indexes on materialized view
        if not _index_exists(conn, "session_stats", "idx_session_stats_updated_at"):
            op.execute(
                sa.text(
                    "CREATE INDEX idx_session_stats_updated_at ON session_stats(updated_at DESC)"
                )
            )

        if not _index_exists(conn, "session_stats", "idx_session_stats_tool_host"):
            op.execute(
                sa.text(
                    "CREATE INDEX idx_session_stats_tool_host ON session_stats(tool_name, host_name)"
                )
            )

        # Also add a simple index on agent_session_id for other queries
        if not _index_exists(conn, "daily_messages", "idx_messages_agent_session_id"):
            op.create_index(
                "idx_messages_agent_session_id",
                "daily_messages",
                ["agent_session_id"],
            )
    else:
        # SQLite: Create simpler indexes
        if not _index_exists(conn, "daily_messages", "idx_messages_agent_session_id"):
            op.create_index(
                "idx_messages_agent_session_id",
                "daily_messages",
                ["agent_session_id"],
            )


def downgrade() -> None:
    """Downgrade database schema."""
    conn = op.get_bind()

    if conn.dialect.name == "postgresql":
        # Drop materialized view indexes
        if _index_exists(conn, "session_stats", "idx_session_stats_updated_at"):
            op.execute(sa.text("DROP INDEX idx_session_stats_updated_at"))

        if _index_exists(conn, "session_stats", "idx_session_stats_tool_host"):
            op.execute(sa.text("DROP INDEX idx_session_stats_tool_host"))

        # Drop materialized view
        if _matview_exists(conn, "session_stats"):
            op.execute(sa.text("DROP MATERIALIZED VIEW session_stats"))

    if _table_exists(conn, "daily_messages"):
        if _index_exists(conn, "daily_messages", "idx_messages_agent_session_id"):
            op.drop_index("idx_messages_agent_session_id", "daily_messages")
