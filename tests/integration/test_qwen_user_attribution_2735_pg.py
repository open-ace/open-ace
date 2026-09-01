"""Issue #2735: user-id and tenant attribution for qwen collection (real PG).

These were placeholder stubs whose bodies unconditionally skipped — the
pg_db fixture existed, the postgres lane existed, and the SUT existed, but
the tests never ran (#3186 Phase B batch 2b). Implemented for real against
`fetch_qwen._resolve_user_id` and the tenant-scoped summary aggregation.
"""

import sys
from pathlib import Path

import pytest

# Marks every test in this module as requiring a live PostgreSQL server
# (selected by the postgres CI lane; auto-skips locally without one).
pytestmark = pytest.mark.postgres

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import fetch_qwen  # noqa: E402
from app.repositories.usage_repo import UsageRepository  # noqa: E402
from shared import config as shared_config  # noqa: E402
from shared import db as shared_db  # noqa: E402


@pytest.fixture(autouse=True)
def _bind_shared_db_to_test_database(monkeypatch, pg_db):
    """Route the SCRIPTS-side shared.db (a separate module identity from
    scripts.shared.db: fetch_qwen imports it via the scripts/ sys.path entry)
    at this test's throwaway database.

    - shared.db caches the resolved URL globally; pg_db builds a fresh
      database per test, so the cache must be cleared (and restored) or every
      test after the first would resolve the first test's already-dropped
      database (#3186 rev2 review finding).
    - conftest patches scripts.shared.config.get_database_url; the
      top-level shared.config used by fetch_qwen needs the same URL (the
      pg_db handle carries it as .db_url)."""
    monkeypatch.setattr(shared_db, "_db_url_cache", None)
    test_url = pg_db.db_url
    monkeypatch.setattr(shared_config, "get_database_url", lambda: test_url)


def _insert_tenant(pg_db, name):
    return pg_db.fetch_one(
        "INSERT INTO tenants (name, slug) VALUES (%s, %s) RETURNING id",
        (name, name.replace("_", "-")),
        commit=True,
    )["id"]


def _insert_user(pg_db, username, system_account=None, tenant_id=None):
    return pg_db.fetch_one(
        "INSERT INTO users (username, email, password_hash, role, system_account, tenant_id) "
        "VALUES (%s, %s, %s, 'user', %s, %s) RETURNING id",
        (username, f"{username}@example.com", "hashed_pw", system_account, tenant_id),
        commit=True,
    )["id"]


def _insert_daily_message(
    pg_db,
    date,
    sender_name,
    user_id,
    tool_name="qwen",
    tokens_used=100,
    host_name="localhost",
):
    return pg_db.fetch_one(
        "INSERT INTO daily_messages (date, tool_name, host_name, message_id, role, "
        "sender_name, user_id, tokens_used) "
        "VALUES (%s, %s, %s, %s, 'user', %s, %s, %s) RETURNING id",
        (
            date,
            tool_name,
            host_name,
            f"msg-{sender_name}-{date}",
            sender_name,
            user_id,
            tokens_used,
        ),
        commit=True,
    )["id"]


class TestUserIdResolution:
    """fetch_qwen._resolve_user_id drives daily_messages.user_id attribution."""

    def test_resolve_user_id_returns_correct_id(self, pg_db):
        """The resolved id is the users.id of the system_account owner."""
        uid = _insert_user(pg_db, "alice", system_account="alice-acct")
        assert fetch_qwen._resolve_user_id(None, "alice-acct") == uid

    def test_resolve_user_id_by_username(self, pg_db):
        """Without a system_account match, the username itself resolves."""
        uid = _insert_user(pg_db, "bob")
        assert fetch_qwen._resolve_user_id(None, "bob") == uid

    def test_resolve_user_id_returns_none_for_unknown(self, pg_db):
        """An unknown account resolves to None (NULL attribution), not a raise."""
        _insert_user(pg_db, "carol", system_account="carol-acct")
        assert fetch_qwen._resolve_user_id(None, "nobody-here") is None
        assert fetch_qwen._resolve_user_id(None, None) is None


class TestTenantAttribution:
    """user_id attribution is what makes tenant-scoped aggregation see qwen rows."""

    def test_daily_messages_user_id_filled(self, pg_db):
        """A attributed qwen row carries the owner's users.id (the column
        _resolve_user_id exists to fill)."""
        tenant = _insert_tenant(pg_db, "tenant_x")
        uid = _insert_user(pg_db, "dave", system_account="dave-acct", tenant_id=tenant)
        resolved = fetch_qwen._resolve_user_id(None, "dave-acct")
        assert resolved == uid
        mid = _insert_daily_message(pg_db, "2026-01-01", "dave-acct-host-qwen", resolved)
        row = pg_db.fetch_one("SELECT user_id FROM daily_messages WHERE id = %s", (mid,))
        assert row["user_id"] == uid

    def test_agent_sessions_user_id_filled(self, pg_db):
        """update_agent_sessions_stats attributes new sessions by the sender's
        system_account when the session does not exist yet."""
        tenant = _insert_tenant(pg_db, "tenant_y")
        uid = _insert_user(pg_db, "erin", system_account="erin", tenant_id=tenant)
        messages = [
            {
                "agent_session_id": "sess-erin-1",
                "role": "user",
                "timestamp": "2026-01-02T10:00:00",
                "sender_name": "erin-host-qwen",
                "tool_name": "qwen",
                "host_name": "localhost",
                "project_path": "/home/erin/project",
                "model": "qwen-max",
                "tokens_used": 50,
            }
        ]
        updated = fetch_qwen.update_agent_sessions_stats(messages)
        assert updated == 1
        row = pg_db.fetch_one(
            "SELECT user_id, session_id, message_count FROM agent_sessions WHERE session_id = %s",
            ("sess-erin-1",),
        )
        assert row is not None
        assert row["user_id"] == uid
        assert row["message_count"] == 1

    def test_tenant_summary_includes_qwen_data(self, pg_db):
        """get_summary_by_tool(tenant_id=...) counts only that tenant's rows —
        including qwen rows attributed via user_id."""
        tenant = _insert_tenant(pg_db, "tenant_z")
        uid = _insert_user(pg_db, "frank", system_account="frank-acct", tenant_id=tenant)
        _insert_daily_message(pg_db, "2026-01-03", "frank-acct-host-qwen", uid, tokens_used=500)
        repo = UsageRepository(db=pg_db)
        summary = repo.get_summary_by_tool(tenant_id=tenant)
        assert "qwen" in summary
        assert summary["qwen"]["total_tokens"] == 500

    def test_tenant_isolation_in_aggregation(self, pg_db):
        """Another tenant's rows are invisible to this tenant's summary."""
        tenant_a = _insert_tenant(pg_db, "tenant_iso_a")
        tenant_b = _insert_tenant(pg_db, "tenant_iso_b")
        uid_a = _insert_user(pg_db, "gina", system_account="gina-acct", tenant_id=tenant_a)
        uid_b = _insert_user(pg_db, "hank", system_account="hank-acct", tenant_id=tenant_b)
        _insert_daily_message(pg_db, "2026-01-04", "gina-acct-host-qwen", uid_a, tokens_used=100)
        _insert_daily_message(pg_db, "2026-01-04", "hank-acct-host-qwen", uid_b, tokens_used=999)
        repo = UsageRepository(db=pg_db)
        summary_a = repo.get_summary_by_tool(tenant_id=tenant_a)
        assert summary_a["qwen"]["total_tokens"] == 100
        summary_b = repo.get_summary_by_tool(tenant_id=tenant_b)
        assert summary_b["qwen"]["total_tokens"] == 999
