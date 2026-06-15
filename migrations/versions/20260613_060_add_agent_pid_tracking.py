"""Add agent PID tracking to autonomous workflows

Revision ID: 060_agent_pid_tracking
Revises: 058_workflow_definition_snapshot
Create Date: 2026-06-13
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260613_060_agent_pid_tracking"
down_revision: Union[str, None] = "059_add_smtp_tables"
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
    """Add agent_pid and agent_session_id columns."""
    conn = op.get_bind()
    if not _column_exists(conn, "agent_pid"):
        op.add_column(
            TABLE_NAME,
            sa.Column("agent_pid", sa.Integer, nullable=True),
        )
    if not _column_exists(conn, "agent_session_id"):
        op.add_column(
            TABLE_NAME,
            sa.Column("agent_session_id", sa.Text, server_default="", nullable=False),
        )


def downgrade() -> None:
    """Remove agent_pid and agent_session_id columns."""
    conn = op.get_bind()
    if _column_exists(conn, "agent_session_id"):
        op.drop_column(TABLE_NAME, "agent_session_id")
    if _column_exists(conn, "agent_pid"):
        op.drop_column(TABLE_NAME, "agent_pid")
