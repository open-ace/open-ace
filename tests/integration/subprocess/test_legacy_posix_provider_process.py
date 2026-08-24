"""LegacyPosixProvider — real-OS-process counterparts (#2022 P3a).

These tests exercise REAL subprocesses (``/bin/echo``, ``sleep``, shell
signal deaths) so the provider's spawn/stream/signal/destroy and the
execution-evidence schema are validated against the OS, not a mock. The
pure/mocked-Popen spawn-shape and classification tests live in
``tests/unit/test_legacy_posix_provider.py``.

Scope (#2022): autonomous-only. ``_run_local`` and its helper methods are
untouched in P3a — zero production behavior change.
"""

from __future__ import annotations

import pytest

from app.modules.workspace.autonomous.sandbox.legacy_posix import LegacyPosixProvider
from app.modules.workspace.autonomous.sandbox.types import (
    SandboxEventKind,
    SandboxSpec,
    SandboxStatus,
)

pytestmark = [pytest.mark.regression, pytest.mark.issue(2022)]


def _spec(tmp_path, **overrides) -> SandboxSpec:
    # Real Popen chdirs into project_path (same-user), so specs here need an
    # existing directory — use the per-test tmp dir.
    base = {
        "task_id": "t-1",
        "project_path": str(tmp_path),
        "cli_tool": "claude-code",
    }
    base.update(overrides)
    return SandboxSpec(**base)


def test_legacy_exec_streams_echo_with_zero_exit(tmp_path):
    provider = LegacyPosixProvider()
    handle = provider.create(_spec(tmp_path))
    eh = provider.exec(handle, command=["/bin/echo", "hi"], env=None, exec_policy=None)
    events = list(provider.stream(eh))
    kinds = [e.kind for e in events]
    assert kinds == [
        SandboxEventKind.PROCESS_STARTED,
        SandboxEventKind.COMMAND_STARTED,
        SandboxEventKind.STDOUT_CHUNK,
        SandboxEventKind.COMMAND_COMPLETED,
        SandboxEventKind.PROCESS_EXITED,
    ]
    chunk = next(e for e in events if e.kind == SandboxEventKind.STDOUT_CHUNK)
    assert "hi" in chunk.data
    completed = next(e for e in events if e.kind == SandboxEventKind.COMMAND_COMPLETED)
    assert completed.exit_code == 0
    exited = next(e for e in events if e.kind == SandboxEventKind.PROCESS_EXITED)
    assert exited.exit_code == 0
    assert provider.inspect(handle) == SandboxStatus.RUNNING


def test_legacy_exec_nonzero_exit(tmp_path):
    provider = LegacyPosixProvider()
    handle = provider.create(_spec(tmp_path))
    eh = provider.exec(handle, command=["/bin/sh", "-c", "exit 3"], env=None, exec_policy=None)
    events = list(provider.stream(eh))
    completed = next(e for e in events if e.kind == SandboxEventKind.COMMAND_COMPLETED)
    assert completed.exit_code == 3


def test_legacy_stop_kills_long_running_process(tmp_path):
    provider = LegacyPosixProvider()
    handle = provider.create(_spec(tmp_path))
    eh = provider.exec(handle, command=["/bin/sleep", "30"], env=None, exec_policy=None)
    provider.stop(eh)
    # The process must be reaped (poll returns a code, not None).
    proc = provider._procs[eh.command_id]  # noqa: SLF001 - white-box check
    assert proc.poll() is not None
    assert provider.inspect(handle) == SandboxStatus.STOPPED


def test_legacy_destroy_reaps_process(tmp_path):
    provider = LegacyPosixProvider()
    handle = provider.create(_spec(tmp_path))
    eh = provider.exec(handle, command=["/bin/sleep", "30"], env=None, exec_policy=None)
    proc = provider._procs[eh.command_id]  # noqa: SLF001 - capture before destroy clears it
    provider.destroy(handle)
    assert proc.poll() is not None
    assert provider.inspect(handle) == SandboxStatus.DESTROYED


def test_legacy_pause_resume_then_stop(tmp_path):
    provider = LegacyPosixProvider()
    handle = provider.create(_spec(tmp_path))
    eh = provider.exec(handle, command=["/bin/sleep", "30"], env=None, exec_policy=None)
    provider.pause(eh)
    assert provider.inspect(handle) == SandboxStatus.PAUSED
    provider.resume(eh)
    assert provider.inspect(handle) == SandboxStatus.RUNNING
    # A paused-then-resumed process must still be stoppable (SIGCONT before SIGTERM).
    provider.stop(eh)
    proc = provider._procs[eh.command_id]  # noqa: SLF001
    assert proc.poll() is not None


