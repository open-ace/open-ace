"""Add sensitive keyword config to tenant_settings

Revision ID: 20260720_002_add_sensitive_keyword_config
Revises: 20260720_001_backfill_notenant_users
Create Date: 2026-07-20

Issue: #1904

Add block_sensitive_keyword and sensitive_keyword_match_mode columns to
tenant_settings table for configurable sensitive keyword filtering.

- block_sensitive_keyword: Whether to block requests containing sensitive keywords
- sensitive_keyword_match_mode: Matching algorithm ('word_boundary' or 'substring')
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260720_002_add_sensitive_keyword_config"
down_revision: str | None = "20260720_001_backfill_notenant_users"
branch_labels: str | None = None
depends_on: str | None = None


def _column_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    """Add sensitive keyword config columns to tenant_settings."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    is_postgres = conn.dialect.name == "postgresql"

    columns = _column_names(inspector, "tenant_settings")

    # Add block_sensitive_keyword column
    if "block_sensitive_keyword" not in columns:
        if is_postgres:
            op.add_column(
                "tenant_settings",
                sa.Column("block_sensitive_keyword", sa.Boolean(), nullable=True, server_default="false"),
            )
        else:
            op.add_column(
                "tenant_settings",
                sa.Column("block_sensitive_keyword", sa.Integer(), nullable=True, server_default="0"),
            )

    # Add sensitive_keyword_match_mode column
    if "sensitive_keyword_match_mode" not in columns:
        if is_postgres:
            op.add_column(
                "tenant_settings",
                sa.Column(
                    "sensitive_keyword_match_mode",
                    sa.String(50),
                    nullable=True,
                    server_default="word_boundary",
                ),
            )
        else:
            op.add_column(
                "tenant_settings",
                sa.Column(
                    "sensitive_keyword_match_mode",
                    sa.String(50),
                    nullable=True,
                    server_default="word_boundary",
                ),
            )


def downgrade() -> None:
    """Remove sensitive keyword config columns from tenant_settings."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = _column_names(inspector, "tenant_settings")

    if "sensitive_keyword_match_mode" in columns:
        op.drop_column("tenant_settings", "sensitive_keyword_match_mode")

    if "block_sensitive_keyword" in columns:
        op.drop_column("tenant_settings", "block_sensitive_keyword")
