"""Add missing columns to autonomous_workflows for PostgreSQL

Revision ID: 20260629_001_add_workflow_lock_columns
Revises: 20260627_001_init_project_categories
Create Date: 2026-06-29

Issue: #1347
These columns were defined in the SQLite CREATE TABLE/ALTER TABLE statements
but were missing from PostgreSQL Alembic migrations, causing runtime errors.

Columns:
- locked_at: TIMESTAMP - when the workflow was locked for processing
- locked_by: TEXT - identifier of the process holding the lock
- transient_retry_count: INTEGER - counter for transient network error retries
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260629_001_add_workflow_lock_columns"
down_revision: Union[str, None] = "20260627_001_init_project_categories"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Check if columns already exist (SQLite baseline may already have them)
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    existing_columns = {col["name"] for col in inspector.get_columns("autonomous_workflows")}

    # Add locked_at column for distributed lock timestamp
    if "locked_at" not in existing_columns:
        op.add_column(
            "autonomous_workflows",
            sa.Column("locked_at", sa.TIMESTAMP(), nullable=True),
        )
    # Add locked_by column for distributed lock owner identifier
    if "locked_by" not in existing_columns:
        op.add_column(
            "autonomous_workflows",
            sa.Column("locked_by", sa.Text(), nullable=True, server_default=""),
        )
    # Add transient_retry_count for transient network error retry tracking
    if "transient_retry_count" not in existing_columns:
        op.add_column(
            "autonomous_workflows",
            sa.Column("transient_retry_count", sa.Integer(), nullable=True, server_default="0"),
        )


def downgrade() -> None:
    # Use batch_alter_table for SQLite compatibility (DROP COLUMN requires batch mode)
    with op.batch_alter_table("autonomous_workflows") as batch_op:
        batch_op.drop_column("transient_retry_count")
        batch_op.drop_column("locked_by")
        batch_op.drop_column("locked_at")
