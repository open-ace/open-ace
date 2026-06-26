"""Fix tenant_quotas integer overflow for PostgreSQL

Revision ID: 20260626_004_fix_tenant_quotas_overflow
Revises: 001_add_project_categories
Create Date: 2026-06-26

Issue: #1259
Enterprise plan monthly_token_limit (3,000,000,000) exceeds PostgreSQL integer
max value (2,147,483,647), causing overflow errors when inserting tenant_quotas.

This migration changes daily_token_limit and monthly_token_limit from integer
to bigint, supporting larger quota values for enterprise-tier tenants.
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

revision: str = "20260626_004_fix_tenant_quotas_overflow"
down_revision: str | None = "001_add_project_categories"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Change tenant_quotas token limit columns to bigint for PostgreSQL."""
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return

    op.execute(
        """
        ALTER TABLE tenant_quotas
        ALTER COLUMN daily_token_limit TYPE bigint
        """
    )
    op.execute(
        """
        ALTER TABLE tenant_quotas
        ALTER COLUMN monthly_token_limit TYPE bigint
        """
    )


def downgrade() -> None:
    """Revert tenant_quotas token limit columns to integer for PostgreSQL."""
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return

    op.execute(
        """
        ALTER TABLE tenant_quotas
        ALTER COLUMN daily_token_limit TYPE integer
        USING daily_token_limit::integer
        """
    )
    op.execute(
        """
        ALTER TABLE tenant_quotas
        ALTER COLUMN monthly_token_limit TYPE integer
        USING monthly_token_limit::integer
        """
    )
