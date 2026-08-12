"""Add CI repair and preferred worktree fields to autonomous_workflows

Revision ID: 20260714_001_add_ci_repair_fields_to_workflows
Revises: 20260709_003_add_tenant_usage_aggregation
Create Date: 2026-07-14

Issue: #1647
The merge phase needs durable state for:
- preferred_worktree_path: recreate worktree-based workflows after merge cleanup
- ci_repair_context: carry failed CI details back into a development round
- ci_repair_attempts: cap automatic CI repair loops
- last_ci_failure_signature: stop repeating the same failed-check loop forever
"""

import logging

import sqlalchemy as sa
from alembic import op

log = logging.getLogger(__name__)

revision: str = "20260714_001_add_ci_repair_fields_to_workflows"
down_revision: str | None = "20260709_003_add_tenant_usage_aggregation"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add CI repair state columns to autonomous_workflows."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    existing_columns = {col["name"] for col in inspector.get_columns("autonomous_workflows")}

    if "preferred_worktree_path" not in existing_columns:
        op.add_column(
            "autonomous_workflows",
            sa.Column("preferred_worktree_path", sa.Text(), nullable=True, server_default=""),
        )
    if "ci_repair_context" not in existing_columns:
        op.add_column(
            "autonomous_workflows",
            sa.Column("ci_repair_context", sa.Text(), nullable=True, server_default=""),
        )
    if "ci_repair_attempts" not in existing_columns:
        op.add_column(
            "autonomous_workflows",
            sa.Column("ci_repair_attempts", sa.Integer(), nullable=True, server_default="0"),
        )
    if "last_ci_failure_signature" not in existing_columns:
        op.add_column(
            "autonomous_workflows",
            sa.Column("last_ci_failure_signature", sa.Text(), nullable=True, server_default=""),
        )

    op.execute(
        """
        UPDATE autonomous_workflows
        SET preferred_worktree_path = CASE
            WHEN worktree_path IS NOT NULL AND worktree_path != '' THEN worktree_path
            WHEN branch_strategy = 'worktree'
                 AND project_path IS NOT NULL AND project_path != ''
                 AND workflow_id IS NOT NULL AND workflow_id != ''
            THEN project_path || '/.worktrees/' || workflow_id
            ELSE COALESCE(preferred_worktree_path, '')
        END
        WHERE preferred_worktree_path IS NULL OR preferred_worktree_path = ''
        """
    )


def downgrade() -> None:
    """Remove CI repair state columns from autonomous_workflows."""
    with op.batch_alter_table("autonomous_workflows") as batch_op:
        batch_op.drop_column("last_ci_failure_signature")
        batch_op.drop_column("ci_repair_attempts")
        batch_op.drop_column("ci_repair_context")
        batch_op.drop_column("preferred_worktree_path")
