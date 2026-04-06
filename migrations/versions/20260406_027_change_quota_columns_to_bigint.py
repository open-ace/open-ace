"""change quota columns to bigint

Revision ID: 027_quota_bigint
Revises: 026_add_projects
Create Date: 2026-04-06

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '027_quota_bigint'
down_revision = '026_add_projects'
branch_labels = None
depends_on = None


def upgrade():
    """Change quota columns from integer to bigint to support larger values."""
    # PostgreSQL: integer max is ~2.1 billion, bigint max is ~9.2 quintillion
    # Frontend sends values multiplied by 1,000,000 (million tokens)
    # So 90,000 million = 90 billion, which overflows integer
    
    op.alter_column('users', 'daily_token_quota',
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True
    )
    
    op.alter_column('users', 'monthly_token_quota',
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True
    )
    
    op.alter_column('users', 'daily_request_quota',
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True
    )
    
    op.alter_column('users', 'monthly_request_quota',
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True
    )


def downgrade():
    """Change quota columns back to integer."""
    op.alter_column('users', 'monthly_request_quota',
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=True
    )
    
    op.alter_column('users', 'daily_request_quota',
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=True
    )
    
    op.alter_column('users', 'monthly_token_quota',
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=True
    )
    
    op.alter_column('users', 'daily_token_quota',
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=True
    )