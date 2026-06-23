"""Add definition snapshot to autonomous workflows

Revision ID: 058_workflow_definition_snapshot
Revises: 057_auto_merge
Create Date: 2026-06-11
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "058_workflow_definition_snapshot"
down_revision: Union[str, None] = "057_auto_merge"
branch_labels = None
depends_on = None

TABLE_NAME = "autonomous_workflows"
TABLE_INFO_SQL = sa.text("PRAGMA table_info(autonomous_workflows)")


def _column_exists(conn, column: str) -> bool:
    """Check if a column already exists."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_name = :table AND column_name = :column"
            ),
            {"table": TABLE_NAME, "column": column},
        )
        return result.scalar() > 0

    result = conn.execute(TABLE_INFO_SQL)
    return any(row[1] == column for row in result.fetchall())


def upgrade() -> None:
    """Add definition snapshot column."""
    conn = op.get_bind()
    if not _column_exists(conn, "definition_snapshot"):
        op.add_column(
            TABLE_NAME,
            sa.Column("definition_snapshot", sa.Text, nullable=True),
        )


def downgrade() -> None:
    """Remove definition snapshot column."""
    conn = op.get_bind()
    if _column_exists(conn, "definition_snapshot"):
        op.drop_column(TABLE_NAME, "definition_snapshot")
