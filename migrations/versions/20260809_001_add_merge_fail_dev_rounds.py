"""Add merge_fail_dev_rounds to autonomous_workflows.

Revision ID: 20260809_001_add_merge_fail_dev_rounds
Revises: 20260805_010_acceptance_verification_columns
Create Date: 2026-08-09

Issue: #2443 (PR-C)

Counter for Tier1 CI-repair-exhaustion escalations back to a fresh development
round. When a merge-phase PR exhausts automatic CI repair (MAX attempts,
no-code-change, or an unchanged meaningful failure signature) and the PR branch
is still recoverable, the orchestrator rebases the branch onto main and
re-enters development instead of terminal-failing. This column bounds that
escalation (``MAX_MERGE_FAIL_DEV_ROUNDS``); at the cap the workflow falls
through to a Tier2 terminal report + ``failed``. ``retry_workflow`` zeroes it so
a retried workflow gets a fresh allowance.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260809_001_add_merge_fail_dev_rounds"
down_revision: str | None = "20260808_001_add_schema_metadata"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add the merge_fail_dev_rounds column."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    if "autonomous_workflows" not in set(inspector.get_table_names()):
        # Fresh databases create the column directly in CREATE TABLE; nothing
        # to migrate here.
        return

    existing_columns = {col["name"] for col in inspector.get_columns("autonomous_workflows")}
    if "merge_fail_dev_rounds" not in existing_columns:
        op.add_column(
            "autonomous_workflows",
            sa.Column(
                "merge_fail_dev_rounds",
                sa.Integer(),
                nullable=True,
                server_default="0",
            ),
        )


def downgrade() -> None:
    """Remove the merge_fail_dev_rounds column."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    if "autonomous_workflows" not in set(inspector.get_table_names()):
        return

    existing_columns = {col["name"] for col in inspector.get_columns("autonomous_workflows")}
    with op.batch_alter_table("autonomous_workflows") as batch_op:
        if "merge_fail_dev_rounds" in existing_columns:
            batch_op.drop_column("merge_fail_dev_rounds")
