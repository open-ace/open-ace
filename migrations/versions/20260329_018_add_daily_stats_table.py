"""Add daily_stats table for pre-aggregated trend analysis data

Revision ID: 018_add_daily_stats
Revises: 017_add_usage_summary
Create Date: 2026-03-29

This migration creates a daily_stats table to store pre-aggregated data
for fast trend analysis queries.

Problem:
- Trend analysis page takes 6-9 seconds to load
- Each query scans 200k rows from daily_messages table
- GROUP BY operations are expensive on large datasets

Solution:
- Create daily_stats table with pre-aggregated daily statistics
- Aggregate by date, tool_name, host_name, sender_name
- Query from daily_stats instead of daily_messages (millisecond response)

Table structure:
- date: Date of the statistics
- tool_name: Tool identifier
- host_name: Host identifier
- sender_name: Sender identifier (optional, for user-level stats)
- total_tokens: Sum of tokens_used
- total_input_tokens: Sum of input_tokens
- total_output_tokens: Sum of output_tokens
- message_count: Number of messages
- updated_at: Last update timestamp

"""

from typing import Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "018_add_daily_stats"
down_revision: Union[str, None] = "017_add_usage_summary"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


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

    if not _table_exists(conn, "daily_stats"):
        # Create daily_stats table
        op.create_table(
            "daily_stats",
            sa.Column("date", sa.String(10), nullable=False),
            sa.Column("tool_name", sa.String(50), nullable=False),
            sa.Column("host_name", sa.String(100), nullable=False, server_default="localhost"),
            sa.Column("sender_name", sa.String(100), nullable=True),  # NULL for tool-level stats
            sa.Column("total_tokens", sa.BigInteger, nullable=False, default=0),
            sa.Column("total_input_tokens", sa.BigInteger, nullable=False, default=0),
            sa.Column("total_output_tokens", sa.BigInteger, nullable=False, default=0),
            sa.Column("message_count", sa.Integer, nullable=False, default=0),
            sa.Column(
                "updated_at",
                sa.DateTime,
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )

        # Create unique constraint for date + tool_name + host_name + sender_name
        op.create_unique_constraint(
            "uq_daily_stats_date_tool_host_sender",
            "daily_stats",
            ["date", "tool_name", "host_name", "sender_name"],
        )

        # Create indexes for fast lookup
        op.create_index("idx_daily_stats_date", "daily_stats", ["date"])
        op.create_index("idx_daily_stats_tool", "daily_stats", ["tool_name"])
        op.create_index("idx_daily_stats_host", "daily_stats", ["host_name"])
        op.create_index("idx_daily_stats_sender", "daily_stats", ["sender_name"])
        op.create_index(
            "idx_daily_stats_date_tool_host", "daily_stats", ["date", "tool_name", "host_name"]
        )

        # Populate initial data from daily_messages
        if conn.dialect.name == "postgresql":
            conn.execute(
                sa.text("""
                    INSERT INTO daily_stats
                    (date, tool_name, host_name, sender_name, total_tokens, total_input_tokens,
                     total_output_tokens, message_count, updated_at)
                    SELECT
                        date,
                        tool_name,
                        host_name,
                        sender_name,
                        SUM(tokens_used) as total_tokens,
                        SUM(input_tokens) as total_input_tokens,
                        SUM(output_tokens) as total_output_tokens,
                        COUNT(*) as message_count,
                        CURRENT_TIMESTAMP
                    FROM daily_messages
                    GROUP BY date, tool_name, host_name, sender_name
                    ON CONFLICT (date, tool_name, host_name, sender_name) DO NOTHING
                """)
            )
        else:
            conn.execute(
                sa.text("""
                    INSERT OR IGNORE INTO daily_stats
                    (date, tool_name, host_name, sender_name, total_tokens, total_input_tokens,
                     total_output_tokens, message_count, updated_at)
                    SELECT
                        date,
                        tool_name,
                        host_name,
                        sender_name,
                        SUM(tokens_used) as total_tokens,
                        SUM(input_tokens) as total_input_tokens,
                        SUM(output_tokens) as total_output_tokens,
                        COUNT(*) as message_count,
                        CURRENT_TIMESTAMP
                    FROM daily_messages
                    GROUP BY date, tool_name, host_name, sender_name
                """)
            )
        conn.commit()


def downgrade() -> None:
    """Downgrade database schema."""
    conn = op.get_bind()

    if _table_exists(conn, "daily_stats"):
        op.drop_index("idx_daily_stats_date_tool_host", "daily_stats")
        op.drop_index("idx_daily_stats_sender", "daily_stats")
        op.drop_index("idx_daily_stats_host", "daily_stats")
        op.drop_index("idx_daily_stats_tool", "daily_stats")
        op.drop_index("idx_daily_stats_date", "daily_stats")
        op.drop_constraint("uq_daily_stats_date_tool_host_sender", "daily_stats")
        op.drop_table("daily_stats")