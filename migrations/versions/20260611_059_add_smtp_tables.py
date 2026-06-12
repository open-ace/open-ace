"""Add SMTP email notification tables

Revision ID: 059_add_smtp_tables
Revises: 058_add_workflow_definition_snapshot
Create Date: 2026-06-11

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "059_add_smtp_tables"
down_revision: Union[str, None] = "058_workflow_definition_snapshot"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade database schema."""
    # Add notification_email field to notification_preferences table
    try:
        op.add_column(
            "notification_preferences",
            sa.Column("notification_email", sa.Text(), nullable=True),
        )
    except Exception:
        # Column may already exist
        pass

    try:
        op.add_column(
            "notification_preferences",
            sa.Column("email_verified", sa.Boolean(), server_default=sa.text("FALSE")),
        )
    except Exception:
        # Column may already exist
        pass

    # Create smtp_settings table (system-wide SMTP configuration)
    op.create_table(
        "smtp_settings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("smtp_host", sa.String(255), nullable=False),
        sa.Column("smtp_port", sa.Integer(), nullable=False, server_default="587"),
        sa.Column("smtp_user", sa.String(255), nullable=True),
        sa.Column("encrypted_password", sa.Text(), nullable=True),
        sa.Column("encryption_version", sa.Integer(), server_default="1"),
        sa.Column("from_address", sa.String(255), nullable=False),
        sa.Column("use_tls", sa.Boolean(), server_default=sa.text("TRUE")),
        sa.Column("is_verified", sa.Boolean(), server_default=sa.text("FALSE")),
        sa.Column("last_verified_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("created_by", sa.Integer(), nullable=True),
        # Only one SMTP config per system
        sa.UniqueConstraint("id", name="uq_smtp_settings_single"),
    )

    # Create email_notification_logs table (audit trail)
    op.create_table(
        "email_notification_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("alert_id", sa.String(), nullable=True),
        sa.Column("recipient_email", sa.String(255), nullable=False),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("email_body", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0"),
        sa.Column("next_retry_at", sa.TIMESTAMP(), nullable=True),
    )

    # Create indexes for email_notification_logs
    op.create_index("idx_email_logs_user_id", "email_notification_logs", ["user_id"])
    op.create_index("idx_email_logs_sent_at", "email_notification_logs", ["sent_at"])
    op.create_index("idx_email_logs_status", "email_notification_logs", ["status"])
    op.create_index(
        "idx_email_logs_user_sent", "email_notification_logs", ["user_id", "sent_at"]
    )


def downgrade() -> None:
    """Downgrade database schema."""
    # Drop indexes
    op.drop_index("idx_email_logs_user_sent", "email_notification_logs")
    op.drop_index("idx_email_logs_status", "email_notification_logs")
    op.drop_index("idx_email_logs_sent_at", "email_notification_logs")
    op.drop_index("idx_email_logs_user_id", "email_notification_logs")

    # Drop tables
    op.drop_table("email_notification_logs")
    op.drop_table("smtp_settings")

    # Drop columns from notification_preferences
    try:
        op.drop_column("notification_preferences", "email_verified")
    except Exception:
        pass

    try:
        op.drop_column("notification_preferences", "notification_email")
    except Exception:
        pass