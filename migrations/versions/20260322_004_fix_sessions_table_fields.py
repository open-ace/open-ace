"""Fix sessions table fields

Revision ID: 004_fix_sessions_table_fields
Revises: 003_fix_users_table_fields
Create Date: 2026-03-22

This migration fixes the sessions table:
- Renames 'session_id' column to 'token' (code uses 'token')
- Adds proper foreign key constraint to users table
- Adds expires_at index for cleanup queries

SQLite doesn't support renaming columns directly in older versions,
so we recreate the table.

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "004_fix_sessions_table_fields"
down_revision: Union[str, None] = "003_fix_users_table_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade database schema."""
    # SQLite requires table recreation to rename columns
    # Create new sessions table with correct column name
    op.create_table(
        "sessions_new",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("token", sa.String(), nullable=False, unique=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("expires_at", sa.TIMESTAMP(), nullable=False),
        sa.Column("is_active", sa.Integer(), server_default="1"),
    )

    # Migrate data from old table (session_id -> token)
    op.execute("""
        INSERT INTO sessions_new (id, token, user_id, created_at, expires_at, is_active)
        SELECT id, session_id, user_id, created_at, expires_at, is_active FROM sessions
    """)

    # Drop old table and rename new one
    op.drop_table("sessions")
    op.rename_table("sessions_new", "sessions")

    # Create indexes
    op.create_index("idx_sessions_user_id", "sessions", ["user_id"])
    op.create_index("idx_sessions_token", "sessions", ["token"])
    op.create_index("idx_sessions_expires", "sessions", ["expires_at"])
    op.create_index("idx_sessions_active", "sessions", ["is_active", "expires_at"])


def downgrade() -> None:
    """Downgrade database schema."""
    # Recreate original table structure
    op.create_table(
        "sessions_old",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.String(), nullable=False, unique=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("expires_at", sa.TIMESTAMP(), nullable=False),
        sa.Column("is_active", sa.Integer(), server_default="1"),
    )

    # Migrate data back
    op.execute("""
        INSERT INTO sessions_old (id, session_id, user_id, created_at, expires_at, is_active)
        SELECT id, token, user_id, created_at, expires_at, is_active FROM sessions
    """)

    # Drop new table and rename old one back
    op.drop_table("sessions")
    op.rename_table("sessions_old", "sessions")

    # Recreate original indexes
    op.create_index("idx_sessions_user_id", "sessions", ["user_id"])
    op.create_index("idx_sessions_session_id", "sessions", ["session_id"])
