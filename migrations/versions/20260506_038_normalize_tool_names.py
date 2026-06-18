"""Normalize tool names: unify qwen-code and qwen-code-cli to qwen

Revision ID: 038_normalize_tool_names
Revises: 037_deduplicate_remote_machines
Create Date: 2026-05-06

GitHub Issue #233: Dashboard and Request Dashboard trend charts showed
inconsistent data because different code paths stored different tool_name
variants (qwen, qwen-code, qwen-code-cli) for the same tool. This migration
normalizes all historical records to the canonical name 'qwen'.
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "038_normalize_tool_names"
down_revision: Union[str, None] = "037_deduplicate_remote_machines"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def _table_exists(conn, table_name: str) -> bool:
    """Check whether a table exists."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text("SELECT 1 FROM information_schema.tables WHERE table_name = :table_name"),
            {"table_name": table_name},
        )
        return result.fetchone() is not None

    result = conn.execute(
        sa.text("SELECT 1 FROM sqlite_master WHERE type='table' AND name = :table_name"),
        {"table_name": table_name},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    conn = op.get_bind()
    for table in ("agent_sessions", "daily_messages", "daily_usage"):
        if _table_exists(conn, table):
            op.execute(
                sa.text(
                    f"UPDATE {table} SET tool_name = 'qwen' "
                    f"WHERE tool_name IN ('qwen-code', 'qwen-code-cli')"
                )
            )


def downgrade() -> None:
    pass
