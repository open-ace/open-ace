"""Rename linux_account to system_account or add if missing

This migration:
1. Renames linux_account column to system_account in users table (legacy databases)
2. Adds system_account column if neither column exists (fresh installations)
3. Updates the column name to better reflect cross-platform support

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
    """Rename linux_account to system_account or add if missing.

    Handles two scenarios:
    1. Legacy databases with linux_account column → rename to system_account
    2. Fresh installations without either column → add system_account column
    """

    bind = op.get_bind()
    has_linux_account = _column_exists(bind, "users", "linux_account")
    has_system_account = _column_exists(bind, "users", "system_account")

    if bind.dialect.name == "postgresql":
        # If system_account already exists, skip (idempotent)
        if has_system_account:
            return

        # If linux_account exists, rename it
        if has_linux_account:
            op.execute(
                """
                ALTER TABLE users RENAME COLUMN linux_account TO system_account
            """
            )
        else:
            # Fresh installation: add system_account directly
            op.execute(
                """
                ALTER TABLE users ADD COLUMN system_account TEXT
            """
            )
    else:
        # SQLite: same logic
        if has_system_account:
            return

        if has_linux_account:
            try:
                op.execute(
                    """
                    ALTER TABLE users RENAME COLUMN linux_account TO system_account
                """
                )
            except Exception:
                # Fallback for older SQLite
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
        else:
            # Fresh installation
            op.execute(
                """
                ALTER TABLE users ADD COLUMN system_account TEXT
            """
            )


def downgrade():
    """Rename system_account back to linux_account.

    For consistency with historical schema, renames system_account to linux_account.
    """

    bind = op.get_bind()
    has_linux_account = _column_exists(bind, "users", "linux_account")
    has_system_account = _column_exists(bind, "users", "system_account")

    if bind.dialect.name == "postgresql":
        # If system_account doesn't exist, skip
        if not has_system_account:
            return

        # If linux_account already exists, skip
        if has_linux_account:
            return

        op.execute(
            """
            ALTER TABLE users RENAME COLUMN system_account TO linux_account
        """
        )
    else:
        # SQLite: same logic
        if not has_system_account:
            return

        if has_linux_account:
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
