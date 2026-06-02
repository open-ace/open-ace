"""Add user_id column to daily_stats table for accurate user statistics

Revision ID: 049_add_user_id_to_daily_stats
Revises: 048_fix_api_key_scope
Create Date: 2026-06-02

This migration adds user_id column to daily_stats table to fix the issue
where users are counted multiple times due to different sender_name formats.

Issue #626: User statistics inconsistency - same user appears as multiple
entries when using different sender_name formats (WebUI vs Feishu).

"""

from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "049_add_user_id_to_daily_stats"
down_revision: Union[str, None] = "048_fix_api_key_scope"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT FROM information_schema.columns
                    WHERE table_schema = 'public'
                    AND table_name = :table_name
                    AND column_name = :column_name
                )
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        )
        return result.fetchone()[0]
    else:
        result = conn.execute(
            sa.text(f"PRAGMA table_info({table_name})"),
        )
        for row in result.fetchall():
            if row[1] == column_name:
                return True
        return False


def _index_exists(conn, table_name: str, index_name: str) -> bool:
    """Check if an index exists."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text("SELECT 1 FROM pg_indexes WHERE indexname = :index_name"),
            {"index_name": index_name},
        )
    else:
        result = conn.execute(
            sa.text(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name = :index_name"
            ),
            {"index_name": index_name},
        )
    return result.fetchone() is not None


def upgrade() -> None:
    """Add user_id column to daily_stats table."""
    conn = op.get_bind()
    is_postgresql = conn.dialect.name == "postgresql"

    # Add user_id column to daily_stats if not exists
    if not _column_exists(conn, "daily_stats", "user_id"):
        if is_postgresql:
            op.execute(
                """
                ALTER TABLE daily_stats ADD COLUMN user_id INTEGER NULL
                """
            )
        else:
            op.execute(
                """
                ALTER TABLE daily_stats ADD COLUMN user_id INTEGER
                """
            )

        print("Added user_id column to daily_stats table.")

    # Create index on user_id for fast lookups
    if not _index_exists(conn, "daily_stats", "idx_daily_stats_user_id"):
        if is_postgresql:
            op.execute(
                """
                CREATE INDEX idx_daily_stats_user_id ON daily_stats (user_id)
                """
            )
        else:
            op.execute(
                """
                CREATE INDEX idx_daily_stats_user_id ON daily_stats (user_id)
                """
            )

        print("Created index idx_daily_stats_user_id.")

    # Populate user_id for existing data by matching sender_name to users
    # sender_name formats:
    # 1. WebUI: {system_account}-{hostname}-{tool} -> match users.system_account
    # 2. Feishu: username (real name) -> match users.username
    if _column_exists(conn, "daily_stats", "user_id") and _column_exists(
        conn, "users", "system_account"
    ):
        if is_postgresql:
            # PostgreSQL: use substring matching for system_account prefix
            op.execute(
                """
                UPDATE daily_stats ds
                SET user_id = (
                    SELECT u.id FROM users u
                    WHERE ds.sender_name LIKE (u.system_account || '-%')
                       OR ds.sender_name = u.username
                    LIMIT 1
                )
                WHERE ds.user_id IS NULL
                  AND ds.sender_name IS NOT NULL
                  AND EXISTS (
                      SELECT 1 FROM users u
                      WHERE ds.sender_name LIKE (u.system_account || '-%')
                         OR ds.sender_name = u.username
                  )
                """
            )
        else:
            # SQLite: use LIKE pattern matching
            op.execute(
                """
                UPDATE daily_stats
                SET user_id = (
                    SELECT u.id FROM users u
                    WHERE sender_name LIKE (u.system_account || '-%')
                       OR sender_name = u.username
                    LIMIT 1
                )
                WHERE user_id IS NULL
                  AND sender_name IS NOT NULL
                """
            )

        print("Populated user_id for existing daily_stats records.")


def downgrade() -> None:
    """Remove user_id column from daily_stats table."""
    conn = op.get_bind()
    is_postgresql = conn.dialect.name == "postgresql"

    # Drop index first
    if _index_exists(conn, "daily_stats", "idx_daily_stats_user_id"):
        op.execute(
            """
            DROP INDEX idx_daily_stats_user_id
            """
        )
        print("Dropped index idx_daily_stats_user_id.")

    # Drop column
    if _column_exists(conn, "daily_stats", "user_id"):
        if is_postgresql:
            op.execute(
                """
                ALTER TABLE daily_stats DROP COLUMN user_id
                """
            )
        else:
            # SQLite doesn't support DROP COLUMN directly
            # Need to recreate table without the column
            print(
                "Warning: SQLite doesn't support DROP COLUMN. "
                "user_id column will remain but won't be used."
            )

        print("Removed user_id column from daily_stats table.")
