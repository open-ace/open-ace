"""Add webhook delivery-state tracking table

Revision ID: 20260725_001_add_webhook_deliveries
Revises: 20260722_001_add_llm_proxy_resolved_ips
Create Date: 2026-07-25

Issue: #1831
Adds a durable delivery-state table for outbound webhook notifications so
transient receiver failures (5xx/timeout/reset) can be retried with backoff
instead of being silently dropped. Only a hash of the webhook URL is stored —
the plaintext URL (which carries bot tokens for Feishu/DingTalk) is never
persisted here.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260725_001_add_webhook_deliveries"
down_revision: str | None = "20260722_001_add_llm_proxy_resolved_ips"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add the webhook_deliveries delivery-state table and indexes."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    existing_tables = set(inspector.get_table_names())
    if "webhook_deliveries" not in existing_tables:
        op.create_table(
            "webhook_deliveries",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("alert_id", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            # Hash of the configured webhook URL — never the plaintext URL,
            # which may embed bot tokens (Feishu/DingTalk).
            sa.Column("webhook_url_hash", sa.String(length=64), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("next_retry_at", sa.DateTime(), nullable=True),
            sa.Column("last_error_type", sa.String(length=64), nullable=True),
            sa.Column("last_error_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.CheckConstraint(
                "status IN ('pending', 'in_flight', 'delivered', 'dead')",
                name="ck_webhook_deliveries_status",
            ),
        )
        # Reaper scan index: due-pending rows ordered by next retry time.
        op.create_index(
            "idx_webhook_deliveries_status_retry",
            "webhook_deliveries",
            ["status", "next_retry_at"],
        )
        op.create_index(
            "idx_webhook_deliveries_user",
            "webhook_deliveries",
            ["user_id"],
        )
        op.create_index(
            "idx_webhook_deliveries_alert",
            "webhook_deliveries",
            ["alert_id"],
        )


def downgrade() -> None:
    """Remove the webhook_deliveries delivery-state table and indexes."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    existing_tables = set(inspector.get_table_names())
    if "webhook_deliveries" in existing_tables:
        existing_indexes = {idx["name"] for idx in inspector.get_indexes("webhook_deliveries")}
        for idx_name in (
            "idx_webhook_deliveries_alert",
            "idx_webhook_deliveries_user",
            "idx_webhook_deliveries_status_retry",
        ):
            if idx_name in existing_indexes:
                op.drop_index(idx_name, table_name="webhook_deliveries")
        op.drop_table("webhook_deliveries")
