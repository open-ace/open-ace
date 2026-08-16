"""Add centralized notification and collaboration settings.

Revision ID: 20260814_002
Revises: 20260814_001
"""

import sqlalchemy as sa
from alembic import op

revision = "20260814_002"
down_revision = "20260814_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())

    def common():
        return (
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )

    if "feishu_settings" not in existing_tables:
        op.create_table(
            "feishu_settings",
            sa.Column("app_id", sa.String(255), nullable=False),
            sa.Column("app_secret_enc", sa.Text(), nullable=False),
            sa.Column("sync_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("target_tenant_id", sa.Integer(), nullable=True),
            sa.Column("interval_minutes", sa.Integer(), server_default="60", nullable=False),
            sa.Column("max_runtime_seconds", sa.Integer(), server_default="1800", nullable=False),
            sa.Column("auto_recovery", sa.Boolean(), server_default=sa.false(), nullable=False),
            *common(),
            sa.CheckConstraint("id = 1", name="ck_feishu_settings_singleton"),
        )
    if "dingtalk_settings" not in existing_tables:
        op.create_table(
            "dingtalk_settings",
            sa.Column("app_key", sa.String(255), nullable=True),
            sa.Column("app_secret_enc", sa.Text(), nullable=True),
            sa.Column("fallback_webhook_secret_enc", sa.Text(), nullable=True),
            sa.Column("sync_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("target_tenant_id", sa.Integer(), nullable=True),
            sa.Column("interval_minutes", sa.Integer(), server_default="60", nullable=False),
            sa.Column("root_dept_id", sa.String(255), server_default="1", nullable=False),
            sa.Column("max_runtime_seconds", sa.Integer(), server_default="1800", nullable=False),
            sa.Column("auto_recovery", sa.Boolean(), server_default=sa.false(), nullable=False),
            *common(),
            sa.CheckConstraint("id = 1", name="ck_dingtalk_settings_singleton"),
        )
    if "webhook_settings" not in existing_tables:
        op.create_table(
            "webhook_settings",
            sa.Column("webhook_secret_enc", sa.Text(), nullable=True),
            sa.Column(
                "allow_private_webhook_urls",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            ),
            sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
            *common(),
            sa.CheckConstraint("id = 1", name="ck_webhook_settings_singleton"),
        )
    if "config_import_state" not in existing_tables:
        op.create_table(
            "config_import_state",
            sa.Column("config_key", sa.String(64), primary_key=True),
            sa.Column("state", sa.String(32), nullable=False),
            sa.Column("source", sa.String(255), nullable=True),
            sa.Column("imported_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )


def downgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    for table in (
        "config_import_state",
        "webhook_settings",
        "dingtalk_settings",
        "feishu_settings",
    ):
        if table in existing_tables:
            op.drop_table(table)
