"""add rate limit log table

Revision ID: 20260814_002
Revises: 20260814_001
Create Date: 2026-08-14

创建速率限制日志表，用于多进程环境下的速率限制。

注意：此表会随请求增长，建议：
1. 在生产环境配置 Redis 时优先使用 Redis 后端
2. 定期运行清理任务：DELETE FROM rate_limit_log WHERE timestamp < <now-window>
3. 或添加数据库级 TTL 机制（PostgreSQL: pg_cron, SQLite: 外部脚本）
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260814_002"
down_revision = "20260814_001"
branch_labels = None
depends_on = None


def upgrade():
    """创建速率限制日志表"""
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    # 创建 rate_limit_log 表
    if "rate_limit_log" not in existing_tables:
        op.create_table(
            "rate_limit_log",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("key", sa.String(255), nullable=False),
            sa.Column(
                "timestamp",
                sa.Float(),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
        )

    # 创建索引
    rate_limit_indexes = {idx["name"] for idx in inspector.get_indexes("rate_limit_log")}
    if "idx_rate_limit_key_timestamp" not in rate_limit_indexes:
        if is_postgres:
            op.execute("""
                CREATE INDEX idx_rate_limit_key_timestamp
                ON rate_limit_log(key, timestamp)
            """)
        else:
            op.create_index(
                "idx_rate_limit_key_timestamp",
                "rate_limit_log",
                ["key", "timestamp"],
            )
    if "idx_rate_limit_timestamp" not in rate_limit_indexes:
        if is_postgres:
            op.execute("""
                CREATE INDEX idx_rate_limit_timestamp
                ON rate_limit_log(timestamp)
            """)
        else:
            op.create_index(
                "idx_rate_limit_timestamp",
                "rate_limit_log",
                ["timestamp"],
            )


def downgrade():
    """回滚迁移"""
    op.drop_index("idx_rate_limit_timestamp", table_name="rate_limit_log")
    op.drop_index("idx_rate_limit_key_timestamp", table_name="rate_limit_log")
    op.drop_table("rate_limit_log")