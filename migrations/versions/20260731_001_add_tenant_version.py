"""add tenant_version columns for migration tracking

Revision ID: 20260731_001_add_tenant_version
Revises: 20260730_001_validate_daily_usage_tenant
Create Date: 2026-07-31

Issue #2163: Adds tenant_version columns to users and agent_sessions tables
for tracking tenant migrations, and creates tenant_migrations table for
migration history with progress tracking.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "20260731_001_add_tenant_version"
down_revision: str | None = "20260730_001_validate_daily_usage_tenant"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add tenant_version columns and tenant_migrations table."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    # Add tenant_version to users table
    users_columns = {col["name"] for col in inspector.get_columns("users")}
    if "tenant_version" not in users_columns:
        op.add_column(
            "users",
            sa.Column("tenant_version", sa.Integer, nullable=False, server_default="1"),
        )

    # Add tenant_version to agent_sessions table
    sessions_columns = {col["name"] for col in inspector.get_columns("agent_sessions")}
    if "tenant_version" not in sessions_columns:
        op.add_column(
            "agent_sessions",
            sa.Column("tenant_version", sa.Integer, nullable=False, server_default="1"),
        )

    # Create tenant_migrations table
    existing_tables = set(inspector.get_table_names())
    if "tenant_migrations" not in existing_tables:
        op.create_table(
            "tenant_migrations",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
            sa.Column("old_tenant_id", sa.Integer, nullable=False),
            sa.Column("new_tenant_id", sa.Integer, nullable=False),
            sa.Column("migrated_by", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
            sa.Column(
                "migrated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("affected_sessions", sa.Integer),
            sa.Column("affected_projects", sa.Integer),
            sa.Column("batch_number", sa.Integer),
            sa.Column("total_batches", sa.Integer),
            sa.Column(
                "status",
                sa.String(20),
                nullable=False,
                server_default="pending",
            ),
        )

        # Create indexes
        op.create_index("idx_tenant_migrations_user", "tenant_migrations", ["user_id"])
        op.create_index("idx_tenant_migrations_status", "tenant_migrations", ["status"])


def downgrade() -> None:
    """Remove tenant_version columns and tenant_migrations table."""
    # Drop tenant_migrations table and indexes
    op.drop_index("idx_tenant_migrations_status", table_name="tenant_migrations")
    op.drop_index("idx_tenant_migrations_user", table_name="tenant_migrations")
    op.drop_table("tenant_migrations")

    # Drop tenant_version from agent_sessions
    op.drop_column("agent_sessions", "tenant_version")

    # Drop tenant_version from users
    op.drop_column("users", "tenant_version")
