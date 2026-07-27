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
import tempfile

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

# P3b made exec chdir into project_path (same-user), so specs that drive a
# REAL Popen need an existing directory. Mock-Popen tests still pass "/repo"
# explicitly (no real chdir happens).
_REAL_PROJECT_PATH = tempfile.gettempdir()


def _spec(**overrides) -> SandboxSpec:
    base = {
        "task_id": "t-1",
        "project_path": _REAL_PROJECT_PATH,
        "cli_tool": "claude-code",
    }
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
    proc = provider._procs[eh.command_id]  # noqa: SLF001 - capture before destroy clears it
    provider.destroy(handle)
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
    assert _REAL_PROJECT_PATH in argv  # project_path
    assert "/usr/bin/env" in argv
    assert "OPENACE_PROXY_TOKEN=tok" in argv
    assert "claude" in argv and "--print" in argv


# ── regression: concurrent stream + exec contract (#2068 review) ──

from unittest.mock import MagicMock, patch  # noqa: E402

import pytest  # noqa: E402


@pytest.mark.timeout(15)
def test_legacy_stream_does_not_deadlock_on_large_concurrent_output():
    # Regression for the sequential stdout→stderr deadlock: when the child
    # fills its 64KB stderr pipe while the parent is still reading stdout, a
    # sequential read hangs forever (Python subprocess docs warn about this).
    # Two background writers (>64KB each) force that condition; the concurrent
    # stream must drain both and complete.
    provider = LegacyPosixProvider()
    handle = provider.create(_spec())
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


def test_exec_internally_wraps_cross_user_command():
    # P1 boundary: the provider owns the sudo/openace-run-as ACL wrap, so exec
    # must apply build_launch_argv itself — a cross-user spec gets the wrap
    # automatically and P3b cannot silently skip it by calling exec raw.
    provider = LegacyPosixProvider()
    handle = provider.create(_spec(system_account="openace-agent"))
    with patch(
        "app.modules.workspace.autonomous.sandbox.legacy_posix.subprocess.Popen"
    ) as mock_popen:
        mock_popen.return_value = MagicMock()
        provider.exec(handle, command=["claude", "--print"], env={"PATH": "/x"}, exec_policy=None)
    spawned_argv = mock_popen.call_args.args[0]
    assert spawned_argv[:4] == ["sudo", "-n", "-u", "root"]
    assert "--isolated" in spawned_argv
    assert "claude" in spawned_argv


def test_exec_env_none_does_not_inherit_credentials():
    # env=None must NOT pass None to Popen (which inherits the orchestrator's
    # full env, leaking credentials). It spawns with an empty env instead;
    # callers must pass a scrubbed env (#2019) for the agent to function.
    provider = LegacyPosixProvider()
    handle = provider.create(_spec())
    with patch(
        "app.modules.workspace.autonomous.sandbox.legacy_posix.subprocess.Popen"
    ) as mock_popen:
        mock_popen.return_value = MagicMock()
        provider.exec(handle, command=["/bin/echo", "hi"], env=None, exec_policy=None)
    assert mock_popen.call_args.kwargs["env"] == {}


# ── #2022 P3b: cwd + get_process (spawn-cut prerequisites) ──


def test_exec_passes_project_path_cwd_for_same_user():
    # Same-user agents (claude-code/qwen-code) infer project root from cwd —
    # there is no --cwd flag — so the provider must spawn with cwd=project_path,
    # matching the inline ``Popen(..., cwd=project_path)`` it replaces. P3a
    # omitted cwd; this closes the gap (otherwise the agent runs in the
    # orchestrator's cwd).
    provider = LegacyPosixProvider()
    handle = provider.create(_spec(project_path="/repo"))  # no system_account → same-user
    with patch(
        "app.modules.workspace.autonomous.sandbox.legacy_posix.subprocess.Popen"
    ) as mock_popen:
        mock_popen.return_value = MagicMock()
        provider.exec(handle, command=["claude", "--print"], env={"PATH": "/x"}, exec_policy=None)
    assert mock_popen.call_args.kwargs.get("cwd") == "/repo"


