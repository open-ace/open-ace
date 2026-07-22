"""Add usage report idempotency and indexes

Revision ID: 20260721_002_add_usage_report_idempotency
Revises: 20260721_001_add_ci_diagnostics_attempts
Create Date: 2026-07-21

Issue: #1891
Adds durable receipt and shared rate-limit tables plus indexes for
session-machine binding validation queries.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_002_add_usage_report_idempotency"
down_revision: str | None = "20260721_001_add_ci_diagnostics_attempts"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add usage report replay protection and shared rate limiting."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    existing_tables = set(inspector.get_table_names())
    if "usage_report_receipts" not in existing_tables:
        op.create_table(
            "usage_report_receipts",
            sa.Column("report_id", sa.String(length=128), primary_key=True),
            sa.Column("session_id", sa.Text(), nullable=False),
            sa.Column("machine_id", sa.Text(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("tenant_id", sa.Integer(), nullable=False),
            sa.Column("payload_hash", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("processed_at", sa.DateTime(), nullable=True),
            sa.CheckConstraint(
                "status IN ('processing', 'completed', 'failed')",
                name="ck_usage_report_receipts_status",
            ),
        )
        op.create_index(
            "idx_usage_report_receipts_session",
            "usage_report_receipts",
            ["session_id", "created_at"],
        )

    if "usage_report_rate_limits" not in existing_tables:
        op.create_table(
            "usage_report_rate_limits",
            sa.Column("rate_key", sa.String(length=512), primary_key=True),
            sa.Column("window_started_at", sa.DateTime(), nullable=False),
            sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.CheckConstraint(
                "request_count >= 0",
                name="ck_usage_report_rate_limits_count",
            ),
        )
        op.create_index(
            "idx_usage_report_rate_limits_updated",
            "usage_report_rate_limits",
            ["updated_at"],
        )

    # Add indexes for usage report binding validation queries
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("agent_sessions")}

    if "idx_agent_sessions_remote_machine_id" not in existing_indexes:
        op.create_index(
            "idx_agent_sessions_remote_machine_id",
            "agent_sessions",
            ["remote_machine_id"],
            unique=False,
        )

    if "idx_agent_sessions_tenant_id" not in existing_indexes:
        op.create_index(
            "idx_agent_sessions_tenant_id",
            "agent_sessions",
            ["tenant_id"],
            unique=False,
        )


def downgrade() -> None:
    """Remove usage report replay protection and indexes."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    # Drop indexes
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("agent_sessions")}
    if "idx_agent_sessions_tenant_id" in existing_indexes:
        op.drop_index("idx_agent_sessions_tenant_id", table_name="agent_sessions")
    if "idx_agent_sessions_remote_machine_id" in existing_indexes:
        op.drop_index("idx_agent_sessions_remote_machine_id", table_name="agent_sessions")

    existing_tables = set(inspector.get_table_names())
    if "usage_report_rate_limits" in existing_tables:
        op.drop_table("usage_report_rate_limits")
    if "usage_report_receipts" in existing_tables:
        op.drop_table("usage_report_receipts")