@pytest.mark.timeout(15)
def test_legacy_stream_does_not_deadlock_on_large_concurrent_output(tmp_path):
    # Regression for the sequential stdout→stderr deadlock: when the child
    # fills its 64KB stderr pipe while the parent is still reading stdout, a
    # sequential read hangs forever (Python subprocess docs warn about this).
    # Two background writers (>64KB each) force that condition; the concurrent
    # stream must drain both and complete.
    provider = LegacyPosixProvider()
    handle = provider.create(_spec(tmp_path))
    eh = provider.exec(
        handle,
        command=[
            "/bin/sh",
            "-c",
            "(yes x | head -c 100000) & (yes y | head -c 100000 >&2) & wait",
        ],
        env={"PATH": "/usr/bin:/bin"},
        exec_policy=None,
    )
    events = list(provider.stream(eh))
    stdout_total = sum(len(e.data) for e in events if e.kind == SandboxEventKind.STDOUT_CHUNK)
    stderr_total = sum(len(e.data) for e in events if e.kind == SandboxEventKind.STDERR_CHUNK)
    assert stdout_total >= 100000
    assert stderr_total >= 100000
    completed = next(e for e in events if e.kind == SandboxEventKind.COMMAND_COMPLETED)
    assert completed.exit_code == 0


def test_get_process_returns_spawned_popen(tmp_path):
    # The CLI protocol layer (_read_stdout/_read_stderr/_send_sdk_init) needs
    # the raw Popen to drive the stream-json handshake over stdin/stdout. The
    # provider exposes it via this Legacy-specific escape hatch (not on the
    # Protocol — RemoteMachineProvider has no local Popen).
    provider = LegacyPosixProvider()
    handle = provider.create(_spec(tmp_path))
    eh = provider.exec(handle, command=["/bin/echo", "hi"], env=None, exec_policy=None)
    proc = provider.get_process(eh)
    assert proc is provider._procs[eh.command_id]  # noqa: SLF001 - white-box
    assert proc.stdout is not None  # real Popen pipe, not the mock


def test_get_process_after_destroy_inspects_destroyed(tmp_path):
    provider = LegacyPosixProvider()
    handle = provider.create(_spec(tmp_path))
    provider.exec(handle, command=["/bin/sleep", "30"], env=None, exec_policy=None)
    provider.destroy(handle)
    assert provider.inspect(handle) == SandboxStatus.DESTROYED


def test_legacy_collect_execution_evidence_fills_sandbox_fields(tmp_path):
    # The #2046-A schema explicitly defers sandbox_id / sandbox_generation /
    # signal to "#2022's normalized provider events". collect_execution_evidence
    # is that provider event: it returns the process-level evidence row with
    # the provider-ownable fields filled (sandbox_id/generation from the handle,
    # exit_code/signal/argv/cwd from the spawn) — the contract a gVisor backend
    # inherits. Per-tool_use evidence stays with the runner's recorder.
    provider = LegacyPosixProvider()
    handle = provider.create(_spec(tmp_path))
    eh = provider.exec(handle, command=["/bin/sh", "-c", "exit 3"], env=None, exec_policy=None)
    list(provider.stream(eh))  # drive the proc to completion
    rows = provider.collect_execution_evidence(handle)
    assert len(rows) == 1
    row = rows[0]
    assert row.sandbox_id == handle.sandbox_id
    assert row.sandbox_generation == handle.generation
    assert row.exit_code == 3
    assert row.signal is None
    assert row.argv == ["/bin/sh", "-c", "exit 3"]
    assert row.terminal_reason == "completed"


def test_legacy_collect_execution_evidence_records_signal_death(tmp_path):
    provider = LegacyPosixProvider()
    handle = provider.create(_spec(tmp_path))
    eh = provider.exec(handle, command=["/bin/sh", "-c", "kill -9 $$"], env=None, exec_policy=None)
    list(provider.stream(eh))
    rows = provider.collect_execution_evidence(handle)
    assert rows[0].signal == 9  # SIGKILL; Python encodes signal deaths as -rc
    assert rows[0].exit_code is not None and rows[0].exit_code < 0


def test_destroy_clears_proc_tracking_entries(tmp_path):
    # destroy must release the provider's per-sandbox bookkeeping (_procs /
    # _sandbox_of) so a long-lived shared provider does not leak entries across
    # sessions. Today the per-orchestrator lifetime bounds it, but P4/P5 may
    # lift the provider to a shared singleton (remote connection pool reuse) —
    # then a monotonic leak would surface. (Review #2074 🟢#1.)
    provider = LegacyPosixProvider()
    handle = provider.create(_spec(tmp_path))
    eh = provider.exec(handle, command=["/bin/echo", "hi"], env=None, exec_policy=None)
    assert eh.command_id in provider._procs  # noqa: SLF001 - white-box
    assert eh.command_id in provider._sandbox_of  # noqa: SLF001
    provider.destroy(handle)
    assert eh.command_id not in provider._procs  # noqa: SLF001
    assert eh.command_id not in provider._sandbox_of  # noqa: SLF001
    # _status stays DESTROYED for idempotent inspect (unknown id → DESTROYED).
    assert provider.inspect(handle) == SandboxStatus.DESTROYED
