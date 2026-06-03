"""Add index on agent_sessions.session_type

Revision ID: 050_session_type_index
Revises: 049_add_user_id_to_daily_stats
Create Date: 2026-06-03

This migration adds an index on agent_sessions.session_type column
to optimize filtering by session_type in the sessions list API.

Problem:
- /api/workspace/sessions API filters by session_type
- session_type column has no index
- Large datasets may cause full table scan

Solution:
- Add index idx_agent_sessions_session_type on session_type

Performance improvement:
- Query with session_type filter will use index scan instead of full scan

"""

from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "050_session_type_index"
down_revision: Union[str, None] = "049_add_user_id_to_daily_stats"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def _index_exists(conn, table_name: str, index_name: str) -> bool:
    """Check if an index exists."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text("SELECT 1 FROM pg_indexes WHERE indexname = :index_name"),
            {"index_name": index_name},
        )
        return result.fetchone() is not None
    elif conn.dialect.name == "sqlite":
        result = conn.execute(
            sa.text(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name=:index_name"
            ),
            {"index_name": index_name},
        )
        return result.fetchone() is not None
    return False


def upgrade() -> None:
    """Upgrade database schema."""
    conn = op.get_bind()

    index_name = "idx_agent_sessions_session_type"

    if not _index_exists(conn, "agent_sessions", index_name):
        if conn.dialect.name == "postgresql":
            op.execute(
                sa.text(f"CREATE INDEX {index_name} ON agent_sessions(session_type)")
            )
        elif conn.dialect.name == "sqlite":
            op.execute(
                sa.text(f"CREATE INDEX {index_name} ON agent_sessions(session_type)")
            )


def downgrade() -> None:
    """Downgrade database schema."""
    conn = op.get_bind()

    index_name = "idx_agent_sessions_session_type"

    if _index_exists(conn, "agent_sessions", index_name):
        op.execute(sa.text(f"DROP INDEX {index_name}"))