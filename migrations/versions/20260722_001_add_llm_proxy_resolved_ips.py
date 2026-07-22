"""Add resolved_ips and resolved_at columns to api_key_store

Revision ID: 20260722_001_add_llm_proxy_resolved_ips
Revises: 20260721_001_add_ci_diagnostics_attempts
Create Date: 2026-07-22

Issue: #1894
Adds IP pinning fields to api_key_store for SSRF protection:
- resolved_ips: comma-separated public IPs resolved at config time
- resolved_at: timestamp when IPs were resolved
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260722_001_add_llm_proxy_resolved_ips"
down_revision: str | None = "20260721_001_add_ci_diagnostics_attempts"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add resolved_ips and resolved_at columns to api_key_store."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    if "api_key_store" not in set(inspector.get_table_names()):
        # Table doesn't exist, nothing to migrate
        return

    existing_columns = {col["name"] for col in inspector.get_columns("api_key_store")}

    if "resolved_ips" not in existing_columns:
        op.add_column(
            "api_key_store",
            sa.Column("resolved_ips", sa.Text(), nullable=True),
        )

    if "resolved_at" not in existing_columns:
        op.add_column(
            "api_key_store",
            sa.Column("resolved_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    """Remove resolved_ips and resolved_at columns from api_key_store."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    if "api_key_store" not in set(inspector.get_table_names()):
        return

    existing_columns = {col["name"] for col in inspector.get_columns("api_key_store")}

    if "resolved_ips" in existing_columns:
        op.drop_column("api_key_store", "resolved_ips")

    if "resolved_at" in existing_columns:
        op.drop_column("api_key_store", "resolved_at")