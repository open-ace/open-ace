"""Structured error classification for isolated agent exits (Issue #2020).

A resource/cleanup failure must surface as a structured ``error_code`` on
``AgentTaskResult`` (acceptance #6: "超限产生结构化错误并回收 task 资源"),
not just an opaque non-zero exit. The launcher emits distinct exit codes and
stderr sentinels; this pins the mapping.
"""

from __future__ import annotations

from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner as R


def test_cgroup_required_unavailable_classified():
    code, _msg = R._classify_isolated_exit_code(
        66, "OPENACE_CGROUP_REQUIRED: cgroup enforcement forced but unavailable"
    )
    assert code == "task_resource_policy_unavailable"


def test_repo_integrity_violation_classified():
    code, _msg = R._classify_isolated_exit_code(
        68, "OPENACE_REPO_INTEGRITY_VIOLATION: .git entry changed"
    )
    assert code == "repo_integrity_violation"


def test_signal_killed_classified_as_resource_limit():
    # 137 = 128 + SIGKILL: typical of a cgroup OOM kill or prlimit breach.
    code, _msg = R._classify_isolated_exit_code(137, "")
    assert code == "task_resource_limit_exceeded"


def test_clean_exit_is_none():
    code, _msg = R._classify_isolated_exit_code(0, "")
    assert code is None


def test_generic_failure_is_none():
    # An ordinary non-zero exit (e.g. CLI error 1) is not a resource/isolation
    # failure and should not be misclassified.
    code, _msg = R._classify_isolated_exit_code(1, "some cli error")
    assert code is None


def test_wall_clock_timeout_code_constant_exists():
    # The timeout result path uses this stable code.
    assert R.TASK_WALL_CLOCK_TIMEOUT_ERROR_CODE == "task_wall_clock_timeout"
