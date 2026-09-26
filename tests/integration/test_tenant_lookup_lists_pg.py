"""Tenant-scoped tool/host lists on PostgreSQL (#3424).

Companion to test_tenant_lookup_lists.py; the ``_pg.py`` suffix puts it in
the postgres CI lane.
"""

import psycopg2
import pytest

from app.repositories.usage_repo import UsageRepository
from tests.integration.test_tenant_lookup_lists import _EXPECTED, _lists, _message, _user

pytestmark = [
    pytest.mark.integration,
    pytest.mark.postgres,
    pytest.mark.regression,
    pytest.mark.issue(3424),
]


@pytest.fixture
def seeded_pg(pg_db):
    UsageRepository._known_indexes.clear()
    for tenant_id in (1, 2, 3):
        pg_db.execute(
            "INSERT INTO tenants (id, name, slug) VALUES (?, ?, ?) ON CONFLICT (id) DO NOTHING",
            (tenant_id, f"t{tenant_id}", f"t{tenant_id}"),
        )
    _user(pg_db, 101, 1)
    _user(pg_db, 102, 1)
    _user(pg_db, 201, 2)
    _message(pg_db, 101, "claude", "host-a")
    _message(pg_db, 101, "qwen", "host-b")
    _message(pg_db, 102, "codex", "host-a")
    _message(pg_db, 201, "zcode", "host-other")
    _message(pg_db, None, "openclaw", "host-orphan")
    yield pg_db
    UsageRepository._known_indexes.clear()


def test_indexed_walk_on_postgres(seeded_pg):
    assert seeded_pg.index_exists("idx_messages_user_tool")
    assert seeded_pg.index_exists("idx_messages_user_host")
    assert _lists(seeded_pg) == (_EXPECTED, 6)


def test_fallback_on_postgres(seeded_pg):
    seeded_pg.execute("DROP INDEX idx_messages_user_tool")
    seeded_pg.execute("DROP INDEX idx_messages_user_host")
    assert _lists(seeded_pg) == (_EXPECTED, 0)


def test_migration_rebuilds_invalid_index(pg_db):
    """A failed CONCURRENTLY build leaves an INVALID index under the target
    name; re-running the migration must rebuild it, not skip it."""
    import importlib.util
    from pathlib import Path

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    conn = psycopg2.connect(pg_db.db_url)
    conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute("DROP INDEX idx_messages_user_tool")
        # Make a CONCURRENTLY build of that name fail, leaving it INVALID.
        cur.execute(
            "INSERT INTO daily_messages (date, tool_name, host_name, message_id, role)"
            " VALUES ('2025-01-15', 't', 'h', 'dup-1', 'user'),"
            " ('2025-01-16', 't', 'h', 'dup-1', 'user')"
        )
        with pytest.raises(psycopg2.errors.UniqueViolation):
            cur.execute(
                "CREATE UNIQUE INDEX CONCURRENTLY idx_messages_user_tool"
                " ON daily_messages (message_id)"
            )
        cur.execute("DELETE FROM daily_messages WHERE message_id = 'dup-1'")
    finally:
        conn.close()
    assert pg_db.index_exists("idx_messages_user_tool") is False

    path = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "20260926_002_daily_messages_user_lookup_indexes.py"
    )
    spec = importlib.util.spec_from_file_location("user_lookup_indexes_pg", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = sa.create_engine(pg_db.db_url)
    try:
        with engine.connect() as sa_conn:
            # Like migrations/env.py: autocommit_block needs alembic's transaction.
            context = MigrationContext.configure(sa_conn)
            with context.begin_transaction(), Operations.context(context):
                migration.upgrade()
    finally:
        engine.dispose()

    assert pg_db.index_exists("idx_messages_user_tool") is True
    row = pg_db.fetch_one(
        "SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_messages_user_tool'"
    )
    assert "(user_id, tool_name)" in row["indexdef"]


def test_invalid_index_is_not_usable(pg_db):
    """A failed CREATE INDEX CONCURRENTLY leaves an INVALID index behind under
    the same name; the planner cannot use it, so it must not count."""
    conn = psycopg2.connect(pg_db.db_url)
    conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute("CREATE TABLE lookup_probe (v INTEGER)")
        cur.execute("INSERT INTO lookup_probe VALUES (1), (1)")
        with pytest.raises(psycopg2.errors.UniqueViolation):
            cur.execute("CREATE UNIQUE INDEX CONCURRENTLY lookup_probe_v ON lookup_probe (v)")
    finally:
        conn.close()

    assert pg_db.fetch_one("SELECT 1 AS present FROM pg_class WHERE relname = 'lookup_probe_v'")
    assert pg_db.index_exists("lookup_probe_v") is False
