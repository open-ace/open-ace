"""Add base_commit_sha column to autonomous_workflows

Revision ID: 20260709_001_add_base_commit_sha
Revises: 20260707_001_add_system_account_to_workflows
Create Date: 2026-07-09

Issue: #1552
When creating batch workflows, different workflows may be created at different
times while origin/main is moving, causing race condition where workflows end
up pointing to different base commits. This leads to incorrect "no changes"
detection when a branch created from an older commit ends up behind main.

Column:
- base_commit_sha: TEXT(40) - locked SHA of origin/main at batch creation time
  NULL for single workflows (use dynamic origin/main)
  Non-NULL for batch workflows (use locked SHA to ensure consistency)
"""

import logging
from typing import Union

import sqlalchemy as sa
from alembic import op

log = logging.getLogger(__name__)

revision: str = "20260709_001_add_base_commit_sha"
down_revision: Union[str, None] = "20260707_001_add_system_account_to_workflows"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    """Add base_commit_sha column to autonomous_workflows."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    existing_columns = {col["name"] for col in inspector.get_columns("autonomous_workflows")}

    # Add base_commit_sha column for locking commit during batch creation
    if "base_commit_sha" not in existing_columns:
        log.info("Adding base_commit_sha column to autonomous_workflows table")
        op.add_column(
            "autonomous_workflows",
            sa.Column(
                "base_commit_sha",
                sa.String(40),  # Git SHA is 40 characters
                nullable=True,  # NULL for single workflows
                server_default=None,
            ),
        )
    else:
        log.info("base_commit_sha column already exists, skipping")


def downgrade() -> None:
    """Remove base_commit_sha column."""
    connection = op.get_bind()

    # Use batch_alter_table for SQLite compatibility
    with op.batch_alter_table("autonomous_workflows") as batch_op:
        batch_op.drop_column("base_commit_sha")
