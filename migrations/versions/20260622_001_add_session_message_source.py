"""Add source column to session_messages

Separates workflow-local persisted messages from transcript sync/fetch rows so
autonomous milestone/session detail views can filter raw importer data.

Revision ID: 064_add_session_msg_source
Revises: 7bcf07ee658e
Create Date: 2026-06-22
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "064_add_session_msg_source"
down_revision: Union[str, None] = "7bcf07ee658e"
branch_labels = None
depends_on = None


def _column_exists(conn, table: str, column: str) -> bool:
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
    if not _column_exists(conn, "session_messages", "source"):
        op.add_column(
            "session_messages",
            sa.Column("source", sa.Text, server_default="", nullable=False),
        )


def downgrade() -> None:
    conn = op.get_bind()
    if _column_exists(conn, "session_messages", "source"):
        op.drop_column("session_messages", "source")
