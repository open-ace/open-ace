"""Fix api_key_store.is_active column type to BOOLEAN

Revision ID: 040_fix_api_key_store_is_active_type
Revises: 039_normalize_derived_tables
Create Date: 2026-05-07

Migration 033 defined is_active as BOOLEAN DEFAULT TRUE, but the table was
actually created by _ensure_tables() with INTEGER DEFAULT 1. Code was later
changed to use is_active = TRUE (commit 874d737), causing type mismatch
errors on PostgreSQL. This migration aligns the actual column type with
what the code expects.
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "040_api_key_bool"
down_revision: Union[str, None] = "039_normalize_derived_tables"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    conn = op.get_bind()

    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'api_key_store' AND column_name = 'is_active'"
            )
        )
        row = result.fetchone()
        if row and row[0] in ("integer", "smallint", "bigint"):
            op.execute(sa.text("ALTER TABLE api_key_store ALTER COLUMN is_active DROP DEFAULT"))
            op.execute(
                sa.text(
                    "ALTER TABLE api_key_store ALTER COLUMN is_active TYPE BOOLEAN USING (is_active != 0)"
                )
            )
            op.execute(sa.text("ALTER TABLE api_key_store ALTER COLUMN is_active SET DEFAULT TRUE"))


def downgrade() -> None:
    conn = op.get_bind()

    if conn.dialect.name == "postgresql":
        op.execute(sa.text("ALTER TABLE api_key_store ALTER COLUMN is_active DROP DEFAULT"))
        op.execute(
            sa.text(
                "ALTER TABLE api_key_store ALTER COLUMN is_active "
                "TYPE INTEGER USING (CASE WHEN is_active THEN 1 ELSE 0 END)"
            )
        )
        op.execute(sa.text("ALTER TABLE api_key_store ALTER COLUMN is_active SET DEFAULT 1"))
