"""Optimize indexes

Revision ID: 005_optimize_indexes
Revises: 004_fix_sessions_table_fields
Create Date: 2026-03-22

This migration optimizes indexes:
- Removes redundant indexes on daily_messages
- Adds missing indexes for common queries
- Adds composite indexes for query optimization

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "005_optimize_indexes"
down_revision: Union[str, None] = "004_fix_sessions_table_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade database schema."""
    # ============================================
    # daily_messages table - Remove redundant indexes
    # ============================================
    # These are covered by composite indexes or rarely used
    op.drop_index("idx_messages_date_tool", "daily_messages")
    op.drop_index("idx_messages_date_host", "daily_messages")
    op.drop_index("idx_messages_date_sender", "daily_messages")

    # ============================================
    # daily_messages table - Add missing indexes
    # ============================================
    # For conversation queries
    op.create_index("idx_messages_feishu_conv", "daily_messages", ["feishu_conversation_id"])
    op.create_index("idx_messages_conv_id", "daily_messages", ["conversation_id"])
    # Composite index for conversation history queries
    op.create_index("idx_messages_date_conv", "daily_messages", ["date", "feishu_conversation_id"])

    # Optimized composite index for common query patterns
    # Replaces multiple single-column indexes for date-based queries
    op.create_index(
        "idx_messages_query_optimized", "daily_messages", ["date", "tool_name", "host_name"]
    )

    # ============================================
    # daily_usage table - Add composite index
    # ============================================
    op.create_index("idx_usage_date_tool_host", "daily_usage", ["date", "tool_name", "host_name"])


def downgrade() -> None:
    """Downgrade database schema."""
    # Drop new indexes
    op.drop_index("idx_usage_date_tool_host", "daily_usage")

    op.drop_index("idx_messages_query_optimized", "daily_messages")
    op.drop_index("idx_messages_date_conv", "daily_messages")
    op.drop_index("idx_messages_conv_id", "daily_messages")
    op.drop_index("idx_messages_feishu_conv", "daily_messages")

    # Restore removed indexes
    op.create_index("idx_messages_date_sender", "daily_messages", ["date", "sender_name"])
    op.create_index("idx_messages_date_host", "daily_messages", ["date", "host_name"])
    op.create_index("idx_messages_date_tool", "daily_messages", ["date", "tool_name"])
