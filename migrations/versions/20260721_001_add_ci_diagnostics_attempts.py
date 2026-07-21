"""Bound retries while CI failure logs are unavailable.

Revision ID: 20260721_001_add_ci_diagnostics_attempts
Revises: 20260720_001_backfill_notenant_users
Create Date: 2026-07-21
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_001_add_ci_diagnostics_attempts"
down_revision: str | None = "20260720_001_backfill_notenant_users"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    connection = op.get_bind()
    columns = {
        column["name"] for column in sa.inspect(connection).get_columns("autonomous_workflows")
    }
    if "ci_diagnostics_attempts" not in columns:
        op.add_column(
            "autonomous_workflows",
            sa.Column("ci_diagnostics_attempts", sa.Integer(), nullable=True, server_default="0"),
        )


def downgrade() -> None:
    with op.batch_alter_table("autonomous_workflows") as batch_op:
        batch_op.drop_column("ci_diagnostics_attempts")
