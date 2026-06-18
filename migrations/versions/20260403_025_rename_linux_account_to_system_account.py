"""Rename linux_account to system_account

This migration:
1. Renames linux_account column to system_account in users table
2. Updates the column to better reflect cross-platform support

Revision ID: 025
Revises: 024
Create Date: 2026-04-03
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "025_rename_linux_account_to_system_account"
down_revision = ("024_add_user_id_and_tool_accounts",)
branch_labels = None
depends_on = None


def _column_exists(bind, table_name: str, column_name: str) -> bool:
    """Check whether a column exists in the current database."""
    if bind.dialect.name == "postgresql":
        result = bind.execute(
            sa.text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = :table_name AND column_name = :column_name
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        )
        return result.fetchone() is not None

    result = bind.execute(sa.text(f"PRAGMA table_info({table_name})"))
    return any(row[1] == column_name for row in result.fetchall())


def upgrade():
    """Rename linux_account to system_account."""

    # Check if we're using PostgreSQL or SQLite
    bind = op.get_bind()
    has_linux_account = _column_exists(bind, "users", "linux_account")
    has_system_account = _column_exists(bind, "users", "system_account")

    if bind.dialect.name == "postgresql":
        if not has_linux_account or has_system_account:
            return
        # PostgreSQL: Use ALTER COLUMN with RENAME
        op.execute(
            """
            ALTER TABLE users RENAME COLUMN linux_account TO system_account
        """
        )
    else:
        if not has_linux_account or has_system_account:
            return

        # Try to rename (works in SQLite 3.25.0+)
        try:
            op.execute(
                """
                ALTER TABLE users RENAME COLUMN linux_account TO system_account
            """
            )
        except Exception:
            # Fallback for older SQLite: add new column, copy data, drop old column
            # This is more complex and requires table recreation
            op.execute(
                """
                ALTER TABLE users ADD COLUMN system_account TEXT
            """
            )
            op.execute(
                """
                UPDATE users SET system_account = linux_account
            """
            )
            # Note: SQLite doesn't easily support DROP COLUMN before 3.35.0
            # We keep the old column for compatibility


def downgrade():
    """Rename system_account back to linux_account."""

    bind = op.get_bind()
    has_linux_account = _column_exists(bind, "users", "linux_account")
    has_system_account = _column_exists(bind, "users", "system_account")

    if bind.dialect.name == "postgresql":
        if not has_system_account or has_linux_account:
            return
        op.execute(
            """
            ALTER TABLE users RENAME COLUMN system_account TO linux_account
        """
        )
    else:
        if not has_system_account or has_linux_account:
            return
        try:
            op.execute(
                """
                ALTER TABLE users RENAME COLUMN system_account TO linux_account
            """
            )
        except Exception:
            op.execute(
                """
                ALTER TABLE users ADD COLUMN linux_account TEXT
            """
            )
            op.execute(
                """
                UPDATE users SET linux_account = system_account
            """
            )
