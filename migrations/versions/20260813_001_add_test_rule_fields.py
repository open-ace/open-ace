"""add test rule fields

Revision ID: 20260813_001
Revises: 20260812_001_add_token_version
Create Date: 2026-08-13

为 content_filter_rules 表添加测试规则相关字段：
- is_test: 是否为测试规则
- approval_status: 审批状态
- approved_by: 审批人
- approved_at: 审批时间
- created_by: 创建人
- priority: 规则优先级
- tenant_id: 租户ID
- valid_from: 规则生效时间（可选）
- valid_until: 规则失效时间（可选）
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision = '20260813_001'
down_revision = '20260812_001_add_token_version'
branch_labels = None
depends_on = None


def upgrade():
    """添加测试规则相关字段"""
    # 检查是否为 PostgreSQL
    bind = op.get_bind()
    is_postgres = bind.dialect.name == 'postgresql'

    # 1. 添加 is_test 字段
    op.add_column(
        'content_filter_rules',
        sa.Column('is_test', sa.Boolean(), nullable=True, server_default='0')
    )

    # 2. 添加 approval_status 字段
    op.add_column(
        'content_filter_rules',
        sa.Column('approval_status', sa.String(20), nullable=True, server_default='approved')
    )

    # 3. 添加 approved_by 字段
    op.add_column(
        'content_filter_rules',
        sa.Column('approved_by', sa.Integer(), nullable=True)
    )

    # 4. 添加 approved_at 字段
    op.add_column(
        'content_filter_rules',
        sa.Column('approved_at', sa.DateTime(), nullable=True)
    )

    # 5. 添加 created_by 字段
    op.add_column(
        'content_filter_rules',
        sa.Column('created_by', sa.Integer(), nullable=True)
    )

    # 6. 添加 priority 字段
    op.add_column(
        'content_filter_rules',
        sa.Column('priority', sa.Integer(), nullable=True, server_default='100')
    )

    # 7. 添加 tenant_id 字段
    op.add_column(
        'content_filter_rules',
        sa.Column('tenant_id', sa.Integer(), nullable=True)
    )

    # 8. 添加 valid_from 字段（可选）
    op.add_column(
        'content_filter_rules',
        sa.Column('valid_from', sa.DateTime(), nullable=True)
    )

    # 9. 添加 valid_until 字段（可选）
    op.add_column(
        'content_filter_rules',
        sa.Column('valid_until', sa.DateTime(), nullable=True)
    )

    # 10. 标记现有测试规则（ID 2-5）
    # 根据 Issue #2550，这些是测试规则
    op.execute("""
        UPDATE content_filter_rules
        SET is_test = 1,
            approval_status = 'approved'
        WHERE id IN (2, 3, 4, 5)
    """)

    # 11. 确保系统默认规则为已审批状态
    op.execute("""
        UPDATE content_filter_rules
        SET approval_status = 'approved',
            approved_at = created_at
        WHERE id NOT IN (2, 3, 4, 5)
          AND approval_status IS NULL
    """)

    # 12. 添加索引以优化查询
    if is_postgres:
        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_filter_rules_is_test
            ON content_filter_rules(is_test)
        """)
        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_filter_rules_approval_status
            ON content_filter_rules(approval_status)
        """)
        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_filter_rules_priority
            ON content_filter_rules(priority)
        """)
        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_filter_rules_tenant_id
            ON content_filter_rules(tenant_id)
        """)
    else:
        # SQLite
        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_filter_rules_is_test
            ON content_filter_rules(is_test)
        """)
        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_filter_rules_approval_status
            ON content_filter_rules(approval_status)
        """)
        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_filter_rules_priority
            ON content_filter_rules(priority)
        """)
        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_filter_rules_tenant_id
            ON content_filter_rules(tenant_id)
        """)


def downgrade():
    """回滚迁移"""
    # 删除索引
    bind = op.get_bind()
    is_postgres = bind.dialect.name == 'postgresql'

    op.execute("DROP INDEX IF EXISTS idx_filter_rules_is_test")
    op.execute("DROP INDEX IF EXISTS idx_filter_rules_approval_status")
    op.execute("DROP INDEX IF EXISTS idx_filter_rules_priority")
    op.execute("DROP INDEX IF EXISTS idx_filter_rules_tenant_id")

    # 删除字段
    op.drop_column('content_filter_rules', 'valid_until')
    op.drop_column('content_filter_rules', 'valid_from')
    op.drop_column('content_filter_rules', 'tenant_id')
    op.drop_column('content_filter_rules', 'priority')
    op.drop_column('content_filter_rules', 'created_by')
    op.drop_column('content_filter_rules', 'approved_at')
    op.drop_column('content_filter_rules', 'approved_by')
    op.drop_column('content_filter_rules', 'approval_status')
    op.drop_column('content_filter_rules', 'is_test')