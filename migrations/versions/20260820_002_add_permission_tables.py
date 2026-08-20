"""Add permission_tasks and permission_checkpoints tables

Revision ID: 002_add_permission_tables
Revises: 001_add_permission_status
Create Date: 2026-08-20

Issue: #2746
Create tables for managing asynchronous permission setup tasks,
including task status tracking and checkpoint recovery.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_002"
down_revision: str | None = "20260820_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create permission_tasks and permission_checkpoints tables."""
    # Create permission_tasks table
    op.create_table(
        "permission_tasks",
        sa.Column(
            "task_id",
            sa.String(36),
            nullable=False,
            primary_key=True,
            comment="Unique task identifier (UUID)",
        ),
        sa.Column(
            "project_id",
            sa.Integer(),
            nullable=True,
            comment="Associated project ID",
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            nullable=True,
            comment="User who initiated the task",
        ),
        sa.Column(
            "path",
            sa.String(512),
            nullable=False,
            comment="Project path for permission setup",
        ),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            comment="Task status: pending/running/completed/failed/partial_success",
        ),
        sa.Column(
            "priority",
            sa.Integer(),
            nullable=False,
            server_default="10",
            comment="Task priority (1-10, lower is higher priority)",
        ),
        sa.Column(
            "progress",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Progress percentage (0-100)",
        ),
        sa.Column(
            "files_processed",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Number of files processed",
        ),
        sa.Column(
            "total_files",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Total number of files to process",
        ),
        sa.Column(
            "depth_limit",
            sa.Integer(),
            nullable=True,
            comment="Recursion depth limit used",
        ),
        sa.Column(
            "checkpoint_data",
            sa.Text(),
            nullable=True,
            comment="Checkpoint data (JSON) for recovery",
        ),
        sa.Column(
            "checksum",
            sa.String(32),
            nullable=True,
            comment="Task deduplication checksum",
        ),
        sa.Column(
            "error_message",
            sa.Text(),
            nullable=True,
            comment="Error message if task failed",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            comment="Task creation timestamp",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=True,
            onupdate=sa.func.now(),
            comment="Task last update timestamp",
        ),
        sa.Column(
            "started_at",
            sa.DateTime(),
            nullable=True,
            comment="Task execution start timestamp",
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(),
            nullable=True,
            comment="Task completion timestamp",
        ),
    )

    # Add foreign key constraint to projects
    op.create_foreign_key(
        "fk_permission_tasks_project",
        "permission_tasks",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Add foreign key constraint to users
    op.create_foreign_key(
        "fk_permission_tasks_user",
        "permission_tasks",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Create indexes for permission_tasks
    op.create_index(
        "idx_permission_tasks_status",
        "permission_tasks",
        ["status"],
        unique=False,
    )
    op.create_index(
        "idx_permission_tasks_project",
        "permission_tasks",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        "idx_permission_tasks_checksum",
        "permission_tasks",
        ["checksum"],
        unique=False,
    )
    op.create_index(
        "idx_permission_tasks_priority_created",
        "permission_tasks",
        ["priority", "created_at"],
        unique=False,
    )

    # Create permission_checkpoints table
    op.create_table(
        "permission_checkpoints",
        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column(
            "task_id",
            sa.String(36),
            nullable=False,
            comment="Associated task ID",
        ),
        sa.Column(
            "processed_paths",
            sa.Text(),
            nullable=True,
            comment="JSON array of processed paths",
        ),
        sa.Column(
            "last_position",
            sa.String(512),
            nullable=True,
            comment="Last processed position",
        ),
        sa.Column(
            "snapshot_time",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            comment="Checkpoint snapshot timestamp",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            comment="Checkpoint creation timestamp",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=True,
            onupdate=sa.func.now(),
            comment="Checkpoint last update timestamp",
        ),
    )

    # Add foreign key constraint to permission_tasks
    op.create_foreign_key(
        "fk_permission_checkpoints_task",
        "permission_checkpoints",
        "permission_tasks",
        ["task_id"],
        ["task_id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    """Drop permission_tasks and permission_checkpoints tables."""
    # Drop permission_checkpoints table
    op.drop_constraint(
        "fk_permission_checkpoints_task",
        "permission_checkpoints",
        type_="foreignkey",
    )
    op.drop_table("permission_checkpoints")

    # Drop permission_tasks table
    op.drop_index("idx_permission_tasks_priority_created", table_name="permission_tasks")
    op.drop_index("idx_permission_tasks_checksum", table_name="permission_tasks")
    op.drop_index("idx_permission_tasks_project", table_name="permission_tasks")
    op.drop_index("idx_permission_tasks_status", table_name="permission_tasks")
    op.drop_constraint(
        "fk_permission_tasks_user",
        "permission_tasks",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_permission_tasks_project",
        "permission_tasks",
        type_="foreignkey",
    )
    op.drop_table("permission_tasks")
