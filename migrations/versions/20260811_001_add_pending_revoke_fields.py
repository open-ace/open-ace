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
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TIMESTAMP
from datetime import datetime, timezone

# revision identifiers, used by Alembic.
revision = "20260811_001"
down_revision = "baseline_2026_06_23"
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
        with op.batch_alter_table("agent_tokens", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column("pending_revoke", sa.Boolean(), nullable=False, server_default="false")
            )
            batch_op.add_column(
                sa.Column("revoke_after", TIMESTAMP(), nullable=True)
            )
            batch_op.add_column(
                sa.Column("rotation_id", sa.String(36), nullable=True)
            )

        # Create unique index for active tokens (partial index)
        op.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_tokens_one_active_per_machine
            ON agent_tokens (machine_id)
            WHERE is_revoked = False AND pending_revoke = False
        """)

        # Create index for timeout cleanup (partial index)
        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_agent_tokens_pending_revoke_timeout
            ON agent_tokens (revoke_after)
            WHERE pending_revoke = True AND is_revoked = False
        """)

        # Create index for authentication queries
        op.create_index(
            "idx_agent_tokens_machine_pending",
            "agent_tokens",
            ["machine_id", "pending_revoke", "revoke_after"],
            unique=False
        )

    else:
        # SQLite: Add columns with default values
        with op.batch_alter_table("agent_tokens", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column("pending_revoke", sa.Integer(), nullable=False, server_default="0")
            )
            batch_op.add_column(
                sa.Column("revoke_after", sa.Text(), nullable=True)
            )
            batch_op.add_column(
                sa.Column("rotation_id", sa.Text(), nullable=True)
            )

        # SQLite doesn't support partial indexes, create regular indexes
        op.create_index(
            "idx_agent_tokens_machine_pending",
            "agent_tokens",
            ["machine_id", "pending_revoke", "revoke_after"],
            unique=False
        )

    # Add fields to remote_machines table
    if dialect == "postgresql":
        with op.batch_alter_table("remote_machines", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column("agent_version", sa.String(32), nullable=True)
            )
            batch_op.add_column(
                sa.Column("token_revoke_timeout", sa.Integer(), nullable=True, server_default="300")
            )
    else:
        with op.batch_alter_table("remote_machines", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column("agent_version", sa.Text(), nullable=True)
            )
            batch_op.add_column(
                sa.Column("token_revoke_timeout", sa.Integer(), nullable=True, server_default="300")
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

    # Drop columns from agent_tokens
    with op.batch_alter_table("agent_tokens", schema=None) as batch_op:
        batch_op.drop_column("rotation_id")
        batch_op.drop_column("revoke_after")
        batch_op.drop_column("pending_revoke")

    # Drop columns from remote_machines
    with op.batch_alter_table("remote_machines", schema=None) as batch_op:
        batch_op.drop_column("token_revoke_timeout")
        batch_op.drop_column("agent_version")