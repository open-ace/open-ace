"""Add login_attempts table for login lockout tracking

Revision ID: 046_login_attempts
Revises: 045_granted_at_type
Create Date: 2026-05-27

The login_attempts table was only created via runtime DDL (get_ddl_statements)
or in schema-postgres.sql (fresh installs). It had no Alembic migration,
so databases set up via `alembic upgrade head` would miss it when the
runtime DDL failed silently.
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "046_login_attempts"
down_revision: Union[str, None] = "045_granted_at_type"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def _table_exists(conn, table_name: str) -> bool:
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = :table_name)"
            ),
            {"table_name": table_name},
        )
        return result.fetchone()[0]
    else:
        result = conn.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='table' AND name = :table_name"),
            {"table_name": table_name},
        )
        return result.fetchone() is not None


def upgrade() -> None:
    conn = op.get_bind()

    if _table_exists(conn, "login_attempts"):
        return

    op.create_table(
        "login_attempts",
        sa.Column("username", sa.String(255), primary_key=True),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("locked_until", sa.TIMESTAMP, nullable=True),
    )
    op.create_index(
        "idx_login_attempts_locked_until",
        "login_attempts",
        ["locked_until"],
    )


def downgrade() -> None:
    conn = op.get_bind()

    if _table_exists(conn, "login_attempts"):
        op.drop_index("idx_login_attempts_locked_until", table_name="login_attempts")
        op.drop_table("login_attempts")
