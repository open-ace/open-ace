"""Unit tests for the Run Timeline repository (SQLite-backed).

Exercises the real SQL against an isolated temp SQLite database so that
idempotency, ordering, cursor pagination, and the approval upsert/response
state machine are validated end-to-end (not just SQL-string structure).
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

import app.repositories.database as db_mod
from app.modules.workspace.run_timeline import get_ddl_statements
from app.modules.workspace.run_timeline.models import RunEvent, _dump_json, _parse_json
from app.repositories.database import Database
from app.repositories.run_timeline_repo import RunTimelineRepository

TABLES = ("agent_runs", "agent_run_events", "agent_approvals")


@pytest.fixture
def rt_db(tmp_path):
    """Temp SQLite database with the run-timeline tables created.

    Forces the SQLite code path by patching is_postgresql to False in every
    module that consults it at runtime — the DDL generator (so tables use
    INTEGER PRIMARY KEY AUTOINCREMENT, not SERIAL), the Database layer
    (adapt_sql placeholders), and the repo (RETURNING-vs-lastrowid branch) —
    so tests are correct regardless of the dev box's configured DATABASE_URL.
    """
    db_path = str(tmp_path / "rt_test.db")
    db = Database(db_url=f"sqlite:///{db_path}")
    with (
        patch.object(db_mod, "is_postgresql", return_value=False),
        patch("app.repositories.run_timeline_repo.is_postgresql", return_value=False),
        patch("app.modules.workspace.run_timeline.is_postgresql", return_value=False),
    ):
        conn = db.get_connection()
        try:
            cur = conn.cursor()
            for sql in get_ddl_statements():
                cur.execute(sql)
            conn.commit()
        finally:
            conn.close()
        yield db


@pytest.fixture
def repo(rt_db):
    return RunTimelineRepository(db=rt_db)


def _table_exists(rt_db, name):
    row = rt_db.fetch_one("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return row is not None


class TestDDL:
    def test_tables_created(self, rt_db):
        for t in TABLES:
            assert _table_exists(rt_db, t), f"{t} not created"

    def test_ddl_idempotent(self, rt_db):
        # Running the DDL again must not raise (CREATE ... IF NOT EXISTS).
        conn = rt_db.get_connection()
        try:
            cur = conn.cursor()
            for sql in get_ddl_statements():
                cur.execute(sql)
            conn.commit()
        finally:
            conn.close()
        for t in TABLES:
            assert _table_exists(rt_db, t)


class TestRuns:
    def test_ensure_run_idempotent(self, repo):
        repo.ensure_run("run-1", "sess-1", user_id=7, tool_name="claude-code", status="active")
        repo.ensure_run("run-1-dup", "sess-1", user_id=8)  # same session_id → no-op
        run = repo.get_run_by_session("sess-1")
        assert run is not None
        assert run.run_id == "run-1"  # original preserved
        assert run.user_id == 7
        assert run.tool_name == "claude-code"
        assert run.status == "active"

    def test_update_run_status_sets_ended_at_on_terminal(self, repo):
        repo.ensure_run("run-1", "sess-1")
        repo.update_run_status("sess-1", "completed")
        run = repo.get_run_by_session("sess-1")
        assert run.status == "completed"
        assert run.ended_at is not None

    def test_update_run_status_paused_keeps_ended_null(self, repo):
        repo.ensure_run("run-1", "sess-1")
        repo.update_run_status("sess-1", "paused")
        run = repo.get_run_by_session("sess-1")
        assert run.status == "paused"
        assert run.ended_at is None

    def test_update_run_usage_sets_snapshot(self, repo):
        # The repo stores an absolute cumulative snapshot; accumulation across
        # reports is the recorder's job (read current + add delta).
        repo.ensure_run("run-1", "sess-1")
        repo.update_run_usage("sess-1", 100, 80, 20, 1)
        run = repo.get_run_by_session("sess-1")
        assert run.total_tokens == 100
        assert run.total_input_tokens == 80
        assert run.total_output_tokens == 20
        assert run.total_requests == 1


class TestEvents:
    def _seed_run(self, repo, session_id="sess-1"):
        repo.ensure_run(session_id, session_id)

    def test_append_returns_monotonic_ids(self, repo):
        self._seed_run(repo)
        id1 = repo.append_event("run-1", "sess-1", "session_created")
        id2 = repo.append_event("run-1", "sess-1", "user_message", content="hi")
        id3 = repo.append_event("run-1", "sess-1", "tool_use", tool_name="Bash")
        assert isinstance(id1, int) and id2 > id1 and id3 > id2

    def test_query_events_asc_and_desc(self, repo):
        self._seed_run(repo)
        for et in ("session_created", "user_message", "tool_use", "stop"):
            repo.append_event("run-1", "sess-1", et)

        asc = repo.query_events("sess-1", order="asc")
        assert [e.event_type for e in asc] == [
            "session_created",
            "user_message",
            "tool_use",
            "stop",
        ]
        desc = repo.query_events("sess-1", order="desc")
        assert [e.event_type for e in desc] == list(reversed([e.event_type for e in asc]))

    def test_query_events_after_cursor(self, repo):
        self._seed_run(repo)
        ids = [
            repo.append_event("run-1", "sess-1", et)
            for et in ("session_created", "user_message", "tool_use")
        ]
        # Events strictly after the first id → the remaining two.
        page = repo.query_events("sess-1", after_id=ids[0], order="asc")
        assert [e.id for e in page] == ids[1:]

    def test_query_events_event_type_filter_and_count(self, repo):
        self._seed_run(repo)
        repo.append_event("run-1", "sess-1", "user_message")
        repo.append_event("run-1", "sess-1", "tool_use")
        repo.append_event("run-1", "sess-1", "tool_use", tool_name="Edit")

        tool_events = repo.query_events("sess-1", event_type="tool_use")
        assert len(tool_events) == 2
        assert repo.count_events("sess-1", event_type="tool_use") == 2
        assert repo.count_events("sess-1") == 3

    def test_query_events_since_until_window(self, repo):
        self._seed_run(repo)
        t0 = datetime.utcnow() - timedelta(hours=2)
        t1 = datetime.utcnow() - timedelta(minutes=30)
        t2 = datetime.utcnow()
        repo.append_event("run-1", "sess-1", "user_message", event_ts=t0)
        repo.append_event("run-1", "sess-1", "tool_use", event_ts=t1)
        repo.append_event("run-1", "sess-1", "stop", event_ts=t2)

        in_window = repo.query_events("sess-1", since=t0 + timedelta(hours=1), until=t2)
        assert [e.event_type for e in in_window] == ["tool_use", "stop"]

    def test_prune_events_before(self, repo):
        self._seed_run(repo)
        old = datetime.utcnow() - timedelta(days=10)
        repo.append_event("run-1", "sess-1", "user_message", event_ts=old)
        repo.append_event("run-1", "sess-1", "tool_use", event_ts=datetime.utcnow())
        # prune uses created_at; sleep-free: delete everything older than now.
        deleted = repo.prune_events_before(datetime.utcnow() + timedelta(seconds=1))
        assert deleted == 2
        assert repo.count_events("sess-1") == 0


class TestApprovals:
    def test_upsert_then_respond(self, repo):
        repo.ensure_run("run-1", "sess-1")
        repo.upsert_approval_request(
            "req-1", "run-1", "sess-1", tool_name="Bash", request_subtype="execute"
        )
        a = repo.get_approval("req-1")
        assert a is not None
        assert a.status == "pending"
        assert a.tool_name == "Bash"

        rows = repo.update_approval_response(
            "req-1", "allow", decided_by=7, decided_by_name="alice"
        )
        assert rows == 1
        a = repo.get_approval("req-1")
        assert a.status == "approved"
        assert a.decision == "allow"
        assert a.decided_by == 7
        assert a.decided_by_name == "alice"

    def test_respond_twice_second_is_noop(self, repo):
        repo.ensure_run("run-1", "sess-1")
        repo.upsert_approval_request("req-1", "run-1", "sess-1")
        assert repo.update_approval_response("req-1", "allow") == 1
        # Already resolved (not pending) → 0 rows.
        assert repo.update_approval_response("req-1", "deny") == 0
        assert repo.get_approval("req-1").decision == "allow"

    def test_upsert_resets_resolved_to_pending(self, repo):
        repo.ensure_run("run-1", "sess-1")
        repo.upsert_approval_request("req-1", "run-1", "sess-1")
        repo.update_approval_response("req-1", "deny")
        assert repo.get_approval("req-1").status == "denied"

        # Agent re-requests the same request_id → reset to pending.
        repo.upsert_approval_request("req-1", "run-1", "sess-1")
        a = repo.get_approval("req-1")
        assert a.status == "pending"
        assert a.decision is None
        assert a.decided_by is None

    def test_latest_pending_and_list_ordering(self, repo):
        repo.ensure_run("run-1", "sess-1")
        repo.upsert_approval_request("req-1", "run-1", "sess-1")
        repo.upsert_approval_request("req-2", "run-1", "sess-1")
        repo.update_approval_response("req-1", "allow")

        latest = repo.get_latest_pending_approval("sess-1")
        assert latest is not None
        assert latest.request_id == "req-2"

        listed = repo.list_approvals("sess-1")
        assert [a.request_id for a in listed] == ["req-1", "req-2"]


class TestModels:
    def test_parse_json_robust(self):
        assert _parse_json(None) is None
        assert _parse_json("") is None
        assert _parse_json('{"a": 1}') == {"a": 1}
        assert _parse_json("[1, 2]") == [1, 2]
        assert _parse_json("not json") is None  # never raises
        assert _parse_json({"already": "dict"}) == {"already": "dict"}

    def test_dump_json_empty_returns_none(self):
        assert _dump_json(None) is None
        assert _dump_json({}) is None
        assert _dump_json([]) is None
        assert _dump_json({"a": 1}) == '{"a": 1}'

    def test_run_event_round_trip(self, repo):
        repo.ensure_run("run-1", "sess-1")
        repo.append_event("run-1", "sess-1", "tool_use", tool_name="Bash", metadata={"x": 1})
        ev = repo.query_events("sess-1")[0]
        d = ev.to_dict()
        assert d["event_type"] == "tool_use"
        assert d["metadata"] == {"x": 1}
        # from_row(to_dict()) round-trips without losing data.
        rebuilt = RunEvent.from_row(d)
        assert rebuilt.event_type == "tool_use"
        assert rebuilt.tool_name == "Bash"
        assert rebuilt.metadata == {"x": 1}

    def test_approval_to_dict_includes_json_fields(self, repo):
        repo.ensure_run("run-1", "sess-1")
        repo.upsert_approval_request("req-1", "run-1", "sess-1", request_details={"tool": "Bash"})
        a = repo.get_approval("req-1")
        d = a.to_dict()
        assert d["request_details"] == {"tool": "Bash"}
        assert d["decision_metadata"] == {}  # default empty dict, not None


class TestIncrementRunUsage:
    """The atomic ``UPDATE ... SET col = col + ?`` replaces the prior
    read-modify-write (which cost a SELECT and lost updates under concurrency)."""

    def test_accumulates_across_calls(self, repo):
        repo.ensure_run(run_id="s1", session_id="s1", user_id=1)
        repo.increment_run_usage("s1", 120, 100, 20, 1)
        repo.increment_run_usage("s1", 60, 50, 10, 1)
        run = repo.get_run_by_session("s1")
        assert run.total_tokens == 180
        assert run.total_input_tokens == 150
        assert run.total_output_tokens == 30
        assert run.total_requests == 2

    def test_handles_null_columns(self, repo):
        # COALESCE(..., 0) keeps the increment safe even if a column is NULL.
        repo.ensure_run(run_id="s2", session_id="s2")
        repo.db.execute("UPDATE agent_runs SET total_tokens=NULL WHERE session_id=?", ("s2",))
        repo.increment_run_usage("s2", 5, 3, 2, 1)
        run = repo.get_run_by_session("s2")
        assert run.total_tokens == 5
        assert run.total_requests == 1
