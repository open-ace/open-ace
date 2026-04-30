"""Add foreign key constraints

Revision ID: 006_add_foreign_keys
Revises: 005_optimize_indexes
Create Date: 2026-03-22

This migration adds foreign key constraints to ensure data integrity.
SQLite requires table recreation to add foreign keys.

Tables affected:
- sessions: user_id -> users.id
- tenant_usage: tenant_id -> tenants.id
- quota_usage: user_id -> users.id
- quota_alerts: user_id -> users.id

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "006_add_foreign_keys"
down_revision: Union[str, None] = "005_optimize_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade database schema."""
    # Check if we're using PostgreSQL
    bind = op.get_bind()
    is_postgresql = bind.dialect.name == "postgresql"

    # ============================================
    # sessions table - Add foreign key to users
    # ============================================
    # For PostgreSQL, drop the unique constraint on token first
    if is_postgresql:
        op.execute("ALTER TABLE sessions DROP CONSTRAINT IF EXISTS sessions_token_key")

    op.create_table(
        "sessions_new",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("token", sa.String(), nullable=False, unique=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("expires_at", sa.TIMESTAMP(), nullable=False),
        sa.Column("is_active", sa.Integer(), server_default="1"),
    )

    op.execute("""
        INSERT INTO sessions_new (id, token, user_id, created_at, expires_at, is_active)
        SELECT id, token, user_id, created_at, expires_at, is_active FROM sessions
    """)

    op.drop_table("sessions")
    op.rename_table("sessions_new", "sessions")

    # Recreate indexes
    op.create_index("idx_sessions_user_id", "sessions", ["user_id"])
    op.create_index("idx_sessions_token", "sessions", ["token"])
    op.create_index("idx_sessions_expires", "sessions", ["expires_at"])
    op.create_index("idx_sessions_active", "sessions", ["is_active", "expires_at"])

    # ============================================
    # tenant_usage table - Add foreign key to tenants
    # ============================================
    # For PostgreSQL, drop the unique constraint first to avoid name conflict
    if is_postgresql:
        op.execute("ALTER TABLE tenant_usage DROP CONSTRAINT IF EXISTS uq_tenant_usage_tenant_date")

    op.create_table(
        "tenant_usage_new",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "tenant_id",
            sa.Integer(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("date", sa.Text(), nullable=False),
        sa.Column("tokens_used", sa.Integer(), server_default="0"),
        sa.Column("requests_made", sa.Integer(), server_default="0"),
        sa.Column("active_users", sa.Integer(), server_default="0"),
        sa.Column("new_users", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("tenant_id", "date", name="uq_tenant_usage_tenant_date_new"),
    )

    op.execute("""
        INSERT INTO tenant_usage_new (id, tenant_id, date, tokens_used, requests_made, active_users, new_users, created_at)
        SELECT id, tenant_id, date, tokens_used, requests_made, active_users, new_users, created_at FROM tenant_usage
    """)

    op.drop_table("tenant_usage")
    op.rename_table("tenant_usage_new", "tenant_usage")

    # Recreate indexes
    op.create_index("idx_tenant_usage_tenant", "tenant_usage", ["tenant_id"])
    op.create_index("idx_tenant_usage_date", "tenant_usage", ["date"])

    # ============================================
    # quota_usage table - Add foreign key to users
    # ============================================
    # For PostgreSQL, drop the unique constraint first to avoid name conflict
    if is_postgresql:
        op.execute(
            "ALTER TABLE quota_usage DROP CONSTRAINT IF EXISTS uq_quota_usage_user_date_period"
        )

    op.create_table(
        "quota_usage_new",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("date", sa.Text(), nullable=False),
        sa.Column("period", sa.Text(), server_default="daily"),
        sa.Column("tokens_used", sa.Integer(), server_default="0"),
        sa.Column("requests_used", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint(
            "user_id", "date", "period", name="uq_quota_usage_user_date_period_new"
        ),
    )

    op.execute("""
        INSERT INTO quota_usage_new (id, user_id, date, period, tokens_used, requests_used, created_at, updated_at)
        SELECT id, user_id, date, period, tokens_used, requests_used, created_at, updated_at FROM quota_usage
    """)

    op.drop_table("quota_usage")
    op.rename_table("quota_usage_new", "quota_usage")

    # Recreate indexes
    op.create_index("idx_quota_usage_user", "quota_usage", ["user_id"])
    op.create_index("idx_quota_usage_date", "quota_usage", ["date"])

    # ============================================
    # quota_alerts table - Add foreign key to users
    # ============================================
    op.create_table(
        "quota_alerts_new",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("alert_type", sa.Text(), nullable=False),
        sa.Column("quota_type", sa.Text(), nullable=False),
        sa.Column("period", sa.Text(), server_default="daily"),
        sa.Column("threshold", sa.REAL(), nullable=False),
        sa.Column("current_usage", sa.Integer(), nullable=False),
        sa.Column("quota_limit", sa.Integer(), nullable=False),
        sa.Column("percentage", sa.REAL(), nullable=False),
        sa.Column("message", sa.Text()),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("acknowledged", sa.Integer(), server_default="0"),
        sa.Column("acknowledged_at", sa.TIMESTAMP()),
        sa.Column("acknowledged_by", sa.Integer()),
    )

    op.execute("""
        INSERT INTO quota_alerts_new (id, user_id, alert_type, quota_type, period, threshold,
            current_usage, quota_limit, percentage, message, created_at, acknowledged, acknowledged_at, acknowledged_by)
        SELECT id, user_id, alert_type, quota_type, period, threshold,
            current_usage, quota_limit, percentage, message, created_at, acknowledged, acknowledged_at, acknowledged_by
        FROM quota_alerts
    """)

    op.drop_table("quota_alerts")
    op.rename_table("quota_alerts_new", "quota_alerts")

    # Recreate indexes
    op.create_index("idx_quota_alerts_user", "quota_alerts", ["user_id"])
    op.create_index("idx_quota_alerts_created", "quota_alerts", ["created_at"])
    op.create_index("idx_quota_alerts_unack", "quota_alerts", ["acknowledged", "created_at"])


def downgrade() -> None:
    """Downgrade database schema - remove foreign keys."""
    # Note: This is a simplified downgrade that removes foreign keys
    # In practice, you might want to keep the data

    # Recreate quota_alerts without FK
    op.create_table(
        "quota_alerts_old",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("alert_type", sa.Text(), nullable=False),
        sa.Column("quota_type", sa.Text(), nullable=False),
        sa.Column("period", sa.Text(), server_default="daily"),
        sa.Column("threshold", sa.REAL(), nullable=False),
        sa.Column("current_usage", sa.Integer(), nullable=False),
        sa.Column("quota_limit", sa.Integer(), nullable=False),
        sa.Column("percentage", sa.REAL(), nullable=False),
        sa.Column("message", sa.Text()),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("acknowledged", sa.Integer(), server_default="0"),
        sa.Column("acknowledged_at", sa.TIMESTAMP()),
        sa.Column("acknowledged_by", sa.Integer()),
    )
    op.execute("""
        INSERT INTO quota_alerts_old SELECT * FROM quota_alerts
    """)
    op.drop_table("quota_alerts")
    op.rename_table("quota_alerts_old", "quota_alerts")
    op.create_index("idx_quota_alerts_user", "quota_alerts", ["user_id"])
    op.create_index("idx_quota_alerts_created", "quota_alerts", ["created_at"])

    # Recreate quota_usage without FK
    op.create_table(
        "quota_usage_old",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Text(), nullable=False),
        sa.Column("period", sa.Text(), server_default="daily"),
        sa.Column("tokens_used", sa.Integer(), server_default="0"),
        sa.Column("requests_used", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("user_id", "date", "period", name="uq_quota_usage_user_date_period"),
    )
    op.execute("""
        INSERT INTO quota_usage_old SELECT * FROM quota_usage
    """)
    op.drop_table("quota_usage")
    op.rename_table("quota_usage_old", "quota_usage")
    op.create_index("idx_quota_usage_user", "quota_usage", ["user_id"])
    op.create_index("idx_quota_usage_date", "quota_usage", ["date"])

    # Recreate tenant_usage without FK
    op.create_table(
        "tenant_usage_old",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Text(), nullable=False),
        sa.Column("tokens_used", sa.Integer(), server_default="0"),
        sa.Column("requests_made", sa.Integer(), server_default="0"),
        sa.Column("active_users", sa.Integer(), server_default="0"),
        sa.Column("new_users", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("tenant_id", "date", name="uq_tenant_usage_tenant_date"),
    )
    op.execute("""
        INSERT INTO tenant_usage_old SELECT * FROM tenant_usage
    """)
    op.drop_table("tenant_usage")
    op.rename_table("tenant_usage_old", "tenant_usage")
    op.create_index("idx_tenant_usage_tenant", "tenant_usage", ["tenant_id"])
    op.create_index("idx_tenant_usage_date", "tenant_usage", ["date"])

    # Recreate sessions without FK
    op.create_table(
        "sessions_old",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("token", sa.String(), nullable=False, unique=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("expires_at", sa.TIMESTAMP(), nullable=False),
        sa.Column("is_active", sa.Integer(), server_default="1"),
    )
    op.execute("""
        INSERT INTO sessions_old SELECT * FROM sessions
    """)
    op.drop_table("sessions")
    op.rename_table("sessions_old", "sessions")
    op.create_index("idx_sessions_user_id", "sessions", ["user_id"])
    op.create_index("idx_sessions_token", "sessions", ["token"])
    op.create_index("idx_sessions_expires", "sessions", ["expires_at"])
