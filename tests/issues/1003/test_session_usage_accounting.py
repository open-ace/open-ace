"""Tests for unified session-counter accounting (#1003).

``agent_sessions.{request_count,total_tokens,total_input_tokens,
total_output_tokens}`` must accumulate monotonically (per-call increment from
the agent runner, NOT overwrite) so ``Σ milestone.phase_* == session.*`` holds.

Background (#1003 review): the column was written by three parties with
different semantics — llm_proxy_handler (+= per proxy call; does NOT fire for
autonomous local runs, which bypass the proxy), session_manager.add_message
(+= per message), and agent_runner.update_session_fields (= overwrite with the
per-call local count, clobbering the others). The overwrite made the column
unstable, so Σ milestone never matched the session. The fix: the agent runner
now INCREMENTS via ``increment_session_usage``, and ``add_message`` only owns
``message_count``.
"""

from unittest.mock import MagicMock

import pytest

from app.modules.workspace import session_manager as sm_mod
from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner
from app.modules.workspace.session_manager import SessionManager


@pytest.fixture
def sqlite_sm(tmp_path, monkeypatch):
    """A SessionManager over a temp SQLite DB (force non-postgres)."""
    monkeypatch.setattr(sm_mod, "is_postgresql", lambda: False)
    sm = SessionManager(db_path=str(tmp_path / "test_sessions.db"))
    sm._ensure_tables()
    # _ensure_tables builds the base schema; project_id/project_path come from
    # Alembic migrations on a real DB. Add them so create_session's INSERT works.
    conn = sm._get_connection()
    cur = conn.cursor()
    for col in ("project_id", "project_path"):
        try:
            cur.execute(f"ALTER TABLE agent_sessions ADD COLUMN {col} TEXT")
        except Exception:
            pass
    conn.commit()
    conn.close()
    return sm


def _create(sm, sid="sess-1"):
    return sm.create_session(tool_name="test", session_id=sid, user_id=1)


# ── increment_session_usage ──────────────────────────────────────────────


class TestIncrementSessionUsage:
    def test_accumulates_across_calls(self, sqlite_sm):
        sm = sqlite_sm
        _create(sm)
        sm.increment_session_usage(
            "sess-1",
            request_delta=3,
            total_tokens_delta=100,
            total_input_delta=80,
            total_output_delta=20,
        )
        sm.increment_session_usage(
            "sess-1",
            request_delta=2,
            total_tokens_delta=50,
            total_input_delta=40,
            total_output_delta=10,
        )
        s = sm.get_session("sess-1")
        assert s.request_count == 5  # 3 + 2, not overwritten to 2
        assert s.total_tokens == 150
        assert s.total_input_tokens == 120
        assert s.total_output_tokens == 30

    def test_starts_from_existing_value(self, sqlite_sm):
        # simulate a session that already has counts (e.g. resumed)
        sm = sqlite_sm
        _create(sm)
        sm.increment_session_usage("sess-1", request_delta=10, total_tokens_delta=1000)
        sm.increment_session_usage("sess-1", request_delta=3, total_tokens_delta=150)
        s = sm.get_session("sess-1")
        assert s.request_count == 13
        assert s.total_tokens == 1150

    def test_missing_session_returns_false(self, sqlite_sm):
        assert sqlite_sm.increment_session_usage("nope", request_delta=1) is False

    def test_empty_session_id_returns_false(self, sqlite_sm):
        assert sqlite_sm.increment_session_usage("", request_delta=1) is False


# ── add_message counter accounting (count_usage gate) ────────────────────


class TestAddMessageCountUsageGate:
    def test_autonomous_count_usage_false_leaves_counters(self, sqlite_sm):
        """Autonomous runner passes count_usage=False (it owns the counters via
        increment_session_usage), so add_message only bumps message_count."""
        sm = sqlite_sm
        _create(sm)
        sm.increment_session_usage("sess-1", request_delta=5, total_tokens_delta=500)
        sm.add_message(session_id="sess-1", role="assistant", content="hi", count_usage=False)
        sm.add_message(session_id="sess-1", role="tool", content="result", count_usage=False)
        s = sm.get_session("sess-1")
        assert s.request_count == 5  # unchanged
        assert s.total_tokens == 500  # unchanged
        assert s.message_count == 2  # add_message still owns this

    def test_non_autonomous_default_accumulates(self, sqlite_sm):
        """Non-autonomous callers (remote_session_manager, session_sync) rely on
        add_message for request_count/total_tokens — the default count_usage=True
        must keep accumulating (regression guard for the #1007 review)."""
        sm = sqlite_sm
        _create(sm)
        sm.add_message(
            session_id="sess-1", role="assistant", content="hi", tokens_used=120
        )  # default count_usage=True
        sm.add_message(session_id="sess-1", role="assistant", content="bye", tokens_used=80)
        sm.add_message(session_id="sess-1", role="tool", content="r")  # tool: no request bump
        s = sm.get_session("sess-1")
        assert s.request_count == 2  # only assistant messages count
        assert s.total_tokens == 200  # 120 + 80
        assert s.message_count == 3


# ── sidebar streaming sync must NOT write counters (Blocker from #1007 review)


class TestSidebarSyncOmitsCounters:
    """For local claude-code (sidebar session source), _sync_sidebar_session_totals
    fires per assistant turn during streaming. It must NOT write the counters —
    the finish-path increment_session_usage owns them, or it double-counts."""

    def test_sidebar_sync_does_not_set_counters(self):
        runner = AutonomousAgentRunner(session_manager=MagicMock())
        session = MagicMock()
        session.cli_tool = "claude-code"
        session.workspace_type = "local"
        session.persisted_session_id = "sidebar-1"
        session.encoded_project_path = "-tmp-proj"
        session.user_id = None

        runner._sync_sidebar_session_totals(session, status="active")

        fields = runner.session_manager.update_session_fields.call_args.args[1]
        for counter in (
            "request_count",
            "total_tokens",
            "total_input_tokens",
            "total_output_tokens",
        ):
            assert counter not in fields, f"{counter} must not be written by sidebar-sync"
        assert "project_path" in fields  # non-counter fields still synced


# ── the invariant: Σ milestone == session ─────────────────────────────────


class TestMilestoneSessionInvariant:
    def test_session_equals_sum_of_per_call_deltas(self, sqlite_sm):
        """Each agent call increments the session by its result.request_count
        (and stores that same value on its milestone). After N calls the
        session total equals Σ milestone values."""
        sm = sqlite_sm
        _create(sm)
        per_call_request = [1, 4, 2, 3]
        per_call_tokens = [c * 100 for c in per_call_request]
        for rc, tk in zip(per_call_request, per_call_tokens):
            # agent_runner finish path: increment session by this call's counts
            sm.increment_session_usage("sess-1", request_delta=rc, total_tokens_delta=tk)

        s = sm.get_session("sess-1")
        # Σ milestone.phase_request_count == session.request_count
        assert s.request_count == sum(per_call_request)
        assert s.total_tokens == sum(per_call_tokens)
