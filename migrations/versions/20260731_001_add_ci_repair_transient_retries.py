"""Add ci_repair_transient_retries to autonomous_workflows.

Revision ID: 20260731_001_add_ci_repair_transient_retries
Revises: 20260730_001_validate_daily_usage_tenant
Create Date: 2026-07-31

Issue: #1820

Dedicated counter for transient-API deferrals (429/5xx) during merge-phase CI
repair. Previously the transient-retry count was stored in
``ci_diagnostics_attempts``, which is already used to bound CI log-fetch
polling (``MAX_CI_DIAGNOSTICS_ATTEMPTS``). The dual use caused the two counters
to clobber each other when diagnostics polling and transient deferrals
interleaved, resetting the transient budget and allowing ``ci_repair_attempts``
to be consumed by infra glitches. This migration introduces a separate column
so each counter has its own lifecycle.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260731_001_add_ci_repair_transient_retries"
down_revision: str | None = "20260730_001_validate_daily_usage_tenant"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add the ci_repair_transient_retries column."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    if "autonomous_workflows" not in set(inspector.get_table_names()):
        # Fresh databases create the column directly in CREATE TABLE; nothing
        # to migrate here.
        return

    existing_columns = {col["name"] for col in inspector.get_columns("autonomous_workflows")}
    if "ci_repair_transient_retries" not in existing_columns:
        op.add_column(
            "autonomous_workflows",
            sa.Column(
                "ci_repair_transient_retries",
                sa.Integer(),
                nullable=True,
                server_default="0",
            ),
        )


def downgrade() -> None:
    """Remove the ci_repair_transient_retries column."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    if "autonomous_workflows" not in set(inspector.get_table_names()):
        return

    existing_columns = {col["name"] for col in inspector.get_columns("autonomous_workflows")}
    with op.batch_alter_table("autonomous_workflows") as batch_op:
        if "ci_repair_transient_retries" in existing_columns:
            batch_op.drop_column("ci_repair_transient_retries")
