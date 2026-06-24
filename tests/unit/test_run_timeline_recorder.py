"""Unit tests for the Run Timeline recorder (DbRunRecorder + factory).

Validates: feature-flag → recorder-type selection, the NullRunRecorder no-op
contract, attribution persistence, cumulative usage accumulation, the approval
request/response state machine, the never-raise contract, and that the timeline
survives a process restart (a fresh recorder reads prior persisted data).
"""

from unittest.mock import patch

import pytest

import app.repositories.database as db_mod
from app.modules.workspace.run_timeline import get_ddl_statements
from app.modules.workspace.run_timeline.recorder import (
    DbRunRecorder,
    NullRunRecorder,
    RunRecorder,
    get_run_recorder,
    reset_run_recorder_for_tests,
)
from app.repositories.database import Database
from app.repositories.run_timeline_repo import RunTimelineRepository


@pytest.fixture
def rt_db(tmp_path):
    """Temp SQLite DB with run-timeline tables; SQLite code path forced."""
    db_path = str(tmp_path / "rt_rec_test.db")
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


def _recorder(rt_db):
    return DbRunRecorder(repo=RunTimelineRepository(db=rt_db))


# ── Factory / flag selection ──────────────────────────────────────────────


class TestFactory:
    def setup_method(self):
        reset_run_recorder_for_tests()

    def teardown_method(self):
        reset_run_recorder_for_tests()

    def test_disabled_flag_yields_null_recorder(self):
        with patch("app.utils.config.is_run_timeline_enabled", return_value=False):
            rec = get_run_recorder()
        assert isinstance(rec, NullRunRecorder)
        assert rec.is_noop is True

    def test_enabled_flag_yields_db_recorder(self):
        with patch("app.utils.config.is_run_timeline_enabled", return_value=True):
            rec = get_run_recorder()
        assert isinstance(rec, DbRunRecorder)
        assert rec.is_noop is False

    def test_factory_caches_singleton(self):
        with patch("app.utils.config.is_run_timeline_enabled", return_value=True):
            a = get_run_recorder()
            b = get_run_recorder()
        assert a is b


class TestNullRecorder:
    def test_all_methods_are_noops_and_never_touch_repo(self):
        repo = RunTimelineRepository  # class, not instance — would raise if called
        rec = NullRunRecorder()
        # Every method must accept the call and return None without calling repo.
        assert rec.record_session_created("s", user_id=1) is None
        assert rec.record_run_status("s", "completed") is None
        assert rec.record_event("s", "tool_use") is None
        assert rec.record_usage("s", {"input": 5, "output": 5}) is None
        assert rec.record_approval_request("s", {"request": {}}) is None
        assert rec.record_approval_response("s", "req-1", "allow", decided_by=1) is None
        # repo was never instantiated.
        del repo


# ── DbRunRecorder behaviour ───────────────────────────────────────────────


