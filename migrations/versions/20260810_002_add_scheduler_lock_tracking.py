"""Add lock tracking columns to scheduler tables.

Issue #2333: Add columns for scheduler concurrency safety tracking.

New columns:
- scheduler_runs: lock_strategy, fencing_token, lock_acquired_at, lock_released_at,
  skip_reason, leader_host
- scheduler_leaders: fencing_token, lock_strategy

New sequence:
- fencing_token_seq: Monotonic counter for fencing tokens (PostgreSQL only)

Revision ID: 20260810_002_add_scheduler_lock_tracking
Revises: 20260810_001_enforce_admin_role_migration
Create Date: 2026-08-10

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence


# revision identifiers, used by Alembic.
revision: str = "20260810_002_add_scheduler_lock_tracking"
down_revision: str | None = "20260810_001_enforce_admin_role_migration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add lock tracking columns to scheduler tables.

    These columns support the unified SchedulerExecutionGuard:
    - lock_strategy: Which strategy was used ('session_lock' | 'heartbeat')
    - fencing_token: Monotonic token for stale leader detection
    - lock_acquired_at/released_at: Timing for debugging
    - skip_reason: Why a job was skipped
    - leader_host: Which host/process ran the job
    """
    connection = op.get_bind()
    dialect = connection.dialect.name

    # Add columns to scheduler_runs
    _add_column_if_not_exists(
        "scheduler_runs",
        "lock_strategy",
        sa.String(20),
        nullable=True,
        server_default=None,
    )

    _add_column_if_not_exists(
        "scheduler_runs",
        "fencing_token",
        sa.BigInteger(),
        nullable=True,
    )

    _add_column_if_not_exists(
        "scheduler_runs",
        "lock_acquired_at",
        sa.DateTime(),
        nullable=True,
    )

    _add_column_if_not_exists(
        "scheduler_runs",
        "lock_released_at",
        sa.DateTime(),
        nullable=True,
    )

    _add_column_if_not_exists(
        "scheduler_runs",
        "skip_reason",
        sa.Text(),
        nullable=True,
    )

    _add_column_if_not_exists(
        "scheduler_runs",
        "leader_host",
        sa.String(255),
        nullable=True,
    )

    # Add columns to scheduler_leaders
    _add_column_if_not_exists(
        "scheduler_leaders",
        "fencing_token",
        sa.BigInteger(),
        nullable=True,
    )

    _add_column_if_not_exists(
        "scheduler_leaders",
        "lock_strategy",
        sa.String(20),
        nullable=True,
    )

    # Create fencing_token_seq sequence (PostgreSQL only)
    if dialect == "postgresql":
        op.execute("CREATE SEQUENCE IF NOT EXISTS fencing_token_seq")


def downgrade() -> None:
    """Remove lock tracking columns."""
    connection = op.get_bind()
    dialect = connection.dialect.name

    # Drop sequence first (PostgreSQL only)
    if dialect == "postgresql":
        op.execute("DROP SEQUENCE IF EXISTS fencing_token_seq")

    # Drop columns from scheduler_leaders (use batch_alter_table for SQLite compatibility)
    with op.batch_alter_table("scheduler_leaders") as batch_op:
        batch_op.drop_column("lock_strategy")
        batch_op.drop_column("fencing_token")

    # Drop columns from scheduler_runs (use batch_alter_table for SQLite compatibility)
    with op.batch_alter_table("scheduler_runs") as batch_op:
        batch_op.drop_column("leader_host")
        batch_op.drop_column("skip_reason")
        batch_op.drop_column("lock_released_at")
        batch_op.drop_column("lock_acquired_at")
        batch_op.drop_column("fencing_token")
        batch_op.drop_column("lock_strategy")


def _add_column_if_not_exists(
    table_name: str,
    column_name: str,
    column_type: sa.TypeEngine,
    nullable: bool = True,
    server_default: str | None = None,
) -> None:
    """Add column if it doesn't already exist."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    # Get columns for the table
    columns = [col["name"] for col in inspector.get_columns(table_name)]

    if column_name not in columns:
        op.add_column(
            table_name,
            sa.Column(column_name, column_type, nullable=nullable, server_default=server_default),
        )
