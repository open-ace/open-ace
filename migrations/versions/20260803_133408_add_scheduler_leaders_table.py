"""add_scheduler_leaders_table

Issue #2187: Create tables for distributed scheduler leader election.

Tables created:
- scheduler_leaders: Leader election state for each job
- scheduler_runs: Execution history for metrics and troubleshooting

Revision ID: 9508dfc8c62a
Revises: 0ae05f5e2247
Create Date: 2026-08-03 13:34:08.308282

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence


# revision identifiers, used by Alembic.
revision: str = "20260803_003_add_scheduler_leaders_table"
down_revision: str | None = "20260803_002_create_retention_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create scheduler leader election tables.

    These tables support distributed scheduler coordination:
    - scheduler_leaders: Tracks which instance is the leader for each job
    - scheduler_runs: Records execution history for observability

    Both PostgreSQL and SQLite are supported.
    """
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    existing_tables = set(inspector.get_table_names())

    # 1. scheduler_leaders table
    if "scheduler_leaders" not in existing_tables:
        op.create_table(
            "scheduler_leaders",
            sa.Column("job_name", sa.String(100), nullable=False),
            sa.Column("leader_id", sa.String(255), nullable=False),
            sa.Column("owner_info", sa.Text(), nullable=True),
            sa.Column("acquired_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("heartbeat_at", sa.DateTime(), nullable=False),
            sa.Column("last_run_at", sa.DateTime(), nullable=True),
            sa.Column("run_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("skip_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("fail_count", sa.Integer(), nullable=False, server_default="0"),
            sa.PrimaryKeyConstraint("job_name"),
        )

    # Create indexes for scheduler_leaders
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_scheduler_leaders_expires "
        "ON scheduler_leaders (expires_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_scheduler_leaders_heartbeat "
        "ON scheduler_leaders (heartbeat_at)"
    )

    # 2. scheduler_runs table
    if "scheduler_runs" not in existing_tables:
        # Note: Use INTEGER for id in SQLite (autoincrement), SERIAL for PostgreSQL
        # SQLAlchemy will handle this via sa.Integer with autoincrement=True
        op.create_table(
            "scheduler_runs",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("job_name", sa.String(100), nullable=False),
            sa.Column("leader_id", sa.String(255), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("ended_at", sa.DateTime(), nullable=True),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            # Note: JSONB is PostgreSQL-specific, use Text for SQLite compatibility
            # The application layer will handle JSON serialization
            sa.Column("metrics", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    # Create indexes for scheduler_runs
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_scheduler_runs_job_time "
        "ON scheduler_runs (job_name, started_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_scheduler_runs_status "
        "ON scheduler_runs (status)"
    )


def downgrade() -> None:
    """Remove scheduler tables."""
    op.drop_index("idx_scheduler_runs_status", table_name="scheduler_runs")
    op.drop_index("idx_scheduler_runs_job_time", table_name="scheduler_runs")
    op.drop_table("scheduler_runs")

    op.drop_index("idx_scheduler_leaders_heartbeat", table_name="scheduler_leaders")
    op.drop_index("idx_scheduler_leaders_expires", table_name="scheduler_leaders")
    op.drop_table("scheduler_leaders")