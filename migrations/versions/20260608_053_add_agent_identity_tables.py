"""Add registration_tokens, agent_tokens tables and legacy_mode column

Revision ID: 053_agent_identity_tables
Revises: 052_planning_timeout_extension
Create Date: 2026-06-08

Creates tables needed for remote agent identity hardening (Issue #754):
- registration_tokens: one-time-use tokens for enrolling new machines
- agent_tokens: long-lived Bearer tokens for agent authentication
- legacy_mode column on remote_machines: tracks pre-token-registered machines

"""

import sqlalchemy as sa
from alembic import op

revision: str = "053_agent_identity_tables"
down_revision: str = "052_planning_timeout_extension"
branch_labels = None
depends_on = None


def _table_exists(conn, table: str) -> bool:
    """Check if a table already exists."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text("SELECT COUNT(*) FROM information_schema.tables WHERE table_name = :table"),
            {"table": table},
        )
        return result.scalar() > 0

    result = conn.execute(
        sa.text("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name = :table"),
        {"table": table},
    )
    return result.scalar() > 0


def _column_exists(conn, table: str, column: str) -> bool:
    """Check if a column already exists."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_name = :table AND column_name = :column"
            ),
            {"table": table, "column": column},
        )
        return result.scalar() > 0

    result = conn.execute(sa.text(f"PRAGMA table_info({table})"))
    return any(row[1] == column for row in result.fetchall())


def upgrade() -> None:
    conn = op.get_bind()

    # registration_tokens table
    if not _table_exists(conn, "registration_tokens"):
        op.create_table(
            "registration_tokens",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("token_hash", sa.String, nullable=False, unique=True),
            sa.Column("tenant_id", sa.Integer, nullable=False),
            sa.Column("created_by", sa.Integer, nullable=False),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
            sa.Column("expires_at", sa.DateTime, nullable=True),
            sa.Column("is_consumed", sa.Boolean, server_default=sa.text("false")),
            sa.Column("consumed_at", sa.DateTime, nullable=True),
        )
        op.create_index(
            "idx_registration_tokens_hash",
            "registration_tokens",
            ["token_hash"],
        )

    # agent_tokens table
    if not _table_exists(conn, "agent_tokens"):
        op.create_table(
            "agent_tokens",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("token_hash", sa.String, nullable=False, unique=True),
            sa.Column("machine_id", sa.String, nullable=False),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
            sa.Column("is_revoked", sa.Boolean, server_default=sa.text("false")),
            sa.Column("revoked_at", sa.DateTime, nullable=True),
            sa.Column("revoked_by", sa.Integer, nullable=True),
            sa.Column("rotated_at", sa.DateTime, nullable=True),
        )
        op.create_index("idx_agent_tokens_hash", "agent_tokens", ["token_hash"])
        op.create_index("idx_agent_tokens_machine", "agent_tokens", ["machine_id"])

    # legacy_mode column on remote_machines
    if not _column_exists(conn, "remote_machines", "legacy_mode"):
        op.add_column(
            "remote_machines",
            sa.Column(
                "legacy_mode",
                sa.Boolean,
                server_default=sa.text("false"),
                nullable=True,
            ),
        )


def downgrade() -> None:
    conn = op.get_bind()

    if _column_exists(conn, "remote_machines", "legacy_mode"):
        op.drop_column("remote_machines", "legacy_mode")

    if _table_exists(conn, "agent_tokens"):
        op.drop_index("idx_agent_tokens_machine", table_name="agent_tokens")
        op.drop_index("idx_agent_tokens_hash", table_name="agent_tokens")
        op.drop_table("agent_tokens")

    if _table_exists(conn, "registration_tokens"):
        op.drop_index("idx_registration_tokens_hash", table_name="registration_tokens")
        op.drop_table("registration_tokens")
