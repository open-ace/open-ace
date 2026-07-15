"""Add last_ci_failure_head_sha to autonomous_workflows

Revision ID: 20260715_001_add_last_ci_failure_head_sha
Revises: 20260714_001_add_ci_repair_fields_to_workflows
Create Date: 2026-07-15

Issue: #1574
CI repair now runs in merge phase on an existing PR. We need to remember the
PR head SHA associated with the last failed-check signature so we can
distinguish "same failure on the same commit" from "same failure after a new
repair commit was pushed".
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260715_001_add_last_ci_failure_head_sha"
down_revision: Union[str, None] = "20260714_001_add_ci_repair_fields_to_workflows"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    """Add last_ci_failure_head_sha to autonomous_workflows."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    existing_columns = {col["name"] for col in inspector.get_columns("autonomous_workflows")}

    if "last_ci_failure_head_sha" not in existing_columns:
        op.add_column(
            "autonomous_workflows",
            sa.Column("last_ci_failure_head_sha", sa.Text(), nullable=True, server_default=""),
        )


def downgrade() -> None:
    """Remove last_ci_failure_head_sha from autonomous_workflows."""
    with op.batch_alter_table("autonomous_workflows") as batch_op:
        batch_op.drop_column("last_ci_failure_head_sha")
