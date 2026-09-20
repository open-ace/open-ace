"""add content filter rule enhancements

Revision ID: 20260918_001
Revises: 20260911_002_add_users_system_uid
Create Date: 2026-09-18

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "20260918_001"
down_revision: str | None = "20260911_002_add_users_system_uid"
branch_labels: str | None = None
depends_on: str | None = None


def _column_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table_name)}


def _index_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    """Add content filter rule enhancements with idempotent column additions."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    columns = _column_names(inspector, "content_filter_rules")

    # 新增字段（条件检查）
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

    # 创建索引
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

    # 新增约束
    with op.batch_alter_table("content_filter_rules", schema=None) as batch_op:
        batch_op.create_check_constraint(
            "chk_system_rule_immutable", "source != 'system' OR is_test = FALSE"
        )
        batch_op.create_check_constraint(
            "chk_approval_status_valid", "approval_status IN ('pending', 'approved', 'rejected')"
        )

    # 标记系统默认规则（通过 description 字段识别）
    op.execute("""
        UPDATE content_filter_rules
        SET source = 'system', is_test = FALSE, priority = 1
        WHERE description IN (
            'PII Email Detection',
            'PII Phone Detection',
            'Credit Card Detection',
            'Password Exposure',
            'API Key Exposure',
            'Secret Exposure',
            'SSN Detection'
        )
    """)

    # 创建触发统计表
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


def downgrade():
    # 删除触发统计表
    op.drop_index("idx_filter_rule_trigger_stats_rule_id", "filter_rule_trigger_stats")
    op.drop_table("filter_rule_trigger_stats")

    # 删除约束
    with op.batch_alter_table("content_filter_rules", schema=None) as batch_op:
        batch_op.drop_constraint("chk_approval_status_valid")
        batch_op.drop_constraint("chk_system_rule_immutable")

    # 删除索引
    op.drop_index("idx_content_filter_rules_priority", "content_filter_rules")
    op.drop_index("idx_content_filter_rules_approval_status", "content_filter_rules")
    op.drop_index("idx_content_filter_rules_source", "content_filter_rules")
    op.drop_index("idx_content_filter_rules_is_test", "content_filter_rules")
    op.drop_index("idx_content_filter_rules_tenant_id", "content_filter_rules")

    # 删除字段
    with op.batch_alter_table("content_filter_rules", schema=None) as batch_op:
        batch_op.drop_column("valid_until")
        batch_op.drop_column("valid_from")
        batch_op.drop_column("created_by")
        batch_op.drop_column("approved_at")
        batch_op.drop_column("approved_by")
        batch_op.drop_column("priority")
        batch_op.drop_column("approval_status")
        batch_op.drop_column("tenant_id")
        batch_op.drop_column("source")
        batch_op.drop_column("is_test")
