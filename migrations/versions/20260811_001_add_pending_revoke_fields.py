"""Add pending_revoke fields for token rotation

Revision ID: 20260811_001
Revises: baseline_2026_06_23
Create Date: 2026-08-11

Issue #2499: Fix Rotate Token functionality

This migration adds support for delayed token revocation:
- pending_revoke: Mark old token as pending revocation
- revoke_after: Timeout for forced revocation
- rotation_id: UUID for idempotent confirmation handling

Also adds agent version tracking and configurable timeout.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

# revision identifiers, used by Alembic.
revision = "20260811_001"
down_revision = "20260812_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add fields for delayed token revocation and version tracking."""

    # Get database connection to check dialect
    bind = op.get_bind()
    dialect = bind.dialect.name

    # Add fields to agent_tokens table
    if dialect == "postgresql":
        # PostgreSQL: Use batch_alter_table for safety
        # Check if columns exist before adding
        inspector = sa.inspect(bind)
        agent_tokens_columns = [col["name"] for col in inspector.get_columns("agent_tokens")]

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

    # Create indexes - these are PostgreSQL-only (partial indexes)
    if dialect == "postgresql":
        # Create unique index for active tokens (partial index)
        op.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_tokens_one_active_per_machine
            ON agent_tokens (machine_id)
            WHERE is_revoked = False AND pending_revoke = False
        """
        )

        # Create index for timeout cleanup (partial index)
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_agent_tokens_pending_revoke_timeout
            ON agent_tokens (revoke_after)
            WHERE pending_revoke = True AND is_revoked = False
        """
        )

        # Create index for authentication queries
        op.create_index(
            "idx_agent_tokens_machine_pending",
            "agent_tokens",
            ["machine_id", "pending_revoke", "revoke_after"],
            unique=False,
        )

    else:
        # SQLite: Add columns with default values
        # Check if columns exist before adding (schema-sqlite.sql may already have them)
        inspector = sa.inspect(bind)
        agent_tokens_columns = [col["name"] for col in inspector.get_columns("agent_tokens")]

        with op.batch_alter_table("agent_tokens", schema=None) as batch_op:
            if "pending_revoke" not in agent_tokens_columns:
                batch_op.add_column(
                    sa.Column("pending_revoke", sa.Integer(), nullable=False, server_default="0")
                )
            if "revoke_after" not in agent_tokens_columns:
                batch_op.add_column(sa.Column("revoke_after", sa.Text(), nullable=True))
            if "rotation_id" not in agent_tokens_columns:
                batch_op.add_column(sa.Column("rotation_id", sa.Text(), nullable=True))

        # SQLite doesn't support partial indexes, create regular indexes
        # Check if indexes exist before creating
        existing_indexes = [idx["name"] for idx in inspector.get_indexes("agent_tokens")]
        if "idx_agent_tokens_one_active_per_machine" not in existing_indexes:
            op.create_index(
                "idx_agent_tokens_one_active_per_machine",
                "agent_tokens",
                ["machine_id"],
                unique=True,
            )
        if "idx_agent_tokens_pending_revoke_timeout" not in existing_indexes:
            op.create_index(
                "idx_agent_tokens_pending_revoke_timeout",
                "agent_tokens",
                ["revoke_after"],
                unique=False,
            )
        if "idx_agent_tokens_machine_pending" not in existing_indexes:
            op.create_index(
                "idx_agent_tokens_machine_pending",
                "agent_tokens",
                ["machine_id", "pending_revoke", "revoke_after"],
                unique=False,
            )

    # Add fields to remote_machines table
    # Check if columns exist before adding (agent_version may already exist from other migrations)
    inspector = sa.inspect(bind)
    remote_machines_columns = [col["name"] for col in inspector.get_columns("remote_machines")]

    if dialect == "postgresql":
        with op.batch_alter_table("remote_machines", schema=None) as batch_op:
            if "agent_version" not in remote_machines_columns:
                batch_op.add_column(sa.Column("agent_version", sa.String(32), nullable=True))
            if "token_revoke_timeout" not in remote_machines_columns:
                batch_op.add_column(
                    sa.Column(
                        "token_revoke_timeout", sa.Integer(), nullable=True, server_default="300"
                    )
                )
    else:
        with op.batch_alter_table("remote_machines", schema=None) as batch_op:
            if "agent_version" not in remote_machines_columns:
                batch_op.add_column(sa.Column("agent_version", sa.Text(), nullable=True))
            if "token_revoke_timeout" not in remote_machines_columns:
                batch_op.add_column(
                    sa.Column(
                        "token_revoke_timeout", sa.Integer(), nullable=True, server_default="300"
                    )
                )


def downgrade() -> None:
    """Remove fields added for delayed token revocation."""

    # Get database connection to check dialect
    bind = op.get_bind()
    dialect = bind.dialect.name

    # Drop indexes first
    if dialect == "postgresql":
        op.execute("DROP INDEX IF EXISTS idx_agent_tokens_one_active_per_machine")
        op.execute("DROP INDEX IF EXISTS idx_agent_tokens_pending_revoke_timeout")
        op.drop_index("idx_agent_tokens_machine_pending", table_name="agent_tokens")
    else:
        # SQLite: drop all three indexes
        op.drop_index("idx_agent_tokens_one_active_per_machine", table_name="agent_tokens")
        op.drop_index("idx_agent_tokens_pending_revoke_timeout", table_name="agent_tokens")
        op.drop_index("idx_agent_tokens_machine_pending", table_name="agent_tokens")

    # Drop columns from agent_tokens
    with op.batch_alter_table("agent_tokens", schema=None) as batch_op:
        batch_op.drop_column("rotation_id")
        batch_op.drop_column("revoke_after")
        batch_op.drop_column("pending_revoke")

    # Drop columns from remote_machines
    with op.batch_alter_table("remote_machines", schema=None) as batch_op:
        batch_op.drop_column("token_revoke_timeout")
        batch_op.drop_column("agent_version")
