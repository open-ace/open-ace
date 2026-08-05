"""Add max_changed_files_override to autonomous_workflows.

Revision ID: 20260805_001_add_max_changed_files_override
Revises: 20260803_007
Create Date: 2026-08-05

Issue: #2309

Per-workflow override of the global MAX_AUTONOMOUS_CHANGED_FILES cap. NULL
(the default) → the scope guard falls back to the global bound, so this is
opt-in per workflow. Set via ``POST /workflows/<id>/retry`` so a failed round
whose only blocker was the changed-files cap can be retried with a higher
limit without re-creating the workflow or weakening the global safety rail.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260805_001_add_max_changed_files_override"
down_revision: str | None = "20260803_007"  # current head
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add the max_changed_files_override column."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    if "autonomous_workflows" not in set(inspector.get_table_names()):
        return  # fresh DBs create the column in CREATE TABLE

    existing_columns = {col["name"] for col in inspector.get_columns("autonomous_workflows")}
    if "max_changed_files_override" not in existing_columns:
        op.add_column(
            "autonomous_workflows",
            sa.Column(
                "max_changed_files_override",
                sa.Integer(),
                nullable=True,  # NULL = use the global bound
            ),
        )


def downgrade() -> None:
    """Remove the max_changed_files_override column."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    if "autonomous_workflows" not in set(inspector.get_table_names()):
        return

    existing_columns = {col["name"] for col in inspector.get_columns("autonomous_workflows")}
    with op.batch_alter_table("autonomous_workflows") as batch_op:
        if "max_changed_files_override" in existing_columns:
            batch_op.drop_column("max_changed_files_override")
