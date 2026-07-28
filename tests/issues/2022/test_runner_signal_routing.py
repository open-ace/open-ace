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
from app.modules.workspace.autonomous.models import AgentTaskResult
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


def test_pause_resume_return_false_for_process_less_remote_tracker():
    # #2078 P2: a real remote tracker has process=None (no local Popen). The
    # process guard precedes the provider branch, so pause/resume return False
    # — honestly reflecting that remote can't SIGSTOP a CLI session. The
    # provider-routed pause/resume is therefore Legacy-effective; remote pause
    # is unsupported (not silently claimed).
    provider = FakeSandboxProvider()
    runner = AutonomousAgentRunner(sandbox_provider=provider)
    handle = provider.create(SandboxSpec(task_id="t", project_path="/tmp", cli_tool="c"))
    eh = provider.exec(handle, command=["x"], env=None, exec_policy=None)
    session = _LocalSession(session_id="s1", process=None)  # remote-shape tracker
    session.sandbox_provider = provider
    session.exec_handle = eh
    runner._local_sessions["s1"] = session
    assert runner.pause_session("s1") is False
    assert runner.resume_session("s1") is False


def test_select_sandbox_provider_returns_legacy_for_local():
    legacy = FakeSandboxProvider()
    runner = AutonomousAgentRunner(sandbox_provider=legacy)
    assert runner._select_sandbox_provider("local") is legacy


def test_stamp_sandbox_attribution_fills_provider_and_state():
    # #2022 P6: state is TASK-terminal (success→destroyed, failure→error), NOT
    # provider.inspect() — a remote session left alive on success must not read
    # 'running' (the startup reconciler would mis-flag it as a crash orphan).
    provider = FakeSandboxProvider()
    handle = provider.create(SandboxSpec(task_id="t", project_path="/tmp", cli_tool="c"))
    ok = AgentTaskResult(session_id="s", success=True)
    AutonomousAgentRunner._stamp_sandbox_attribution(ok, handle, provider)
    assert ok.sandbox_id == handle.sandbox_id
    assert ok.sandbox_generation == handle.generation
    assert ok.sandbox_provider == "fake"
    assert ok.sandbox_state == "destroyed"
    failed = AgentTaskResult(session_id="s", success=False)
    AutonomousAgentRunner._stamp_sandbox_attribution(failed, handle, provider)
    assert failed.sandbox_state == "error"


def test_stamp_sandbox_attribution_noop_without_handle():
    # Spawn-failed-before-create paths pass sandbox_handle=None → no attribution.
    result = AgentTaskResult(session_id="s")
    AutonomousAgentRunner._stamp_sandbox_attribution(result, None, FakeSandboxProvider())
    assert result.sandbox_id is None
    assert result.sandbox_provider == ""


def test_select_sandbox_provider_returns_remote_for_remote():
    runner = AutonomousAgentRunner(remote_session_manager=MagicMock())
    provider = runner._select_sandbox_provider("remote")
    # RemoteMachineProvider wraps the remote_session_manager (gVisor would add
    # a third branch here).
    assert provider.__class__.__name__ == "RemoteMachineProvider"


def _spec() -> SandboxSpec:
    return SandboxSpec(task_id="t", project_path="/tmp", cli_tool="c")


def test_notify_sandbox_created_invokes_callback_with_attribution():
    # #2022 P6: right after exec the runner fires on_sandbox_created so the
    # orchestrator can persist a mid-run 'running' row (crash orphan bait for
    # the reconciler). Callback gets (session_id, sandbox_id, provider_name,
    # remote_session_id_or_None, effective_policy_snapshot).
    captured: list = []
    provider = FakeSandboxProvider()
    runner = AutonomousAgentRunner(
        sandbox_provider=provider,
        on_sandbox_created=lambda *a: captured.append(a),
    )
    handle = provider.create(_spec())
    runner._notify_sandbox_created("s1", handle, "remote-42")
    # #2020 Phase B: 5th arg is the effective-policy snapshot built from the
    # provider's declared caps + spec policy.
    assert len(captured) == 1
    s_id, sandbox_id, provider_name, remote_id, snap = captured[0]
    assert (s_id, sandbox_id, provider_name, remote_id) == (
        "s1",
        handle.sandbox_id,
        "fake",
        "remote-42",
    )
    assert snap["provider"] == "fake"
    assert "enforced" in snap


def test_notify_sandbox_created_none_remote_id_for_local():
    # Local path passes remote_session_id=None (no external session id).
    captured: list = []
    provider = FakeSandboxProvider()
    runner = AutonomousAgentRunner(
        sandbox_provider=provider,
        on_sandbox_created=lambda *a: captured.append(a),
    )
    handle = provider.create(_spec())
    runner._notify_sandbox_created("s1", handle, None)
    assert len(captured) == 1
    s_id, sandbox_id, provider_name, remote_id, _snap = captured[0]
    assert (s_id, sandbox_id, provider_name, remote_id) == (
        "s1",
        handle.sandbox_id,
        "fake",
        None,
    )


def test_notify_sandbox_created_noop_without_callback():
    # No callback registered -> no-op, no raise.
    provider = FakeSandboxProvider()
    runner = AutonomousAgentRunner(sandbox_provider=provider)
    handle = provider.create(_spec())
    runner._notify_sandbox_created("s1", handle, None)


def test_notify_sandbox_created_swallows_callback_errors():
    # Best-effort: a failing callback must not propagate to the runner path.
    def _boom(*a):
        raise RuntimeError("callback failed")

    provider = FakeSandboxProvider()
    runner = AutonomousAgentRunner(sandbox_provider=provider, on_sandbox_created=_boom)
    handle = provider.create(_spec())
    runner._notify_sandbox_created("s1", handle, None)  # no raise
