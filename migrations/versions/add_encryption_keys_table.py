"""Add encryption_keys table

Revision ID: add_encryption_keys_table
Revises:
Create Date: 2026-09-05

存储加密密钥元数据（指纹、状态、版本等）
密钥明文存储在环境变量中，数据库仅存储元数据
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'add_encryption_keys_table'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 创建 encryption_keys 表
    op.create_table(
        'encryption_keys',
        sa.Column('key_id', sa.Integer(), nullable=False, primary_key=True),
        sa.Column('key_fingerprint', sa.String(64), nullable=False, unique=True),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('rotated_at', sa.DateTime(), nullable=True),
        sa.Column('config_version', sa.BigInteger(), nullable=False),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
    )

    # 创建索引
    op.create_index('idx_encryption_keys_status', 'encryption_keys', ['status'])
    op.create_index('idx_encryption_keys_fingerprint', 'encryption_keys', ['key_fingerprint'])


def downgrade() -> None:
    op.drop_index('idx_encryption_keys_fingerprint', table_name='encryption_keys')
    op.drop_index('idx_encryption_keys_status', table_name='encryption_keys')
    op.drop_table('encryption_keys')