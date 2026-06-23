"""Add autonomous workflow batch fields for multi-issue sequencing

Revision ID: 056_autonomous_batch_fields
Revises: 055_add_auto_provision_users, 055_fork_and_cancel_feedback
Create Date: 2026-06-10
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "056_autonomous_batch_fields"
down_revision: Union[str, Sequence[str], None] = (
    "055_add_auto_provision_users",
    "055_fork_and_cancel_feedback",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
    """Upgrade database schema."""
    conn = op.get_bind()

    for col_name, col_type in (
        ("batch_id", sa.Text),
        ("batch_order", sa.Integer),
        ("batch_total", sa.Integer),
    ):
        if not _column_exists(conn, "autonomous_workflows", col_name):
            op.add_column("autonomous_workflows", sa.Column(col_name, col_type, nullable=True))

    op.create_index(
        "idx_workflows_batch_order",
        "autonomous_workflows",
        ["batch_id", "batch_order"],
        if_not_exists=True,
    )


def downgrade() -> None:
    """Downgrade database schema."""
    conn = op.get_bind()

    op.drop_index("idx_workflows_batch_order", "autonomous_workflows", if_exists=True)

    for col_name in ("batch_total", "batch_order", "batch_id"):
        if _column_exists(conn, "autonomous_workflows", col_name):
            op.drop_column("autonomous_workflows", col_name)
