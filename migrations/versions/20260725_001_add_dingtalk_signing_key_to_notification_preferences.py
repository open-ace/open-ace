"""Add dingtalk_webhook_secret column to notification_preferences

Revision ID: 20260725_001_add_dingtalk_signing_key_to_notification_preferences
Revises: 20260722_001_add_llm_proxy_resolved_ips
Create Date: 2026-07-25

Issue: #1829, F6
Adds a per-user DingTalk signing-secret column to notification_preferences for
multi-tenant key isolation. The column holds the Fernet-encrypted per-user
secret (see app/utils/smtp_crypto.py). It is nullable: existing rows keep
behaving as before (signing falls back to the global
``alerts.dingtalk_webhook_secret`` config). The plaintext is never stored — it
is lifted out of the webhook URL query at write time, encrypted, and decrypted
lazily only when signing an outbound DingTalk webhook (see
``AlertNotifier._prepare_webhook_url``).
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260725_001_add_dingtalk_signing_key_to_notification_preferences"
down_revision: str | None = "20260722_001_add_llm_proxy_resolved_ips"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add dingtalk_webhook_secret (nullable TEXT) to notification_preferences."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    if "notification_preferences" not in set(inspector.get_table_names()):
        # Fresh databases create the column directly in CREATE TABLE
        # (schema_init / _ensure_tables); nothing to migrate here.
        return

    existing_columns = {col["name"] for col in inspector.get_columns("notification_preferences")}
    if "dingtalk_webhook_secret" in existing_columns:
        # Already applied (e.g. by _ensure_tables or a previous run); idempotent.
        return

    op.add_column(
        "notification_preferences",
        sa.Column("dingtalk_webhook_secret", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Remove the dingtalk_webhook_secret column."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    if "notification_preferences" not in set(inspector.get_table_names()):
        return

    existing_columns = {col["name"] for col in inspector.get_columns("notification_preferences")}
    if "dingtalk_webhook_secret" not in existing_columns:
        return

    op.drop_column("notification_preferences", "dingtalk_webhook_secret")
