"""Add deregister_failures table for tracking failed batch terminations.

Issue #2596: Create table to track failed session termination batches
during machine deregistration, enabling background compensation.

Revision ID: 20260820_004_add_deregister_failures_table
Revises: 20260820_003_add_daily_usage_synced
Create Date: 2026-08-20

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence


# revision identifiers, used by Alembic.
revision: str = "20260820_004"
down_revision: str | None = "20260820_003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create deregister_failures table.

    This table records failed batch terminations during machine deregistration,
    enabling a background compensation worker to retry failed batches.

    Schema:
    - id: Primary key
    - machine_id: UUID of the deregistered machine
    - batch_index: Index of the failed batch (0-based)
    - session_ids: JSON array of session IDs in this batch
    - error_message: Error details
    - retry_count: Number of retry attempts
    - status: pending/retrying/failed/resolved
    - created_at: Record creation timestamp
    - updated_at: Last update timestamp
    """
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    existing_tables = set(inspector.get_table_names())

    if "deregister_failures" not in existing_tables:
        op.create_table(
            "deregister_failures",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("machine_id", sa.Text(), nullable=False),
            sa.Column("batch_index", sa.Integer(), nullable=False),
            sa.Column("session_ids", sa.Text(), nullable=False),  # JSON array
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.PrimaryKeyConstraint("id"),
        )

    # Create indexes for efficient querying
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_deregister_failures_status "
        "ON deregister_failures (status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_deregister_failures_machine "
        "ON deregister_failures (machine_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_deregister_failures_created "
        "ON deregister_failures (created_at)"
    )


def downgrade() -> None:
    """Remove deregister_failures table."""
    op.execute("DROP INDEX IF EXISTS idx_deregister_failures_created")
    op.execute("DROP INDEX IF EXISTS idx_deregister_failures_machine")
    op.execute("DROP INDEX IF EXISTS idx_deregister_failures_status")
    op.drop_table("deregister_failures")