def test_exec_cwd_none_for_cross_user():
    # Cross-user: the run-as launcher chdir's as root before dropping to
    # system_account, so Popen cwd must be None (mirrors _wrap_agent_cmd's
    # ``(wrapped, None)`` return). A set cwd would chdir the service user.
    provider = LegacyPosixProvider()
    handle = provider.create(_spec(project_path="/repo", system_account="openace-agent"))
    with patch(
        "app.modules.workspace.autonomous.sandbox.legacy_posix.subprocess.Popen"
    ) as mock_popen:
        mock_popen.return_value = MagicMock()
        provider.exec(handle, command=["claude", "--print"], env={"PATH": "/x"}, exec_policy=None)
    assert mock_popen.call_args.kwargs.get("cwd") is None


def test_exec_start_new_session_preserved():
    # The process-group invariant (pgid == child pid) that pause/resume/stop
    # rely on must survive the spawn cut.
    provider = LegacyPosixProvider()
    handle = provider.create(_spec())
    with patch(
        "app.modules.workspace.autonomous.sandbox.legacy_posix.subprocess.Popen"
    ) as mock_popen:
        mock_popen.return_value = MagicMock()
        provider.exec(handle, command=["claude"], env={"PATH": "/x"}, exec_policy=None)
    assert mock_popen.call_args.kwargs.get("start_new_session") is True


def test_get_process_returns_spawned_popen():
    # The CLI protocol layer (_read_stdout/_read_stderr/_send_sdk_init) needs
    # the raw Popen to drive the stream-json handshake over stdin/stdout. The
    # provider exposes it via this Legacy-specific escape hatch (not on the
    # Protocol — RemoteMachineProvider has no local Popen).
    provider = LegacyPosixProvider()
    handle = provider.create(_spec())
    eh = provider.exec(handle, command=["/bin/echo", "hi"], env=None, exec_policy=None)
    proc = provider.get_process(eh)
    assert proc is provider._procs[eh.command_id]  # noqa: SLF001 - white-box
    assert proc.stdout is not None  # real Popen pipe, not the mock


def test_get_process_after_destroy_inspects_destroyed():
    provider = LegacyPosixProvider()
    handle = provider.create(_spec())
    provider.exec(handle, command=["/bin/sleep", "30"], env=None, exec_policy=None)
    provider.destroy(handle)
    assert provider.inspect(handle) == SandboxStatus.DESTROYED


# ── #2022 P4 ②: collect_execution_evidence fills the #2046-A schema's ──
# ── sandbox-deferred fields (sandbox_id / generation / signal) ─────────


def test_legacy_collect_execution_evidence_fills_sandbox_fields():
    # The #2046-A schema explicitly defers sandbox_id / sandbox_generation /
    # signal to "#2022's normalized provider events". collect_execution_evidence
    # is that provider event: it returns the process-level evidence row with
    # the provider-ownable fields filled (sandbox_id/generation from the handle,
    # exit_code/signal/argv/cwd from the spawn) — the contract a gVisor backend
    # inherits. Per-tool_use evidence stays with the runner's recorder.
    provider = LegacyPosixProvider()
    handle = provider.create(_spec())
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


def test_legacy_collect_execution_evidence_records_signal_death():
    provider = LegacyPosixProvider()
    handle = provider.create(_spec())
    eh = provider.exec(handle, command=["/bin/sh", "-c", "kill -9 $$"], env=None, exec_policy=None)
    list(provider.stream(eh))
    rows = provider.collect_execution_evidence(handle)
    assert rows[0].signal == 9  # SIGKILL; Python encodes signal deaths as -rc
    assert rows[0].exit_code is not None and rows[0].exit_code < 0


