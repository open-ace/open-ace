"""Normalize tool names in derived tables (daily_stats, hourly_stats, usage_summary)

Revision ID: 039_normalize_derived_tables
Revises: 038_normalize_tool_names
Create Date: 2026-05-06

Complements migration 038 which only covered agent_sessions, daily_messages,
and daily_usage. This migration normalizes tool_name in the pre-aggregated
derived tables.
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "039_normalize_derived_tables"
down_revision: Union[str, None] = "038_normalize_tool_names"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

ALIASES_IN = "'qwen-code', 'qwen-code-cli'"


def _table_exists(conn, table_name: str) -> bool:
    """Check whether a table exists."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text("SELECT 1 FROM information_schema.tables WHERE table_name = :table_name"),
            {"table_name": table_name},
        )
        return result.fetchone() is not None

    result = conn.execute(
        sa.text("SELECT 1 FROM sqlite_master WHERE type='table' AND name = :table_name"),
        {"table_name": table_name},
    )
    return result.fetchone() is not None


def _normalize_sqlite_daily_stats() -> None:
    """Merge alias rows into a single qwen row before constraints can collide."""
    op.execute(
        sa.text(
            f"""
            CREATE TEMP TABLE tmp_qwen_daily_stats AS
            SELECT
                date,
                'qwen' AS tool_name,
                host_name,
                sender_name,
                SUM(total_tokens) AS total_tokens,
                SUM(total_input_tokens) AS total_input_tokens,
                SUM(total_output_tokens) AS total_output_tokens,
                SUM(message_count) AS message_count,
                MAX(updated_at) AS updated_at,
                MIN(project_id) AS project_id,
                MIN(project_path) AS project_path
            FROM daily_stats
            WHERE tool_name = 'qwen' OR tool_name IN ({ALIASES_IN})
            GROUP BY date, host_name, sender_name
            """
        )
    )
    op.execute(sa.text(f"DELETE FROM daily_stats WHERE tool_name = 'qwen' OR tool_name IN ({ALIASES_IN})"))
    op.execute(
        sa.text(
            """
            INSERT INTO daily_stats
            (date, tool_name, host_name, sender_name, total_tokens, total_input_tokens,
             total_output_tokens, message_count, updated_at, project_id, project_path)
            SELECT
                date, tool_name, host_name, sender_name, total_tokens, total_input_tokens,
                total_output_tokens, message_count, updated_at, project_id, project_path
            FROM tmp_qwen_daily_stats
            """
        )
    )
    op.execute(sa.text("DROP TABLE tmp_qwen_daily_stats"))


def _normalize_sqlite_hourly_stats() -> None:
    """Merge alias rows into a single qwen hourly row."""
    op.execute(
        sa.text(
            f"""
            CREATE TEMP TABLE tmp_qwen_hourly_stats AS
            SELECT
                date,
                hour,
                'qwen' AS tool_name,
                host_name,
                SUM(total_tokens) AS total_tokens,
                SUM(total_input_tokens) AS total_input_tokens,
                SUM(total_output_tokens) AS total_output_tokens,
                SUM(message_count) AS message_count,
                MAX(updated_at) AS updated_at
            FROM hourly_stats
            WHERE tool_name = 'qwen' OR tool_name IN ({ALIASES_IN})
            GROUP BY date, hour, host_name
            """
        )
    )
    op.execute(
        sa.text(f"DELETE FROM hourly_stats WHERE tool_name = 'qwen' OR tool_name IN ({ALIASES_IN})")
    )
    op.execute(
        sa.text(
            """
            INSERT INTO hourly_stats
            (date, hour, tool_name, host_name, total_tokens, total_input_tokens,
             total_output_tokens, message_count, updated_at)
            SELECT
                date, hour, tool_name, host_name, total_tokens, total_input_tokens,
                total_output_tokens, message_count, updated_at
            FROM tmp_qwen_hourly_stats
            """
        )
    )
    op.execute(sa.text("DROP TABLE tmp_qwen_hourly_stats"))


