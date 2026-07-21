"""Add proxy_token_jtis lifecycle registry

Revision ID: 20260717_001_add_proxy_token_jti_registry
Revises: 20260714_002_add_users_mapping_indexes
Create Date: 2026-07-17

Issue: #1758
Server-side lifecycle tracking for LLM proxy tokens enables revocation on
session stop / rotation and optional single-use replay protection for
high-sensitivity flows.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260717_001_add_proxy_token_jti_registry"
down_revision: str | None = "20260714_002_add_users_mapping_indexes"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create the proxy-token lifecycle registry table and indexes."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    existing_tables = set(inspector.get_table_names())

    if "proxy_token_jtis" not in existing_tables:
        op.create_table(
            "proxy_token_jtis",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("jti", sa.Text(), nullable=False, unique=True),
            sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("session_id", sa.Text(), nullable=False),
            sa.Column("tenant_id", sa.Integer(), nullable=True),
            sa.Column("provider", sa.Text(), nullable=False),
            sa.Column("session_type", sa.Text(), nullable=False),
            sa.Column("scope", sa.Text(), nullable=True),
            sa.Column(
                "reuse_mode",
                sa.Text(),
                nullable=False,
                server_default="multi_use",
            ),
            sa.Column(
                "is_single_use",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column(
                "issued_at",
                sa.DateTime(),
                nullable=True,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("first_used_at", sa.DateTime(), nullable=True),
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
            sa.Column("consumed_at", sa.DateTime(), nullable=True),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.Column("revoke_reason", sa.Text(), nullable=True),
            sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("metadata", sa.Text(), nullable=True),
        )

    existing_indexes = {idx["name"] for idx in inspector.get_indexes("proxy_token_jtis")}
    if "idx_proxy_token_jtis_session" not in existing_indexes:
        op.create_index(
            "idx_proxy_token_jtis_session",
            "proxy_token_jtis",
            ["session_id"],
            unique=False,
        )
    if "idx_proxy_token_jtis_expires" not in existing_indexes:
        op.create_index(
            "idx_proxy_token_jtis_expires",
            "proxy_token_jtis",
            ["expires_at"],
            unique=False,
        )
    if "idx_proxy_token_jtis_active" not in existing_indexes:
        op.create_index(
            "idx_proxy_token_jtis_active",
            "proxy_token_jtis",
            ["revoked_at", "consumed_at"],
            unique=False,
        )


def downgrade() -> None:
    """Drop the proxy-token lifecycle registry table."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    existing_tables = set(inspector.get_table_names())
    if "proxy_token_jtis" not in existing_tables:
        return

    existing_indexes = {idx["name"] for idx in inspector.get_indexes("proxy_token_jtis")}
    for index_name in (
        "idx_proxy_token_jtis_active",
        "idx_proxy_token_jtis_expires",
        "idx_proxy_token_jtis_session",
    ):
        if index_name in existing_indexes:
            op.drop_index(index_name, table_name="proxy_token_jtis")

    op.drop_table("proxy_token_jtis")
