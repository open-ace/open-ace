"""Add autonomous workflow tables for AI autonomous development

Revision ID: 050_autonomous_workflows
Revises: 049_user_id_daily_stats
Create Date: 2026-06-05

Tables:
- autonomous_workflows: Top-level workflow records
- workflow_milestones: Timeline milestones per workflow
- workflow_events: Append-only event log for real-time updates
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "050_autonomous_workflows"
down_revision: Union[str, None] = "049_user_id_daily_stats"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def _table_exists(conn, table_name: str) -> bool:
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = :table_name)"
            ),
            {"table_name": table_name},
        )
        return result.fetchone()[0]
    else:
        result = conn.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='table' AND name = :table_name"),
            {"table_name": table_name},
        )
        return result.fetchone() is not None


def upgrade() -> None:
    """Create autonomous workflow tables."""
    conn = op.get_bind()

    if _table_exists(conn, "autonomous_workflows"):
        return

    op.create_table(
        "autonomous_workflows",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workflow_id", sa.String(36), nullable=False, unique=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("title", sa.Text, server_default=""),
        sa.Column("status", sa.Text, server_default="pending"),
        sa.Column("requirements_text", sa.Text, server_default=""),
        sa.Column("requirements_issue_url", sa.Text, server_default=""),
        sa.Column("project_path", sa.Text, server_default=""),
        sa.Column("project_repo_url", sa.Text, server_default=""),
        sa.Column("is_new_project", sa.Boolean, server_default=sa.text("0")),
        sa.Column("cli_tool", sa.Text, server_default=""),
        sa.Column("model", sa.Text, server_default=""),
        sa.Column("permission_mode", sa.Text, server_default="auto-edit"),
        sa.Column("branch_name", sa.Text, server_default=""),
        sa.Column("branch_strategy", sa.Text, server_default="new-branch"),
        sa.Column("workspace_type", sa.Text, server_default="local"),
        sa.Column("remote_machine_id", sa.Text, server_default=""),
        sa.Column("worktree_path", sa.Text, server_default=""),
        sa.Column("github_issue_number", sa.Integer, nullable=True),
        sa.Column("github_pr_number", sa.Integer, nullable=True),
        sa.Column("github_pr_url", sa.Text, server_default=""),
        sa.Column("current_phase", sa.Text, server_default="preparation"),
        sa.Column("current_round", sa.Integer, server_default="0"),
        sa.Column("dev_round", sa.Integer, server_default="1"),
        sa.Column("max_plan_rounds", sa.Integer, server_default="3"),
        sa.Column("max_pr_review_rounds", sa.Integer, server_default="5"),
        sa.Column("total_tokens", sa.Integer, server_default="0"),
        sa.Column("total_input_tokens", sa.Integer, server_default="0"),
        sa.Column("total_output_tokens", sa.Integer, server_default="0"),
        sa.Column("total_requests", sa.Integer, server_default="0"),
        sa.Column("error_message", sa.Text, server_default=""),
        sa.Column("created_at", sa.TIMESTAMP),
        sa.Column("updated_at", sa.TIMESTAMP),
        sa.Column("completed_at", sa.TIMESTAMP, nullable=True),
        sa.Column("paused_at", sa.TIMESTAMP, nullable=True),
    )
    op.create_index(
        "idx_workflows_user_status",
        "autonomous_workflows",
        ["user_id", "status"],
    )

    op.create_table(
        "workflow_milestones",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "workflow_id",
            sa.String(36),
            sa.ForeignKey("autonomous_workflows.workflow_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("milestone_id", sa.String(36), nullable=False, unique=True),
        sa.Column("phase", sa.Text, nullable=False, server_default=""),
        sa.Column("dev_round", sa.Integer, server_default="1"),
        sa.Column("round_number", sa.Integer, server_default="0"),
        sa.Column("milestone_type", sa.Text, nullable=False, server_default=""),
        sa.Column("status", sa.Text, server_default="pending"),
        sa.Column("title", sa.Text, server_default=""),
        sa.Column("description", sa.Text, server_default=""),
        sa.Column("session_id", sa.Text, server_default=""),
        sa.Column("review_session_id", sa.Text, server_default=""),
        sa.Column("github_issue_number", sa.Integer, nullable=True),
        sa.Column("github_pr_number", sa.Integer, nullable=True),
        sa.Column("github_comment_id", sa.Text, server_default=""),
        sa.Column("commit_shas", sa.Text, server_default=""),
        sa.Column("diff_stats", sa.Text, server_default=""),
        sa.Column("result_summary", sa.Text, server_default=""),
        sa.Column("plan_content", sa.Text, server_default=""),
        sa.Column("review_content", sa.Text, server_default=""),
        sa.Column("error_message", sa.Text, server_default=""),
        sa.Column("parent_milestone_id", sa.Text, server_default=""),
        sa.Column("fork_branch", sa.Text, server_default=""),
        sa.Column("metadata", sa.Text, server_default=""),
        sa.Column("started_at", sa.TIMESTAMP, nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP, nullable=True),
        sa.Column("created_at", sa.TIMESTAMP, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP, server_default=sa.func.now()),
    )
    op.create_index(
        "idx_milestones_workflow_phase",
        "workflow_milestones",
        ["workflow_id", "phase", "status"],
    )
    op.create_index(
        "idx_milestones_workflow_round",
        "workflow_milestones",
        ["workflow_id", "dev_round"],
    )

    op.create_table(
        "workflow_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workflow_id", sa.String(36), nullable=False),
        sa.Column("milestone_id", sa.String(36), server_default=""),
        sa.Column("event_type", sa.Text, nullable=False, server_default=""),
        sa.Column("event_data", sa.Text, server_default=""),
        sa.Column("created_at", sa.TIMESTAMP, server_default=sa.func.now()),
    )
    op.create_index(
        "idx_events_workflow_created",
        "workflow_events",
        ["workflow_id", "created_at"],
    )


def downgrade() -> None:
    """Drop autonomous workflow tables.

    Note: This is intentionally non-destructive. Tables may have been created
    by runtime DDL (get_ddl_statements) before this migration ran. Dropping
    tables would lose production data. Enable manual downgrade if needed:

        op.drop_index("idx_events_workflow_created")
        op.drop_table("workflow_events")
        op.drop_index("idx_milestones_workflow_round")
        op.drop_index("idx_milestones_workflow_phase")
        op.drop_table("workflow_milestones")
        op.drop_index("idx_workflows_user_status")
        op.drop_table("autonomous_workflows")
    """
