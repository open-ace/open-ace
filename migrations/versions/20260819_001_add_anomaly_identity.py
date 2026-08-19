"""Add anomaly_id and tenant_id to anomaly_status

Revision ID: 20260819_001_add_anomaly_identity
Revises: 20260721_003_add_sensitive_keyword_config
Create Date: 2026-08-19

Issue: #2749

The previous anomaly_status identity (anomaly_type + affected_users_hash)
was too coarse – it collapsed multiple independent anomaly instances
(e.g. rapid_activity in different hours) into a single status row,
causing historical state to pollute new anomalies.

Schema changes:
  - Add ``anomaly_id TEXT NOT NULL DEFAULT ''`` column.
  - Add ``tenant_id INTEGER`` column.
  - Create partial unique index on ``anomaly_id`` (non-empty only).

Legacy rows keep anomaly_id = '' and are ignored by the application
when matching against newly detected anomalies.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260819_001_add_anomaly_identity"
down_revision: str | None = "20260818_002_add_tool_account_mapping_fields"
branch_labels: str | None = None
depends_on: str | None = None


def _column_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    """Add anomaly_id and tenant_id columns to anomaly_status."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    is_postgres = conn.dialect.name == "postgresql"

    columns = _column_names(inspector, "anomaly_status")

    if "anomaly_id" not in columns:
        if is_postgres:
            op.add_column(
                "anomaly_status",
                sa.Column(
                    "anomaly_id",
                    sa.String(64),
                    nullable=False,
                    server_default="",
                ),
            )
        else:
            op.add_column(
                "anomaly_status",
                sa.Column(
                    "anomaly_id",
                    sa.Text(),
                    nullable=False,
                    server_default="",
                ),
            )

    if "tenant_id" not in columns:
        op.add_column(
            "anomaly_status",
            sa.Column("tenant_id", sa.Integer(), nullable=True),
        )

    # Partial unique index on anomaly_id (skip empty strings so that
    # legacy rows do not collide).
    indexes = {idx["name"] for idx in inspector.get_indexes("anomaly_status")}
    if "ix_anomaly_status_anomaly_id" not in indexes:
        if is_postgres:
            op.create_index(
                "ix_anomaly_status_anomaly_id",
                "anomaly_status",
                ["anomaly_id"],
                unique=True,
                postgresql_where=sa.text("anomaly_id <> ''"),
            )
        else:
            op.create_index(
                "ix_anomaly_status_anomaly_id",
                "anomaly_status",
                ["anomaly_id"],
                unique=True,
                sqlite_where=sa.text("anomaly_id != ''"),
            )

    # Issue #2748: Add composite unique index including tenant_id
    # This allows same anomaly_key to have different states per tenant
    if "ix_anomaly_status_type_hash_tenant" not in indexes:
        op.create_index(
            "ix_anomaly_status_type_hash_tenant",
            "anomaly_status",
            ["anomaly_type", "affected_users_hash", "tenant_id"],
            unique=True,
        )


def downgrade() -> None:
    """Remove anomaly_id and tenant_id columns from anomaly_status."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    indexes = {idx["name"] for idx in inspector.get_indexes("anomaly_status")}
    if "ix_anomaly_status_anomaly_id" in indexes:
        op.drop_index("ix_anomaly_status_anomaly_id", table_name="anomaly_status")
    if "ix_anomaly_status_type_hash_tenant" in indexes:
        op.drop_index("ix_anomaly_status_type_hash_tenant", table_name="anomaly_status")

    columns = _column_names(inspector, "anomaly_status")
    if "tenant_id" in columns:
        op.drop_column("anomaly_status", "tenant_id")
    if "anomaly_id" in columns:
        op.drop_column("anomaly_status", "anomaly_id")
