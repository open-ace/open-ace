"""Split tenant JSON fields

Revision ID: 007_split_tenant_json_fields
Revises: 006_add_foreign_keys
Create Date: 2026-03-22

This migration splits the JSON fields in tenants table into separate tables:
- tenant_quotas: quota configuration
- tenant_settings: tenant settings

This improves query performance and data integrity.

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "007_split_tenant_json_fields"
down_revision: Union[str, None] = "006_add_foreign_keys"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade database schema."""
    # Create tenant_quotas table
    op.create_table(
        "tenant_quotas",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "tenant_id",
            sa.Integer(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("daily_token_limit", sa.Integer(), server_default="1000000"),
        sa.Column("monthly_token_limit", sa.Integer(), server_default="30000000"),
        sa.Column("daily_request_limit", sa.Integer(), server_default="10000"),
        sa.Column("monthly_request_limit", sa.Integer(), server_default="300000"),
        sa.Column("max_users", sa.Integer(), server_default="100"),
        sa.Column("max_sessions_per_user", sa.Integer(), server_default="5"),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("idx_tenant_quotas_tenant", "tenant_quotas", ["tenant_id"])

    # Create tenant_settings table
    op.create_table(
        "tenant_settings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "tenant_id",
            sa.Integer(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("content_filter_enabled", sa.Integer(), server_default="1"),
        sa.Column("audit_log_enabled", sa.Integer(), server_default="1"),
        sa.Column("audit_log_retention_days", sa.Integer(), server_default="90"),
        sa.Column("data_retention_days", sa.Integer(), server_default="365"),
        sa.Column("sso_enabled", sa.Integer(), server_default="0"),
        sa.Column("sso_provider", sa.String(50)),
        sa.Column("custom_branding", sa.Integer(), server_default="0"),
        sa.Column("branding_name", sa.String(100)),
        sa.Column("branding_logo_url", sa.String(500)),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("idx_tenant_settings_tenant", "tenant_settings", ["tenant_id"])


def downgrade() -> None:
    """Downgrade database schema."""
    op.drop_index("idx_tenant_settings_tenant", "tenant_settings")
    op.drop_table("tenant_settings")

    op.drop_index("idx_tenant_quotas_tenant", "tenant_quotas")
    op.drop_table("tenant_quotas")
