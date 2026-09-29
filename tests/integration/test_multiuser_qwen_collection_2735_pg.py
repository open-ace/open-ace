"""Issue #2735: multi-user qwen collection against a real PostgreSQL.

These were placeholder stubs whose bodies unconditionally skipped; the
pg_db fixture, the postgres lane, and the SUT all existed (#3186 Phase B
batch 2b). Implemented for real against `fetch_qwen` collection seams.
"""

import json
import sys
from pathlib import Path

import pytest

# Marks every test in this module as requiring a live PostgreSQL server.
pytestmark = pytest.mark.postgres

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import fetch_qwen  # noqa: E402

from shared import config as shared_config  # noqa: E402
from shared import db as shared_db  # noqa: E402


@pytest.fixture(autouse=True)
def _bind_shared_db_to_test_database(monkeypatch, pg_db):
    """Route the SCRIPTS-side shared.db at this test's throwaway database
    (module-identity note in test_qwen_user_attribution_2735_pg.py)."""
    monkeypatch.setattr(shared_db, "_db_url_cache", None)
    test_url = pg_db.db_url
    monkeypatch.setattr(shared_config, "get_database_url", lambda: test_url)


from collections import defaultdict


def _new_aggregated():
    """fetch_and_save seeds the aggregation dict as a defaultdict with these
    keys (see fetch_and_save); _process_projects_dir relies on that shape."""
    return defaultdict(
        lambda: {
            "prompt_tokens": 0,
            "candidates_tokens": 0,
            "thoughts_tokens": 0,
            "cached_tokens": 0,
            "total_tokens": 0,
            "request_count": 0,
            "models_used": set(),
        }
    )


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


def _entry(
    uuid,
    entry_type,
    ts,
    *,
    parent=None,
    model=None,
    usage=None,
    message_id=None,
    session_id="sess-fixture-1",
    text=None,
):
    """One entry; a real qwen session file shares ONE sessionId across its
    entries, so the default pins every fixture entry to a single session.

    Args:
        text: Optional text content for user/assistant messages. If provided,
            adds a text part to the message parts array. This is required for
            user messages after Issue #3337's strip_qwen_system_envelopes fix
            (empty user messages are skipped to handle Qwen SDK's system-reminder
            envelopes that may be combined with real user input).
    """
    parts = []
    if text:
        parts.append({"text": text})

    entry = {
        "uuid": uuid,
        "parentUuid": parent,
        "type": entry_type,
        "timestamp": ts,
        "sessionId": session_id,
        "message": {"message_id": message_id or uuid, "parts": parts},
    }
    if model:
        entry["model"] = model
    if usage:
        entry["usageMetadata"] = usage
    return entry


def _write_project_jsonl(root: Path, system_account: str, host: str, entries) -> Path:
    """Lay out one qwen projects dir: <root>/<host>/<system_account>/projects/<enc>/sessions/<id>.jsonl."""
    session_dir = root / host / system_account / "projects" / "encproj" / "sessions"
    session_dir.mkdir(parents=True)
    path = session_dir / "2026-01-05-10-00-00.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return root / host / system_account / "projects" / "encproj"


