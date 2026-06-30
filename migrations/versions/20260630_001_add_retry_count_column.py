"""Add retry_count column to autonomous_workflows table

Revision ID: 20260630_001
Revises: 20260629_001
Create Date: 2026-06-30

Issue: retry API returns Internal server error because PostgreSQL lacks
retry_count column which exists in SQLite CREATE TABLE but not in migration.

Column:
- retry_count: INTEGER - counter for manual user-triggered retries
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260630_001_add_retry_count_column"
down_revision: Union[str, None] = "20260629_001_add_workflow_lock_columns"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text(
                "SELECT EXISTS ("
                "  SELECT FROM information_schema.columns "
                "  WHERE table_name = :table_name AND column_name = :column_name"
                ")"
            ),
            {"table_name": table_name, "column_name": column_name},
        )
        return result.fetchone()[0]
    else:
        result = conn.execute(
            sa.text(
                "SELECT name FROM pragma_table_info(:table_name) WHERE name = :column_name"
            ),
            {"table_name": table_name, "column_name": column_name},
        )
        return result.fetchone() is not None


def upgrade() -> None:
    """Add retry_count column to autonomous_workflows."""
    conn = op.get_bind()

    if not _column_exists(conn, "autonomous_workflows", "retry_count"):
        op.add_column(
            "autonomous_workflows",
            sa.Column("retry_count", sa.Integer(), nullable=True, server_default="0"),
        )


def downgrade() -> None:
    """Remove retry_count column."""
    conn = op.get_bind()

    if _column_exists(conn, "autonomous_workflows", "retry_count"):
        if conn.dialect.name == "postgresql":
            op.drop_column("autonomous_workflows", "retry_count")
        else:
            # SQLite requires batch_alter_table
            with op.batch_alter_table("autonomous_workflows") as batch_op:
                batch_op.drop_column("retry_count")
