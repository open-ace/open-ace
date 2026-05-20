"""Add indexes for request statistics queries

Revision ID: 022_add_request_stats_indexes
Revises: 021_postgresql_optimization
Create Date: 2026-04-02

This migration adds indexes to optimize request statistics queries:
1. Index on daily_messages for user-level request counting
2. Index on daily_usage for request trend queries
3. Index on quota_usage for user quota status queries

"""

from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "022_add_request_stats_indexes"
down_revision: Union[str, None] = "021_postgresql_optimization"
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


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.columns
                    WHERE table_name = :table_name AND column_name = :column_name
                )
                """),
            {"table_name": table_name, "column_name": column_name},
        )
        return result.fetchone()[0]
    else:
        result = conn.execute(
            sa.text(f"PRAGMA table_info({table_name})"),
        )
        columns = [row[1] for row in result.fetchall()]
        return column_name in columns


def upgrade() -> None:
    """Add indexes for request statistics queries."""
    conn = op.get_bind()
    is_postgresql = conn.dialect.name == "postgresql"

    # ============================================
    # 1. Indexes for daily_messages (user-level request counting)
    # ============================================
    # Index for counting requests by user (sender_name) and date
    # role='assistant' indicates an API response (1 request)
    if _column_exists(conn, "daily_messages", "sender_name"):
        if not _index_exists(conn, "daily_messages", "idx_daily_messages_sender_date"):
            if is_postgresql:
                op.execute("""
                    CREATE INDEX idx_daily_messages_sender_date
                    ON daily_messages (sender_name, date, role)
                    WHERE role = 'assistant'
                    """)
            else:
                # SQLite doesn't support partial indexes with WHERE in the same way
                op.execute("""
                    CREATE INDEX idx_daily_messages_sender_date
                    ON daily_messages (sender_name, date, role)
                    """)

    # Index for user request trend queries
    if _column_exists(conn, "daily_messages", "sender_name"):
        if not _index_exists(conn, "daily_messages", "idx_daily_messages_sender_timestamp"):
            op.execute("""
                CREATE INDEX idx_daily_messages_sender_timestamp
                ON daily_messages (sender_name, timestamp)
                """)

    # ============================================
    # 2. Indexes for daily_usage (request trend queries)
    # ============================================
    if _column_exists(conn, "daily_usage", "request_count"):
        if not _index_exists(conn, "daily_usage", "idx_daily_usage_date_requests"):
            op.execute("""
                CREATE INDEX idx_daily_usage_date_requests
                ON daily_usage (date, request_count)
                """)

    # Index for request trend by tool
    if not _index_exists(conn, "daily_usage", "idx_daily_usage_tool_date"):
        op.execute("""
            CREATE INDEX idx_daily_usage_tool_date
            ON daily_usage (tool_name, date)
            """)

    # ============================================
    # 3. Indexes for quota_usage (user quota status queries)
    # ============================================
    # Index for getting user's current quota status
    if not _index_exists(conn, "quota_usage", "idx_quota_usage_user_period_date"):
        op.execute("""
            CREATE INDEX idx_quota_usage_user_period_date
            ON quota_usage (user_id, period, date DESC)
            """)

    # ============================================
    # 4. Add monthly_request_quota to users if not exists
    # ============================================
    # This should already exist from migration 003, but check just in case
    if not _column_exists(conn, "users", "monthly_request_quota"):
        if is_postgresql:
            op.execute("""
                ALTER TABLE users
                ADD COLUMN monthly_request_quota INTEGER DEFAULT 30000
                """)
        else:
            op.add_column(
                "users",
                sa.Column("monthly_request_quota", sa.Integer(), server_default="30000"),
            )


def downgrade() -> None:
    """Remove indexes for request statistics queries."""
    conn = op.get_bind()

    # Drop indexes in reverse order
    indexes_to_drop = [
        ("daily_messages", "idx_daily_messages_sender_timestamp"),
        ("daily_messages", "idx_daily_messages_sender_date"),
        ("daily_usage", "idx_daily_usage_date_requests"),
        ("daily_usage", "idx_daily_usage_tool_date"),
        ("quota_usage", "idx_quota_usage_user_period_date"),
    ]

    for table_name, index_name in indexes_to_drop:
        if _index_exists(conn, table_name, index_name):
            op.execute(f"DROP INDEX {index_name}")