class TestMultiUserQwenCollection:
    """Collection seams driven with controlled fixtures (no /home scan)."""

    def _sample_entries(self):
        return [
            # Issue #3337: user messages must have actual text content (empty user
            # messages are skipped by strip_qwen_system_envelopes logic)
            _entry("u1", "user", "2026-01-05T10:00:00Z", message_id="m1", text="Test user message"),
            _entry(
                "a1",
                "assistant",
                "2026-01-05T10:00:05Z",
                parent="u1",
                model="qwen-max",
                usage={
                    "promptTokenCount": 100,
                    "candidatesTokenCount": 40,
                    "totalTokenCount": 140,
                },
            ),
        ]

    def test_single_user_session_collection(self, tmp_path):
        """A single-user JSONL yields messages with sender attribution and
        per-day token aggregation."""
        project = _write_project_jsonl(tmp_path, "alice", "host1", self._sample_entries())
        aggregated = _new_aggregated()
        all_messages: list = []
        files_scanned = fetch_qwen._process_projects_dir(
            project, "host1", "alice", aggregated, all_messages, user_id=77
        )
        assert files_scanned == 1  # one JSONL fixture file
        assert len(all_messages) == 2  # exactly the user + assistant entries
        assert all(m["host_name"] == "host1" for m in all_messages)
        assert all(m.get("user_id") == 77 for m in all_messages)

    def test_multi_user_session_collection(self, tmp_path):
        """Two system accounts' project dirs aggregate independently under one
        collection run."""
        aggregated = _new_aggregated()
        all_messages: list = []
        for account in ("alice", "bob"):
            project = _write_project_jsonl(tmp_path, account, "host1", self._sample_entries())
            fetch_qwen._process_projects_dir(
                project, "host1", account, aggregated, all_messages, user_id=None
            )
        senders = {m["sender_name"].split("-")[0] for m in all_messages}
        assert senders == {"alice", "bob"}

    def test_session_data_persistence(self, pg_db, tmp_path):
        """Collected messages persist an agent_sessions row attributed to the
        sender's system_account via update_agent_sessions_stats."""
        tenant = _insert_tenant(pg_db, "tenant_mu")
        uid = _insert_user(pg_db, "carol", system_account="carol", tenant_id=tenant)
        project = _write_project_jsonl(tmp_path, "carol", "host1", self._sample_entries())
        aggregated = _new_aggregated()
        all_messages: list = []
        fetch_qwen._process_projects_dir(project, "host1", "carol", aggregated, all_messages)
        updated = fetch_qwen.update_agent_sessions_stats(all_messages)
        assert updated >= 1
        row = pg_db.fetch_one(
            "SELECT user_id, tool_name FROM agent_sessions WHERE user_id = %s",
            (uid,),
        )
        assert row is not None and row["tool_name"] == "qwen"

    def test_user_id_resolution(self, pg_db, tmp_path):
        """The collection loop's user_id (resolved per system_account) is
        attached verbatim to every collected message."""
        tenant = _insert_tenant(pg_db, "tenant_ur")
        uid = _insert_user(pg_db, "dana", system_account="dana-acct", tenant_id=tenant)
        resolved = fetch_qwen._resolve_user_id(None, "dana-acct")
        assert resolved == uid
        project = _write_project_jsonl(tmp_path, "dana", "host2", self._sample_entries())
        aggregated = _new_aggregated()
        all_messages: list = []
        fetch_qwen._process_projects_dir(
            project, "host2", "dana-acct", aggregated, all_messages, user_id=resolved
        )
        assert all_messages and {m["user_id"] for m in all_messages} == {uid}

    def test_tenant_attribution(self, pg_db):
        """Rows collected with user_id land inside the tenant's summary scope."""
        tenant = _insert_tenant(pg_db, "tenant_ta")
        uid = _insert_user(pg_db, "evan", system_account="evan-acct", tenant_id=tenant)
        pg_db.fetch_one(
            "INSERT INTO daily_messages (date, tool_name, host_name, message_id, role, "
            "sender_name, user_id, tokens_used) VALUES (%s, 'qwen', 'localhost', %s, 'user', %s, %s, %s) "
            "RETURNING id",
            ("2026-01-06", "msg-ta-1", "evan-acct-host-qwen", uid, 250),
            commit=True,
        )
        from app.repositories.usage_repo import UsageRepository

        summary = UsageRepository(db=pg_db).get_summary_by_tool(tenant_id=tenant)
        assert summary["qwen"]["total_tokens"] == 250

    def test_coverage_data_in_result(self, tmp_path):
        """The daily aggregate carries the coverage data the UI renders:
        token buckets per day and the model set actually used."""
        project = _write_project_jsonl(tmp_path, "frank", "host1", self._sample_entries())
        aggregated = _new_aggregated()
        all_messages: list = []
        fetch_qwen._process_projects_dir(project, "host1", "frank", aggregated, all_messages)
        assert aggregated, "daily aggregates must be produced"
        day = next(iter(aggregated.values()))
        assert day["total_tokens"] == 140
        assert day["request_count"] == 1
        assert "qwen-max" in day["models_used"]
