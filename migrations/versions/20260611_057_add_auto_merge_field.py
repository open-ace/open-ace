"""Add auto_merge field for batch workflow automation

Revision ID: 057_auto_merge
Revises: 056_hostname_indexes
Create Date: 2026-06-11

This migration adds the auto_merge field to autonomous_workflows table,
enabling batch workflows to automatically merge PRs and proceed to the next
workflow without manual intervention.

"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "057_auto_merge"
down_revision: Union[str, None] = "056_hostname_indexes"
branch_labels = None
depends_on = None


def _column_exists(conn, table: str, column: str) -> bool:
    """Check if a column already exists."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_name = :table AND column_name = :column"
            ),
            {"table": table, "column": column},
        )
        return result.scalar() > 0

    result = conn.execute(sa.text(f"PRAGMA table_info({table})"))
    return any(row[1] == column for row in result.fetchall())


def upgrade() -> None:
    """Add auto_merge column with default TRUE."""
    conn = op.get_bind()
    if not _column_exists(conn, "autonomous_workflows", "auto_merge"):
        op.add_column(
            "autonomous_workflows",
            sa.Column(
                "auto_merge",
                sa.Boolean,
                server_default=sa.text("TRUE"),
                nullable=True,
            ),
        )


def downgrade() -> None:
    """Remove auto_merge column."""
    conn = op.get_bind()
    if _column_exists(conn, "autonomous_workflows", "auto_merge"):
        op.drop_column("autonomous_workflows", "auto_merge")
