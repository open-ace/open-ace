"""Optimize daily_messages indexes

Revision ID: 014_optimize_msg_indexes
Revises: 013_add_check_constraints
Create Date: 2026-03-28

This migration optimizes the indexes on daily_messages table:
- Removes redundant single-column indexes that are covered by composite indexes
- Keeps essential composite indexes for common query patterns
- Adds new composite indexes for better query coverage

Current indexes (14+):
- idx_messages_date (redundant - covered by composite indexes)
- idx_messages_tool_name (redundant - covered by composite indexes)
- idx_messages_host_name (redundant - covered by composite indexes)
- idx_messages_sender_name (low usage)
- idx_messages_sender_id (keep for sender queries)
- idx_messages_timestamp (redundant - covered by composite indexes)
- idx_messages_role (redundant - covered by composite indexes)
- idx_messages_date_tool (redundant - covered by date_tool_host)
- idx_messages_date_host (redundant - covered by date_tool_host)
- idx_messages_date_sender (low usage)
- idx_messages_date_tool_host (keep - most common query)
- idx_messages_date_timestamp (redundant - covered by date_role_timestamp)
- idx_messages_date_role_timestamp (keep - sorting queries)

Optimized indexes:
- idx_messages_date_tool_host (keep)
- idx_messages_date_role_timestamp (keep)
- idx_messages_sender_id (keep)
- idx_messages_conversation (new - for conversation queries)

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '014_optimize_msg_indexes'
down_revision: Union[str, None] = '013_add_check_constraints'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade database schema."""
    # Drop redundant indexes
    # These single-column indexes are covered by composite indexes
    op.drop_index('idx_messages_date', 'daily_messages')
    op.drop_index('idx_messages_tool_name', 'daily_messages')
    op.drop_index('idx_messages_host_name', 'daily_messages')
    op.drop_index('idx_messages_sender_name', 'daily_messages')
    op.drop_index('idx_messages_timestamp', 'daily_messages')
    op.drop_index('idx_messages_role', 'daily_messages')

    # Drop redundant composite indexes
    # These are covered by idx_messages_date_tool_host or idx_messages_date_role_timestamp
    op.drop_index('idx_messages_date_tool', 'daily_messages')
    op.drop_index('idx_messages_date_host', 'daily_messages')
    op.drop_index('idx_messages_date_sender', 'daily_messages')
    op.drop_index('idx_messages_date_timestamp', 'daily_messages')

    # Add new composite index for conversation queries
    # This covers queries that filter by conversation_id or agent_session_id
    op.create_index(
        'idx_messages_conversation',
        'daily_messages',
        ['date', 'conversation_id', 'agent_session_id']
    )

    # Add composite index for sender queries with date
    # Replaces idx_messages_date_sender with better coverage
    op.create_index(
        'idx_messages_date_sender_id',
        'daily_messages',
        ['date', 'sender_id']
    )


def downgrade() -> None:
    """Downgrade database schema."""
    # Drop new indexes
    op.drop_index('idx_messages_date_sender_id', 'daily_messages')
    op.drop_index('idx_messages_conversation', 'daily_messages')

    # Recreate dropped indexes
    op.create_index('idx_messages_date_timestamp', 'daily_messages', ['date', sa.text('timestamp DESC')])
    op.create_index('idx_messages_date_sender', 'daily_messages', ['date', 'sender_name'])
    op.create_index('idx_messages_date_host', 'daily_messages', ['date', 'host_name'])
    op.create_index('idx_messages_date_tool', 'daily_messages', ['date', 'tool_name'])
    op.create_index('idx_messages_role', 'daily_messages', ['role'])
    op.create_index('idx_messages_timestamp', 'daily_messages', ['timestamp'])
    op.create_index('idx_messages_sender_name', 'daily_messages', ['sender_name'])
    op.create_index('idx_messages_host_name', 'daily_messages', ['host_name'])
    op.create_index('idx_messages_tool_name', 'daily_messages', ['tool_name'])
    op.create_index('idx_messages_date', 'daily_messages', ['date'])