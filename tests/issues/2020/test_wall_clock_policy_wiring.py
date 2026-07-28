"""#2020 Phase B — runner wall-clock timeout honors policy.wall_clock_limit.

The wall-clock cap was previously an orchestrator-only env var
(``AUTONOMOUS_TASK_TIMEOUT``). Phase B promotes it to a contract dimension: the
runner resolves the effective timeout from ``AgentTaskPolicy.wall_clock_limit``
when set (>0), falling back to the explicit/orchestrator timeout otherwise.
``wall_clock_limit`` is covered by the existing ``CPU_MEM_PIDS_TIME_QUOTA``
capability (Legacy enforces it via this timeout), so it does NOT trigger the
storage-style fail-closed gate.
"""

from __future__ import annotations

from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner
from app.modules.workspace.autonomous.task_isolation import AgentTaskPolicy


def test_policy_wall_clock_overrides_explicit_default():
    policy = AgentTaskPolicy(wall_clock_limit=1800)
    assert AutonomousAgentRunner._resolve_wall_clock_timeout(3600, policy) == 1800


def test_no_policy_falls_back_to_explicit():
    assert AutonomousAgentRunner._resolve_wall_clock_timeout(3600, None) == 3600


def test_policy_zero_wall_clock_falls_back_to_explicit():
    """wall_clock_limit=0 means unset → use the explicit/orchestrator timeout."""
    policy = AgentTaskPolicy()  # wall_clock_limit=0
    assert AutonomousAgentRunner._resolve_wall_clock_timeout(3600, policy) == 3600
