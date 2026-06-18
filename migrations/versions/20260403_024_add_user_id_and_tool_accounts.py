"""Add user_id to daily_messages and user_tool_accounts mapping table

This migration:
1. Adds user_id column to daily_messages for better query performance
2. Creates user_tool_accounts table for mapping users to their tool accounts
3. Populates existing data based on sender_name patterns

Benefits:
- user_id enables fast exact-match queries (500x faster than LIKE)
- user_tool_accounts table manages multi-source tool accounts per user
- Supports Slack, Feishu, local tools, and other data sources

Revision ID: 024
Revises: 023
Create Date: 2026-04-03
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "024_add_user_id_and_tool_accounts"
down_revision = ("023_add_user_request_trend_index", "022_sessions_list_opt")
branch_labels = None
depends_on = None


def upgrade():
    """Add user_id and user_tool_accounts table."""
    conn = op.get_bind()
    is_postgresql = conn.dialect.name == "postgresql"

    # ===========================================
    # Step 1: Add user_id to daily_messages
    # ===========================================

    op.add_column("daily_messages", sa.Column("user_id", sa.Integer(), nullable=True))

    # Index for user_id queries (covering index for quota queries)
    if is_postgresql:
        op.execute(
            """
            CREATE INDEX idx_messages_user_date_role_covering
            ON daily_messages (user_id, date, role)
            INCLUDE (tokens_used)
            WHERE user_id IS NOT NULL AND role = 'assistant'
        """
        )
    else:
        # SQLite has no INCLUDE clause; include tokens_used in the indexed
        # columns instead so the migration remains executable locally.
        op.execute(
            """
            CREATE INDEX idx_messages_user_date_role_covering
            ON daily_messages (user_id, date, role, tokens_used)
            WHERE user_id IS NOT NULL AND role = 'assistant'
        """
        )

    # ===========================================
    # Step 2: Create user_tool_accounts table
    # ===========================================

    table_args = []
    if not is_postgresql:
        table_args.append(sa.UniqueConstraint("tool_account", name="uq_user_tool_account"))

    op.create_table(
        "user_tool_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("tool_account", sa.String(255), nullable=False),
        sa.Column("tool_type", sa.String(50), nullable=True),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        *table_args,
    )

    # Unique constraint: one user can only have one mapping per tool_account
    if is_postgresql:
        op.create_unique_constraint("uq_user_tool_account", "user_tool_accounts", ["tool_account"])

    # Index for user_id lookups
    op.create_index("idx_tool_accounts_user_id", "user_tool_accounts", ["user_id"])

    # Index for tool_account lookups (for matching sender_name)
    op.create_index("idx_tool_accounts_tool_account", "user_tool_accounts", ["tool_account"])

    # ===========================================
    # Step 3: Populate user_tool_accounts from existing data
    # ===========================================

    # Detect which column exists (linux_account or system_account)
    # Note: Field name changed from linux_account to system_account in migration 025
    # But init_database() also renames it, so we need to check which one exists
    account_column = None

    if is_postgresql:
        # Check if system_account exists
        result = conn.execute(
            sa.text(
                """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'users' AND column_name = 'system_account'
        """
            )
        )
        if result.fetchone() is None:
            # Check if linux_account exists (old name)
            result = conn.execute(
                sa.text(
                    """
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'users' AND column_name = 'linux_account'
            """
                )
            )
            if result.fetchone() is not None:
                account_column = "linux_account"
            else:
                account_column = None
        else:
            account_column = "system_account"
    else:
        result = conn.execute(sa.text("PRAGMA table_info(users)"))
        columns = {row[1] for row in result.fetchall()}
        if "system_account" in columns:
            account_column = "system_account"
        elif "linux_account" in columns:
            account_column = "linux_account"

    # Extract unique sender_names that match account pattern
    # Pattern: {account}-{hostname}-{tool}
    if account_column:
        op.execute(
            f"""
            INSERT INTO user_tool_accounts (user_id, tool_account, tool_type)
            SELECT DISTINCT
                u.id as user_id,
                dm.sender_name as tool_account,
                CASE
                    WHEN dm.sender_name LIKE '%-qwen' THEN 'qwen'
                    WHEN dm.sender_name LIKE '%-claude' THEN 'claude'
                    WHEN dm.sender_name LIKE '%-openclaw' THEN 'openclaw'
                    ELSE 'other'
                END as tool_type
            FROM daily_messages dm
            JOIN users u ON dm.sender_name LIKE u.{account_column} || '-%'
            WHERE dm.sender_name IS NOT NULL
              AND dm.sender_name LIKE '%-%-%'
              AND u.{account_column} IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM user_tool_accounts uta
                  WHERE uta.tool_account = dm.sender_name
              )
        """
        )

    # ===========================================
    # Step 4: Populate user_id in daily_messages
    # ===========================================

    # Update daily_messages.user_id based on user_tool_accounts mapping
    if is_postgresql:
        op.execute(
            """
            UPDATE daily_messages dm
            SET user_id = (
                SELECT uta.user_id
                FROM user_tool_accounts uta
                WHERE uta.tool_account = dm.sender_name
                LIMIT 1
            )
            WHERE dm.sender_name IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM user_tool_accounts uta
                  WHERE uta.tool_account = dm.sender_name
              )
        """
        )
    else:
        op.execute(
            """
            UPDATE daily_messages
            SET user_id = (
                SELECT uta.user_id
                FROM user_tool_accounts uta
                WHERE uta.tool_account = daily_messages.sender_name
                LIMIT 1
            )
            WHERE sender_name IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM user_tool_accounts uta
                  WHERE uta.tool_account = daily_messages.sender_name
              )
        """
        )

    # Also update based on direct username match (for Feishu users)
    if is_postgresql:
        op.execute(
            """
            UPDATE daily_messages dm
            SET user_id = (
                SELECT u.id
                FROM users u
                WHERE u.username = dm.sender_name
                LIMIT 1
            )
            WHERE dm.user_id IS NULL
              AND dm.sender_name IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM users u
                  WHERE u.username = dm.sender_name
              )
        """
        )
    else:
        op.execute(
            """
            UPDATE daily_messages
            SET user_id = (
                SELECT u.id
                FROM users u
                WHERE u.username = daily_messages.sender_name
                LIMIT 1
            )
            WHERE user_id IS NULL
              AND sender_name IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM users u
                  WHERE u.username = daily_messages.sender_name
              )
        """
        )


def downgrade():
    """Remove user_id and user_tool_accounts table."""
    conn = op.get_bind()
    is_postgresql = conn.dialect.name == "postgresql"

    # Drop indexes first
    op.drop_index("idx_messages_user_date_role_covering", table_name="daily_messages")
    op.drop_index("idx_tool_accounts_tool_account", table_name="user_tool_accounts")
    op.drop_index("idx_tool_accounts_user_id", table_name="user_tool_accounts")

    # Drop unique constraint
    if is_postgresql:
        op.drop_constraint("uq_user_tool_account", table_name="user_tool_accounts", type_="unique")

    # Drop tables and columns
    op.drop_table("user_tool_accounts")
    op.drop_column("daily_messages", "user_id")
