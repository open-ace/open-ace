"""Add usage report idempotency and indexes

Revision ID: 20260721_002_add_usage_report_idempotency
Revises: 20260721_001_add_ci_diagnostics_attempts
Create Date: 2026-07-21

Issue: #1891
Adds last_usage_report_at column for idempotency protection and indexes
for session-machine binding validation queries.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_002_add_usage_report_idempotency"
down_revision: str | None = "20260721_001_add_ci_diagnostics_attempts"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add last_usage_report_at column and performance indexes."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    # Add last_usage_report_at column to agent_sessions if not exists
    existing_columns = {col["name"] for col in inspector.get_columns("agent_sessions")}
    if "last_usage_report_at" not in existing_columns:
        op.add_column(
            "agent_sessions",
            sa.Column(
                "last_usage_report_at",
                sa.DateTime(),
                nullable=True,
            ),
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
    """Remove last_usage_report_at column and indexes."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    # Drop indexes
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("agent_sessions")}
    if "idx_agent_sessions_tenant_id" in existing_indexes:
        op.drop_index("idx_agent_sessions_tenant_id", table_name="agent_sessions")
    if "idx_agent_sessions_remote_machine_id" in existing_indexes:
        op.drop_index("idx_agent_sessions_remote_machine_id", table_name="agent_sessions")

    # Drop column
    existing_columns = {col["name"] for col in inspector.get_columns("agent_sessions")}
    if "last_usage_report_at" in existing_columns:
        op.drop_column("agent_sessions", "last_usage_report_at")