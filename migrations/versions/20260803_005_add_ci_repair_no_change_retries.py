"""Add ci_repair_no_change_retries to autonomous_workflows.

Revision ID: 20260803_005_add_ci_repair_no_change_retries
Revises: 20260803_004_backfill_session_messages_columns
Create Date: 2026-08-03

Issue: #2187

Dedicated counter for consecutive 'genuine no code changes' deferrals during
merge-phase CI repair. Previously a single round where the agent ran cleanly
but committed nothing terminal-failed the workflow (bypassing the
MAX_CI_REPAIR_ATTEMPTS=5 budget). This migration adds a column so the new
MAX_CI_REPAIR_NO_CHANGE_RETRIES bound has its own lifecycle, mirroring
ci_repair_transient_retries.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260803_005_add_ci_repair_no_change_retries"
down_revision: str | None = "20260803_004_backfill_session_messages_columns"  # confirm current head
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add the ci_repair_no_change_retries column."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    if "autonomous_workflows" not in set(inspector.get_table_names()):
        return  # fresh DBs create the column in CREATE TABLE

    existing_columns = {col["name"] for col in inspector.get_columns("autonomous_workflows")}
    if "ci_repair_no_change_retries" not in existing_columns:
        op.add_column(
            "autonomous_workflows",
            sa.Column(
                "ci_repair_no_change_retries",
                sa.Integer(),
                nullable=True,
                server_default="0",
            ),
        )


def downgrade() -> None:
    """Remove the ci_repair_no_change_retries column."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    if "autonomous_workflows" not in set(inspector.get_table_names()):
        return

    existing_columns = {col["name"] for col in inspector.get_columns("autonomous_workflows")}
    with op.batch_alter_table("autonomous_workflows") as batch_op:
        if "ci_repair_no_change_retries" in existing_columns:
            batch_op.drop_column("ci_repair_no_change_retries")
