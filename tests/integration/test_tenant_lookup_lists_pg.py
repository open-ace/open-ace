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
    assert _lists(seeded_pg) == _EXPECTED


def test_fallback_on_postgres(seeded_pg):
    seeded_pg.execute("DROP INDEX idx_messages_user_tool")
    seeded_pg.execute("DROP INDEX idx_messages_user_host")
    assert _lists(seeded_pg) == _EXPECTED


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
