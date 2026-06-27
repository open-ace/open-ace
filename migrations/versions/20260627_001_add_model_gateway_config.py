"""add model gateway config

Revision ID: 20260627_001_add_model_gateway_config
Revises: 20260626_004_fix_tenant_quotas_overflow
Create Date: 2026-06-27

Adds the single admin row that configures the optional LiteLLM-compatible model
gateway: base URL, encrypted gateway API key, and model-prefix options. The
runtime DDL mirror lives in
app/modules/workspace/model_gateway/__init__.py:get_ddl_statements().
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "20260627_001_add_model_gateway_config"
down_revision: str | None = "20260626_004_fix_tenant_quotas_overflow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the model_gateway_config table (single admin row)."""
    op.create_table(
        "model_gateway_config",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("mode", sa.Text, server_default="direct"),
        sa.Column("base_url", sa.Text),
        sa.Column("encrypted_api_key", sa.Text),
        sa.Column("encryption_version", sa.Integer, server_default="1"),
        sa.Column("model_prefix_mode", sa.Boolean, server_default=sa.text("0")),
        sa.Column("model_prefix", sa.Text),
        sa.Column("created_by", sa.Integer),
        sa.Column("created_at", sa.TIMESTAMP, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.TIMESTAMP, server_default=sa.text("CURRENT_TIMESTAMP")),
    )


def downgrade() -> None:
    """Drop the model_gateway_config table."""
    op.drop_table("model_gateway_config")
