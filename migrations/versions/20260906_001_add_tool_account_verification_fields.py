"""Add verification fields to user_tool_accounts table

Issue #3273: Add verification_status, verification_result, and verified_at fields
to support tool account mapping verification functionality.

Revision ID: 20260906_001_add_tool_account_verification_fields
Revises: 20260905_001_add_encryption_keys_table
Create Date: 2026-09-06

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260906_001_add_tool_account_verification_fields'
down_revision = '20260905_001_add_encryption_keys_table'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add verification fields to user_tool_accounts table."""
    # Add verification_status field
    op.add_column(
        'user_tool_accounts',
        sa.Column('verification_status', sa.String(50), nullable=True)
    )

    # Add verification_result field
    op.add_column(
        'user_tool_accounts',
        sa.Column('verification_result', sa.Text(), nullable=True)
    )

    # Add verified_at field
    op.add_column(
        'user_tool_accounts',
        sa.Column('verified_at', sa.DateTime(), nullable=True)
    )

    # Set default value for existing records
    op.execute(
        "UPDATE user_tool_accounts SET verification_status = 'unverified' WHERE verification_status IS NULL"
    )


def downgrade() -> None:
    """Remove verification fields from user_tool_accounts table."""
    op.drop_column('user_tool_accounts', 'verified_at')
    op.drop_column('user_tool_accounts', 'verification_result')
    op.drop_column('user_tool_accounts', 'verification_status')