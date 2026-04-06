"""Add project_path to daily_messages

Revision ID: 028_project_path
Revises: 027_session_stats_indexes
Create Date: 2026-04-06

"""
from typing import Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "028_project_path"
down_revision: Union[str, None] = "027_session_stats_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add project_path column to daily_messages table."""
    # Check if column already exists (for SQLite compatibility)
    conn = op.get_bind()
    if conn.dialect.name == 'postgresql':
        # PostgreSQL: Check if column exists
        check_query = """
            SELECT COUNT(*) 
            FROM information_schema.columns 
            WHERE table_name = 'daily_messages' 
            AND column_name = 'project_path'
        """
        result = conn.execute(sa.text(check_query)).fetchone()
        if result[0] > 0:
            print("Column project_path already exists in daily_messages")
            return
    
    # Add column
    op.add_column('daily_messages', sa.Column('project_path', sa.Text(), nullable=True))
    
    # Create index for better query performance
    op.create_index(
        'idx_messages_project_path',
        'daily_messages',
        ['project_path'],
        unique=False
    )
    
    op.create_index(
        'idx_messages_agent_session_project',
        'daily_messages',
        ['agent_session_id', 'project_path'],
        unique=False
    )


def downgrade() -> None:
    """Remove project_path column from daily_messages table."""
    # Drop indexes first
    op.drop_index('idx_messages_agent_session_project', table_name='daily_messages')
    op.drop_index('idx_messages_project_path', table_name='daily_messages')
    
    # Drop column
    op.drop_column('daily_messages', 'project_path')
