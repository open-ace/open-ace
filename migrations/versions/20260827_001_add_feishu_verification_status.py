"""Add verification status fields to feishu_settings.

Revision ID: 20260827_001
Revises: 20260827_001_fix_scheduler_runs_idleness
"""

import sqlalchemy as sa
from alembic import op

revision = "20260827_001"
down_revision = "20260825_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())

    if "feishu_settings" in existing_tables:
        # Get existing columns
        conn = op.get_bind()
        inspector = sa.inspect(conn)
        existing_columns = {col["name"] for col in inspector.get_columns("feishu_settings")}

        # Add verification status columns if not exist
        if "verification_status" not in existing_columns:
            op.add_column(
                "feishu_settings",
                sa.Column("verification_status", sa.String(32), nullable=True),
            )

        if "last_tested_at" not in existing_columns:
            op.add_column(
                "feishu_settings",
                sa.Column("last_tested_at", sa.DateTime(), nullable=True),
            )

        if "last_test_error_code" not in existing_columns:
            op.add_column(
                "feishu_settings",
                sa.Column("last_test_error_code", sa.String(64), nullable=True),
            )

        if "last_test_error_summary" not in existing_columns:
            op.add_column(
                "feishu_settings",
                sa.Column("last_test_error_summary", sa.Text(), nullable=True),
            )

        if "verified_config_fingerprint" not in existing_columns:
            op.add_column(
                "feishu_settings",
                sa.Column("verified_config_fingerprint", sa.String(128), nullable=True),
            )


def downgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())

    if "feishu_settings" in existing_tables:
        conn = op.get_bind()
        inspector = sa.inspect(conn)
        existing_columns = {col["name"] for col in inspector.get_columns("feishu_settings")}

        for column in [
            "verified_config_fingerprint",
            "last_test_error_summary",
            "last_test_error_code",
            "last_tested_at",
            "verification_status",
        ]:
            if column in existing_columns:
                op.drop_column("feishu_settings", column)
