"""LegacyPosixProvider — wraps the real local POSIX mechanics (#2022 P3a).

P3a proves the SandboxProvider contract can sit on the existing local execution
mechanics (Popen + process group + signals + exit classification) WITHOUT
wiring into ``_run_local`` (that strangler cut is P3b). These tests exercise
real subprocesses (``/bin/echo``, ``sleep``) so the provider's spawn/stream/
signal/destroy are validated against the OS, not a mock.

Scope (#2022): autonomous-only. ``_run_local`` and its helper methods are
untouched in P3a — zero production behavior change.
"""

from __future__ import annotations

import signal

from app.modules.workspace.autonomous.sandbox.legacy_posix import LegacyPosixProvider
from app.modules.workspace.autonomous.sandbox.provider import CapabilityUnsupported
from app.modules.workspace.autonomous.sandbox.types import (
    SandboxCapability,
    SandboxEventKind,
    SandboxSpec,
    SandboxStatus,
)

_LEGACY_CAPS = frozenset(
    {
        SandboxCapability.PRIVATE_HOME_TMP_XDG,
        SandboxCapability.FILESYSTEM_ACL,
        SandboxCapability.CPU_MEM_PIDS_TIME_QUOTA,
        SandboxCapability.CREDENTIAL_TOKEN_BINDING,
    }
)


def _spec(**overrides) -> SandboxSpec:
    base = {"task_id": "t-1", "project_path": "/repo", "cli_tool": "claude-code"}
    base.update(overrides)
    return SandboxSpec(**base)


def test_legacy_declares_four_capabilities():
    provider = LegacyPosixProvider()
    assert provider.capabilities() == _LEGACY_CAPS
    # Legacy cannot supply the #2023-only isolation guarantees.
    assert SandboxCapability.NAMESPACE_ISOLATION not in provider.capabilities()
    assert SandboxCapability.NETWORK_EGRESS_POLICY not in provider.capabilities()


def test_legacy_create_rejects_namespace_requirement():
    provider = LegacyPosixProvider()
    spec = _spec(required_capabilities=frozenset({SandboxCapability.NAMESPACE_ISOLATION}))
    try:
        provider.create(spec)
    except CapabilityUnsupported as exc:
        assert SandboxCapability.NAMESPACE_ISOLATION in exc.missing_capabilities
        return
    raise AssertionError("Legacy must refuse namespace isolation (fail-closed)")


def test_legacy_create_mints_handle():
    provider = LegacyPosixProvider()
    handle = provider.create(_spec())
    assert handle.sandbox_id
    assert handle.generation == 1
    assert handle.provider_name == "legacy_posix"


def test_legacy_exec_streams_echo_with_zero_exit():
    provider = LegacyPosixProvider()
    handle = provider.create(_spec())
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


def test_legacy_exec_nonzero_exit():
    provider = LegacyPosixProvider()
    handle = provider.create(_spec())
    eh = provider.exec(handle, command=["/bin/sh", "-c", "exit 3"], env=None, exec_policy=None)
    events = list(provider.stream(eh))
    completed = next(e for e in events if e.kind == SandboxEventKind.COMMAND_COMPLETED)
    assert completed.exit_code == 3


def test_legacy_stop_kills_long_running_process():
    provider = LegacyPosixProvider()
    handle = provider.create(_spec())
    eh = provider.exec(handle, command=["/bin/sleep", "30"], env=None, exec_policy=None)
    provider.stop(eh)
    # The process must be reaped (poll returns a code, not None).
    proc = provider._procs[eh.command_id]  # noqa: SLF001 - white-box check
    assert proc.poll() is not None
    assert provider.inspect(handle) == SandboxStatus.STOPPED


def test_legacy_destroy_reaps_process():
    provider = LegacyPosixProvider()
    handle = provider.create(_spec())
    eh = provider.exec(handle, command=["/bin/sleep", "30"], env=None, exec_policy=None)
    provider.destroy(handle)
    proc = provider._procs[eh.command_id]  # noqa: SLF001
    assert proc.poll() is not None
    assert provider.inspect(handle) == SandboxStatus.DESTROYED


