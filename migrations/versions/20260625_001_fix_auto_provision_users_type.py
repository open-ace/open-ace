"""Fix auto_provision_users type to boolean for PostgreSQL

Revision ID: 001_fix_auto_provision
Revises: baseline_2026_06_23
Create Date: 2026-06-25

Issue: #1261
The auto_provision_users column was incorrectly defined as integer
in PostgreSQL, causing type mismatch errors when inserting boolean values.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "001_fix_auto_provision"
down_revision: Union[str, None] = "baseline_2026_06_23"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Fix auto_provision_users column type for PostgreSQL."""
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return

    # PostgreSQL: convert integer to boolean
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

    op.execute("ALTER TABLE tenant_settings ALTER COLUMN auto_provision_users DROP DEFAULT")
    op.execute(
        """
        ALTER TABLE tenant_settings
        ALTER COLUMN auto_provision_users TYPE INTEGER
        USING CASE WHEN auto_provision_users THEN 1 ELSE 0 END
        """
    )
    op.execute("ALTER TABLE tenant_settings ALTER COLUMN auto_provision_users SET DEFAULT 0")