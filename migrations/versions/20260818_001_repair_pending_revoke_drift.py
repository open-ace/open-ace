"""Repair agent_tokens/remote_machines drift skipped on production

Revision ID: 20260818_001
Revises: 20260814_004
Create Date: 2026-08-18

Production reached alembic_version 20260814_004 while the agent_tokens
columns and indexes from 20260811_001 (pending_revoke, revoke_after,
rotation_id + three indexes) and remote_machines.token_revoke_timeout
were never applied — the hotpatched pending_revoke_token_cleanup job
has failed every minute with ``column "rotation_id" does not exist``.
Later migrations (20260812_001 token_version) did apply, so replaying
the version chain cannot heal the gap.

This migration re-applies exactly what 20260811_001 defines — the
committed schema snapshots already describe these objects, so the
target schema does not change. Every add/create is guarded by an
existence check, making the migration safe on databases where
20260811_001 did apply (fresh CI runs: no-op) and on drifted ones
(production: repairs).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

# revision identifiers, used by Alembic.
revision = "20260818_001"
down_revision = "20260814_004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Idempotently re-apply the 20260811_001 agent_tokens/remote_machines objects."""

    bind = op.get_bind()
    dialect = bind.dialect.name
    inspector = sa.inspect(bind)

    agent_tokens_columns = [col["name"] for col in inspector.get_columns("agent_tokens")]

    if dialect == "postgresql":
        with op.batch_alter_table("agent_tokens", schema=None) as batch_op:
            if "pending_revoke" not in agent_tokens_columns:
                batch_op.add_column(
                    sa.Column(
                        "pending_revoke", sa.Boolean(), nullable=False, server_default="false"
                    )
                )
            if "revoke_after" not in agent_tokens_columns:
                batch_op.add_column(sa.Column("revoke_after", TIMESTAMP(), nullable=True))
            if "rotation_id" not in agent_tokens_columns:
                batch_op.add_column(sa.Column("rotation_id", sa.String(36), nullable=True))

        op.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_tokens_one_active_per_machine
            ON agent_tokens (machine_id)
            WHERE is_revoked = False AND pending_revoke = False
            """)
        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_agent_tokens_pending_revoke_timeout
            ON agent_tokens (revoke_after)
            WHERE pending_revoke = True AND is_revoked = False
            """)
        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_agent_tokens_machine_pending
            ON agent_tokens (machine_id, pending_revoke, revoke_after)
            """)
    else:
        with op.batch_alter_table("agent_tokens", schema=None) as batch_op:
            if "pending_revoke" not in agent_tokens_columns:
                batch_op.add_column(
                    sa.Column("pending_revoke", sa.Integer(), nullable=False, server_default="0")
                )
            if "revoke_after" not in agent_tokens_columns:
                batch_op.add_column(sa.Column("revoke_after", sa.DateTime(), nullable=True))
            if "rotation_id" not in agent_tokens_columns:
                batch_op.add_column(sa.Column("rotation_id", sa.Text(), nullable=True))

        existing_indexes = [idx["name"] for idx in inspector.get_indexes("agent_tokens")]
        if "idx_agent_tokens_one_active_per_machine" not in existing_indexes:
            op.create_index(
                "idx_agent_tokens_one_active_per_machine",
                "agent_tokens",
                ["machine_id"],
                unique=True,
                sqlite_where=sa.text("is_revoked = false AND pending_revoke = false"),
            )
        if "idx_agent_tokens_pending_revoke_timeout" not in existing_indexes:
            op.create_index(
                "idx_agent_tokens_pending_revoke_timeout",
                "agent_tokens",
                ["revoke_after"],
                unique=False,
                sqlite_where=sa.text("pending_revoke = true AND is_revoked = false"),
            )
        if "idx_agent_tokens_machine_pending" not in existing_indexes:
            op.create_index(
                "idx_agent_tokens_machine_pending",
                "agent_tokens",
                ["machine_id", "pending_revoke", "revoke_after"],
                unique=False,
            )

    remote_machines_columns = [col["name"] for col in inspector.get_columns("remote_machines")]
    if "token_revoke_timeout" not in remote_machines_columns:
        with op.batch_alter_table("remote_machines", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column("token_revoke_timeout", sa.Integer(), nullable=True, server_default="300")
            )


def downgrade() -> None:
    """Drop the objects this repair re-applied (mirrors 20260811_001 downgrade)."""

    bind = op.get_bind()
    dialect = bind.dialect.name
    inspector = sa.inspect(bind)

    if dialect == "postgresql":
        op.execute("DROP INDEX IF EXISTS idx_agent_tokens_one_active_per_machine")
        op.execute("DROP INDEX IF EXISTS idx_agent_tokens_pending_revoke_timeout")
        op.execute("DROP INDEX IF EXISTS idx_agent_tokens_machine_pending")
    else:
        existing_indexes = [idx["name"] for idx in inspector.get_indexes("agent_tokens")]
        for name in (
            "idx_agent_tokens_one_active_per_machine",
            "idx_agent_tokens_pending_revoke_timeout",
            "idx_agent_tokens_machine_pending",
        ):
            if name in existing_indexes:
                op.drop_index(name, table_name="agent_tokens")

    agent_tokens_columns = [col["name"] for col in inspector.get_columns("agent_tokens")]
    drop_columns = [
        col
        for col in ("rotation_id", "revoke_after", "pending_revoke")
        if col in agent_tokens_columns
    ]
    if drop_columns:
        with op.batch_alter_table("agent_tokens", schema=None) as batch_op:
            for col in drop_columns:
                batch_op.drop_column(col)

    remote_machines_columns = [col["name"] for col in inspector.get_columns("remote_machines")]
    if "token_revoke_timeout" in remote_machines_columns:
        with op.batch_alter_table("remote_machines", schema=None) as batch_op:
            batch_op.drop_column("token_revoke_timeout")
