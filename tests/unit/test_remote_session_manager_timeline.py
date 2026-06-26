"""Tests for the run-timeline integration seam inside RemoteSessionManager.

These guard the *wiring*, not the recorder: they assert that each manager
lifecycle/output method actually funnels through the single ``_timeline`` seam
into the recorder with the expected call. Without them, a future refactor of
``remote_session_manager`` could silently drop a recorder call (e.g. rename a
method, drop a hook) while every recorder/repo test still passes — exactly the
"quietly broken integration" gap flagged in the PR review.

The manager is built with ``__new__`` + mocked dependencies so these run without
a database; only the real method bodies and the ``_timeline`` delegation execute.
"""

from unittest.mock import MagicMock

import pytest

from app.modules.policy.evaluator import NullPolicyEvaluator
from app.modules.workspace.remote_session_manager import RemoteSessionManager
from app.modules.workspace.run_timeline.recorder import NullRunRecorder


@pytest.fixture
def spy_recorder():
    rec = MagicMock()
    rec.is_noop = False
    return rec


@pytest.fixture
def manager(spy_recorder):
    """A RemoteSessionManager whose dependencies are mocks; recorder is a spy.

    Built via __new__ to skip __init__'s real SessionManager/agent-manager
    construction (which needs a DB). ``_get_machine_id`` resolves against the
    mocked agent manager and returns a truthy value, so the lifecycle methods
    proceed past their early-return guards.
    """
    mgr = RemoteSessionManager.__new__(RemoteSessionManager)
    mgr._session_manager = MagicMock()
    mgr._agent_manager = MagicMock()
    mgr._session_permission_modes = {}
    mgr._run_recorder = spy_recorder
    # Policy evaluator stands in for __init__'s get_evaluator(); Null mirrors
    # the default disabled state so the consume chokepoint is a no-op here.
    mgr._policy_evaluator = NullPolicyEvaluator()
    return mgr


# ── the _timeline seam itself ──────────────────────────────────────────────


class TestTimelineSeam:
    def test_delegates_to_recorder_when_enabled(self, manager, spy_recorder):
        manager._timeline("record_run_status", "sess-1", "completed")
        spy_recorder.record_run_status.assert_called_once_with("sess-1", "completed")

    def test_short_circuits_when_recorder_is_noop(self):
        # A noop recorder must never reach getattr() — a bogus method name must
        # not raise. This is the property that lets the feature be removed by
        # deleting the recorder without touching call sites.
        mgr = RemoteSessionManager.__new__(RemoteSessionManager)
        mgr._run_recorder = NullRunRecorder()
        mgr._timeline("this_method_does_not_exist", "sess-1")

    def test_noop_recorder_is_never_called(self):
        mgr = RemoteSessionManager.__new__(RemoteSessionManager)
        noop = NullRunRecorder()
        # Spy on the real noop instance.
        noop.record_run_status = MagicMock(return_value=None)  # type: ignore[assignment]
        mgr._run_recorder = noop
        mgr._timeline("record_run_status", "sess-1", "completed")
        noop.record_run_status.assert_not_called()

    def test_unknown_method_does_not_raise_when_enabled(self):
        # Enabled recorder + a typo'd method name must not raise on the hot path.
        # A real recorder object (not a MagicMock, which auto-vivifies attrs) is
        # used so getattr(missing) genuinely returns None.
        class EnabledStub:
            is_noop = False

            def record_run_status(self, *args, **kwargs):
                self.dispatched = (args, kwargs)

        mgr = RemoteSessionManager.__new__(RemoteSessionManager)
        stub = EnabledStub()
        mgr._run_recorder = stub
        mgr._timeline("record_typo_does_not_exist", "sess-1")  # must not raise
        assert not hasattr(stub, "dispatched")  # the real method was not reached
        # A valid method still dispatches.
        mgr._timeline("record_run_status", "sess-1", "completed")
        assert stub.dispatched == (("sess-1", "completed"), {})

    def test_swallows_unexpected_recorder_exception(self):
        # Even if a recorder method raised, _timeline must not propagate it.
        mgr = RemoteSessionManager.__new__(RemoteSessionManager)
        bad = MagicMock()
        bad.is_noop = False
        bad.record_run_status.side_effect = RuntimeError("boom")
        mgr._run_recorder = bad
        mgr._timeline("record_run_status", "sess-1", "completed")  # must not raise
        bad.record_run_status.assert_called_once_with("sess-1", "completed")


# ── manager lifecycle → recorder wiring ────────────────────────────────────


class TestManagerRecorderWiring:
    def test_send_message_records_user_message_event(self, manager, spy_recorder):
        manager.send_message("sess-1", "hello there")
        spy_recorder.record_event.assert_called_once_with(
            "sess-1", "user_message", role="user", content="hello there"
        )

    def test_stop_session_records_completed_status(self, manager, spy_recorder):
        manager.stop_session("sess-1")
        spy_recorder.record_run_status.assert_called_once_with("sess-1", "completed")

    def test_pause_and_resume_record_lifecycle(self, manager, spy_recorder):
        manager.pause_session("sess-1")
        manager.resume_session("sess-1")
        assert spy_recorder.record_run_status.call_count == 2
        assert spy_recorder.record_run_status.call_args_list[0].args == ("sess-1", "pause")
        assert spy_recorder.record_run_status.call_args_list[1].args == ("sess-1", "resume")

    def test_abort_request_records_request_aborted(self, manager, spy_recorder):
        manager.abort_request("sess-1", reason="user")
        spy_recorder.record_event.assert_called_once_with(
            "sess-1", "request_aborted", metadata={"reason": "user"}
        )

    def test_respond_to_permission_records_approval_response(self, manager, spy_recorder):
        manager.respond_to_permission(
            "sess-1",
            "req-1",
            "allow",
            tool_name="Bash",
            decided_by=7,
            decided_by_name="alice",
        )
        spy_recorder.record_approval_response.assert_called_once()
        _, args, kwargs = spy_recorder.record_approval_response.mock_calls[0]
        assert args[0] == "sess-1"
        assert args[1] == "req-1"
        assert args[2] == "allow"
        assert kwargs["decided_by"] == 7
        assert kwargs["decided_by_name"] == "alice"
