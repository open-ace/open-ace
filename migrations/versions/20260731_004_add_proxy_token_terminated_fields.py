"""Add terminated_at and termination_reason to proxy_token_jtis

Revision ID: 20260731_004_add_proxy_token_terminated_fields
Revises: 20260731_003_add_teams_sync_source_indexes
Create Date: 2026-07-31

Issue: #1822
Track token termination state for audit and cleanup purposes.
When a single-use proxy token expires without being consumed, mark it
as 'terminated' to distinguish from 'consumed' for audit queries.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260731_004_add_proxy_token_terminated_fields"
down_revision: str | None = "20260731_003_add_teams_sync_source_indexes"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add terminated_at and termination_reason columns."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    existing_columns = {col["name"] for col in inspector.get_columns("proxy_token_jtis")}

    if "terminated_at" not in existing_columns:
        op.add_column(
            "proxy_token_jtis",
            sa.Column("terminated_at", sa.DateTime(), nullable=True),
        )

    if "termination_reason" not in existing_columns:
        op.add_column(
            "proxy_token_jtis",
            sa.Column("termination_reason", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    """Remove terminated_at and termination_reason columns."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    existing_columns = {col["name"] for col in inspector.get_columns("proxy_token_jtis")}

    if "termination_reason" in existing_columns:
        op.drop_column("proxy_token_jtis", "termination_reason")

    if "terminated_at" in existing_columns:
        op.drop_column("proxy_token_jtis", "terminated_at")
