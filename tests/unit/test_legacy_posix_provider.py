"""LegacyPosixProvider — spawn-shape and classification contract (#2022 P3a/P3b).

Pure and mocked-Popen tests: capabilities, fail-closed create, exit-code
classification, launch-argv wrapping (same-user verbatim / cross-user sudo
wrap), spawn kwargs (cwd/env/start_new_session), and golden equivalence vs
the old inline ``_wrap_agent_cmd`` + ``Popen`` path. No real OS process is
spawned here — the real-process counterparts (stream/stop/destroy/signals/
evidence) live in
``tests/integration/subprocess/test_legacy_posix_provider_process.py``.

Scope (#2022): autonomous-only. ``_run_local`` and its helper methods are
untouched in P3a — zero production behavior change.
"""

from __future__ import annotations

import tempfile
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner
from app.modules.workspace.autonomous.sandbox.legacy_posix import (
    LegacyPosixProvider,
    classify_isolated_exit_code,
)
from app.modules.workspace.autonomous.sandbox.provider import CapabilityUnsupported
from app.modules.workspace.autonomous.sandbox.types import (
    SandboxCapability,
    SandboxSpec,
    SandboxStatus,
)

pytestmark = [pytest.mark.regression, pytest.mark.issue(2022)]

_LEGACY_CAPS = frozenset(
    {
        SandboxCapability.PRIVATE_HOME_TMP_XDG,
        SandboxCapability.FILESYSTEM_ACL,
        SandboxCapability.CPU_MEM_PIDS_TIME_QUOTA,
        SandboxCapability.CREDENTIAL_TOKEN_BINDING,
    }
)

# Mock-Popen tests pass "/repo" explicitly (no real chdir happens); the pure
# non-spawning tests only need a syntactically valid existing path.
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
    with pytest.raises(CapabilityUnsupported) as exc_info:
        provider.create(spec)
    assert SandboxCapability.NAMESPACE_ISOLATION in exc_info.value.missing_capabilities


def test_legacy_create_mints_handle():
    provider = LegacyPosixProvider()
    handle = provider.create(_spec())
    assert handle.sandbox_id
    assert handle.generation == 1
    assert handle.provider_name == "legacy_posix"


def test_legacy_destroy_is_idempotent():
    provider = LegacyPosixProvider()
    handle = provider.create(_spec())
    provider.destroy(handle)
    provider.destroy(handle)  # no raise
    assert provider.inspect(handle) == SandboxStatus.DESTROYED


# ── exit classification (#2022 P3a — mirrors agent_runner P1 #2067 logic) ──


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


# ── spawn-shape contract (mocked Popen) ──


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


# ── #2022 P3b: golden spawn equivalence vs the old inline path ──
#
# Locks the invariant the _run_local cut relies on: provider.exec must spawn
# byte-identical (argv, cwd, env, start_new_session) to the old _wrap_agent_cmd
# + inline Popen, for both same-user and cross-user. If these break, C2's cut
# would silently change execution behavior.


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
