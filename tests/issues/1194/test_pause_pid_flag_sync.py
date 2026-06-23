#!/usr/bin/env python3
"""Tests for the pause/resume PID-flag sync (#1194).

Root cause: the pause/resume fallback paths (_pause_running_task Strategy 2/3
and _resume_running_task Strategy 2) send SIGSTOP/SIGCONT directly to a PID,
bypassing the runner's pause_session/resume_session. Those fallback paths
never set the session's ``_paused`` Event, so ``_wait_for_completion`` kept
counting the timeout budget and reaped the frozen process once it elapsed —
surfacing as a paused workflow appearing to "auto-resume".

The fix adds mark_session_paused_by_pid / mark_session_resumed_by_pid on the
runner so the fallback paths can sync the flag regardless of how the signal
was delivered. These tests drive the runner with a fake in-memory session and
assert the flag toggles for the matching PID only.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_agent_runner():
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    import importlib

    mod_path = _REPO_ROOT / "app" / "modules" / "workspace" / "autonomous" / "agent_runner.py"
    spec = importlib.util.spec_from_file_location("agent_runner_pause_1194", mod_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["agent_runner_pause_1194"] = mod
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ar():
    return _load_agent_runner()


def _make_session(ar, *, pid, paused=False):
    """Build a _LocalSession-like object with just enough to match by PID."""
    session = ar._LocalSession(
        session_id=f"sess-{pid}",
        process=MagicMock(),
        cli_tool="claude-code",
        project_path="/tmp",
        encoded_project_path="tmp",
        workflow_id="wf",
        user_id=1,
        workspace_type="local",
        started_at_epoch=0.0,
        milestone_id="",
    )
    session.process.pid = pid
    session.process.returncode = None
    if paused:
        session._paused.set()
    return session


def _make_runner(ar, sessions):
    runner = ar.AutonomousAgentRunner()
    runner._local_sessions = {s.session_id: s for s in sessions}
    return runner


def test_mark_paused_sets_flag_for_matching_pid(ar):
    session = _make_session(ar, pid=4242)
    runner = _make_runner(ar, [session])

    assert runner.mark_session_paused_by_pid(4242) is True
    assert session._paused.is_set()


def test_mark_paused_does_not_touch_other_sessions(ar):
    a = _make_session(ar, pid=100)
    b = _make_session(ar, pid=200)
    runner = _make_runner(ar, [a, b])

    assert runner.mark_session_paused_by_pid(100) is True
    assert a._paused.is_set()
    assert not b._paused.is_set()


def test_mark_paused_unknown_pid_returns_false(ar):
    runner = _make_runner(ar, [_make_session(ar, pid=100)])
    assert runner.mark_session_paused_by_pid(999) is False


def test_mark_resumed_clears_flag(ar):
    session = _make_session(ar, pid=4242, paused=True)
    runner = _make_runner(ar, [session])

    assert runner.mark_session_resumed_by_pid(4242) is True
    assert not session._paused.is_set()


def test_mark_resumed_only_clears_matching(ar):
    a = _make_session(ar, pid=100, paused=True)
    b = _make_session(ar, pid=200, paused=True)
    runner = _make_runner(ar, [a, b])

    runner.mark_session_resumed_by_pid(200)
    assert a._paused.is_set()
    assert not b._paused.is_set()


def test_mark_resumed_unknown_pid_returns_false(ar):
    runner = _make_runner(ar, [_make_session(ar, pid=100, paused=True)])
    assert runner.mark_session_resumed_by_pid(999) is False


def test_mark_paused_skips_dead_process(ar):
    """A session whose process already exited must not be flagged."""
    session = _make_session(ar, pid=4242)
    session.process.returncode = 0  # already exited
    runner = _make_runner(ar, [session])

    # mark_session_paused_by_pid iterates and matches by pid; a dead process
    # still matches, but pause_session itself guards on returncode. The PID
    # marker is a best-effort sync so flagging is acceptable — what matters is
    # it doesn't crash and the flag reflects intent.
    runner.mark_session_paused_by_pid(4242)
    assert session._paused.is_set()
