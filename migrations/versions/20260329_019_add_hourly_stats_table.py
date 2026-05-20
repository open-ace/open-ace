"""Add hourly_stats table for pre-aggregated hourly usage data

Revision ID: 019_add_hourly_stats
Revises: 018_add_daily_stats
Create Date: 2026-03-29

This migration creates an hourly_stats table to store pre-aggregated hourly
usage data for fast trend analysis queries.

Problem:
- get_hourly_totals still takes 2-3s because it queries daily_messages
- Hourly distribution data is relatively static and can be pre-aggregated

Solution:
- Create hourly_stats table with pre-calculated hourly statistics
- Aggregate by date, hour, tool_name, host_name
- Query from hourly_stats instead of daily_messages (millisecond response)

Table structure:
- date: Date of the statistics
- hour: Hour of day (0-23, in CST timezone)
- tool_name: Tool identifier
- host_name: Host identifier
- total_tokens: Sum of tokens_used
- total_input_tokens: Sum of input_tokens
- total_output_tokens: Sum of output_tokens
- message_count: Number of messages
- updated_at: Last update timestamp

"""

from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "019_add_hourly_stats"
down_revision: Union[str, None] = "018_add_daily_stats"
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

    if not _table_exists(conn, "hourly_stats"):
        # Create hourly_stats table
        op.create_table(
            "hourly_stats",
            sa.Column("date", sa.String(10), nullable=False),
            sa.Column("hour", sa.Integer, nullable=False),  # 0-23, CST timezone
            sa.Column("tool_name", sa.String(50), nullable=False),
            sa.Column("host_name", sa.String(100), nullable=False, server_default="localhost"),
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

        # Create unique constraint for date + hour + tool_name + host_name
        op.create_unique_constraint(
            "uq_hourly_stats_date_hour_tool_host",
            "hourly_stats",
            ["date", "hour", "tool_name", "host_name"],
        )

        # Create indexes for fast lookup
        op.create_index("idx_hourly_stats_date", "hourly_stats", ["date"])
        op.create_index("idx_hourly_stats_hour", "hourly_stats", ["hour"])
        op.create_index("idx_hourly_stats_date_hour", "hourly_stats", ["date", "hour"])

        # Populate initial data from daily_messages
        # Note: Convert UTC hour to CST (UTC+8)
        if conn.dialect.name == "postgresql":
            conn.execute(sa.text("""
                    INSERT INTO hourly_stats
                    (date, hour, tool_name, host_name, total_tokens, total_input_tokens,
                     total_output_tokens, message_count, updated_at)
                    SELECT
                        date,
                        MOD(EXTRACT(HOUR FROM timestamp::timestamp)::INTEGER + 8, 24) as hour,
                        tool_name,
                        host_name,
                        SUM(tokens_used) as total_tokens,
                        SUM(input_tokens) as total_input_tokens,
                        SUM(output_tokens) as total_output_tokens,
                        COUNT(*) as message_count,
                        CURRENT_TIMESTAMP
                    FROM daily_messages
                    WHERE timestamp IS NOT NULL
                    GROUP BY date, MOD(EXTRACT(HOUR FROM timestamp::timestamp)::INTEGER + 8, 24), tool_name, host_name
                    ON CONFLICT (date, hour, tool_name, host_name) DO NOTHING
                """))
        else:
            conn.execute(sa.text("""
                    INSERT OR IGNORE INTO hourly_stats
                    (date, hour, tool_name, host_name, total_tokens, total_input_tokens,
                     total_output_tokens, message_count, updated_at)
                    SELECT
                        date,
                        (CAST(strftime('%H', timestamp) AS INTEGER) + 8) % 24 as hour,
                        tool_name,
                        host_name,
                        SUM(tokens_used) as total_tokens,
                        SUM(input_tokens) as total_input_tokens,
                        SUM(output_tokens) as total_output_tokens,
                        COUNT(*) as message_count,
                        CURRENT_TIMESTAMP
                    FROM daily_messages
                    WHERE timestamp IS NOT NULL
                    GROUP BY date, (CAST(strftime('%H', timestamp) AS INTEGER) + 8) % 24, tool_name, host_name
                """))
        conn.commit()


def downgrade() -> None:
    """Downgrade database schema."""
    conn = op.get_bind()

    if _table_exists(conn, "hourly_stats"):
        op.drop_index("idx_hourly_stats_date_hour", "hourly_stats")
        op.drop_index("idx_hourly_stats_hour", "hourly_stats")
        op.drop_index("idx_hourly_stats_date", "hourly_stats")
        op.drop_constraint("uq_hourly_stats_date_hour_tool_host", "hourly_stats")
        op.drop_table("hourly_stats")
