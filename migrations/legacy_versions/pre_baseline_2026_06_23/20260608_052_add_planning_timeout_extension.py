"""Add planning_timeout_extension column to autonomous_workflows

Revision ID: 052_planning_timeout_extension
Revises: 050_autonomous_workflows
Create Date: 2026-06-08

Stores accumulated user-requested timeout extensions for the planning
phase (Issue #761).  The orchestrator computes the actual planning
timeout as PLANNING_TIMEOUT + planning_timeout_extension, so extending
actually increases the cap.

"""

import sqlalchemy as sa
from alembic import op

revision: str = "052_planning_timeout_extension"
down_revision: str = "050_autonomous_workflows"
branch_labels = None
depends_on = None


def _column_exists(conn, table: str, column: str) -> bool:
    """Check if a column already exists (idempotent migration)."""
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
    conn = op.get_bind()
    if not _column_exists(conn, "autonomous_workflows", "planning_timeout_extension"):
        op.add_column(
            "autonomous_workflows",
            sa.Column(
                "planning_timeout_extension",
                sa.Integer,
                server_default="0",
                nullable=True,
            ),
        )


def downgrade() -> None:
    conn = op.get_bind()
    if _column_exists(conn, "autonomous_workflows", "planning_timeout_extension"):
        op.drop_column("autonomous_workflows", "planning_timeout_extension")
