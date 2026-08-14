"""add approval and trigger log tables

Revision ID: 20260813_002
Revises: 20260813_001
Create Date: 2026-08-13

创建审批流程和触发日志相关表：
- filter_rule_approval_log: 审批日志表
- filter_rule_trigger_log: 触发日志表
- filter_rule_versions: 规则版本快照表
- rule_cache_sync: 缓存同步通知表
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "20260813_002"
down_revision = "20260813_001"
branch_labels = None
depends_on = None


def upgrade():
    """创建审批和触发日志相关表"""
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    # 1. 创建 filter_rule_approval_log 表
    if "filter_rule_approval_log" not in existing_tables:
        op.create_table(
            "filter_rule_approval_log",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("rule_id", sa.Integer(), nullable=False),
            sa.Column("action", sa.String(20), nullable=False),
            sa.Column("actor_user_id", sa.Integer(), nullable=False),
            sa.Column("actor_username", sa.String(128), nullable=True),
            sa.Column(
                "timestamp",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.current_timestamp(),
            ),
            sa.Column("details", JSONB if is_postgres else sa.JSON(), nullable=True),
            sa.Column("tenant_id", sa.Integer(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    # 创建索引（无论表是刚创建还是已存在）
    approval_log_indexes = {
        idx["name"] for idx in inspector.get_indexes("filter_rule_approval_log")
    }
        if "idx_approval_log_rule_time" not in approval_log_indexes:
            if is_postgres:
                op.execute("""
                    CREATE INDEX idx_approval_log_rule_time
                    ON filter_rule_approval_log(rule_id, timestamp)
                """)
            else:
                op.create_index(
                    "idx_approval_log_rule_time",
                    "filter_rule_approval_log",
                    ["rule_id", "timestamp"],
                )
        if "idx_approval_log_tenant_time" not in approval_log_indexes:
            if is_postgres:
                op.execute("""
                    CREATE INDEX idx_approval_log_tenant_time
                    ON filter_rule_approval_log(tenant_id, timestamp)
                """)
            else:
                op.create_index(
                    "idx_approval_log_tenant_time",
                    "filter_rule_approval_log",
                    ["tenant_id", "timestamp"],
                )

    # 2. 创建 filter_rule_trigger_log 表
    if "filter_rule_trigger_log" not in existing_tables:
        op.create_table(
            "filter_rule_trigger_log",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("rule_id", sa.Integer(), nullable=False),
            sa.Column("matched_content_hash", sa.String(64), nullable=True),
            sa.Column(
                "matched_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.current_timestamp(),
            ),
            sa.Column("action_taken", sa.String(20), nullable=True),
            sa.Column("session_id", sa.String(128), nullable=True),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("tenant_id", sa.Integer(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    # 创建索引（无论表是刚创建还是已存在）
    trigger_log_indexes = {
        idx["name"] for idx in inspector.get_indexes("filter_rule_trigger_log")
    }
        if "idx_trigger_log_rule_time" not in trigger_log_indexes:
            if is_postgres:
                op.execute("""
                    CREATE INDEX idx_trigger_log_rule_time
                    ON filter_rule_trigger_log(rule_id, matched_at)
                """)
            else:
                op.create_index(
                    "idx_trigger_log_rule_time",
                    "filter_rule_trigger_log",
                    ["rule_id", "matched_at"],
                )
        if "idx_trigger_log_user_time" not in trigger_log_indexes:
            if is_postgres:
                op.execute("""
                    CREATE INDEX idx_trigger_log_user_time
                    ON filter_rule_trigger_log(user_id, matched_at)
                """)
            else:
                op.create_index(
                    "idx_trigger_log_user_time",
                    "filter_rule_trigger_log",
                    ["user_id", "matched_at"],
                )
        if "idx_trigger_log_tenant_time" not in trigger_log_indexes:
            if is_postgres:
                op.execute("""
                    CREATE INDEX idx_trigger_log_tenant_time
                    ON filter_rule_trigger_log(tenant_id, matched_at)
                """)
            else:
                op.create_index(
                    "idx_trigger_log_tenant_time",
                    "filter_rule_trigger_log",
                    ["tenant_id", "matched_at"],
                )
        if "idx_trigger_log_rule_time_action" not in trigger_log_indexes:
            if is_postgres:
                op.execute("""
                    CREATE INDEX idx_trigger_log_rule_time_action
                    ON filter_rule_trigger_log(rule_id, matched_at, action_taken)
                """)
            else:
                op.create_index(
                    "idx_trigger_log_rule_time_action",
                    "filter_rule_trigger_log",
                    ["rule_id", "matched_at", "action_taken"],
                )
        if "idx_trigger_log_time_action" not in trigger_log_indexes:
            if is_postgres:
                op.execute("""
                    CREATE INDEX idx_trigger_log_time_action
                    ON filter_rule_trigger_log(matched_at, action_taken)
                """)
            else:
                op.create_index(
                    "idx_trigger_log_time_action",
                    "filter_rule_trigger_log",
                    ["matched_at", "action_taken"],
                )

    # 3. 创建 filter_rule_versions 表
    if "filter_rule_versions" not in existing_tables:
        op.create_table(
            "filter_rule_versions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("rule_id", sa.Integer(), nullable=False),
            sa.Column("version_number", sa.Integer(), nullable=False),
            sa.Column("rule_snapshot", JSONB if is_postgres else sa.JSON(), nullable=False),
            sa.Column("created_by", sa.Integer(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.current_timestamp(),
            ),
            sa.Column("change_reason", sa.String(500), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("rule_id", "version_number", name="uq_rule_version"),
        )

    # 创建索引（无论表是刚创建还是已存在）
    rule_versions_indexes = {
        idx["name"] for idx in inspector.get_indexes("filter_rule_versions")
    }
        if "idx_rule_versions_rule_version" not in rule_versions_indexes:
            if is_postgres:
                op.execute("""
                    CREATE INDEX idx_rule_versions_rule_version
                    ON filter_rule_versions(rule_id, version_number)
                """)
            else:
                op.create_index(
                    "idx_rule_versions_rule_version",
                    "filter_rule_versions",
                    ["rule_id", "version_number"],
                )

    # 4. 创建 rule_cache_sync 表
    if "rule_cache_sync" not in existing_tables:
        op.create_table(
            "rule_cache_sync",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("rule_id", sa.Integer(), nullable=False),
            sa.Column("action", sa.String(20), nullable=False),
            sa.Column("tenant_id", sa.Integer(), nullable=True),
            sa.Column(
                "timestamp",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.current_timestamp(),
            ),
            sa.Column("processed", sa.Boolean(), nullable=True, server_default="0"),
            sa.PrimaryKeyConstraint("id"),
        )

    # 创建索引（无论表是刚创建还是已存在）
    cache_sync_indexes = {idx["name"] for idx in inspector.get_indexes("rule_cache_sync")}
        if "idx_cache_sync_unprocessed" not in cache_sync_indexes:
            if is_postgres:
                op.execute("""
                    CREATE INDEX idx_cache_sync_unprocessed
                    ON rule_cache_sync(processed, timestamp)
                """)
            else:
                op.create_index(
                    "idx_cache_sync_unprocessed", "rule_cache_sync", ["processed", "timestamp"]
                )
        if "idx_cache_sync_tenant_unprocessed" not in cache_sync_indexes:
            if is_postgres:
                op.execute("""
                    CREATE INDEX idx_cache_sync_tenant_unprocessed
                    ON rule_cache_sync(tenant_id, processed, timestamp)
                """)
            else:
                op.create_index(
                    "idx_cache_sync_tenant_unprocessed",
                    "rule_cache_sync",
                    ["tenant_id", "processed", "timestamp"],
                )


def downgrade():
    """回滚迁移"""
    op.drop_table("rule_cache_sync")
    op.drop_table("filter_rule_versions")
    op.drop_table("filter_rule_trigger_log")
    op.drop_table("filter_rule_approval_log")
