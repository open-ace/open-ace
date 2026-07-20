"""Fix auto_provision_users type to boolean for PostgreSQL

Revision ID: 001_fix_auto_provision
Revises: baseline_2026_06_23
Create Date: 2026-06-25

Issue: #1261
The auto_provision_users column was incorrectly defined as integer
in PostgreSQL, causing type mismatch errors when inserting boolean values.

Note: This migration checks the current column type before attempting
conversion. For new databases created from baseline_2026_06_23-postgres.sql,
the column is already boolean, so no conversion is needed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "001_fix_auto_provision"
down_revision: str | None = "baseline_2026_06_23"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def get_column_type(conn, table_name: str, column_name: str) -> str:
    """Get the current data type of a column in PostgreSQL."""
    result = conn.execute(
        sa.text(
            """
            SELECT data_type
            FROM information_schema.columns
            WHERE table_name = :table_name AND column_name = :column_name
            """
        ),
        {"table_name": table_name, "column_name": column_name},
    )
    row = result.fetchone()
    return row[0] if row else None


def upgrade() -> None:
    """Fix auto_provision_users column type for PostgreSQL."""
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return

    # Check current column type
    current_type = get_column_type(conn, "tenant_settings", "auto_provision_users")

    # If already boolean, no conversion needed (new databases from baseline)
    if current_type == "boolean":
        return

    # PostgreSQL: convert integer to boolean (existing databases)
    op.execute("ALTER TABLE tenant_settings ALTER COLUMN auto_provision_users DROP DEFAULT")
    op.execute(
        """
        ALTER TABLE tenant_settings
        ALTER COLUMN auto_provision_users TYPE BOOLEAN
        USING CASE WHEN auto_provision_users = 1 THEN TRUE ELSE FALSE END
        """
    )
    op.execute("ALTER TABLE tenant_settings ALTER COLUMN auto_provision_users SET DEFAULT FALSE")


def downgrade() -> None:
    """Revert auto_provision_users column type to integer for PostgreSQL."""
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return

    # Check current column type
    current_type = get_column_type(conn, "tenant_settings", "auto_provision_users")

    # If already integer, no conversion needed
    if current_type == "integer":
        return

    op.execute("ALTER TABLE tenant_settings ALTER COLUMN auto_provision_users DROP DEFAULT")
    op.execute(
        """
        ALTER TABLE tenant_settings
        ALTER COLUMN auto_provision_users TYPE INTEGER
        USING CASE WHEN auto_provision_users THEN 1 ELSE 0 END
        """
    )
    op.execute("ALTER TABLE tenant_settings ALTER COLUMN auto_provision_users SET DEFAULT 0")
