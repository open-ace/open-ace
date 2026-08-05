"""SandboxProvider contract — normalized events + exec/stream/lifecycle (#2022 P1).

Pins the normalized lifecycle event taxonomy (issue §3) and the
exec/stream/pause/resume/stop surface on the in-memory Fake. Phase 3's
``LegacyPosixProvider`` and Phase 4's ``RemoteMachineProvider`` must emit this
same event sequence (acceptance: "local 与 remote 使用同一 provider contract"),
so this is the shared contract test those phases replay against.
"""

from __future__ import annotations

from app.modules.workspace.autonomous.sandbox.fake import FakeSandboxProvider
from app.modules.workspace.autonomous.sandbox.types import (
    ExecHandle,
    SandboxEvent,
    SandboxEventKind,
    SandboxSpec,
    SandboxStatus,
)

_EXPECTED_EVENT_KINDS = {
    "process_started",
    "command_started",
    "stdout_chunk",
    "stderr_chunk",
    "command_completed",
    "command_timed_out",
    "command_cancelled",
    "process_exited",
    "resource_limit_exceeded",
    "sandbox_error",
}


def _spec() -> SandboxSpec:
    return SandboxSpec(task_id="t-1", project_path="/repo", cli_tool="claude-code")


def test_sandbox_event_kinds_documented():
    assert {kind.value for kind in SandboxEventKind} == _EXPECTED_EVENT_KINDS


def test_sandbox_event_is_frozen():
    event = SandboxEvent(kind=SandboxEventKind.STDOUT_CHUNK, data="hello")
    try:
        event.data = "tampered"  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("SandboxEvent must be frozen")


def test_exec_returns_exec_handle_bound_to_sandbox():
    provider = FakeSandboxProvider()
    handle = provider.create(_spec())
    eh = provider.exec(handle, command=["echo", "hi"], env={}, exec_policy=None)
    assert isinstance(eh, ExecHandle)
    assert eh.sandbox_id == handle.sandbox_id
    assert eh.command_id  # provider-minted, non-empty


def test_stream_emits_normalized_lifecycle_sequence():
    provider = FakeSandboxProvider()
    handle = provider.create(_spec())
    eh = provider.exec(handle, command=["echo", "hi"], env={}, exec_policy=None)
    events = list(provider.stream(eh))
    kinds = [e.kind for e in events]
    # The canonical happy-path sequence every provider must emit.
    assert kinds == [
        SandboxEventKind.PROCESS_STARTED,
        SandboxEventKind.COMMAND_STARTED,
        SandboxEventKind.STDOUT_CHUNK,
        SandboxEventKind.COMMAND_COMPLETED,
        SandboxEventKind.PROCESS_EXITED,
    ]
    # Each event carries the sandbox id so consumers can correlate.
    assert all(e.sandbox_id == handle.sandbox_id for e in events)
    # The completed event carries the authoritative exit code (#2046 contract).
    completed = next(e for e in events if e.kind == SandboxEventKind.COMMAND_COMPLETED)
    assert completed.exit_code == 0
    assert completed.command_id == eh.command_id


def test_stream_stdout_chunk_carries_command_output():
    provider = FakeSandboxProvider()
    handle = provider.create(_spec())
    eh = provider.exec(handle, command=["pytest", "-q"], env={}, exec_policy=None)
    events = list(provider.stream(eh))
    chunk = next(e for e in events if e.kind == SandboxEventKind.STDOUT_CHUNK)
    assert isinstance(chunk.data, str)


def test_pause_resume_transitions_status():
    provider = FakeSandboxProvider()
    handle = provider.create(_spec())
    eh = provider.exec(handle, command=["sleep", "1"], env={}, exec_policy=None)
    provider.pause(eh)
    assert provider.inspect(handle) == SandboxStatus.PAUSED
    provider.resume(eh)
    assert provider.inspect(handle) == SandboxStatus.RUNNING


def test_stop_transitions_to_stopped():
    provider = FakeSandboxProvider()
    handle = provider.create(_spec())
    eh = provider.exec(handle, command=["echo", "hi"], env={}, exec_policy=None)
    provider.stop(eh)
    assert provider.inspect(handle) == SandboxStatus.STOPPED


def test_collect_execution_evidence_is_a_list():
    # P1: the contract method exists and returns a list; P3 fills it from the
    # real command stream (test_exec_emits_command_execution_evidence).
    provider = FakeSandboxProvider()
    handle = provider.create(_spec())
    eh = provider.exec(handle, command=["echo", "hi"], env={}, exec_policy=None)
    list(provider.stream(eh))
    evidence = provider.collect_execution_evidence(handle)
    assert isinstance(evidence, list)
