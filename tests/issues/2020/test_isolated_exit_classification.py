"""Structured error classification for isolated agent exits (Issue #2020).

A resource/cleanup failure must surface as a structured ``error_code`` on
``AgentTaskResult`` (acceptance #6), not an opaque non-zero exit. The launcher
emits distinct exit codes and stderr sentinels; this pins the mapping.

Conventions pinned here (review feedback on PR #2067):

* Python ``subprocess.Popen.returncode`` is **negative** for signal deaths
  (-15 SIGTERM, -9 SIGKILL), NOT the shell's 128+N. The classifier detects
  signals via ``returncode < 0``.
* SIGTERM/SIGINT are the orchestrator's own stop/timeout signals, never a
  resource breach.
* A signal-death is only a resource breach when the orchestrator did NOT
  initiate the kill AND a resource policy is actually configured AND the
  signal indicates exhaustion (SIGKILL/SIGXCPU). This keeps timeouts/stops
  from being misreported as ``task_resource_limit_exceeded`` (which would
  clobber ``task_wall_clock_timeout`` via the ``or`` fallback).
* exit 66 is heavily overloaded in the launcher; the cgroup-policy code is
  matched by the ``OPENACE_CGROUP_REQUIRED`` sentinel only.
"""

from __future__ import annotations

from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner as R


def test_cgroup_required_unavailable_classified():
    code, _msg = R._classify_isolated_exit_code(
        66, "OPENACE_CGROUP_REQUIRED: cgroup enforcement forced but unavailable"
    )
    assert code == "task_resource_policy_unavailable"


def test_exit_66_without_sentinel_is_not_cgroup_policy():
    # exit 66 is also validator-unavailable / reject_conf / flock-missing /
    # base64-missing in the launcher; only the cgroup sentinel means policy.
    code, _msg = R._classify_isolated_exit_code(66, "openace-run-as: flock is required")
    assert code is None


def test_repo_integrity_violation_classified():
    code, _msg = R._classify_isolated_exit_code(
        68, "OPENACE_REPO_INTEGRITY_VIOLATION: .git entry changed"
    )
    assert code == "repo_integrity_violation"


def test_sigkill_classified_as_resource_limit_when_policy_configured():
    # Python returncode for SIGKILL is -9 (not 137).
    code, _msg = R._classify_isolated_exit_code(-9, "", resource_policy_configured=True)
    assert code == "task_resource_limit_exceeded"


def test_sigxcpu_classified_as_resource_limit_when_policy_configured():
    code, _msg = R._classify_isolated_exit_code(-24, "", resource_policy_configured=True)
    assert code == "task_resource_limit_exceeded"


def test_sigterm_never_classified_as_resource_limit():
    # SIGTERM is the orchestrator's own stop/timeout signal.
    code, _msg = R._classify_isolated_exit_code(-15, "", resource_policy_configured=True)
    assert code is None


def test_sigint_never_classified_as_resource_limit():
    code, _msg = R._classify_isolated_exit_code(-2, "", resource_policy_configured=True)
    assert code is None


def test_orchestrator_initiated_kill_not_resource_limit():
    # A stop/timeout that escalated to SIGKILL must not be reported as a
    # resource breach — the orchestrator killed it.
    code, _msg = R._classify_isolated_exit_code(
        -9, "", orchestrator_initiated=True, resource_policy_configured=True
    )
    assert code is None


def test_no_resource_classification_without_configured_policy():
    code, _msg = R._classify_isolated_exit_code(-9, "", resource_policy_configured=False)
    assert code is None


def test_clean_exit_is_none():
    code, _msg = R._classify_isolated_exit_code(0, "")
    assert code is None


def test_generic_failure_is_none():
    code, _msg = R._classify_isolated_exit_code(1, "some cli error")
    assert code is None


def test_wall_clock_timeout_code_constant_exists():
    assert R.TASK_WALL_CLOCK_TIMEOUT_ERROR_CODE == "task_wall_clock_timeout"
