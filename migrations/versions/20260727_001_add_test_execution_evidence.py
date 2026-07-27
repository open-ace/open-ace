"""add test execution evidence

Revision ID: 20260727_001_add_test_execution_evidence
Revises: 20260727_007_add_sandbox_remote_session_id
Create Date: 2026-07-27

Adds ``test_execution_evidence`` — the structured per-command test verdict
for autonomous workflows (#2046 Phase B). Pairs 1:1 with a
``command_execution_evidence`` row via ``command_execution_id`` (the row PK)
and shares its ``(session_id, command_id)`` UNIQUE identity.

A pluggable parser (pytest/jest/go/cargo/generic) reads the command evidence
``output_excerpt`` + ``exit_code`` and writes one ``test_execution_evidence``
row: framework, collected/passed/failed counts, selectors, parser confidence,
and an authoritative per-command ``verdict``. The run-level verdict is
computed in code (``test_verdict.compute_run_verdict``); this table is the
persisted audit trail and the input to shadow comparison.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "20260727_001_add_test_execution_evidence"
down_revision: str | None = "20260727_007_add_sandbox_remote_session_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the test_execution_evidence table and indexes."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    existing_tables = set(inspector.get_table_names())

    if "test_execution_evidence" not in existing_tables:
        op.create_table(
            "test_execution_evidence",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("command_id", sa.Text, nullable=False),
            sa.Column("command_execution_id", sa.Integer, nullable=True),
            sa.Column("framework", sa.Text, nullable=False, server_default=""),
            sa.Column("collected", sa.Integer),
            sa.Column("passed", sa.Integer),
            sa.Column("failed", sa.Integer),
            sa.Column("skipped", sa.Integer),
            sa.Column("errors", sa.Integer),
            sa.Column("selectors", sa.Text),
            sa.Column("coverage_scope", sa.Text),
            sa.Column("parser", sa.Text, nullable=False, server_default=""),
            sa.Column("parser_confidence", sa.Text, nullable=False, server_default=""),
            sa.Column("verdict", sa.Text, nullable=False, server_default=""),
            sa.Column("session_id", sa.Text, nullable=False, server_default=""),
            sa.Column("workflow_id", sa.Text, nullable=False, server_default=""),
            sa.Column("milestone_id", sa.Text, nullable=False, server_default=""),
            sa.Column("tenant_id", sa.Integer, nullable=False, server_default="1"),
            sa.Column("created_at", sa.TIMESTAMP, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint(
                "session_id", "command_id", name="uq_test_evidence_session_command"
            ),
            sa.ForeignKeyConstraint(
                ["command_execution_id"],
                ["command_execution_evidence.id"],
                name="fk_test_evidence_command_execution",
            ),
        )
        op.create_index(
            "idx_test_evidence_session_command",
            "test_execution_evidence",
            ["session_id", "command_id"],
        )
        op.create_index(
            "idx_test_evidence_workflow_milestone",
            "test_execution_evidence",
            ["workflow_id", "milestone_id"],
        )


def downgrade() -> None:
    """Drop the test_execution_evidence table."""
    op.drop_table("test_execution_evidence")
