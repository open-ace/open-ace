"""Fix agent identity boolean columns to proper BOOLEAN type

Revision ID: 060_fix_agent_identity_boolean
Revises: 059_add_smtp_tables
Create Date: 2026-06-12

Fixes Issue #881: PostgreSQL INTEGER vs BOOLEAN type mismatch

Tables affected:
- registration_tokens: is_consumed
- agent_tokens: is_revoked
- remote_machines: legacy_mode

SQLite uses type affinity (INTEGER for boolean), so no changes needed.

"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "060_fix_agent_identity_boolean"
down_revision: Union[str, None] = "059_add_smtp_tables"
branch_labels = None
depends_on = None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    """Check if a column exists in the table."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :table_name AND column_name = :column_name"
            ),
            {"table_name": table_name, "column_name": column_name},
        )
    else:
        result = conn.execute(
            sa.text("SELECT 1 FROM pragma_table_info(:table_name) WHERE name = :column_name"),
            {"table_name": table_name, "column_name": column_name},
        )
    return result.fetchone() is not None


def _is_integer_column(conn, table_name: str, column_name: str) -> bool:
    """Check if a column is INTEGER type (for idempotency)."""
    if conn.dialect.name != "postgresql":
        return False
    result = conn.execute(
        sa.text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = :table_name AND column_name = :column_name"
        ),
        {"table_name": table_name, "column_name": column_name},
    )
    row = result.fetchone()
    return row is not None and row[0] == "integer"


def _is_postgresql() -> bool:
    """Check if using PostgreSQL."""
    conn = op.get_bind()
    return conn.dialect.name == "postgresql"


def upgrade() -> None:
    """Convert integer boolean fields to proper BOOLEAN type."""
    if not _is_postgresql():
        return  # SQLite uses type affinity, no changes needed

    conn = op.get_bind()

    # ============================================
    # registration_tokens: is_consumed -> BOOLEAN
    # ============================================
    if _column_exists(conn, "registration_tokens", "is_consumed") and _is_integer_column(
        conn, "registration_tokens", "is_consumed"
    ):
        op.execute("ALTER TABLE registration_tokens ALTER COLUMN is_consumed DROP DEFAULT")
        op.execute(
            """
            ALTER TABLE registration_tokens
            ALTER COLUMN is_consumed TYPE BOOLEAN
            USING CASE WHEN is_consumed = 1 THEN TRUE ELSE FALSE END
            """
        )
        op.execute("ALTER TABLE registration_tokens ALTER COLUMN is_consumed SET DEFAULT FALSE")

    # ============================================
    # agent_tokens: is_revoked -> BOOLEAN
    # ============================================
    if _column_exists(conn, "agent_tokens", "is_revoked") and _is_integer_column(
        conn, "agent_tokens", "is_revoked"
    ):
        op.execute("ALTER TABLE agent_tokens ALTER COLUMN is_revoked DROP DEFAULT")
        op.execute(
            """
            ALTER TABLE agent_tokens
            ALTER COLUMN is_revoked TYPE BOOLEAN
            USING CASE WHEN is_revoked = 1 THEN TRUE ELSE FALSE END
            """
        )
        op.execute("ALTER TABLE agent_tokens ALTER COLUMN is_revoked SET DEFAULT FALSE")

    # ============================================
    # remote_machines: legacy_mode -> BOOLEAN
    # ============================================
    if _column_exists(conn, "remote_machines", "legacy_mode") and _is_integer_column(
        conn, "remote_machines", "legacy_mode"
    ):
        op.execute("ALTER TABLE remote_machines ALTER COLUMN legacy_mode DROP DEFAULT")
        op.execute(
            """
            ALTER TABLE remote_machines
            ALTER COLUMN legacy_mode TYPE BOOLEAN
            USING CASE WHEN legacy_mode = 1 THEN TRUE ELSE FALSE END
            """
        )
        op.execute("ALTER TABLE remote_machines ALTER COLUMN legacy_mode SET DEFAULT FALSE")


def downgrade() -> None:
    """Revert boolean fields to integer type."""
    if not _is_postgresql():
        return

    # Revert remote_machines: legacy_mode
    if _column_exists(op.get_bind(), "remote_machines", "legacy_mode"):
        op.execute("ALTER TABLE remote_machines ALTER COLUMN legacy_mode DROP DEFAULT")
        op.execute(
            """
            ALTER TABLE remote_machines
            ALTER COLUMN legacy_mode TYPE INTEGER
            USING CASE WHEN legacy_mode THEN 1 ELSE 0 END
            """
        )
        op.execute("ALTER TABLE remote_machines ALTER COLUMN legacy_mode SET DEFAULT 0")

    # Revert agent_tokens: is_revoked
    if _column_exists(op.get_bind(), "agent_tokens", "is_revoked"):
        op.execute("ALTER TABLE agent_tokens ALTER COLUMN is_revoked DROP DEFAULT")
        op.execute(
            """
            ALTER TABLE agent_tokens
            ALTER COLUMN is_revoked TYPE INTEGER
            USING CASE WHEN is_revoked THEN 1 ELSE 0 END
            """
        )
        op.execute("ALTER TABLE agent_tokens ALTER COLUMN is_revoked SET DEFAULT 0")

    # Revert registration_tokens: is_consumed
    if _column_exists(op.get_bind(), "registration_tokens", "is_consumed"):
        op.execute("ALTER TABLE registration_tokens ALTER COLUMN is_consumed DROP DEFAULT")
        op.execute(
            """
            ALTER TABLE registration_tokens
            ALTER COLUMN is_consumed TYPE INTEGER
            USING CASE WHEN is_consumed THEN 1 ELSE 0 END
            """
        )
        op.execute("ALTER TABLE registration_tokens ALTER COLUMN is_consumed SET DEFAULT 0")
