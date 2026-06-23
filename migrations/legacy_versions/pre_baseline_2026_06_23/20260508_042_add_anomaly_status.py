"""Add anomaly_status table for tracking anomaly resolution

Revision ID: 042_add_anomaly_status
Revises: 041_add_audit_threshold_defaults
Create Date: 2026-05-08

Adds a table to track the resolution status of detected audit anomalies,
allowing admins to mark anomalies as processed or ignored.
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "042_add_anomaly_status"
down_revision: Union[str, None] = "041_add_audit_threshold_defaults"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.create_table(
        "anomaly_status",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("anomaly_type", sa.String(100), nullable=False),
        sa.Column("affected_users_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("processed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.current_timestamp()),
    )
    op.create_index(
        "ix_anomaly_status_type_hash",
        "anomaly_status",
        ["anomaly_type", "affected_users_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_anomaly_status_type_hash", table_name="anomaly_status")
    op.drop_table("anomaly_status")
