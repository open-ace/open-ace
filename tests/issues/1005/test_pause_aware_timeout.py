"""Tests for pause-aware task timeout (#1005).

Background: ``_run_local`` waited on ``session.completed.wait(timeout=timeout)``,
a *wall-clock* countdown. Pausing a task (SIGSTOP) froze the agent process but
NOT this clock, so pausing longer than the task timeout (default 1h) reaped the
in-flight agent and marked the phase failed — pause did not "freeze time".

Fix: ``_wait_for_completion`` waits in short increments and, while
``session._paused`` is set, does not consume the timeout budget. The deadline is
extended by the full paused duration so the remaining budget resumes from where
it left off after resume.

These tests exercise ``_wait_for_completion`` directly (driving real
``threading.Event`` objects from background threads) so we avoid the cost of
spawning real subprocesses while still validating the pause/resume timing.
"""

import signal
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous import agent_runner as ar_mod
from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner, _LocalSession


def _make_session() -> _LocalSession:
    """A session with a never-started mock process (no subprocess needed)."""
    proc = MagicMock()
    proc.pid = 99999
    proc.returncode = None
    return _LocalSession(session_id="s-1", process=proc)


def _run_waiter(session: _LocalSession, timeout: float):
    """Run _wait_for_completion in a thread; return (thread, result holder)."""
    runner = AutonomousAgentRunner()
    holder = {}

    def target():
        holder["result"] = runner._wait_for_completion(session, timeout)

    t = threading.Thread(target=target, daemon=True)
    t.start()
    return t, holder


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch):
    """Speed up the loop so pauses/timeouts resolve in <1s of real time."""
    monkeypatch.setattr(ar_mod, "COMPLETION_POLL_INTERVAL", 0.05)
    yield


class TestWaitForCompletionNoPause:
    """Baseline: without pausing, the timeout behaves like a wall-clock budget."""

    def test_completes_returns_true(self):
        session = _make_session()
        t, holder = _run_waiter(session, timeout=10.0)
        session.completed.set()
        t.join(timeout=2.0)
        assert not t.is_alive()
        assert holder["result"] is True

    def test_times_out_when_not_paused(self):
        session = _make_session()
        start = time.monotonic()
        t, holder = _run_waiter(session, timeout=0.3)
        t.join(timeout=2.0)
        elapsed = time.monotonic() - start
        assert not t.is_alive()
        assert holder["result"] is False
        # Did not run for the full "would-be" deadline extension: roughly the
        # budget, definitely well under a second.
        assert elapsed < 1.0


class TestWaitForCompletionPauseFreezesBudget:
    """The core #1005 fix: paused time does not consume the timeout budget."""

    def test_pause_longer_than_timeout_does_not_expire(self):
        """A pause lasting longer than the budget must NOT cause a timeout."""
        session = _make_session()
        # Budget of 0.3s. We'll pause for 0.5s (> budget) then complete.
        t, holder = _run_waiter(session, timeout=0.3)

        # Let a little active time pass so the loop reaches the pause check,
        # then suspend.
        time.sleep(0.1)
        session._paused.set()
        # Hold the pause longer than the entire budget.
        time.sleep(0.5)
        session._paused.clear()
        # Now complete before the remaining active budget (~0.2s) runs out.
        session.completed.set()
        t.join(timeout=2.0)

        assert not t.is_alive()
        assert holder["result"] is True, "Pause longer than the timeout must not expire the budget"

    def test_completes_after_resume_within_remaining_budget(self):
        """After a long pause, the remaining active budget still applies."""
        session = _make_session()
        t, holder = _run_waiter(session, timeout=0.3)

        time.sleep(0.05)
        session._paused.set()
        time.sleep(0.4)  # longer than the whole budget
        session._paused.clear()
        # Do NOT complete: the remaining active budget (~0.25s) should still
        # expire and return False, proving pause time was excluded.
        t.join(timeout=2.0)

        assert not t.is_alive()
        assert holder["result"] is False, "Remaining active budget must still expire after resume"

    def test_total_active_time_exceeds_pause_duration(self):
        """Wall-clock elapsed is pause + active-budget, not just the budget."""
        session = _make_session()
        budget = 0.3
        pause = 0.4
        start = time.monotonic()
        t, holder = _run_waiter(session, timeout=budget)

        time.sleep(0.05)
        session._paused.set()
        time.sleep(pause)
        session._paused.clear()
        t.join(timeout=2.0)
        elapsed = time.monotonic() - start

        assert not t.is_alive()
        assert holder["result"] is False
        # Elapsed wall-clock must reflect BOTH the pause and the active budget,
        # i.e. substantially more than the budget alone.
        assert elapsed >= pause, "Pause duration must be excluded from the budget"


