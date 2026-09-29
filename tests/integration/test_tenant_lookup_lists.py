"""Tenant-scoped tool/host lists (#3424).

``UsageRepository.get_all_tools`` / ``get_all_hosts`` with a tenant used a
full ``SELECT DISTINCT ... WHERE user_id IN (tenant users)`` scan of
daily_messages. They now walk each tenant user's values through the
(user_id, tool_name) / (user_id, host_name) indexes, falling back to the plain
scan when the index is missing. Both paths must return exactly the tenant's
values and nothing from other tenants.
"""

import importlib.util
import itertools
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

import app.repositories.usage_repo as usage_repo_mod
from app.repositories.usage_repo import UsageRepository

pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.issue(3424)]

_ids = itertools.count()

_INDEXES = ("idx_messages_user_tool", "idx_messages_user_host")


def _user(db, user_id, tenant_id):
    db.execute(
        "INSERT INTO users (id, username, email, password_hash, role, tenant_id)"
        " VALUES (?, ?, ?, 'x', 'user', ?)",
        (user_id, f"u{user_id}", f"u{user_id}@example.com", tenant_id),
    )


def _message(db, user_id, tool_name, host_name):
    db.execute(
        "INSERT INTO daily_messages (date, tool_name, host_name, message_id, role, user_id)"
        " VALUES ('2025-01-15', ?, ?, ?, 'assistant', ?)",
        (tool_name, host_name, f"m{next(_ids)}", user_id),
    )


@pytest.fixture
def seeded(tmp_db):
    UsageRepository._known_indexes.clear()
    _user(tmp_db, 101, 1)
    _user(tmp_db, 102, 1)
    _user(tmp_db, 201, 2)
    _message(tmp_db, 101, "claude", "host-a")
    _message(tmp_db, 101, "claude", "host-a")
    _message(tmp_db, 101, "qwen", "host-b")
    _message(tmp_db, 102, "codex", "host-a")
    _message(tmp_db, 201, "zcode", "host-other")  # other tenant
    _message(tmp_db, None, "openclaw", "host-orphan")  # no user
    yield tmp_db
    UsageRepository._known_indexes.clear()


def _lists(db):
    """All six lookups, plus how many used the indexed per-user walk."""
    repo = UsageRepository(db=db)
    with patch.object(
        usage_repo_mod,
        "distinct_values_for_users_sql",
        wraps=usage_repo_mod.distinct_values_for_users_sql,
    ) as walk:
        lists = (
            repo.get_all_tools(tenant_id=1),
            repo.get_all_hosts(tenant_id=1),
            repo.get_all_tools(tenant_id=2),
            repo.get_all_hosts(tenant_id=2),
            repo.get_all_tools(tenant_id=3),
            repo.get_all_hosts(tenant_id=3),
        )
    return lists, walk.call_count


_EXPECTED = (
    ["claude", "codex", "qwen"],
    ["host-a", "host-b"],
    ["zcode"],
    ["host-other"],
    [],
    [],
)


def test_indexed_walk_returns_only_the_tenants_values(seeded):
    assert all(seeded.index_exists(name) for name in _INDEXES)
    assert _lists(seeded) == (_EXPECTED, 6)


def test_fallback_without_index_returns_the_same_values(seeded):
    for name in _INDEXES:
        seeded.execute(f"DROP INDEX {name}")
    assert not any(seeded.index_exists(name) for name in _INDEXES)
    assert _lists(seeded) == (_EXPECTED, 0)


def test_cached_index_confirmation_expires(seeded):
    """A dropped index must stop being used once the cached confirmation
    expires; without its index the walk rescans the table per probe."""
    assert _lists(seeded) == (_EXPECTED, 6)
    for name in _INDEXES:
        seeded.execute(f"DROP INDEX {name}")
    # Still inside the TTL: the cached confirmation is trusted.
    assert _lists(seeded)[1] == 6
    for key in UsageRepository._known_indexes:
        UsageRepository._known_indexes[key] -= UsageRepository._INDEX_CACHE_TTL + 1
    assert _lists(seeded) == (_EXPECTED, 0)


def test_index_exists(tmp_db):
    assert tmp_db.index_exists("idx_messages_user_tool") is True
    assert tmp_db.index_exists("no_such_index") is False


def test_migration_creates_indexes_idempotently_and_downgrades():
    path = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "20260926_002_daily_messages_user_lookup_indexes.py"
    )
    spec = importlib.util.spec_from_file_location("user_lookup_indexes", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE daily_messages (id INTEGER PRIMARY KEY, user_id INTEGER,"
                " tool_name TEXT, host_name TEXT)"
            )
        )

        def names():
            return {i["name"] for i in sa.inspect(conn).get_indexes("daily_messages")}

        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            assert set(_INDEXES) <= names()
            migration.upgrade()  # idempotent
            assert set(_INDEXES) <= names()
            migration.downgrade()
            assert not set(_INDEXES) & names()
