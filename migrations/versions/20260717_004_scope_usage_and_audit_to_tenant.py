"""Scope usage and audit records to tenants

Revision ID: 20260717_004_scope_usage_and_audit_to_tenant
Revises: 20260717_003_add_project_tenant_scope
Create Date: 2026-07-17

Issue: #1760

Add tenant attribution to daily_usage and audit_logs so governance and usage
queries can enforce tenant boundaries inside the data layer.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260717_004_scope_usage_and_audit_to_tenant"
down_revision: str | None = "20260717_003_add_project_tenant_scope"
branch_labels: str | None = None
depends_on: str | None = None


def _column_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table_name)}


def _index_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _unique_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {constraint["name"] for constraint in inspector.get_unique_constraints(table_name)}


def upgrade() -> None:
    """Persist tenant_id on daily_usage and audit_logs."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    daily_usage_columns = _column_names(inspector, "daily_usage")
    if "tenant_id" not in daily_usage_columns:
        op.add_column(
            "daily_usage",
            sa.Column("tenant_id", sa.Integer(), nullable=False, server_default="1"),
        )

    audit_log_columns = _column_names(sa.inspect(conn), "audit_logs")
    if "tenant_id" not in audit_log_columns:
        op.add_column("audit_logs", sa.Column("tenant_id", sa.Integer(), nullable=True))

    conn.execute(
        sa.text(
            """
            UPDATE audit_logs
            SET tenant_id = (
                SELECT users.tenant_id
                FROM users
                WHERE users.id = audit_logs.user_id
            )
            WHERE tenant_id IS NULL AND user_id IS NOT NULL
            """
        )
    )

    usage_indexes = _index_names(sa.inspect(conn), "daily_usage")
    usage_uniques = _unique_names(sa.inspect(conn), "daily_usage")
    is_sqlite = conn.dialect.name == "sqlite"

    if "uq_daily_usage_date_tool_host" in usage_uniques:
        op.drop_constraint("uq_daily_usage_date_tool_host", "daily_usage", type_="unique")
    elif "uq_daily_usage_date_tool_host" in usage_indexes:
        op.drop_index("uq_daily_usage_date_tool_host", table_name="daily_usage")

    if "idx_usage_date_tool_host" in usage_indexes:
        op.drop_index("idx_usage_date_tool_host", table_name="daily_usage")

    if is_sqlite:
        op.create_index(
            "uq_daily_usage_date_tool_host",
            "daily_usage",
            ["tenant_id", "date", "tool_name", "host_name"],
            unique=True,
        )
    else:
        op.create_unique_constraint(
            "uq_daily_usage_date_tool_host",
            "daily_usage",
            ["tenant_id", "date", "tool_name", "host_name"],
        )
    op.create_index(
        "idx_usage_date_tool_host",
        "daily_usage",
        ["tenant_id", "date", "tool_name", "host_name"],
        unique=False,
    )

    usage_indexes = _index_names(sa.inspect(conn), "daily_usage")
    if "idx_usage_tenant_date" not in usage_indexes:
        op.create_index("idx_usage_tenant_date", "daily_usage", ["tenant_id", "date"], unique=False)

    audit_indexes = _index_names(sa.inspect(conn), "audit_logs")
    if "idx_audit_tenant_id" not in audit_indexes:
        op.create_index("idx_audit_tenant_id", "audit_logs", ["tenant_id"], unique=False)


def downgrade() -> None:
    """Remove tenant attribution from daily_usage and audit_logs."""
    conn = op.get_bind()

    audit_indexes = _index_names(sa.inspect(conn), "audit_logs")
    if "idx_audit_tenant_id" in audit_indexes:
        op.drop_index("idx_audit_tenant_id", table_name="audit_logs")

    audit_columns = _column_names(sa.inspect(conn), "audit_logs")
    if "tenant_id" in audit_columns:
        if conn.dialect.name == "postgresql":
            op.drop_column("audit_logs", "tenant_id")
        else:
            with op.batch_alter_table("audit_logs") as batch_op:
                batch_op.drop_column("tenant_id")

    usage_indexes = _index_names(sa.inspect(conn), "daily_usage")
    usage_uniques = _unique_names(sa.inspect(conn), "daily_usage")
    is_sqlite = conn.dialect.name == "sqlite"

    if "idx_usage_tenant_date" in usage_indexes:
        op.drop_index("idx_usage_tenant_date", table_name="daily_usage")
    if "idx_usage_date_tool_host" in usage_indexes:
        op.drop_index("idx_usage_date_tool_host", table_name="daily_usage")
    if "uq_daily_usage_date_tool_host" in usage_uniques:
        op.drop_constraint("uq_daily_usage_date_tool_host", "daily_usage", type_="unique")
    elif "uq_daily_usage_date_tool_host" in usage_indexes:
        op.drop_index("uq_daily_usage_date_tool_host", table_name="daily_usage")

    if is_sqlite:
        op.create_index(
            "uq_daily_usage_date_tool_host",
            "daily_usage",
            ["date", "tool_name", "host_name"],
            unique=True,
        )
    else:
        op.create_unique_constraint(
            "uq_daily_usage_date_tool_host",
            "daily_usage",
            ["date", "tool_name", "host_name"],
        )
    op.create_index(
        "idx_usage_date_tool_host",
        "daily_usage",
        ["date", "tool_name", "host_name"],
        unique=False,
    )

    usage_columns = _column_names(sa.inspect(conn), "daily_usage")
    if "tenant_id" in usage_columns:
        if conn.dialect.name == "postgresql":
            op.drop_column("daily_usage", "tenant_id")
        else:
            with op.batch_alter_table("daily_usage") as batch_op:
                batch_op.drop_column("tenant_id")
