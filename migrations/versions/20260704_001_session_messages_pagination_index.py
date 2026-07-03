"""session_messages pagination index + NOT NULL timestamp

Revision ID: 20260704_001_session_messages_pagination_index
Revises: 20260703_002_add_sso_auth_states
Create Date: 2026-07-04

Issue: #241 adversarial review problem #22 — ``get_session`` /
``/sessions/<id>/messages`` load all session messages without a LIMIT, blowing
up memory and JSON response size for sessions with thousands of messages.

This migration backs the new composite-key keyset pagination:

- Adds ``idx_session_messages_session_timestamp ON session_messages
  (session_id, "timestamp", id)`` so the ``WHERE session_id = ? ORDER BY
  timestamp ASC, id ASC LIMIT n`` page queries (and the conditional milestone
  COUNT) are index-served rather than scanning every message in a session.
- Makes ``session_messages."timestamp"`` NOT NULL. Keyset paging assumes the
  ``(timestamp, id)`` sort key is total; a NULL timestamp would be silently
  dropped by the ``timestamp < ?`` cursor predicate and sorted
  dialect-dependently (PG NULLS LAST vs SQLite NULLS FIRST). Any pre-existing
  NULL rows are backfilled to ``CURRENT_TIMESTAMP`` before the constraint is
  applied, so the invariant holds without data loss.

The defensive ``NULLS LAST`` ordering in ``get_messages_page`` plus the
backfill here form the double-net described in the design (F7).

CONCURRENTLY handling: on PostgreSQL ``CREATE INDEX CONCURRENTLY`` cannot run
inside a transaction, so we wrap it in ``autocommit_block()`` and pass
``postgresql_concurrently=True`` (ignored by SQLite).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

log = logging.getLogger(__name__)

# revision identifiers, used by Alembic.
revision: str = "20260704_001_session_messages_pagination_index"
down_revision: str | None = "20260703_002_add_sso_auth_states"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "idx_session_messages_session_timestamp"
TABLE = "session_messages"
TS_COLUMN = "timestamp"


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _index_exists(conn) -> bool:
    """Check whether the pagination index already exists (PG or SQLite)."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text(
                "SELECT EXISTS ("
                "  SELECT FROM pg_indexes"
                "  WHERE tablename = :table AND indexname = :index"
                ")"
            ),
            {"table": TABLE, "index": INDEX_NAME},
        )
        return result.fetchone()[0]
    result = conn.execute(
        sa.text("SELECT name FROM sqlite_master WHERE type='index' AND name = :index"),
        {"index": INDEX_NAME},
    )
    return result.fetchone() is not None


def _column_nullable(conn) -> bool:
    """Return True if the timestamp column is still nullable."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = :table AND column_name = :col"
            ),
            {"table": TABLE, "col": TS_COLUMN},
        )
        row = result.fetchone()
        return bool(row) and row[0] == "YES"
    # SQLite: inspect pragma_table_info. NB the column is named ``notnull``, but
    # SQLite tokenizes that bare identifier as the NOTNULL keyword (syntax error),
    # so it MUST be quoted.
    result = conn.execute(
        sa.text('SELECT "notnull" FROM pragma_table_info(:table) WHERE name = :col'),
        {"table": TABLE, "col": TS_COLUMN},
    )
    row = result.fetchone()
    # notnull == 0 means nullable
    return bool(row) and row[0] == 0


def upgrade() -> None:
    """Backfill NULL timestamps, add NOT NULL constraint, create pagination index."""
    conn = op.get_bind()

    # 1. Backfill any NULL timestamps so the NOT NULL constraint cannot fail.
    log.info("Backfilling NULL %s.%s rows to CURRENT_TIMESTAMP", TABLE, TS_COLUMN)
    conn.execute(
        sa.text(
            f'UPDATE {TABLE} SET "{TS_COLUMN}" = CURRENT_TIMESTAMP ' f'WHERE "{TS_COLUMN}" IS NULL'
        )
    )

    # 2. Enforce NOT NULL on the timestamp column.
    if _column_nullable(conn):
        log.info("Setting %s.%s NOT NULL", TABLE, TS_COLUMN)
        if conn.dialect.name == "postgresql":
            op.alter_column(
                TABLE,
                TS_COLUMN,
                existing_type=sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            )
        else:
            # SQLite cannot ALTER COLUMN in place; rebuild via batch mode.
            with op.batch_alter_table(TABLE) as batch_op:
                batch_op.alter_column(
                    TS_COLUMN,
                    existing_type=sa.DateTime(),
                    nullable=False,
                    server_default=sa.text("CURRENT_TIMESTAMP"),
                )
    else:
        log.info("%s.%s already NOT NULL, skipping constraint", TABLE, TS_COLUMN)

    # 3. Create the composite pagination index.
    if not _index_exists(conn):
        log.info("Creating index %s on %s(session_id, timestamp, id)", INDEX_NAME, TABLE)
        if _is_postgresql():
            with op.get_context().autocommit_block():
                op.create_index(
                    INDEX_NAME,
                    TABLE,
                    ["session_id", TS_COLUMN, "id"],
                    postgresql_concurrently=True,
                )
        else:
            op.create_index(INDEX_NAME, TABLE, ["session_id", TS_COLUMN, "id"])
    else:
        log.info("Index %s already exists, skipping", INDEX_NAME)


def downgrade() -> None:
    """Drop the pagination index and relax timestamp back to nullable."""
    if _index_exists(op.get_bind()):
        if _is_postgresql():
            with op.get_context().autocommit_block():
                op.drop_index(INDEX_NAME, table_name=TABLE, postgresql_concurrently=True)
        else:
            op.drop_index(INDEX_NAME, table_name=TABLE)

    conn = op.get_bind()
    if not _column_nullable(conn):
        if conn.dialect.name == "postgresql":
            op.alter_column(
                TABLE,
                TS_COLUMN,
                existing_type=sa.DateTime(),
                nullable=True,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            )
        else:
            with op.batch_alter_table(TABLE) as batch_op:
                batch_op.alter_column(
                    TS_COLUMN,
                    existing_type=sa.DateTime(),
                    nullable=True,
                    server_default=sa.text("CURRENT_TIMESTAMP"),
                )
