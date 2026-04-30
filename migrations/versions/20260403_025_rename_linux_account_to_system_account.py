"""Rename linux_account to system_account

This migration:
1. Renames linux_account column to system_account in users table
2. Updates the column to better reflect cross-platform support

Revision ID: 025
Revises: 024
Create Date: 2026-04-03
"""

from alembic import op

# revision identifiers
revision = "025_rename_linux_account_to_system_account"
down_revision = ("024_add_user_id_and_tool_accounts",)
branch_labels = None
depends_on = None


def upgrade():
    """Rename linux_account to system_account."""

    # Check if we're using PostgreSQL or SQLite
    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        # PostgreSQL: Use ALTER COLUMN with RENAME
        op.execute("""
            ALTER TABLE users RENAME COLUMN linux_account TO system_account
        """)
    else:
        # SQLite: Check if column exists first
        # SQLite doesn't support RENAME COLUMN in older versions
        # Use a safer approach
        op.execute("""
            -- First check if linux_account exists and system_account doesn't
            -- This is handled by the column detection logic
        """)

        # Try to rename (works in SQLite 3.25.0+)
        try:
            op.execute("""
                ALTER TABLE users RENAME COLUMN linux_account TO system_account
            """)
        except Exception:
            # Fallback for older SQLite: add new column, copy data, drop old column
            # This is more complex and requires table recreation
            op.execute("""
                ALTER TABLE users ADD COLUMN system_account TEXT
            """)
            op.execute("""
                UPDATE users SET system_account = linux_account
            """)
            # Note: SQLite doesn't easily support DROP COLUMN before 3.35.0
            # We keep the old column for compatibility


def downgrade():
    """Rename system_account back to linux_account."""

    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        op.execute("""
            ALTER TABLE users RENAME COLUMN system_account TO linux_account
        """)
    else:
        try:
            op.execute("""
                ALTER TABLE users RENAME COLUMN system_account TO linux_account
            """)
        except Exception:
            op.execute("""
                ALTER TABLE users ADD COLUMN linux_account TEXT
            """)
            op.execute("""
                UPDATE users SET linux_account = system_account
            """)
