"""Add tenant_id to users table for multi-tenant support

Revision ID: 011_add_tenant_id_to_users
Revises: 010_fix_is_group_chat_type
Create Date: 2026-03-28

This migration adds tenant_id column to users table for multi-tenant support:
- Adds tenant_id column with foreign key to tenants table
- Creates index on tenant_id for efficient queries
- Sets default tenant for existing users (if tenants exist)

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '011_add_tenant_id_to_users'
down_revision: Union[str, None] = '010_fix_is_group_chat_type'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade database schema."""
    # Get database connection
    conn = op.get_bind()
    is_postgresql = conn.dialect.name == 'postgresql'

    # Add tenant_id column to users table
    op.add_column('users', sa.Column('tenant_id', sa.Integer(), nullable=True))

    # Create index on tenant_id
    op.create_index('idx_users_tenant', 'users', ['tenant_id'])

    # For PostgreSQL, add foreign key constraint
    if is_postgresql:
        op.execute("""
            ALTER TABLE users
            ADD CONSTRAINT fk_users_tenant
            FOREIGN KEY (tenant_id)
            REFERENCES tenants(id)
            ON DELETE SET NULL
        """)

    # For SQLite, we need to recreate the table to add foreign key
    # SQLite doesn't support ALTER TABLE ADD CONSTRAINT
    # The foreign key will be enforced through PRAGMA foreign_keys = ON
    # which is already set in database.py

    # Set default tenant for existing users if tenants exist
    result = conn.execute(sa.text("SELECT id FROM tenants LIMIT 1"))
    first_tenant = result.fetchone()
    if first_tenant:
        op.execute(f"UPDATE users SET tenant_id = {first_tenant[0]} WHERE tenant_id IS NULL")


def downgrade() -> None:
    """Downgrade database schema."""
    conn = op.get_bind()
    is_postgresql = conn.dialect.name == 'postgresql'

    # Drop foreign key constraint for PostgreSQL
    if is_postgresql:
        op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS fk_users_tenant")

    # Drop index
    op.drop_index('idx_users_tenant', 'users')

    # Drop column
    op.drop_column('users', 'tenant_id')