"""Add permission_status and permission_task_id to projects table

Revision ID: 001_add_permission_status
Revises: None
Create Date: 2026-08-20

Issue: #2746
Add permission tracking fields to projects table for managing shared
project permission setup status and task association.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_001"
down_revision: str | None = "20260819_003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [col["name"] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    """Add permission_status and permission_task_id columns to projects table."""
    # Add permission_status column if not exists
    if not column_exists("projects", "permission_status"):
        op.add_column(
            "projects",
            sa.Column(
                "permission_status",
                sa.String(20),
                nullable=True,
                comment="Permission setup status for shared projects",
            ),
        )

    # Add permission_task_id column if not exists
    if not column_exists("projects", "permission_task_id"):
        op.add_column(
            "projects",
            sa.Column(
                "permission_task_id",
                sa.String(36),
                nullable=True,
                comment="Associated permission task ID",
            ),
        )

    # Add index for permission_status queries if not exists
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    indexes = [idx["name"] for idx in inspector.get_indexes("projects")]
    if "idx_projects_permission_status" not in indexes:
        op.create_index(
            "idx_projects_permission_status",
            "projects",
            ["permission_status"],
            unique=False,
        )


def downgrade() -> None:
    """Remove permission_status and permission_task_id columns from projects table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    indexes = [idx["name"] for idx in inspector.get_indexes("projects")]
    
    if "idx_projects_permission_status" in indexes:
        op.drop_index("idx_projects_permission_status", table_name="projects")
    
    if column_exists("projects", "permission_task_id"):
        op.drop_column("projects", "permission_task_id")
    
    if column_exists("projects", "permission_status"):
        op.drop_column("projects", "permission_status")