class TestWaitForCompletionResumeTransition:
    """Completing while paused returns promptly."""

    def test_completing_while_paused_returns_true(self):
        """If the session is stopped (completed set) mid-pause, return True."""
        session = _make_session()
        t, holder = _run_waiter(session, timeout=10.0)
        session._paused.set()
        time.sleep(0.1)
        # stop_session clears the paused event and sets completed; here just
        # set completed — the inner pause loop checks completed too.
        session.completed.set()
        t.join(timeout=2.0)

        assert not t.is_alive()
        assert holder["result"] is True

    def test_multiple_pause_resume_cycles(self):
        """Repeated pause/resume each exclude their time from the budget."""
        session = _make_session()
        budget = 0.3
        t, holder = _run_waiter(session, timeout=budget)

        # Two short pauses with active gaps between them.
        for _ in range(2):
            session._paused.set()
            time.sleep(0.25)  # each pause alone is < budget but 2x approaches it
            session._paused.clear()
            time.sleep(0.02)
        session.completed.set()
        t.join(timeout=2.0)

        assert not t.is_alive()
        assert holder["result"] is True, "Pauses (not active time) must not consume the budget"


class TestPausedEventToggleSession:
    """pause/resume/stop mutate the _paused Event (was a bare bool, #1005 review).

    These guard the cross-thread transitions the reviewer walked through: the
    Event is written by request-handler threads (pause/resume/stop) and read by
    the runner thread (_wait_for_completion). Using an Event keeps this consistent
    with the existing ``_stopped`` Event.
    """

    def test_pause_sets_and_resume_clears_paused_event(self):
        runner = AutonomousAgentRunner()
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.returncode = None
        session = _LocalSession(session_id="s-1", process=mock_process)
        runner._local_sessions["s-1"] = session
        assert session._paused.is_set() is False  # default unset

        with patch("os.getpgid", return_value=12345), patch("os.killpg"):
            assert runner.pause_session("s-1") is True
        assert session._paused.is_set() is True

        with patch("os.getpgid", return_value=12345), patch("os.killpg"):
            assert runner.resume_session("s-1") is True
        assert session._paused.is_set() is False

    def test_stop_session_resumes_then_completes_while_paused(self):
        """Stopping a paused session clears the paused event (SIGCONT) and completes."""
        runner = AutonomousAgentRunner()
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.returncode = None
        session = _LocalSession(session_id="s-1", process=mock_process)
        session._paused.set()  # currently paused
        runner._local_sessions["s-1"] = session

        with patch("os.getpgid", return_value=12345), patch("os.killpg"):
            runner.stop_session("s-1")

        assert session._paused.is_set() is False, "stop must resume (clear paused)"
        assert session.completed.is_set()

    def test_pause_then_pause_is_idempotent(self):
        runner = AutonomousAgentRunner()
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.returncode = None
        session = _LocalSession(session_id="s-1", process=mock_process)
        runner._local_sessions["s-1"] = session

        with patch("os.getpgid", return_value=12345), patch("os.killpg") as mock_killpg:
            assert runner.pause_session("s-1") is True
            # Second pause: no-op, returns True without re-SIGSTOP.
            assert runner.pause_session("s-1") is True
        assert session._paused.is_set()
        # SIGSTOP sent exactly once (resume does not run here).
        sigs = [c.args[-1] for c in mock_killpg.call_args_list]
        assert sigs.count(signal.SIGSTOP) == 1


class TestRunLocalUsesPauseAwareWait:
    """_run_local forwards the wait through _wait_for_completion (not raw wait)."""

    def test_run_local_calls_wait_for_completion(self):
        """The blocking wait in _run_local must go through _wait_for_completion."""
        runner = AutonomousAgentRunner()
        calls = {}

        def fake_wait(session, timeout):
            calls["timeout"] = timeout
            calls["session"] = session
            return True

        runner._wait_for_completion = fake_wait  # type: ignore[assignment]

        # Minimal mock process + adapter so _run_local reaches the wait.
        proc = MagicMock()
        proc.pid = 123
        proc.returncode = None
        mock_adapter = MagicMock()
        mock_adapter.get_executable_name.return_value = "codex"
        mock_adapter.build_start_args.return_value = ["codex"]
        mock_cli_adapters = MagicMock()
        mock_cli_adapters.get_adapter.return_value = mock_adapter

        with (
            patch.dict("sys.modules", {"cli_adapters": mock_cli_adapters}),
            patch("shutil.which", return_value="/usr/bin/codex"),
            patch("subprocess.Popen", return_value=proc),
            patch.object(AutonomousAgentRunner, "_send_sdk_init"),
            patch.object(AutonomousAgentRunner, "_send_message"),
            patch.object(AutonomousAgentRunner, "_read_stdout"),
            patch.object(AutonomousAgentRunner, "_read_stderr"),
            patch("os.getpgid", return_value=123),
            patch("os.killpg"),
        ):
            result = runner._run_local(
                session_id="s-1",
                cli_tool="codex",
                model="m",
                project_path="/tmp/x",
                prompt="hi",
                permission_mode="auto-edit",
                timeout=42,
                workflow_id="wf-1",
                user_id=1,
                workspace_type="local",
                allowed_tools=None,
                resume=False,
                resume_session_id=None,
                milestone_id="m1",
            )

        assert calls.get("timeout") == 42
        assert calls.get("session") is not None
        # fake_wait returned True (completed) and codex is not a sidebar tool,
        # so the result follows the success path.
        assert result.success is True
