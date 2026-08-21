"""Add merge_policy_settle_retries to autonomous_workflows.

Bounded settle budget for the merge-phase residual race: a "clean rollup but
GitHub still blocked" transient that outlives the policy-settle grace window.
``merge.py`` counts each such settled-but-blocked cycle against this budget
before persisting a manual-recovery pause, so a genuine block
(missing review / draft / rule) pauses after at most a few scheduler cycles,
while a self-healing transient no longer freezes the workflow.

Revision ID: 20260821_001_add_merge_policy_settle_retries
Revises: 20260820_004
Create Date: 2026-08-21
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence


# revision identifiers, used by Alembic.
revision: str = "20260821_001"
down_revision: str | None = "20260820_004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the merge_policy_settle_retries column."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    if "autonomous_workflows" not in set(inspector.get_table_names()):
        # Fresh databases create the column directly in CREATE TABLE; nothing
        # to migrate here.
        return

    existing_columns = {col["name"] for col in inspector.get_columns("autonomous_workflows")}
    if "merge_policy_settle_retries" not in existing_columns:
        op.add_column(
            "autonomous_workflows",
            sa.Column(
                "merge_policy_settle_retries",
                sa.Integer(),
                nullable=True,
                server_default="0",
            ),
        )


def downgrade() -> None:
    """Remove the merge_policy_settle_retries column."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    if "autonomous_workflows" not in set(inspector.get_table_names()):
        return

    existing_columns = {col["name"] for col in inspector.get_columns("autonomous_workflows")}
    with op.batch_alter_table("autonomous_workflows") as batch_op:
        if "merge_policy_settle_retries" in existing_columns:
            batch_op.drop_column("merge_policy_settle_retries")
