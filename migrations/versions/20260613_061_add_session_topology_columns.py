"""Add session topology and per-phase usage columns

Adds three session-line tracking columns to autonomous_workflows
(main/review/test session ids used to drive --resume), per-phase usage
columns to workflow_milestones (so each milestone card shows only its own
increment instead of a shared session's cumulative total), and a
milestone_id column to session_messages (so milestone detail views can
filter messages by milestone even when multiple milestones share a session).

Revision ID: 20260613_061_session_topology
Revises: 20260613_060_agent_pid_tracking
Create Date: 2026-06-13
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260613_061_session_topology"
down_revision: Union[str, None] = "20260613_060_agent_pid_tracking"
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


def _add_text(conn, table: str, column: str) -> None:
    if not _column_exists(conn, table, column):
        op.add_column(
            table,
            sa.Column(column, sa.Text, server_default="", nullable=False),
        )


def _add_int(conn, table: str, column: str) -> None:
    if not _column_exists(conn, table, column):
        op.add_column(
            table,
            sa.Column(column, sa.Integer, server_default="0", nullable=False),
        )


def upgrade() -> None:
    """Add session topology and per-phase usage columns."""
    conn = op.get_bind()

    # Session-line tracking on autonomous_workflows
    _add_text(conn, "autonomous_workflows", "main_session_id")
    _add_text(conn, "autonomous_workflows", "review_session_id")
    _add_text(conn, "autonomous_workflows", "test_session_id")

    # Per-phase usage on workflow_milestones
    _add_int(conn, "workflow_milestones", "phase_total_tokens")
    _add_int(conn, "workflow_milestones", "phase_input_tokens")
    _add_int(conn, "workflow_milestones", "phase_output_tokens")
    _add_int(conn, "workflow_milestones", "phase_request_count")

    # Milestone attribution on session_messages
    _add_text(conn, "session_messages", "milestone_id")


def downgrade() -> None:
    """Remove session topology and per-phase usage columns."""
    conn = op.get_bind()

    if _column_exists(conn, "session_messages", "milestone_id"):
        op.drop_column("session_messages", "milestone_id")

    for column in (
        "phase_request_count",
        "phase_output_tokens",
        "phase_input_tokens",
        "phase_total_tokens",
    ):
        if _column_exists(conn, "workflow_milestones", column):
            op.drop_column("workflow_milestones", column)

    for column in ("test_session_id", "review_session_id", "main_session_id"):
        if _column_exists(conn, "autonomous_workflows", column):
            op.drop_column("autonomous_workflows", column)
