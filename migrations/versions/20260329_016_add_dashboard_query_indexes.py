"""Add indexes for dashboard query performance

Revision ID: 016_add_dashboard_indexes
Revises: 015_add_must_change_password
Create Date: 2026-03-29

This migration adds indexes to improve dashboard query performance:
- idx_messages_tool_name: for GROUP BY tool_name queries (summary API)
- idx_messages_host_name: for DISTINCT host_name queries (hosts API)

Problem:
- /api/summary query takes ~400ms due to full table scan for GROUP BY tool_name
- /api/hosts query takes ~2s due to scanning 193k rows for DISTINCT host_name

Solution:
- Add single-column indexes on tool_name and host_name
- These were removed in migration 014 as "redundant", but they are actually
  needed for queries that don't include date in the filter

"""
from typing import Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '016_add_dashboard_indexes'
down_revision: Union[str, None] = '015_add_must_change_password'
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def _index_exists(conn, table_name: str, index_name: str) -> bool:
    """Check if an index exists in the database."""
    if conn.dialect.name == 'postgresql':
        result = conn.execute(sa.text(
            "SELECT 1 FROM pg_indexes WHERE indexname = :index_name"
        ), {'index_name': index_name})
    else:
        # SQLite
        result = conn.execute(sa.text(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name = :index_name"
        ), {'index_name': index_name})
    return result.fetchone() is not None


def upgrade() -> None:
    """Upgrade database schema."""
    conn = op.get_bind()
    
    # Add index on tool_name for GROUP BY tool_name queries (summary API)
    if not _index_exists(conn, 'daily_messages', 'idx_messages_tool_name'):
        op.create_index(
            'idx_messages_tool_name',
            'daily_messages',
            ['tool_name']
        )
    
    # Add index on host_name for DISTINCT host_name queries (hosts API)
    if not _index_exists(conn, 'daily_messages', 'idx_messages_host_name'):
        op.create_index(
            'idx_messages_host_name',
            'daily_messages',
            ['host_name']
        )


def downgrade() -> None:
    """Downgrade database schema."""
    conn = op.get_bind()
    
    if _index_exists(conn, 'daily_messages', 'idx_messages_tool_name'):
        op.drop_index('idx_messages_tool_name', 'daily_messages')
    
    if _index_exists(conn, 'daily_messages', 'idx_messages_host_name'):
        op.drop_index('idx_messages_host_name', 'daily_messages')