"""Add cache token columns to agent_sessions

Issue #2184: Multi-provider usage recording with cache token support.

Revision ID: 20260803_005
Revises: 20260803_004_backfill_session_messages_columns
Create Date: 2026-08-03

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260803_005"
down_revision = "20260803_004_backfill_session_messages_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add cache token columns to agent_sessions table."""
    # Get database type for conditional logic
    bind = op.get_bind()
    dialect = bind.dialect.name

    # Check if columns already exist (SQLite doesn't support IF NOT EXISTS for ALTER TABLE)
    if dialect == 'sqlite':
        # SQLite: Check and add columns individually
        inspector = sa.inspect(bind)
        columns = [col['name'] for col in inspector.get_columns('agent_sessions')]

        if 'total_cache_read_tokens' not in columns:
            op.add_column(
                'agent_sessions',
                sa.Column('total_cache_read_tokens', sa.Integer(), nullable=False, server_default='0'),
            )

        if 'total_cache_write_tokens' not in columns:
            op.add_column(
                'agent_sessions',
                sa.Column('total_cache_write_tokens', sa.Integer(), nullable=False, server_default='0'),
            )
    else:
        # PostgreSQL and others: Use IF NOT EXISTS equivalent
        op.add_column(
            'agent_sessions',
            sa.Column('total_cache_read_tokens', sa.Integer(), nullable=False, server_default='0'),
        )
        op.add_column(
            'agent_sessions',
            sa.Column('total_cache_write_tokens', sa.Integer(), nullable=False, server_default='0'),
        )


def downgrade() -> None:
    """Remove cache token columns from agent_sessions table."""
    # Get database type
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == 'sqlite':
        # SQLite: Check if columns exist before dropping
        inspector = sa.inspect(bind)
        columns = [col['name'] for col in inspector.get_columns('agent_sessions')]

        if 'total_cache_write_tokens' in columns:
            op.drop_column('agent_sessions', 'total_cache_write_tokens')

        if 'total_cache_read_tokens' in columns:
            op.drop_column('agent_sessions', 'total_cache_read_tokens')
    else:
        # PostgreSQL and others
        op.drop_column('agent_sessions', 'total_cache_write_tokens')
        op.drop_column('agent_sessions', 'total_cache_read_tokens')
