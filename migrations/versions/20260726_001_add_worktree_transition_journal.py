"""Add worktree transition journal columns to autonomous_workflows

Revision ID: 20260726_001_add_worktree_transition_journal
Revises: 20260725_001_add_dingtalk_signing_key_col
Create Date: 2026-07-26

Issue: #2050 (#2041 acceptance #7)

The merge-conflict worktree transition in ``_resolve_merge_conflicts`` crosses
DB, git worktree registry, and on-disk directories that cannot form a single
atomic transaction:

    remove original -> clear DB worktree_path
        -> create temp -> resolve/test/commit/push
        -> remove temp -> restore original

Issue #2049 (PR #2049) covered *in-process* exception safety with a single
outer ``try/finally``. A SIGKILL, machine restart, or hard crash can still stop
the process at any git/DB boundary, leaving the DB with ``worktree_path=""``
while the original worktree is gone and/or a temp worktree is registered. On
restart ``_ensure_worktree`` treated the empty path as a no-op and the next
phase silently ran against the main checkout (HEAD=main).

This migration adds a minimal persistent transition journal so a single
``_reconcile_worktree_transition`` entry can combine the persisted intent with
the observed git registry/disk state to recover the original worktree or fail
closed. All new columns are nullable with NULL default: a NULL
``worktree_transition_state`` means "no transition in progress" (stable), so
legacy rows keep behaving exactly as before — no backfill, no inference.
"""

import logging

import sqlalchemy as sa
from alembic import op

log = logging.getLogger(__name__)

revision: str = "20260726_001_add_worktree_transition_journal"
down_revision: str | None = "20260725_001_add_dingtalk_signing_key_col"
branch_labels: str | None = None
depends_on: str | None = None

# Columns added for the SIGKILL-resilient worktree transition journal (#2050).
NEW_COLUMNS: tuple[tuple[str, sa.Text], ...] = (
    ("worktree_transition_state", sa.Text()),
    ("transition_original_path", sa.Text()),
    ("transition_temp_path", sa.Text()),
    ("transition_error", sa.Text()),
    ("transition_started_at", sa.Text()),
    ("transition_updated_at", sa.Text()),
)


def upgrade() -> None:
    """Add the worktree transition journal columns."""
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
        op.add_column(
            "autonomous_workflows",
            sa.Column(col_name, col_type, nullable=True),
        )

    # No backfill: legacy rows stay NULL (stable, no transition in progress).


def downgrade() -> None:
    """Remove the worktree transition journal columns."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    if "autonomous_workflows" not in set(inspector.get_table_names()):
        return

    existing_columns = {col["name"] for col in inspector.get_columns("autonomous_workflows")}
    with op.batch_alter_table("autonomous_workflows") as batch_op:
        for col_name, _ in reversed(NEW_COLUMNS):
            if col_name in existing_columns:
                batch_op.drop_column(col_name)