def test_destroy_clears_proc_tracking_entries():
    # destroy must release the provider's per-sandbox bookkeeping (_procs /
    # _sandbox_of) so a long-lived shared provider does not leak entries across
    # sessions. Today the per-orchestrator lifetime bounds it, but P4/P5 may
    # lift the provider to a shared singleton (remote connection pool reuse) —
    # then a monotonic leak would surface. (Review #2074 🟢#1.)
    provider = LegacyPosixProvider()
    handle = provider.create(_spec())
    eh = provider.exec(handle, command=["/bin/echo", "hi"], env=None, exec_policy=None)
    assert eh.command_id in provider._procs  # noqa: SLF001 - white-box
    assert eh.command_id in provider._sandbox_of  # noqa: SLF001
    provider.destroy(handle)
    assert eh.command_id not in provider._procs  # noqa: SLF001
    assert eh.command_id not in provider._sandbox_of  # noqa: SLF001
    # _status stays DESTROYED for idempotent inspect (unknown id → DESTROYED).
    assert provider.inspect(handle) == SandboxStatus.DESTROYED


# ── #2022 P3b: golden spawn equivalence vs the old inline path ──
#
# Locks the invariant the _run_local cut relies on: provider.exec must spawn
# byte-identical (argv, cwd, env, start_new_session) to the old _wrap_agent_cmd
# + inline Popen, for both same-user and cross-user. If these break, C2's cut
# would silently change execution behavior.

from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner  # noqa: E402


def test_exec_spawn_argv_matches_wrap_agent_cmd_same_user():
    cmd = ["claude", "--print", "--model", "sonnet"]
    expected_argv, expected_cwd = AutonomousAgentRunner._wrap_agent_cmd(
        list(cmd), _REAL_PROJECT_PATH, None, task_id="t-1"
    )
    provider = LegacyPosixProvider()
    handle = provider.create(_spec(task_id="t-1", project_path=_REAL_PROJECT_PATH))
    with patch(
        "app.modules.workspace.autonomous.sandbox.legacy_posix.subprocess.Popen"
    ) as mock_popen:
        mock_popen.return_value = MagicMock()
        provider.exec(handle, command=list(cmd), env={"PATH": "/x"}, exec_policy=None)
    call = mock_popen.call_args
    assert call.args[0] == expected_argv
    assert call.kwargs.get("cwd") == expected_cwd == _REAL_PROJECT_PATH
    assert call.kwargs.get("start_new_session") is True


def test_exec_spawn_argv_matches_wrap_agent_cmd_cross_user():
    # Cross-user: build_launch_argv must produce the same wrapped argv as
    # _wrap_agent_cmd, GIVEN the same finalized env. P3b moves the cross-user
    # env finalization (OPENACE_GIT_CACHE_ROOT overwrite + guard validation)
    # out of _wrap_agent_cmd into _run_local's env-prep, so this test feeds
    # both paths an already-finalized env (cacheroot pre-set; guard validation
    # mocked out of _wrap_agent_cmd) and asserts the spawn shape matches.
    cmd = ["claude", "--print"]
    account = "openace-agent"
    finalized_env = {
        "PATH": "/guard/bin:/usr/bin",
        "OPENACE_PROXY_TOKEN": "tok",
        "OPENACE_REAL_GIT": "/usr/bin/git",
        "OPENACE_GIT_CACHE_ROOT": str(
            AutonomousAgentRunner._resolve_home_dir(account) / ".cache" / "pre-commit"
        ),
    }
    with patch.object(AutonomousAgentRunner, "_validate_cross_user_guard_bin"):
        expected_argv, expected_cwd = AutonomousAgentRunner._wrap_agent_cmd(
            list(cmd), _REAL_PROJECT_PATH, account, dict(finalized_env), task_id="t-1"
        )

    provider = LegacyPosixProvider()
    handle = provider.create(
        _spec(task_id="t-1", project_path=_REAL_PROJECT_PATH, system_account=account)
    )
    with patch(
        "app.modules.workspace.autonomous.sandbox.legacy_posix.subprocess.Popen"
    ) as mock_popen:
        mock_popen.return_value = MagicMock()
        provider.exec(handle, command=list(cmd), env=dict(finalized_env), exec_policy=None)
    call = mock_popen.call_args
    assert call.args[0] == expected_argv
    assert call.kwargs.get("cwd") is expected_cwd is None
    assert call.kwargs.get("env") == finalized_env
    assert call.kwargs.get("start_new_session") is True


# keep the signal import referenced
_ = signal
