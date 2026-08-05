"""add command execution evidence

Revision ID: 20260726_002_add_command_execution_evidence
Revises: 20260726_001_add_worktree_transition_journal
Create Date: 2026-07-26

Adds ``command_execution_evidence`` — the authoritative per-command execution
record for autonomous workflows (#2046 Phase A). Distinct from the #2045
verify-before-act ``Evidence`` (Git-signal verification), this captures command
execution facts (argv, cwd, exit code, terminal reason, output digest) so the
test gate can stop inferring pass/fail from agent prose.

``command_id`` reuses the provider ``tool_use_id``; the ``(session_id,
command_id)`` UNIQUE constraint makes repeated provider events idempotent
(re-upsert on terminal state rather than inserting duplicates).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "20260726_002_add_command_execution_evidence"
down_revision: str | None = "20260726_001_add_worktree_transition_journal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the command_execution_evidence table and indexes."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    existing_tables = set(inspector.get_table_names())

    if "command_execution_evidence" not in existing_tables:
        op.create_table(
            "command_execution_evidence",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("command_id", sa.Text, nullable=False),
            sa.Column("workflow_id", sa.Text, nullable=False, server_default=""),
            sa.Column("session_id", sa.Text, nullable=False, server_default=""),
            sa.Column("milestone_id", sa.Text, nullable=False, server_default=""),
            sa.Column("sandbox_id", sa.Text),
            sa.Column("sandbox_generation", sa.Integer),
            sa.Column("tool_name", sa.Text, nullable=False, server_default=""),
            sa.Column("argv", sa.Text),
            sa.Column("shell_command", sa.Text),
            sa.Column("cwd", sa.Text, nullable=False, server_default=""),
            sa.Column("execution_profile", sa.Text, nullable=False, server_default=""),
            sa.Column("started_at", sa.TIMESTAMP),
            sa.Column("completed_at", sa.TIMESTAMP),
            sa.Column("exit_code", sa.Integer),
            sa.Column("signal", sa.Integer),
            sa.Column("timed_out", sa.Boolean, server_default=sa.false()),
            sa.Column("cancelled", sa.Boolean, server_default=sa.false()),
            sa.Column("terminal_reason", sa.Text, nullable=False, server_default=""),
            sa.Column("stdout_digest", sa.Text),
            sa.Column("stderr_digest", sa.Text),
            sa.Column("stdout_artifact", sa.Text),
            sa.Column("stderr_artifact", sa.Text),
            sa.Column("output_excerpt", sa.Text, nullable=False, server_default=""),
            sa.Column("tenant_id", sa.Integer, nullable=False, server_default="1"),
            sa.Column("created_at", sa.TIMESTAMP, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint(
                "session_id", "command_id", name="uq_command_evidence_session_command"
            ),
        )
        op.create_index(
            "idx_command_evidence_session_command",
            "command_execution_evidence",
            ["session_id", "command_id"],
        )
        op.create_index(
            "idx_command_evidence_workflow_milestone",
            "command_execution_evidence",
            ["workflow_id", "milestone_id"],
        )


def downgrade() -> None:
    """Drop the command_execution_evidence table."""
    op.drop_table("command_execution_evidence")
