"""Ensure prompt_templates boolean columns are correct type

Revision ID: 036_prompt_boolean_fix
Revises: 035_remove_redundant_indexes
Create Date: 2026-05-02

Migration 029 was supposed to convert is_public and is_featured from
INTEGER to BOOLEAN on PostgreSQL, but in some environments the alembic
stamp was recorded while the actual ALTER TABLE did not take effect.
This migration detects and fixes the mismatch idempotently.
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "036_prompt_boolean_fix"
down_revision: Union[str, None] = "035_remove_redundant_indexes"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def _column_type_is(bind, table, column, expected_type):
    """Check if a column type matches expected_type."""
    if bind.dialect.name != "postgresql":
        return True
    result = bind.execute(
        sa.text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = :table AND column_name = :col"
        ),
        {"table": table, "col": column},
    ).scalar()
    return result == expected_type


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for col in ("is_public", "is_featured"):
        if not _column_type_is(bind, "prompt_templates", col, "boolean"):
            op.execute(f"ALTER TABLE prompt_templates ALTER COLUMN {col} DROP DEFAULT")
            op.execute(
                f"ALTER TABLE prompt_templates "
                f"ALTER COLUMN {col} TYPE BOOLEAN "
                f"USING CASE WHEN {col} = 1 THEN TRUE ELSE FALSE END"
            )
            op.execute(f"ALTER TABLE prompt_templates ALTER COLUMN {col} SET DEFAULT FALSE")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for col in ("is_public", "is_featured"):
        if not _column_type_is(bind, "prompt_templates", col, "integer"):
            op.execute(f"ALTER TABLE prompt_templates ALTER COLUMN {col} DROP DEFAULT")
            op.execute(
                f"ALTER TABLE prompt_templates "
                f"ALTER COLUMN {col} TYPE INTEGER "
                f"USING CASE WHEN {col} THEN 1 ELSE 0 END"
            )
            op.execute(f"ALTER TABLE prompt_templates ALTER COLUMN {col} SET DEFAULT 0")
