"""Add indexes to session_stats materialized view

Revision ID: 027_session_stats_indexes
Revises: 026_add_projects_tables
Create Date: 2026-04-06

This migration adds indexes to the session_stats materialized view
to optimize the ORDER BY updated_at DESC query.

Problem:
- session_stats materialized view has no indexes
- ORDER BY updated_at DESC requires full scan of 766 rows
- Query takes ~8 seconds due to LEFT JOIN (now removed) and missing index

Solution:
- Add index on updated_at DESC for fast ordering
- Add index on tool_name, host_name for filtering

Performance improvement:
- Query time reduced from ~8 seconds to ~0.01 seconds

"""

from typing import Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "027_session_stats_indexes"
down_revision: Union[str, None] = "026_add_projects"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def _index_exists(conn, table_name: str, index_name: str) -> bool:
    """Check if an index exists in PostgreSQL."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text("SELECT 1 FROM pg_indexes WHERE indexname = :index_name"),
            {"index_name": index_name},
        )
        return result.fetchone() is not None
    return False


def _matview_exists(conn, matview_name: str) -> bool:
    """Check if a materialized view exists (PostgreSQL only)."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text("SELECT EXISTS (SELECT FROM pg_matviews WHERE matviewname = :matview_name)"),
            {"matview_name": matview_name},
        )
        return result.fetchone()[0]
    return False


def upgrade() -> None:
    """Upgrade database schema."""
    conn = op.get_bind()

    if conn.dialect.name == "postgresql":
        # Check if session_stats materialized view exists
        if _matview_exists(conn, "session_stats"):
            # Add index for ORDER BY updated_at DESC
            if not _index_exists(conn, "session_stats", "idx_session_stats_updated_at"):
                op.execute(
                    sa.text(
                        "CREATE INDEX idx_session_stats_updated_at ON session_stats(updated_at DESC)"
                    )
                )

            # Add index for filtering by tool_name and host_name
            if not _index_exists(conn, "session_stats", "idx_session_stats_tool_host"):
                op.execute(
                    sa.text(
                        "CREATE INDEX idx_session_stats_tool_host ON session_stats(tool_name, host_name)"
                    )
                )

            # Add index for session_id lookups
            if not _index_exists(conn, "session_stats", "idx_session_stats_session_id"):
                op.execute(
                    sa.text(
                        "CREATE INDEX idx_session_stats_session_id ON session_stats(session_id)"
                    )
                )


def downgrade() -> None:
    """Downgrade database schema."""
    conn = op.get_bind()

    if conn.dialect.name == "postgresql":
        if _matview_exists(conn, "session_stats"):
            if _index_exists(conn, "session_stats", "idx_session_stats_updated_at"):
                op.execute(sa.text("DROP INDEX idx_session_stats_updated_at"))

            if _index_exists(conn, "session_stats", "idx_session_stats_tool_host"):
                op.execute(sa.text("DROP INDEX idx_session_stats_tool_host"))

            if _index_exists(conn, "session_stats", "idx_session_stats_session_id"):
                op.execute(sa.text("DROP INDEX idx_session_stats_session_id"))