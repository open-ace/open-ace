"""#2020 Phase B — Legacy provider must fail-closed on storage/inode quota.

The issue's required test ``test_provider_rejects_required_policy_when_unsupported``:
a spec whose policy declares ``ephemeral_storage_limit`` / ``inode_limit`` implies
the ``STORAGE_INODE_QUOTA`` capability, which the Legacy POSIX backend cannot
honor (no ``io.max`` / disk quota today). Legacy must refuse creation
(fail-closed) rather than silently accept a limit it will not enforce — the
#2082 lesson (Remote falsely claimed Legacy's caps) generalized into a gate.

wall_clock_limit is covered by the existing ``CPU_MEM_PIDS_TIME_QUOTA`` cap
(Legacy enforces it via the runner timeout), so it does NOT imply an extra
capability and must not trigger the gate.
"""

from __future__ import annotations

import pytest

from app.modules.workspace.autonomous.sandbox.legacy_posix import LegacyPosixProvider
from app.modules.workspace.autonomous.sandbox.provider import (
    CapabilityUnsupported,
    implied_required_capabilities,
)
from app.modules.workspace.autonomous.sandbox.types import SandboxCapability, SandboxSpec
from app.modules.workspace.autonomous.task_isolation import AgentTaskPolicy, read_agent_task_policy


def _spec_with_policy(policy: AgentTaskPolicy) -> SandboxSpec:
    return SandboxSpec(
        task_id="t",
        project_path="/tmp",
        cli_tool="c",
        policy=policy,
    )


def test_ephemeral_storage_limit_implies_storage_inode_quota():
    policy = AgentTaskPolicy(ephemeral_storage_limit=1_073_741_824)  # 1 GiB
    implied = implied_required_capabilities(_spec_with_policy(policy))
    assert SandboxCapability.STORAGE_INODE_QUOTA in implied


def test_inode_limit_implies_storage_inode_quota():
    policy = AgentTaskPolicy(inode_limit=100_000)
    implied = implied_required_capabilities(_spec_with_policy(policy))
    assert SandboxCapability.STORAGE_INODE_QUOTA in implied


def test_legacy_rejects_spec_implying_storage_quota():
    policy = AgentTaskPolicy(ephemeral_storage_limit=1_073_741_824)
    with pytest.raises(CapabilityUnsupported) as exc_info:
        LegacyPosixProvider().create(_spec_with_policy(policy))
    assert SandboxCapability.STORAGE_INODE_QUOTA in exc_info.value.missing_capabilities


def test_legacy_rejects_spec_implying_inode_quota():
    policy = AgentTaskPolicy(inode_limit=100_000)
    with pytest.raises(CapabilityUnsupported) as exc_info:
        LegacyPosixProvider().create(_spec_with_policy(policy))
    assert SandboxCapability.STORAGE_INODE_QUOTA in exc_info.value.missing_capabilities


def test_default_policy_does_not_imply_storage_quota():
    """Default policy (storage/inode=0) implies no extra capability."""
    implied = implied_required_capabilities(_spec_with_policy(AgentTaskPolicy()))
    assert SandboxCapability.STORAGE_INODE_QUOTA not in implied


def test_legacy_accepts_default_policy_spec():
    """Legacy satisfies the default policy's implied caps — creation succeeds."""
    handle = LegacyPosixProvider().create(_spec_with_policy(AgentTaskPolicy()))
    assert handle.sandbox_id  # created without raising


def test_wall_clock_limit_does_not_trigger_gate():
    """wall_clock is covered by CPU_MEM_PIDS_TIME_QUOTA (Legacy declares it)."""
    policy = AgentTaskPolicy(wall_clock_limit=1800)
    handle = LegacyPosixProvider().create(_spec_with_policy(policy))  # no raise
    assert handle.spec.policy.wall_clock_limit == 1800


def test_read_agent_task_policy_parses_phase_b_limits(tmp_path):
    """The three Phase B conf keys round-trip into AgentTaskPolicy fields."""
    conf = tmp_path / "agent-launcher.conf"
    conf.write_text(
        "\n".join(
            [
                "agent_task_wall_clock_limit=1800",
                "agent_task_ephemeral_storage_limit=1073741824",
                "agent_task_inode_limit=100000",
            ]
        ),
        encoding="utf-8",
    )
    policy = read_agent_task_policy(str(conf))
    assert policy.wall_clock_limit == 1800
    assert policy.ephemeral_storage_limit == 1_073_741_824
    assert policy.inode_limit == 100_000


def test_read_agent_task_policy_defaults_phase_b_limits_to_zero(tmp_path):
    """Missing Phase B keys → 0 (Legacy behavior unchanged by default)."""
    conf = tmp_path / "agent-launcher.conf"
    conf.write_text("agent_task_memory_max_bytes=2048\n", encoding="utf-8")
    policy = read_agent_task_policy(str(conf))
    assert policy.wall_clock_limit == 0
    assert policy.ephemeral_storage_limit == 0
    assert policy.inode_limit == 0
