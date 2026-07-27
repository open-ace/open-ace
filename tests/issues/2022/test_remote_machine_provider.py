"""RemoteMachineProvider contract tests (#2022 P4 ⑤a).

Exercises the provider against a fake ``RemoteSessionManager`` (records calls,
returns deterministic responses) — the real manager is off-limits for behavior
change (#2022 scope: wrap autonomous remote-agent execution only). These tests
prove the provider maps create/exec/stream/stop/destroy onto the manager's four
methods and fills the #2046-A sandbox attribution, WITHOUT touching
``remote_session_manager.py`` / ``remote.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.modules.workspace.autonomous.sandbox.provider import CapabilityUnsupported, SandboxError
from app.modules.workspace.autonomous.sandbox.remote_machine import (
    RemoteMachineProvider,
    RemoteTurnSpec,
)
from app.modules.workspace.autonomous.sandbox.types import (
    SandboxCapability,
    SandboxEventKind,
    SandboxSpec,
    SandboxStatus,
)

# Remote provides NO verifiable isolation (#2078 P1#1) — the remote-agent
# executor (dict(os.environ) + plain Popen) has no per-task HOME/ACL/cgroup.
_REMOTE_CAPS = frozenset()


class FakeRemoteSessionManager:
    """Records manager calls; returns deterministic responses."""

    def __init__(
        self,
        *,
        session_id: str = "rsess-1",
        create_ok: bool = True,
        send_ok: bool = True,
        complete_after: int = 1,
    ) -> None:
        self.create_calls: list[dict[str, Any]] = []
        self.send_calls: list[dict[str, Any]] = []
        self.status_calls: list[str] = []
        self.stop_calls: list[str] = []
        self._session_id = session_id
        self._create_ok = create_ok
        self._send_ok = send_ok
        self._complete_after = complete_after
        self._polls = 0

    def create_remote_session(self, **kwargs: Any) -> dict[str, Any]:
        self.create_calls.append(kwargs)
        if not self._create_ok:
            return {"success": False, "error": "remote refused"}
        return {"session_id": self._session_id}

    def send_message(self, **kwargs: Any) -> bool:
        self.send_calls.append(kwargs)
        return self._send_ok

    def get_session_status(self, session_id: str) -> dict[str, Any]:
        self.status_calls.append(session_id)
        self._polls += 1
        if self._polls >= self._complete_after:
            return {"output": [{"is_complete": True, "stream": "stdout"}], "exit_code": 0}
        return {"output": []}

    def stop_session(self, session_id: str) -> bool:
        self.stop_calls.append(session_id)
        return True


def _spec(**overrides: Any) -> SandboxSpec:
    base: dict[str, Any] = {
        "task_id": "t-1",
        "project_path": "/repo",
        "cli_tool": "claude-code",
        "machine_id": "machine-7",
        "user_id": 42,
    }
    base.update(overrides)
    return SandboxSpec(**base)


def _turn(**overrides: Any) -> RemoteTurnSpec:
    base: dict[str, Any] = {"prompt": "do the thing", "model": "sonnet"}
    base.update(overrides)
    return RemoteTurnSpec(**base)


# ── capabilities + create ──


def test_remote_declares_no_isolation_capabilities():
    # #2078 P1#1: remote-agent provides no verifiable isolation, so Remote
    # declares an empty capability set (not the Legacy four).
    rsm = FakeRemoteSessionManager()
    provider = RemoteMachineProvider(rsm)
    assert provider.capabilities() == frozenset()


def test_remote_create_rejects_isolation_requirement():
    # A spec requiring any isolation cap must fail closed on Remote (it can't
    # enforce HOME/ACL/quota/credential-binding).
    provider = RemoteMachineProvider(FakeRemoteSessionManager())
    for cap in (
        SandboxCapability.PRIVATE_HOME_TMP_XDG,
        SandboxCapability.FILESYSTEM_ACL,
        SandboxCapability.CPU_MEM_PIDS_TIME_QUOTA,
        SandboxCapability.CREDENTIAL_TOKEN_BINDING,
        SandboxCapability.NAMESPACE_ISOLATION,
    ):
        with pytest.raises(CapabilityUnsupported):
            provider.create(_spec(required_capabilities=frozenset({cap})))


def test_remote_create_requires_machine_id_and_user_id():
    provider = RemoteMachineProvider(FakeRemoteSessionManager())
    with pytest.raises(SandboxError):
        provider.create(_spec(machine_id=None))
    with pytest.raises(SandboxError):
        provider.create(_spec(user_id=None))


def test_remote_create_mints_handle():
    provider = RemoteMachineProvider(FakeRemoteSessionManager())
    handle = provider.create(_spec())
    assert handle.sandbox_id
    assert handle.provider_name == "remote_machine"
    assert handle.generation == 1


# ── exec ──


def test_remote_exec_calls_create_session_then_send_prompt():
    rsm = FakeRemoteSessionManager()
    provider = RemoteMachineProvider(rsm)
    handle = provider.create(_spec())
    eh = provider.exec(
        handle, command=[], env=None, exec_policy=_turn(prompt="hello", model="sonnet")
    )
    # command_id IS the remote session id, so stop/destroy map directly.
    assert eh.command_id == "rsess-1"
    assert eh.sandbox_id == handle.sandbox_id
    assert len(rsm.create_calls) == 1
    create = rsm.create_calls[0]
    assert create["machine_id"] == "machine-7"
    assert create["user_id"] == 42
    assert create["cli_tool"] == "claude-code"
    assert create["model"] == "sonnet"
    assert len(rsm.send_calls) == 1
    assert rsm.send_calls[0]["content"] == "hello"
    assert rsm.send_calls[0]["session_id"] == "rsess-1"
    assert provider.inspect(handle) == SandboxStatus.RUNNING


def test_remote_exec_requires_remote_turn_spec():
    provider = RemoteMachineProvider(FakeRemoteSessionManager())
    handle = provider.create(_spec())
    with pytest.raises(SandboxError):
        provider.exec(handle, command=[], env=None, exec_policy=None)


def test_remote_exec_fail_closed_when_create_refuses():
    rsm = FakeRemoteSessionManager(create_ok=False)
    provider = RemoteMachineProvider(rsm)
    handle = provider.create(_spec())
    with pytest.raises(SandboxError):
        provider.exec(handle, command=[], env=None, exec_policy=_turn())
    assert provider.inspect(handle) == SandboxStatus.ERROR
    # No prompt was dispatched (create failed first).
    assert rsm.send_calls == []


def test_remote_exec_fail_closed_when_send_fails_and_stops_session():
    rsm = FakeRemoteSessionManager(send_ok=False)
    provider = RemoteMachineProvider(rsm)
    handle = provider.create(_spec())
    with pytest.raises(SandboxError):
        provider.exec(handle, command=[], env=None, exec_policy=_turn())
    # The partially-created remote session must be stopped (no orphan).
    assert rsm.stop_calls == ["rsess-1"]
    assert provider.inspect(handle) == SandboxStatus.ERROR


# ── stream ──


def test_remote_stream_emits_lifecycle_until_turn_complete():
    rsm = FakeRemoteSessionManager(complete_after=2)
    provider = RemoteMachineProvider(rsm, poll_interval=0)
    handle = provider.create(_spec())
    eh = provider.exec(handle, command=[], env=None, exec_policy=_turn())
    events = list(provider.stream(eh))
    kinds = [e.kind for e in events]
    assert kinds == [
        SandboxEventKind.PROCESS_STARTED,
        SandboxEventKind.COMMAND_STARTED,
        SandboxEventKind.COMMAND_COMPLETED,
        SandboxEventKind.PROCESS_EXITED,
    ]
    completed = next(e for e in events if e.kind == SandboxEventKind.COMMAND_COMPLETED)
    assert completed.exit_code == 0
    # Polled at least once for the turn-complete signal.
    assert len(rsm.status_calls) >= 1


def test_remote_stream_emits_timeout_when_poll_budget_exhausts():
    # #2078 P1#3: a poll that never observes completion must emit
    # COMMAND_TIMED_OUT, not COMMAND_COMPLETED(0). poll_timeout=0 makes the loop
    # exit immediately; a fake that never reports is_complete stays incomplete.
    rsm = FakeRemoteSessionManager(complete_after=999)
    provider = RemoteMachineProvider(rsm, poll_interval=0, poll_timeout=0)
    handle = provider.create(_spec())
    eh = provider.exec(handle, command=[], env=None, exec_policy=_turn())
    events = list(provider.stream(eh))
    kinds = [e.kind for e in events]
    assert SandboxEventKind.COMMAND_TIMED_OUT in kinds
    assert SandboxEventKind.COMMAND_COMPLETED not in kinds
    assert provider.inspect(handle) == SandboxStatus.ERROR
    # collect_execution_evidence reports timeout, not completed.
    rows = provider.collect_execution_evidence(handle)
    assert rows[0].terminal_reason == "timeout"


def test_remote_stream_emits_sandbox_error_when_status_unavailable():
    # If the manager has no get_session_status (or it raises), the stream cannot
    # observe a terminal state — emit SANDBOX_ERROR, not a fake success.
    class _NoStatusManager(FakeRemoteSessionManager):
        def get_session_status(self, session_id):  # type: ignore[override]
            raise RuntimeError("status backend down")

    provider = RemoteMachineProvider(_NoStatusManager(), poll_interval=0, poll_timeout=0.5)
    handle = provider.create(_spec())
    eh = provider.exec(handle, command=[], env=None, exec_policy=_turn())
    events = list(provider.stream(eh))
    kinds = [e.kind for e in events]
    assert SandboxEventKind.SANDBOX_ERROR in kinds
    assert SandboxEventKind.COMMAND_COMPLETED not in kinds
    assert provider.inspect(handle) == SandboxStatus.ERROR


# ── stop / destroy / pause ──


def test_remote_stop_calls_stop_session():
    rsm = FakeRemoteSessionManager()
    provider = RemoteMachineProvider(rsm)
    handle = provider.create(_spec())
    eh = provider.exec(handle, command=[], env=None, exec_policy=_turn())
    provider.stop(eh)
    assert rsm.stop_calls == ["rsess-1"]
    assert provider.inspect(handle) == SandboxStatus.STOPPED


def test_remote_destroy_is_idempotent_and_stops_session_once():
    rsm = FakeRemoteSessionManager()
    provider = RemoteMachineProvider(rsm)
    handle = provider.create(_spec())
    provider.exec(handle, command=[], env=None, exec_policy=_turn())
    provider.destroy(handle)
    provider.destroy(handle)  # no raise; does not re-stop
    assert rsm.stop_calls == ["rsess-1"]
    assert provider.inspect(handle) == SandboxStatus.DESTROYED


def test_remote_pause_resume_are_noops():
    rsm = FakeRemoteSessionManager()
    provider = RemoteMachineProvider(rsm)
    handle = provider.create(_spec())
    eh = provider.exec(handle, command=[], env=None, exec_policy=_turn())
    provider.pause(eh)  # no raise
    provider.resume(eh)
    assert provider.inspect(handle) == SandboxStatus.RUNNING


# ── evidence ──


def test_remote_collect_execution_evidence_fills_sandbox_attribution():
    provider = RemoteMachineProvider(FakeRemoteSessionManager())
    handle = provider.create(_spec())
    provider.exec(handle, command=[], env=None, exec_policy=_turn())
    rows = provider.collect_execution_evidence(handle)
    assert len(rows) == 1
    row = rows[0]
    assert row.sandbox_id == handle.sandbox_id
    assert row.sandbox_generation == handle.generation
    assert row.command_id == "rsess-1"


# ── #2078 review fixes ──


def test_remote_exec_cancel_check_intercepts_before_send():
    # 🟡A: cancel_check restores the create↔send cancellation window. If the
    # runner signals cancellation (create succeeded, send pending), exec must
    # stop the just-created session and raise BEFORE send_message dispatches the
    # prompt — the old "intercept before dispatch" guarantee.
    rsm = FakeRemoteSessionManager()
    provider = RemoteMachineProvider(rsm)
    handle = provider.create(_spec())
    with pytest.raises(SandboxError):
        provider.exec(
            handle,
            command=[],
            env=None,
            exec_policy=_turn(),
            cancel_check=lambda: True,
        )
    # The created session was stopped; the prompt was NOT dispatched.
    assert rsm.stop_calls == ["rsess-1"]
    assert rsm.send_calls == []
    assert provider.inspect(handle) == SandboxStatus.STOPPED


def test_remote_exec_cancel_check_false_dispatches_normally():
    # cancel_check returning False (not cancelled) is a no-op — send proceeds.
    rsm = FakeRemoteSessionManager()
    provider = RemoteMachineProvider(rsm)
    handle = provider.create(_spec())
    eh = provider.exec(
        handle, command=[], env=None, exec_policy=_turn(), cancel_check=lambda: False
    )
    assert eh.command_id == "rsess-1"
    assert rsm.send_calls and rsm.send_calls[0]["content"] == "do the thing"


def test_remote_collect_execution_evidence_uses_streamed_exit_code():
    # 🟡B: collect_execution_evidence reads the exit_code stream() polled, not a
    # hardcoded 0.
    rsm = FakeRemoteSessionManager()
    provider = RemoteMachineProvider(rsm, poll_interval=0)
    handle = provider.create(_spec())
    eh = provider.exec(handle, command=[], env=None, exec_policy=_turn())
    rsm.get_session_status = lambda sid: {  # type: ignore[assignment]
        "output": [{"is_complete": True, "stream": "stdout"}],
        "exit_code": 2,
    }
    list(provider.stream(eh))
    rows = provider.collect_execution_evidence(handle)
    assert rows[0].exit_code == 2