def test_legacy_destroy_is_idempotent():
    provider = LegacyPosixProvider()
    handle = provider.create(_spec())
    provider.destroy(handle)
    provider.destroy(handle)  # no raise
    assert provider.inspect(handle) == SandboxStatus.DESTROYED


def test_legacy_pause_resume_then_stop():
    provider = LegacyPosixProvider()
    handle = provider.create(_spec())
    eh = provider.exec(handle, command=["/bin/sleep", "30"], env=None, exec_policy=None)
    provider.pause(eh)
    assert provider.inspect(handle) == SandboxStatus.PAUSED
    provider.resume(eh)
    assert provider.inspect(handle) == SandboxStatus.RUNNING
    # A paused-then-resumed process must still be stoppable (SIGCONT before SIGTERM).
    provider.stop(eh)
    proc = provider._procs[eh.command_id]  # noqa: SLF001
    assert proc.poll() is not None


# ── exit classification (#2022 P3a — mirrors agent_runner P1 #2067 logic) ──

from app.modules.workspace.autonomous.sandbox.legacy_posix import (  # noqa: E402
    classify_isolated_exit_code,
)


def test_classify_clean_exit():
    assert classify_isolated_exit_code(
        0, "", orchestrator_initiated=False, resource_policy_configured=True
    ) == (None, None)


def test_classify_resource_limit_breach_sigkill():
    code, _msg = classify_isolated_exit_code(
        -9, "", orchestrator_initiated=False, resource_policy_configured=True
    )
    assert code == "task_resource_limit_exceeded"


def test_classify_resource_breach_ignored_when_orchestrator_initiated():
    # Orchestrator killed it (stop/timeout) — not a resource breach.
    assert classify_isolated_exit_code(
        -9, "", orchestrator_initiated=True, resource_policy_configured=True
    ) == (None, None)


def test_classify_resource_breach_ignored_when_no_policy():
    assert classify_isolated_exit_code(
        -9, "", orchestrator_initiated=False, resource_policy_configured=False
    ) == (None, None)


def test_classify_sigterm_not_resource_breach():
    # SIGTERM (-15) is the orchestrator's own stop signal; never a breach.
    assert classify_isolated_exit_code(
        -15, "", orchestrator_initiated=False, resource_policy_configured=True
    ) == (None, None)


def test_classify_repo_integrity_exit_68():
    code, _msg = classify_isolated_exit_code(68, "")
    assert code == "repo_integrity_violation"


def test_classify_cgroup_sentinel():
    code, _msg = classify_isolated_exit_code(
        0, "OPENACE_CGROUP_REQUIRED: no write access to cgroup"
    )
    assert code == "task_resource_policy_unavailable"


# ── launch-argv wrap (#2022 P3a — mirrors agent_runner._wrap_agent_cmd) ──


def test_build_launch_argv_same_user_verbatim():
    provider = LegacyPosixProvider()
    handle = provider.create(_spec())  # no system_account → same-user
    argv = provider.build_launch_argv(handle, command=["claude", "--print"], env={"PATH": "/x"})
    assert argv == ["claude", "--print"]


def test_build_launch_argv_cross_user_wraps_with_run_as():
    provider = LegacyPosixProvider()
    handle = provider.create(_spec(system_account="openace-agent"))
    argv = provider.build_launch_argv(
        handle,
        command=["claude", "--print"],
        env={"PATH": "/x", "OPENACE_PROXY_TOKEN": "tok"},
    )
    # sudo -n -u root <launcher> --isolated --task-id <sanitized> <account> <path> /usr/bin/env K=V ... <cmd>
    assert argv[:4] == ["sudo", "-n", "-u", "root"]
    assert "--isolated" in argv
    assert "--task-id" in argv
    assert "openace-agent" in argv
    assert "/repo" in argv  # project_path
    assert "/usr/bin/env" in argv
    assert "OPENACE_PROXY_TOKEN=tok" in argv
    assert "claude" in argv and "--print" in argv


# keep the signal import referenced
_ = signal
