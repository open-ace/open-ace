"""revert content filter rule enhancements

Revision ID: 20260921_001
Revises: 20260918_002_normalize_audit_log_success
Create Date: 2026-09-21

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260921_001"
down_revision: str | None = "20260918_002_normalize_audit_log_success"
branch_labels: str | None = None
depends_on: str | None = None


def _column_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table_name)}


def _index_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _table_names(inspector: sa.Inspector) -> set[str]:
    return set(inspector.get_table_names())


def upgrade() -> None:
    """Revert content filter rule enhancements with idempotent operations."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    columns = _column_names(inspector, "content_filter_rules")
    indexes = _index_names(inspector, "content_filter_rules")
    tables = _table_names(inspector)

    # 删除新增索引（条件检查）
    if "idx_content_filter_rules_tenant_id" in indexes:
        op.drop_index("idx_content_filter_rules_tenant_id", "content_filter_rules")
    if "idx_content_filter_rules_is_test" in indexes:
        op.drop_index("idx_content_filter_rules_is_test", "content_filter_rules")
    if "idx_content_filter_rules_source" in indexes:
        op.drop_index("idx_content_filter_rules_source", "content_filter_rules")
    if "idx_content_filter_rules_approval_status" in indexes:
        op.drop_index("idx_content_filter_rules_approval_status", "content_filter_rules")
    if "idx_content_filter_rules_priority" in indexes:
        op.drop_index("idx_content_filter_rules_priority", "content_filter_rules")

    # 删除新增约束（仅 PostgreSQL 需要）
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        # PostgreSQL: 使用 DROP CONSTRAINT IF EXISTS
        conn.execute(
            sa.text(
                "ALTER TABLE content_filter_rules "
                "DROP CONSTRAINT IF EXISTS chk_system_rule_immutable"
            )
        )
        conn.execute(
            sa.text(
                "ALTER TABLE content_filter_rules "
                "DROP CONSTRAINT IF EXISTS chk_approval_status_valid"
            )
        )

    # 删除新增字段（条件检查：只在字段存在时才删除）
    fields_to_drop = [
        "is_test",
        "source",
        "tenant_id",
        "approval_status",
        "priority",
        "approved_by",
        "approved_at",
        "created_by",
        "valid_from",
        "valid_until",
    ]
    fields_present = [f for f in fields_to_drop if f in columns]

    if fields_present:
        # 只有在需要删除字段时才创建 batch_alter_table
        with op.batch_alter_table("content_filter_rules", schema=None) as batch_op:
            for field in fields_present:
                batch_op.drop_column(field)

    # 删除触发统计表（条件检查）
    if "filter_rule_trigger_stats" in tables:
        indexes_stats = _index_names(inspector, "filter_rule_trigger_stats")
        if "idx_filter_rule_trigger_stats_rule_id" in indexes_stats:
            op.drop_index("idx_filter_rule_trigger_stats_rule_id", "filter_rule_trigger_stats")
        op.drop_table("filter_rule_trigger_stats")


def downgrade() -> None:
    """Re-apply content filter rule enhancements."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    columns = _column_names(inspector, "content_filter_rules")

    # 重新添加字段
    with op.batch_alter_table("content_filter_rules", schema=None) as batch_op:
        if "is_test" not in columns:
            batch_op.add_column(
                sa.Column("is_test", sa.Boolean(), nullable=False, server_default="0")
            )
        if "source" not in columns:
            batch_op.add_column(
                sa.Column("source", sa.String(20), nullable=False, server_default="manual")
            )
        if "tenant_id" not in columns:
            batch_op.add_column(sa.Column("tenant_id", sa.Integer(), nullable=True))
        if "approval_status" not in columns:
            batch_op.add_column(
                sa.Column(
                    "approval_status", sa.String(20), nullable=False, server_default="approved"
                )
            )
        if "priority" not in columns:
            batch_op.add_column(
                sa.Column("priority", sa.Integer(), nullable=False, server_default="100")
            )
        if "approved_by" not in columns:
            batch_op.add_column(sa.Column("approved_by", sa.Integer(), nullable=True))
        if "approved_at" not in columns:
            batch_op.add_column(sa.Column("approved_at", sa.DateTime(), nullable=True))
        if "created_by" not in columns:
            batch_op.add_column(sa.Column("created_by", sa.Integer(), nullable=True))
        if "valid_from" not in columns:
            batch_op.add_column(sa.Column("valid_from", sa.DateTime(), nullable=True))
        if "valid_until" not in columns:
            batch_op.add_column(sa.Column("valid_until", sa.DateTime(), nullable=True))

    # 重新创建索引
    indexes = _index_names(inspector, "content_filter_rules")
    if "idx_content_filter_rules_tenant_id" not in indexes:
        op.create_index("idx_content_filter_rules_tenant_id", "content_filter_rules", ["tenant_id"])
    if "idx_content_filter_rules_is_test" not in indexes:
        op.create_index("idx_content_filter_rules_is_test", "content_filter_rules", ["is_test"])
    if "idx_content_filter_rules_source" not in indexes:
        op.create_index("idx_content_filter_rules_source", "content_filter_rules", ["source"])
    if "idx_content_filter_rules_approval_status" not in indexes:
        op.create_index(
            "idx_content_filter_rules_approval_status", "content_filter_rules", ["approval_status"]
        )
    if "idx_content_filter_rules_priority" not in indexes:
        op.create_index("idx_content_filter_rules_priority", "content_filter_rules", ["priority"])

    # 重新创建约束
    with op.batch_alter_table("content_filter_rules", schema=None) as batch_op:
        batch_op.create_check_constraint(
            "chk_system_rule_immutable", "source != 'system' OR is_test = FALSE"
        )
        batch_op.create_check_constraint(
            "chk_approval_status_valid", "approval_status IN ('pending', 'approved', 'rejected')"
        )

    # 重新创建触发统计表
    existing_tables = set(inspector.get_table_names())
    if "filter_rule_trigger_stats" not in existing_tables:
        op.create_table(
            "filter_rule_trigger_stats",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "rule_id",
                sa.Integer(),
                sa.ForeignKey("content_filter_rules.id", ondelete="CASCADE"),
                unique=True,
                nullable=False,
            ),
            sa.Column("trigger_count", sa.BigInteger(), server_default="0"),
            sa.Column("last_triggered_at", sa.DateTime(), nullable=True),
        )
        op.create_index(
            "idx_filter_rule_trigger_stats_rule_id", "filter_rule_trigger_stats", ["rule_id"]
        )
