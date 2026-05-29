"""Fix api_key_store scope default and existing data

Revision ID: 048_fix_api_key_scope
Revises: 047_api_key_scope
Create Date: 2026-05-29

Migration 047 set scope server_default to 'remote' which was incorrect —
keys created before the scope field were used by local sessions, so 'shared'
is the correct default. This migration fixes:
1. Existing rows with scope='remote' or NULL → 'shared'
2. Column server_default → 'shared' (PostgreSQL)
"""

from typing import Union

from alembic import op

revision: str = "048_fix_api_key_scope"
down_revision: Union[str, None] = "047_api_key_scope"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    """Fix scope default and existing data."""
    # Fix existing keys that got the wrong default from migration 047
    op.execute(
        "UPDATE api_key_store SET scope = 'shared' " "WHERE scope IS NULL OR scope = 'remote'"
    )
    # Fix column default (PostgreSQL uses ALTER COLUMN for server_default)
    # SQLite doesn't support ALTER COLUMN, but schema.sql has the correct default
    # for new installations, so this is a no-op for SQLite.
    try:
        op.alter_column("api_key_store", "scope", server_default="shared")
    except Exception:
        pass  # SQLite — handled by schema.sql


def downgrade() -> None:
    """Revert scope default to 'remote'."""
    try:
        op.alter_column("api_key_store", "scope", server_default="remote")
    except Exception:
        pass
