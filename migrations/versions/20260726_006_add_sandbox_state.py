"""Add sandbox state columns to autonomous_workflows

Revision ID: 20260726_006_add_sandbox_state
Revises: 20260726_005_add_cleanup_tracking
Create Date: 2026-07-26

Issue: #2022 (Phase 2)

Persist per-workflow sandbox state so a restart can reconcile orphan sandboxes
by generation. The SandboxProvider contract (#2022 P1) mints a ``sandbox_id``
and ``sandbox_generation`` on create; the workflow row carries them so the
scheduler's startup sweep can detect a sandbox whose state claims "active" but
whose workflow is no longer running (crash / restart mid-task) and reset it,
bumping the generation so a stale handle cannot operate on a future sandbox.

All columns are nullable with no backfill: a NULL ``sandbox_state`` means
"legacy row, never ran under a provider" — those rows are invisible to the
reconciliation scan and keep behaving as before. P2 only persists + reconciles
state; real ``provider.destroy()`` resource cleanup lands in P3 (LegacyPosixProvider).
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260726_006_add_sandbox_state"
down_revision: str | None = "20260726_005_add_cleanup_tracking"
branch_labels: str | None = None
depends_on: str | None = None

# Columns added for SandboxProvider state tracking (#2022 P2).
NEW_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("sandbox_provider", sa.Text()),  # legacy_posix | remote_machine | future
    ("sandbox_id", sa.Text()),  # provider-minted sandbox id
    ("sandbox_generation", sa.Integer()),  # bumped on reconcile/restart (stale-handle guard)
    ("sandbox_state", sa.Text()),  # created|running|paused|stopped|destroyed|error
    ("sandbox_policy_digest", sa.Text()),  # digest of the SandboxSpec policy (UI + drift)
    ("sandbox_last_error", sa.Text()),  # last sandbox error / reconcile reason
)


def upgrade() -> None:
    """Add the sandbox state columns."""
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
        # All sandbox_* columns are nullable: NULL = legacy row (never ran under
        # a provider), consistent with sandbox_state. Avoiding NOT NULL here
        # means an in-place upgrade of a populated table succeeds without a
        # server_default; a NOT NULL add would fail on existing rows.
        op.add_column(
            "autonomous_workflows",
            sa.Column(col_name, col_type, nullable=True),
        )


def downgrade() -> None:
    """Remove the sandbox state columns."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    if "autonomous_workflows" not in set(inspector.get_table_names()):
        return

    existing_columns = {col["name"] for col in inspector.get_columns("autonomous_workflows")}
    with op.batch_alter_table("autonomous_workflows") as batch_op:
        for col_name, _ in reversed(NEW_COLUMNS):
            if col_name in existing_columns:
                batch_op.drop_column(col_name)
