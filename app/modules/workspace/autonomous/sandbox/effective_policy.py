"""Effective-policy snapshot builder (Issue #2020 Phase B).

``build_effective_policy`` is a PURE function of (provider name, the
capabilities a provider actually declares, and the AgentTaskPolicy in effect for
the task). It returns a JSON-serializable dict snapshotted at sandbox-create
time and persisted on the workflow row, so the workflow detail UI can show what
resource/isolation policy was actually in effect for a run — independent of the
live ``agent-launcher.conf`` (which can change between runs).

The ``enforced`` map is DERIVED from the declared capabilities, NOT hardcoded
per provider, so it cannot lie. ``memory``/``pids``/``cpu``/``wall_clock`` are
enforced iff the provider declares ``CPU_MEM_PIDS_TIME_QUOTA``;
``ephemeral_storage``/``inode`` iff it declares ``STORAGE_INODE_QUOTA``. A
provider that falsely claimed a capability it does not wire (the #2082 lesson:
Remote copied Legacy's caps) would show ``enforced=True`` here while doing
nothing — which is exactly what the Track 3 capability-realism conformance
probes catch at test time; this snapshot merely reports what was declared.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.modules.workspace.autonomous.sandbox.types import SandboxCapability

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids runtime import cycle
    from app.modules.workspace.autonomous.task_isolation import AgentTaskPolicy

_SCHEMA_VERSION = 1


def build_effective_policy(
    provider_name: str,
    capabilities: frozenset[SandboxCapability],
    policy: AgentTaskPolicy | None,
) -> dict:
    """Build the effective-policy snapshot dict.

    ``capabilities`` is what the provider DECLARES (``provider.capabilities()``);
    ``policy`` is the per-task AgentTaskPolicy (may be None when no conf is
    loaded). Returns a plain dict safe for ``json.dumps``.
    """
    has_time_quota = SandboxCapability.CPU_MEM_PIDS_TIME_QUOTA in capabilities
    has_storage_quota = SandboxCapability.STORAGE_INODE_QUOTA in capabilities
    policy_configured = policy is not None
    return {
        "schema_version": _SCHEMA_VERSION,
        "provider": provider_name,
        "capabilities": sorted(cap.value for cap in capabilities),
        "policy_configured": policy_configured,
        "limits": (
            {
                "memory_max_bytes": policy.memory_max_bytes,
                "pids_max": policy.pids_max,
                "cpu_max": policy.cpu_max,
                "wall_clock_limit": policy.wall_clock_limit,
                "ephemeral_storage_limit": policy.ephemeral_storage_limit,
                "inode_limit": policy.inode_limit,
            }
            if policy
            else {}
        ),
        "cgroup_enabled": policy.cgroup_enabled if policy else "",
        "task_root": policy.task_root if policy else "",
        # Derived from declared capabilities — honest, not hardcoded per provider.
        "enforced": {
            "memory": has_time_quota,
            "pids": has_time_quota,
            "cpu": has_time_quota,
            "wall_clock": has_time_quota,
            "ephemeral_storage": has_storage_quota,
            "inode": has_storage_quota,
        },
    }