class TestDbRecorder:
    def test_record_session_created_persists_run_and_event(self, rt_db):
        rec = _recorder(rt_db)
        repo = rec._repo
        rec.record_session_created(
            "sess-1",
            user_id=7,
            tenant_id=5,
            tool_name="claude-code",
            provider="anthropic",
            cli_tool="claude-code",
            model="sonnet",
            title="My run",
        )
        run = repo.get_run_by_session("sess-1")
        assert run is not None
        assert run.user_id == 7
        assert run.tenant_id == 5
        assert run.tool_name == "claude-code"
        assert run.provider == "anthropic"
        assert run.model == "sonnet"
        assert run.status == "active"

        events = repo.query_events("sess-1")
        assert [e.event_type for e in events] == ["session_created"]

    def test_record_event_appends(self, rt_db):
        rec = _recorder(rt_db)
        repo = rec._repo
        rec.record_session_created("sess-1", user_id=1, tool_name="t")
        rec.record_event("sess-1", "user_message", role="user", content="hello")
        rec.record_event("sess-1", "tool_use", tool_name="Bash")
        types = [e.event_type for e in repo.query_events("sess-1")]
        assert types == ["session_created", "user_message", "tool_use"]
        # Per-event attribution is inherited from the run.
        msg = repo.query_events("sess-1", event_type="user_message")[0]
        assert msg.user_id == 1
        assert msg.tool_name == "t"

    def test_record_usage_accumulates_cumulative_snapshot(self, rt_db):
        rec = _recorder(rt_db)
        repo = rec._repo
        rec.record_session_created("sess-1", user_id=1)
        rec.record_usage("sess-1", {"input": 100, "output": 20}, requests=1)
        rec.record_usage("sess-1", {"input": 50, "output": 10}, requests=1)
        run = repo.get_run_by_session("sess-1")
        assert run.total_tokens == 180  # (100+20) + (50+10)
        assert run.total_input_tokens == 150
        assert run.total_output_tokens == 30
        assert run.total_requests == 2
        # Each usage report also emits a usage_reported event.
        usage_events = repo.query_events("sess-1", event_type="usage_reported")
        assert len(usage_events) == 2

    def test_record_run_status_updates_lifecycle(self, rt_db):
        rec = _recorder(rt_db)
        repo = rec._repo
        rec.record_session_created("sess-1")
        rec.record_run_status("sess-1", "completed")
        run = repo.get_run_by_session("sess-1")
        assert run.status == "completed"
        assert run.ended_at is not None

    def test_approval_request_then_response(self, rt_db):
        rec = _recorder(rt_db)
        repo = rec._repo
        rec.record_session_created("sess-1", tool_name="claude-code")
        rec.record_approval_request(
            "sess-1",
            {"request": {"request_id": "req-1", "subtype": "execute", "tool_name": "Bash"}},
        )
        a = repo.get_approval("req-1")
        assert a is not None
        assert a.status == "pending"
        assert a.tool_name == "Bash"

        rec.record_approval_response(
            "sess-1", "req-1", "allow", decided_by=7, decided_by_name="alice"
        )
        a = repo.get_approval("req-1")
        assert a.status == "approved"
        assert a.decision == "allow"
        assert a.decided_by == 7
        # A permission_answered event was emitted.
        answered = repo.query_events("sess-1", event_type="permission_answered")
        assert len(answered) == 1
        assert answered[0].content == "allow"

    def test_approval_response_falls_back_to_latest_pending(self, rt_db):
        rec = _recorder(rt_db)
        repo = rec._repo
        rec.record_session_created("sess-1")
        rec.record_approval_request("sess-1", {"request": {"request_id": "req-1"}})
        # No request_id passed → recorder resolves the latest pending.
        rec.record_approval_response("sess-1", None, "deny", decided_by=2)
        assert repo.get_approval("req-1").status == "denied"


# ── Contracts ─────────────────────────────────────────────────────────────


class TestContracts:
    def test_recorder_never_raises_to_caller(self, rt_db):
        rec = _recorder(rt_db)
        # Corrupt the repo so every call raises; recorder must swallow it.
        rec._repo = None  # type: ignore[assignment]
        # None of these should raise despite the broken repo.
        rec.record_session_created("s", user_id=1)
        rec.record_event("s", "tool_use")
        rec.record_usage("s", {"input": 1, "output": 1})
        rec.record_run_status("s", "completed")
        rec.record_approval_request("s", {"request": {}})
        rec.record_approval_response("s", "req-1", "allow", decided_by=1)

    def test_base_recorder_is_abstract(self):
        base = RunRecorder()
        assert base.is_noop is False
        with pytest.raises(NotImplementedError):
            base.record_event("s", "x")


class TestPersistenceSurvivesRestart:
    def test_new_recorder_reads_prior_data(self, rt_db):
        # Recorder A records a session.
        repo = RunTimelineRepository(db=rt_db)
        rec_a = DbRunRecorder(repo=repo)
        rec_a.record_session_created("sess-1", user_id=9, tool_name="t", model="m")
        rec_a.record_event("sess-1", "user_message", content="hi")

        # Simulate a restart: a brand-new recorder/repo against the same DB.
        repo_b = RunTimelineRepository(db=rt_db)
        rec_b = DbRunRecorder(repo=repo_b)
        # New recorder hasn't seen this session yet, but the data persisted.
        run = repo_b.get_run_by_session("sess-1")
        assert run is not None
        assert run.user_id == 9
        events = repo_b.query_events("sess-1")
        assert [e.event_type for e in events] == ["session_created", "user_message"]
        # And the new recorder can append to the existing run.
        rec_b.record_event("sess-1", "tool_use", tool_name="Edit")
        assert len(repo_b.query_events("sess-1")) == 3
