"""Add expected_head_sha to autonomous_workflows

Revision ID: 20260726_003_add_expected_head_sha
Revises: 20260726_002_add_command_execution_evidence
Create Date: 2026-07-26

Issue: #2042

Worktree recovery (``_ensure_worktree`` missing-dir path) previously fell back
to ``origin/main`` when the branch was gone, silently discarding prior workflow
commits. #2042 introduces a persisted trusted-head checkpoint so recovery
restores to the workflow's last trusted commit instead of the moving main tip.

This migration adds a single nullable ``expected_head_sha`` column. A NULL value
means "no trusted head recorded yet" — legacy rows keep behaving as before
(recovery falls through to base_commit_sha or fail-closed). No backfill: the
field is written only by the orchestrator after each trusted commit/push.
"""

import logging

import sqlalchemy as sa
from alembic import op

log = logging.getLogger(__name__)

revision: str = "20260726_003_add_expected_head_sha"
down_revision: str | None = "20260726_002_add_command_execution_evidence"
branch_labels: str | None = None
depends_on: str | None = None

NEW_COLUMN = "expected_head_sha"


def upgrade() -> None:
    """Add the expected_head_sha column."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    if "autonomous_workflows" not in set(inspector.get_table_names()):
        # Fresh databases create the column directly in CREATE TABLE
        # (schema_init / _ensure_tables); nothing to migrate here.
        return

    existing_columns = {col["name"] for col in inspector.get_columns("autonomous_workflows")}
    if NEW_COLUMN in existing_columns:
        return
    op.add_column(
        "autonomous_workflows",
        sa.Column(NEW_COLUMN, sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Remove the expected_head_sha column."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    if "autonomous_workflows" not in set(inspector.get_table_names()):
        return

    existing_columns = {col["name"] for col in inspector.get_columns("autonomous_workflows")}
    if NEW_COLUMN not in existing_columns:
        return
    with op.batch_alter_table("autonomous_workflows") as batch_op:
        batch_op.drop_column(NEW_COLUMN)
