"""Add tldr column to workflow_milestones

Each phase agent now appends a one-line ``TL;DR: ...`` summary to its output
(see orchestrator ``TLDR_INSTRUCTION``). This column stores it so the timeline
milestone card can show a concise per-round summary, falling back to
``result_summary`` when the agent didn't produce one. See #993.

Revision ID: 20260614_062_milestone_tldr
Revises: 20260613_061_session_topology
Create Date: 2026-06-14
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260614_062_milestone_tldr"
down_revision: Union[str, None] = "20260613_061_session_topology"
branch_labels = None
depends_on = None


def _column_exists(conn, table: str, column: str) -> bool:
    """Check if a column already exists on the given table."""
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
    """Add tldr column to workflow_milestones."""
    conn = op.get_bind()
    if not _column_exists(conn, "workflow_milestones", "tldr"):
        op.add_column(
            "workflow_milestones",
            sa.Column("tldr", sa.Text, server_default="", nullable=False),
        )


def downgrade() -> None:
    """Remove tldr column from workflow_milestones."""
    conn = op.get_bind()
    if _column_exists(conn, "workflow_milestones", "tldr"):
        op.drop_column("workflow_milestones", "tldr")
