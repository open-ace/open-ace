"""add content filter rule enhancements

Revision ID: 20260918_001
Revises: 20260827_001_add_feishu_verification_status
Create Date: 2026-09-18

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "20260918_001"
down_revision = "20260827_001_add_feishu_verification_status"
branch_labels = None
depends_on = None


def upgrade():
    # 新增字段
    with op.batch_alter_table("content_filter_rules", schema=None) as batch_op:
        batch_op.add_column(sa.Column("is_test", sa.Boolean(), nullable=False, server_default="0"))
        batch_op.add_column(
            sa.Column("source", sa.String(20), nullable=False, server_default="manual")
        )
        batch_op.add_column(sa.Column("tenant_id", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("approval_status", sa.String(20), nullable=False, server_default="approved")
        )
        batch_op.add_column(
            sa.Column("priority", sa.Integer(), nullable=False, server_default="100")
        )
        batch_op.add_column(sa.Column("approved_by", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("approved_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("created_by", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("valid_from", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("valid_until", sa.DateTime(), nullable=True))

    # 创建索引
    op.create_index("idx_content_filter_rules_tenant_id", "content_filter_rules", ["tenant_id"])
    op.create_index("idx_content_filter_rules_is_test", "content_filter_rules", ["is_test"])
    op.create_index("idx_content_filter_rules_source", "content_filter_rules", ["source"])
    op.create_index(
        "idx_content_filter_rules_approval_status", "content_filter_rules", ["approval_status"]
    )
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
