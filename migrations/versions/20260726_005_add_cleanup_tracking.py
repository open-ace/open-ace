"""Add cleanup tracking columns to autonomous_workflows

Revision ID: 20260726_005_add_cleanup_tracking
Revises: 20260726_004_add_webhook_deliveries
Create Date: 2026-07-26

Issue: #2043

Post-merge Git cleanup (worktree + branch) previously ran as a fire-and-forget
``try/except`` inside ``_do_merge``: a failure only logged a warning, then the
workflow was unconditionally marked ``completed`` and never re-examined. Residual
worktrees/branches blocked later retries (#1442) with no record that cleanup
had failed.

This migration adds a cleanup-tracking facet so delivery completion and resource
convergence become independent, observable states. All columns are nullable with
no backfill: a NULL ``cleanup_status`` means "legacy row, no cleanup tracking" —
those rows are invisible to the cleanup retry scan and keep behaving as before.
"""

import logging

import sqlalchemy as sa
from alembic import op

log = logging.getLogger(__name__)

revision: str = "20260726_005_add_cleanup_tracking"
down_revision: str | None = "20260726_004_add_webhook_deliveries"
branch_labels: str | None = None
depends_on: str | None = None

# Columns added for post-merge Git resource cleanup tracking (#2043).
NEW_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("cleanup_status", sa.Text()),  # not_started | pending | completed | failed
    ("cleanup_attempts", sa.Integer()),  # retry counter
    ("cleanup_error", sa.Text()),  # last error message
    ("cleanup_updated_at", sa.Text()),  # ISO timestamp of last attempt
    ("cleanup_next_retry_at", sa.Text()),  # ISO timestamp; backoff-gated
)


def upgrade() -> None:
    """Add the cleanup tracking columns."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    if "autonomous_workflows" not in set(inspector.get_table_names()):
        # Fresh databases create the columns directly in CREATE TABLE
        # (schema_init / _ensure_tables); nothing to migrate here.
        return

    existing_columns = {col["name"] for col in inspector.get_columns("autonomous_workflows")}
    for col_name, col_type in NEW_COLUMNS:
        if col_name in existing_columns:
            continue
        # All cleanup_* columns are nullable: NULL = legacy row (no cleanup
        # tracking), consistent with cleanup_status. The sweep treats NULL
        # attempts as 0 via int(... or 0). Avoiding NOT NULL here means an
        # in-place upgrade of a populated table succeeds (no server default
        # needed); a NOT NULL add would fail on existing rows.
        op.add_column(
            "autonomous_workflows",
            sa.Column(col_name, col_type, nullable=True),
        )


def downgrade() -> None:
    """Remove the cleanup tracking columns."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    if "autonomous_workflows" not in set(inspector.get_table_names()):
        return

    existing_columns = {col["name"] for col in inspector.get_columns("autonomous_workflows")}
    with op.batch_alter_table("autonomous_workflows") as batch_op:
        for col_name, _ in reversed(NEW_COLUMNS):
            if col_name in existing_columns:
                batch_op.drop_column(col_name)
