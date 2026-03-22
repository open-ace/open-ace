"""Fix users table fields

Revision ID: 003_fix_users_table_fields
Revises: 002_add_missing_tables
Create Date: 2026-03-22

This migration fixes the users table:
- Adds 'role' column (replacing is_admin functionality)
- Adds quota columns: daily_token_quota, monthly_token_quota,
  daily_request_quota, monthly_request_quota
- Migrates is_admin data to role column

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '003_fix_users_table_fields'
down_revision: Union[str, None] = '002_add_missing_tables'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade database schema."""
    # Add role column
    op.add_column('users', sa.Column('role', sa.String(), server_default='user'))

    # Migrate is_admin to role
    op.execute("UPDATE users SET role = 'admin' WHERE is_admin = 1")

    # Add quota columns
    op.add_column('users', sa.Column('daily_token_quota', sa.Integer(), nullable=True))
    op.add_column('users', sa.Column('monthly_token_quota', sa.Integer(), nullable=True))
    op.add_column('users', sa.Column('daily_request_quota', sa.Integer(), nullable=True))
    op.add_column('users', sa.Column('monthly_request_quota', sa.Integer(), nullable=True))

    # Add indexes
    op.create_index('idx_users_role', 'users', ['role'])
    op.create_index('idx_users_email', 'users', ['email'])
    op.create_index('idx_users_active', 'users', ['is_active'])


def downgrade() -> None:
    """Downgrade database schema."""
    op.drop_index('idx_users_active', 'users')
    op.drop_index('idx_users_email', 'users')
    op.drop_index('idx_users_role', 'users')

    op.drop_column('users', 'monthly_request_quota')
    op.drop_column('users', 'daily_request_quota')
    op.drop_column('users', 'monthly_token_quota')
    op.drop_column('users', 'daily_token_quota')
    op.drop_column('users', 'role')