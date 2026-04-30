"""Add missing tables

Revision ID: 002_add_missing_tables
Revises: 001_initial
Create Date: 2026-03-22

This migration adds tables that were dynamically created in code:
- tenants
- tenant_usage
- content_filter_rules
- quota_usage
- quota_alerts
- audit_logs

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "002_add_missing_tables"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade database schema."""
    # Create tenants table
    op.create_table(
        "tenants",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), unique=True, nullable=False),
        sa.Column("status", sa.Text(), server_default="active"),
        sa.Column("plan", sa.Text(), server_default="standard"),
        sa.Column("contact_email", sa.Text()),
        sa.Column("contact_phone", sa.Text()),
        sa.Column("contact_name", sa.Text()),
        sa.Column("quota", sa.Text()),
        sa.Column("settings", sa.Text()),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("trial_ends_at", sa.TIMESTAMP()),
        sa.Column("subscription_ends_at", sa.TIMESTAMP()),
        sa.Column("user_count", sa.Integer(), server_default="0"),
        sa.Column("total_tokens_used", sa.Integer(), server_default="0"),
        sa.Column("total_requests_made", sa.Integer(), server_default="0"),
    )
    op.create_index("idx_tenants_slug", "tenants", ["slug"])
    op.create_index("idx_tenants_status", "tenants", ["status"])

    # Create tenant_usage table
    op.create_table(
        "tenant_usage",
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
    op.create_index("idx_tenant_usage_tenant", "tenant_usage", ["tenant_id"])
    op.create_index("idx_tenant_usage_date", "tenant_usage", ["date"])

    # Create content_filter_rules table
    op.create_table(
        "content_filter_rules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("pattern", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), server_default="keyword"),
        sa.Column("severity", sa.Text(), server_default="medium"),
        sa.Column("action", sa.Text(), server_default="warn"),
        sa.Column("is_enabled", sa.Integer(), server_default="1"),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text()),
    )
    op.create_index("idx_filter_rules_type", "content_filter_rules", ["type"])
    op.create_index("idx_filter_rules_enabled", "content_filter_rules", ["is_enabled"])

    # Create quota_usage table
    op.create_table(
        "quota_usage",
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
    op.create_index("idx_quota_usage_user", "quota_usage", ["user_id"])
    op.create_index("idx_quota_usage_date", "quota_usage", ["date"])

    # Create quota_alerts table
    op.create_table(
        "quota_alerts",
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
    op.create_index("idx_quota_alerts_user", "quota_alerts", ["user_id"])
    op.create_index("idx_quota_alerts_created", "quota_alerts", ["created_at"])
    op.create_index("idx_quota_alerts_unack", "quota_alerts", ["acknowledged", "created_at"])

    # Create audit_logs table
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("timestamp", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("user_id", sa.Integer()),
        sa.Column("username", sa.Text()),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), server_default="info"),
        sa.Column("resource_type", sa.Text()),
        sa.Column("resource_id", sa.Text()),
        sa.Column("details", sa.Text()),
        sa.Column("ip_address", sa.Text()),
        sa.Column("user_agent", sa.Text()),
        sa.Column("session_id", sa.Text()),
        sa.Column("success", sa.Integer(), server_default="1"),
        sa.Column("error_message", sa.Text()),
    )
    op.create_index("idx_audit_timestamp", "audit_logs", ["timestamp"])
    op.create_index("idx_audit_user_id", "audit_logs", ["user_id"])
    op.create_index("idx_audit_action", "audit_logs", ["action"])
    op.create_index("idx_audit_resource", "audit_logs", ["resource_type", "resource_id"])
    op.create_index("idx_audit_severity", "audit_logs", ["severity"])


def downgrade() -> None:
    """Downgrade database schema."""
    # Drop tables in reverse order
    op.drop_index("idx_audit_severity", "audit_logs")
    op.drop_index("idx_audit_resource", "audit_logs")
    op.drop_index("idx_audit_action", "audit_logs")
    op.drop_index("idx_audit_user_id", "audit_logs")
    op.drop_index("idx_audit_timestamp", "audit_logs")
    op.drop_table("audit_logs")

    op.drop_index("idx_quota_alerts_unack", "quota_alerts")
    op.drop_index("idx_quota_alerts_created", "quota_alerts")
    op.drop_index("idx_quota_alerts_user", "quota_alerts")
    op.drop_table("quota_alerts")

    op.drop_index("idx_quota_usage_date", "quota_usage")
    op.drop_index("idx_quota_usage_user", "quota_usage")
    op.drop_table("quota_usage")

    op.drop_index("idx_filter_rules_enabled", "content_filter_rules")
    op.drop_index("idx_filter_rules_type", "content_filter_rules")
    op.drop_table("content_filter_rules")

    op.drop_index("idx_tenant_usage_date", "tenant_usage")
    op.drop_index("idx_tenant_usage_tenant", "tenant_usage")
    op.drop_table("tenant_usage")

    op.drop_index("idx_tenants_status", "tenants")
    op.drop_index("idx_tenants_slug", "tenants")
    op.drop_table("tenants")
