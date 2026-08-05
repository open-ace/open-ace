"""Add sandbox_remote_session_id to autonomous_workflows

Revision ID: 20260727_007_add_sandbox_remote_session_id
Revises: 20260726_006_add_sandbox_state
Create Date: 2026-07-27

Issue: #2022 (Phase 6 operations)

Persist the remote-agent session id (``RemoteSessionManager``'s row id) next to
the provider-minted ``sandbox_id`` so the startup/periodic reconciliation sweep
can destroy a crash-orphaned remote CLI session by id after a server restart —
when the per-call ``RemoteMachineProvider`` instance (which held the
sandbox_id→remote_session_id mapping) is gone. NULL for local/gVisor sandboxes
(their destroy does not need an external id).

Mirrors 20260726_006 (inspector-idempotent add; nullable, no backfill).
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260727_007_add_sandbox_remote_session_id"
down_revision: str | None = "20260726_006_add_sandbox_state"
branch_labels: str | None = None
depends_on: str | None = None

NEW_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("sandbox_remote_session_id", sa.Text()),  # RemoteSessionManager row id (remote only)
)


def upgrade() -> None:
    """Add the sandbox_remote_session_id column."""
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
    """Remove the sandbox_remote_session_id column."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    if "autonomous_workflows" not in set(inspector.get_table_names()):
        return

    existing_columns = {col["name"] for col in inspector.get_columns("autonomous_workflows")}
    with op.batch_alter_table("autonomous_workflows") as batch_op:
        for col_name, _ in reversed(NEW_COLUMNS):
            if col_name in existing_columns:
                batch_op.drop_column(col_name)
