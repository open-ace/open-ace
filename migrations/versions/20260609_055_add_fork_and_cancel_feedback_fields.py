"""Add fork and cancel feedback fields to autonomous workflows

Revision ID: 055_fork_and_cancel_feedback
Revises: 054_ai_agent_settings
Create Date: 2026-06-09

Adds fields needed for the redesigned "Cancel Round" and "Fork From Here"
features (Issue #886):

- parent_workflow_id: links forked workflow to its parent
- fork_milestone_id: records which milestone was the fork point
- user_feedback: stores user instructions from cancel/fork actions
- original_branch_name: records parent's branch at fork time
- fork_workflow_id (on milestones): links fork marker to child workflow

"""

import sqlalchemy as sa
from alembic import op

revision: str = "055_fork_and_cancel_feedback"
down_revision: str = "054_add_ai_agent_settings_table"
branch_labels = None
depends_on = None


def _column_exists(conn, table: str, column: str) -> bool:
    """Check if a column already exists (idempotent migration)."""
    result = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_name = :table AND column_name = :column"
        ),
        {"table": table, "column": column},
    )
    return result.scalar() > 0


def upgrade() -> None:
    conn = op.get_bind()

    # --- autonomous_workflows: 4 new columns ---
    new_workflow_cols = {
        "parent_workflow_id": sa.Text,
        "fork_milestone_id": sa.Text,
        "user_feedback": sa.Text,
        "original_branch_name": sa.Text,
    }
    defaults = {
        "parent_workflow_id": None,
        "fork_milestone_id": None,
        "user_feedback": "",
        "original_branch_name": "",
    }
    for col_name, col_type in new_workflow_cols.items():
        if not _column_exists(conn, "autonomous_workflows", col_name):
            op.add_column(
                "autonomous_workflows",
                sa.Column(
                    col_name,
                    col_type,
                    server_default=defaults[col_name],
                    nullable=True,
                ),
            )

    # Index for finding child workflows
    op.create_index(
        "idx_workflows_parent",
        "autonomous_workflows",
        ["parent_workflow_id"],
        if_not_exists=True,
    )

    # --- workflow_milestones: 1 new column ---
    if not _column_exists(conn, "workflow_milestones", "fork_workflow_id"):
        op.add_column(
            "workflow_milestones",
            sa.Column(
                "fork_workflow_id",
                sa.Text,
                server_default="",
                nullable=True,
            ),
        )


def downgrade() -> None:
    conn = op.get_bind()

    # Drop milestone column
    if _column_exists(conn, "workflow_milestones", "fork_workflow_id"):
        op.drop_column("workflow_milestones", "fork_workflow_id")

    # Drop index
    op.drop_index("idx_workflows_parent", "autonomous_workflows", if_exists=True)

    # Drop workflow columns
    for col_name in [
        "original_branch_name",
        "user_feedback",
        "fork_milestone_id",
        "parent_workflow_id",
    ]:
        if _column_exists(conn, "autonomous_workflows", col_name):
            op.drop_column("autonomous_workflows", col_name)
