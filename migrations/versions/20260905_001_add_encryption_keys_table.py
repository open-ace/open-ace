"""Add encryption_keys table

Revision ID: 20260905_001
Revises: 20260827_001
Create Date: 2026-09-05

存储加密密钥元数据（指纹、状态、版本等）
密钥明文存储在环境变量中，数据库仅存储元数据
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260905_001"
down_revision = "20260827_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建 encryption_keys 表（幂等，支持 schema.sql bootstrap）

    The schema.sql snapshots also define this table, so freshly-bootstrapped
    databases already have it. Guard each create_table/create_index against
    the existing schema, following the 20260718_001 pattern.
    """
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    existing_tables = set(inspector.get_table_names())

    if "encryption_keys" not in existing_tables:
        op.create_table(
            "encryption_keys",
            sa.Column("key_id", sa.Integer(), nullable=False, primary_key=True),
            sa.Column("key_fingerprint", sa.String(64), nullable=False, unique=True),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("rotated_at", sa.DateTime(), nullable=True),
            sa.Column("config_version", sa.BigInteger(), nullable=False),
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
        )

        # 创建索引
        op.create_index("idx_encryption_keys_status", "encryption_keys", ["status"])
        op.create_index("idx_encryption_keys_fingerprint", "encryption_keys", ["key_fingerprint"])
    else:
        # 表已存在，检查并创建缺失的索引
        existing_indexes = {idx["name"] for idx in inspector.get_indexes("encryption_keys")}
        if "idx_encryption_keys_status" not in existing_indexes:
            op.create_index("idx_encryption_keys_status", "encryption_keys", ["status"])
        if "idx_encryption_keys_fingerprint" not in existing_indexes:
            op.create_index(
                "idx_encryption_keys_fingerprint", "encryption_keys", ["key_fingerprint"]
            )


def downgrade() -> None:
    op.drop_index("idx_encryption_keys_fingerprint", table_name="encryption_keys")
    op.drop_index("idx_encryption_keys_status", table_name="encryption_keys")
    op.drop_table("encryption_keys")
