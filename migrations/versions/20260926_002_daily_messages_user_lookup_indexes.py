"""Index daily_messages by (user_id, tool_name) and (user_id, host_name).

Tenant-scoped tool/host lists (``/api/tools`` and ``/api/hosts`` for tenant
admins and tenant users) filter ``daily_messages`` by the tenant's users.
Without these indexes that is a sequential scan of the whole table (2-3 s on a
~0.5M-row table, growing linearly). With them, UsageRepository walks each
tenant user's distinct values with a few index probes (#3424).

Partial on ``user_id IS NOT NULL``: rows without a user never match the tenant
predicate, and that keeps the indexes small. Built CONCURRENTLY on PostgreSQL
so the upgrade does not block message writes.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260926_002_daily_messages_user_lookup_indexes"
down_revision: str | None = "20260926_001_dedupe_daily_stats_null_sender"
branch_labels: str | None = None
depends_on: str | None = None

_INDEXES = {
    "idx_messages_user_tool": ["user_id", "tool_name"],
    "idx_messages_user_host": ["user_id", "host_name"],
}


def _pg_index_state(conn: sa.Connection, name: str) -> bool | None:
    """True if valid, False if INVALID (a failed CONCURRENTLY build), None if absent."""
    row = conn.execute(
        sa.text(
            "SELECT i.indisvalid FROM pg_class c JOIN pg_index i ON i.indexrelid = c.oid"
            " WHERE c.relname = :name AND pg_table_is_visible(c.oid)"
        ),
        {"name": name},
    ).first()
    return None if row is None else bool(row[0])


def upgrade() -> None:
    conn = op.get_bind()
    existing = {index["name"] for index in sa.inspect(conn).get_indexes("daily_messages")}
    is_postgres = conn.dialect.name == "postgresql"

    for name, columns in _INDEXES.items():
        if is_postgres:
            state = _pg_index_state(conn, name)
            if state:
                continue
            # MIG002: CONCURRENTLY avoids an ACCESS EXCLUSIVE lock during build.
            with op.get_context().autocommit_block():
                if state is False:
                    # A previous CONCURRENTLY build failed and left an INVALID
                    # index the planner never uses; rebuild it rather than
                    # treating the name as done.
                    op.drop_index(name, table_name="daily_messages", postgresql_concurrently=True)
                op.create_index(
                    name,
                    "daily_messages",
                    columns,
                    postgresql_concurrently=True,
                    postgresql_where=sa.text("user_id IS NOT NULL"),
                )
        elif name not in existing:
            op.create_index(
                name,
                "daily_messages",
                columns,
                sqlite_where=sa.text("user_id IS NOT NULL"),
            )


def downgrade() -> None:
    conn = op.get_bind()
    existing = {index["name"] for index in sa.inspect(conn).get_indexes("daily_messages")}
    is_postgres = conn.dialect.name == "postgresql"

    for name in _INDEXES:
        if name not in existing:
            continue
        if is_postgres:
            with op.get_context().autocommit_block():
                op.drop_index(name, table_name="daily_messages", postgresql_concurrently=True)
        else:
            op.drop_index(name, table_name="daily_messages")
