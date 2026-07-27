"""#2022 P4 ③ — runner routes stop/pause/resume through the SandboxProvider.

gVisor (#2023) has no local ``Popen``; cancellation/timeout/pause MUST reach the
sandbox via ``provider.stop/pause/resume``. Legacy (raw-proc killpg) and Remote
both set ``sandbox_provider`` on the tracker, so a session with a provider
handle routes signals through it; the raw-proc branches are the fallback for
legacy tracker paths without a provider.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner, _LocalSession
from app.modules.workspace.autonomous.sandbox.fake import FakeSandboxProvider
from app.modules.workspace.autonomous.sandbox.types import SandboxSpec, SandboxStatus


def _runner_with_session(process=None) -> tuple:
    provider = FakeSandboxProvider()
    runner = AutonomousAgentRunner(sandbox_provider=provider)
    handle = provider.create(SandboxSpec(task_id="t", project_path="/tmp", cli_tool="c"))
    eh = provider.exec(handle, command=["x"], env=None, exec_policy=None)
    session = _LocalSession(session_id="s1", process=process)
    session.sandbox_provider = provider
    session.exec_handle = eh
    runner._local_sessions["s1"] = session
    return runner, provider, handle


def test_stop_session_routes_through_sandbox_provider():
    runner, provider, handle = _runner_with_session(process=None)
    runner.stop_session("s1")
    # provider.stop marked the sandbox STOPPED (not the raw-proc path, which
    # would have no-op'd on process=None).
    assert provider.inspect(handle) == SandboxStatus.STOPPED
    session = runner._local_sessions["s1"]
    assert session._stopped.is_set()
    assert session.completed.is_set()


def test_stop_session_unknown_id_is_noop():
    runner = AutonomousAgentRunner()
    runner.stop_session("nope")  # no raise


def test_pause_resume_route_through_sandbox_provider():
    proc = MagicMock()
    proc.returncode = None  # still running (guard requires returncode is None)
    runner, provider, handle = _runner_with_session(process=proc)
    assert runner.pause_session("s1") is True
    assert provider.inspect(handle) == SandboxStatus.PAUSED
    session = runner._local_sessions["s1"]
    assert session._paused.is_set()
    assert runner.resume_session("s1") is True
    assert provider.inspect(handle) == SandboxStatus.RUNNING
    assert not session._paused.is_set()


def test_select_sandbox_provider_returns_legacy_for_local():
    legacy = FakeSandboxProvider()
    runner = AutonomousAgentRunner(sandbox_provider=legacy)
    assert runner._select_sandbox_provider("local") is legacy


def test_select_sandbox_provider_returns_remote_for_remote():
    runner = AutonomousAgentRunner(remote_session_manager=MagicMock())
    provider = runner._select_sandbox_provider("remote")
    # RemoteMachineProvider wraps the remote_session_manager (gVisor would add
    # a third branch here).
    assert provider.__class__.__name__ == "RemoteMachineProvider"
