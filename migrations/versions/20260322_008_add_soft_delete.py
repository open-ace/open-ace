"""Add soft delete support

Revision ID: 008_add_soft_delete
Revises: 007_split_tenant_json_fields
Create Date: 2026-03-22

This migration adds soft delete support to important tables:
- users: deleted_at column
- tenants: deleted_at column
- daily_messages: deleted_at column

Soft delete allows recovery of accidentally deleted data.

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "008_add_soft_delete"
down_revision: Union[str, None] = "007_split_tenant_json_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade database schema."""
    # Add deleted_at to users table
    op.add_column("users", sa.Column("deleted_at", sa.TIMESTAMP(), nullable=True))
    op.create_index("idx_users_deleted", "users", ["deleted_at"])

    # Add deleted_at to tenants table
    op.add_column("tenants", sa.Column("deleted_at", sa.TIMESTAMP(), nullable=True))
    op.create_index("idx_tenants_deleted", "tenants", ["deleted_at"])

    # Add deleted_at to daily_messages table
    op.add_column("daily_messages", sa.Column("deleted_at", sa.TIMESTAMP(), nullable=True))
    op.create_index("idx_messages_deleted", "daily_messages", ["deleted_at"])


def downgrade() -> None:
    """Downgrade database schema."""
    # Remove from daily_messages
    op.drop_index("idx_messages_deleted", "daily_messages")
    op.drop_column("daily_messages", "deleted_at")

    # Remove from tenants
    op.drop_index("idx_tenants_deleted", "tenants")
    op.drop_column("tenants", "deleted_at")

    # Remove from users
    op.drop_index("idx_users_deleted", "users")
    op.drop_column("users", "deleted_at")