def _rebuild_sqlite_qwen_usage_summary() -> None:
    """Recalculate qwen usage_summary rows from normalized daily_messages."""
    op.execute(
        sa.text(
            "DELETE FROM usage_summary WHERE tool_name = 'qwen' OR tool_name IN "
            f"({ALIASES_IN})"
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO usage_summary
            (tool_name, host_name, days_count, total_tokens, avg_tokens, total_requests,
             total_input_tokens, total_output_tokens, first_date, last_date, updated_at)
            SELECT
                'qwen' AS tool_name,
                host_name,
                COUNT(DISTINCT date) AS days_count,
                SUM(tokens_used) AS total_tokens,
                SUM(tokens_used) / COUNT(DISTINCT date) AS avg_tokens,
                COUNT(CASE WHEN role = 'assistant' THEN 1 END) AS total_requests,
                SUM(input_tokens) AS total_input_tokens,
                SUM(output_tokens) AS total_output_tokens,
                MIN(date) AS first_date,
                MAX(date) AS last_date,
                CURRENT_TIMESTAMP AS updated_at
            FROM daily_messages
            WHERE tool_name = 'qwen'
            GROUP BY host_name
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO usage_summary
            (tool_name, host_name, days_count, total_tokens, avg_tokens, total_requests,
             total_input_tokens, total_output_tokens, first_date, last_date, updated_at)
            SELECT
                'qwen' AS tool_name,
                '' AS host_name,
                COUNT(DISTINCT date) AS days_count,
                SUM(tokens_used) AS total_tokens,
                SUM(tokens_used) / COUNT(DISTINCT date) AS avg_tokens,
                COUNT(CASE WHEN role = 'assistant' THEN 1 END) AS total_requests,
                SUM(input_tokens) AS total_input_tokens,
                SUM(output_tokens) AS total_output_tokens,
                MIN(date) AS first_date,
                MAX(date) AS last_date,
                CURRENT_TIMESTAMP AS updated_at
            FROM daily_messages
            WHERE tool_name = 'qwen'
            GROUP BY tool_name
            """
        )
    )


def _normalize_sqlite_claude_usage_summary() -> None:
    """Collapse usage_summary rows that would collide on claude normalization."""
    op.execute(
        sa.text(
            """
            CREATE TEMP TABLE tmp_claude_usage_summary AS
            SELECT
                'claude' AS tool_name,
                host_name,
                MAX(days_count) AS days_count,
                SUM(total_tokens) AS total_tokens,
                SUM(total_requests) AS total_requests,
                SUM(total_input_tokens) AS total_input_tokens,
                SUM(total_output_tokens) AS total_output_tokens,
                MIN(first_date) AS first_date,
                MAX(last_date) AS last_date,
                MAX(updated_at) AS updated_at
            FROM usage_summary
            WHERE tool_name IN ('claude', 'claude-code')
            GROUP BY host_name
            """
        )
    )
    op.execute(sa.text("DELETE FROM usage_summary WHERE tool_name IN ('claude', 'claude-code')"))
    op.execute(
        sa.text(
            """
            INSERT INTO usage_summary
            (tool_name, host_name, days_count, total_tokens, avg_tokens, total_requests,
             total_input_tokens, total_output_tokens, first_date, last_date, updated_at)
            SELECT
                tool_name,
                host_name,
                days_count,
                total_tokens,
                total_tokens / CASE WHEN days_count > 0 THEN days_count ELSE 1 END AS avg_tokens,
                total_requests,
                total_input_tokens,
                total_output_tokens,
                first_date,
                last_date,
                updated_at
            FROM tmp_claude_usage_summary
            """
        )
    )
    op.execute(sa.text("DROP TABLE tmp_claude_usage_summary"))


def upgrade() -> None:
    conn = op.get_bind()

    if conn.dialect.name == "sqlite":
        if _table_exists(conn, "daily_stats"):
            _normalize_sqlite_daily_stats()
        if _table_exists(conn, "hourly_stats"):
            _normalize_sqlite_hourly_stats()
        if _table_exists(conn, "usage_summary"):
            _rebuild_sqlite_qwen_usage_summary()
            _normalize_sqlite_claude_usage_summary()
        return

    for table in ("daily_stats", "hourly_stats", "usage_summary"):
        if _table_exists(conn, table):
            op.execute(
                sa.text(
                    f"UPDATE {table} SET tool_name = 'qwen' "
                    f"WHERE tool_name IN ({ALIASES_IN})"
                )
            )
    # Also normalize claude-code in all derived tables
    if _table_exists(conn, "usage_summary"):
        op.execute(
            sa.text("UPDATE usage_summary SET tool_name = 'claude' " "WHERE tool_name = 'claude-code'")
        )


def downgrade() -> None:
    pass
