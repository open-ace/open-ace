"""Add sandbox_effective_policy to autonomous_workflows

Revision ID: 20260728_001_add_sandbox_effective_policy
Revises: 20260727_001_add_test_execution_evidence
Create Date: 2026-07-28

Issue: #2020 (Phase B — effective-policy observability)

Persist a JSON snapshot of the resource/isolation policy actually in effect for
a workflow's sandbox at creation time: provider name, declared capabilities,
effective limits (memory/pids/cpu/wall_clock/storage/inode), cgroup state, and
which layer enforces each dimension. Auditing "what was effective when this task
ran" must not depend on the live agent-launcher.conf (which can change between
runs), so the snapshot is taken once in the orchestrator's ``on_sandbox_created``
callback. NULL until a sandbox is created.

Mirrors 20260727_007 (inspector-idempotent add; nullable, no backfill).
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260728_001_add_sandbox_effective_policy"
down_revision: str | None = "20260727_001_add_test_execution_evidence"
branch_labels: str | None = None
depends_on: str | None = None

NEW_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("sandbox_effective_policy", sa.Text()),  # JSON snapshot of effective policy
)


def upgrade() -> None:
    """Add the sandbox_effective_policy column."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    if "autonomous_workflows" not in set(inspector.get_table_names()):
        # Fresh databases create the column directly in CREATE TABLE; nothing
        # to migrate here.
        return

    existing_columns = {col["name"] for col in inspector.get_columns("autonomous_workflows")}
    for col_name, col_type in NEW_COLUMNS:
        if col_name in existing_columns:
            continue
        op.add_column(
            "autonomous_workflows",
            sa.Column(col_name, col_type, nullable=True),
        )


def downgrade() -> None:
    """Remove the sandbox_effective_policy column."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    if "autonomous_workflows" not in set(inspector.get_table_names()):
        return

    existing_columns = {col["name"] for col in inspector.get_columns("autonomous_workflows")}
    with op.batch_alter_table("autonomous_workflows") as batch_op:
        for col_name, _ in reversed(NEW_COLUMNS):
            if col_name in existing_columns:
                batch_op.drop_column(col_name)
