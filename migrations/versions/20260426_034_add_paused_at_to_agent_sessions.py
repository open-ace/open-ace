"""Add paused_at column to agent_sessions

Revision ID: 034_add_paused_at
Revises: 033_add_remote_workspace_tables
Create Date: 2026-04-26

This migration adds a paused_at timestamp column to the agent_sessions
table to support session pause/resume functionality.

"""

from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "034_add_paused_at"
down_revision: Union[str, None] = "033_add_remote_workspace_tables"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def _table_exists(bind, table_name: str) -> bool:
    """Check if a table exists."""
    if bind.dialect.name == "postgresql":
        result = bind.execute(
            sa.text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name = :table_name"
            ),
            {"table_name": table_name},
        )
        return result.fetchone() is not None

    result = bind.execute(
        sa.text("SELECT 1 FROM sqlite_master WHERE type='table' AND name = :table_name"),
        {"table_name": table_name},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    """Add paused_at column to agent_sessions."""
    bind = op.get_bind()
    if not _table_exists(bind, "agent_sessions"):
        return
    if bind.dialect.name == "postgresql":
        result = bind.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='agent_sessions' AND column_name='paused_at'"
            )
        )
        if result.fetchone():
            return
    op.add_column(
        "agent_sessions",
        sa.Column("paused_at", sa.TIMESTAMP(), nullable=True),
    )


def downgrade() -> None:
    """Remove paused_at column from agent_sessions."""
    bind = op.get_bind()
    if _table_exists(bind, "agent_sessions"):
        op.drop_column("agent_sessions", "paused_at")
