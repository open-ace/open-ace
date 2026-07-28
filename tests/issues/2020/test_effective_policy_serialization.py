"""#2020 Phase B — effective-policy snapshot builder + orchestrator persistence.

``build_effective_policy`` is a pure function of (provider_name, declared
capabilities, AgentTaskPolicy) → JSON-serializable dict. The ``enforced`` map is
DERIVED from the declared capabilities, not hardcoded per provider, so it stays
honest (#2082 lesson: Remote falsely claimed Legacy's caps would show up here as
"nothing enforced" because Remote declares no caps). The orchestrator persists
the snapshot at sandbox-create time so the workflow detail UI can show what was
actually in effect for a run, independent of the live agent-launcher.conf.
"""

from __future__ import annotations

import json

from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator
from app.modules.workspace.autonomous.sandbox.effective_policy import build_effective_policy
from app.modules.workspace.autonomous.sandbox.types import SandboxCapability
from app.modules.workspace.autonomous.task_isolation import AgentTaskPolicy

_LEGACY_CAPS = frozenset(
    {
        SandboxCapability.PRIVATE_HOME_TMP_XDG,
        SandboxCapability.FILESYSTEM_ACL,
        SandboxCapability.CPU_MEM_PIDS_TIME_QUOTA,
        SandboxCapability.CREDENTIAL_TOKEN_BINDING,
    }
)


# ── build_effective_policy (pure) ──


def test_legacy_snapshot_records_limits_and_honest_enforcement():
    policy = AgentTaskPolicy(
        memory_max_bytes=2_147_483_648,
        pids_max=512,
        cpu_max="200000/100000",
        wall_clock_limit=3600,
    )
    snap = build_effective_policy("legacy_posix", _LEGACY_CAPS, policy)
    assert snap["provider"] == "legacy_posix"
    assert snap["capabilities"] == sorted(c.value for c in _LEGACY_CAPS)
    assert snap["limits"]["memory_max_bytes"] == 2_147_483_648
    assert snap["limits"]["wall_clock_limit"] == 3600
    assert snap["limits"]["ephemeral_storage_limit"] == 0
    # Legacy declares CPU_MEM_PIDS_TIME_QUOTA → memory/pids/cpu/wall_clock enforced
    assert snap["enforced"]["memory"] is True
    assert snap["enforced"]["wall_clock"] is True
    # Legacy does NOT declare STORAGE_INODE_QUOTA → storage/inode NOT enforced (honest)
    assert snap["enforced"]["ephemeral_storage"] is False
    assert snap["enforced"]["inode"] is False


def test_future_gvisor_declaring_storage_quota_marks_it_enforced():
    """A #2023 backend declaring STORAGE_INODE_QUOTA → storage/inode enforced."""
    caps = _LEGACY_CAPS | {SandboxCapability.STORAGE_INODE_QUOTA}
    snap = build_effective_policy(
        "gvisor", caps, AgentTaskPolicy(ephemeral_storage_limit=10_737_418_240)
    )
    assert snap["enforced"]["ephemeral_storage"] is True
    assert snap["enforced"]["inode"] is True
    assert snap["limits"]["ephemeral_storage_limit"] == 10_737_418_240


def test_remote_with_no_caps_reports_nothing_enforced():
    """Remote declares frozenset() → every dimension honestly 'not enforced'."""
    snap = build_effective_policy("remote_machine", frozenset(), AgentTaskPolicy())
    assert snap["capabilities"] == []
    assert snap["enforced"]["memory"] is False
    assert snap["enforced"]["wall_clock"] is False
    assert snap["enforced"]["ephemeral_storage"] is False


def test_none_policy_yields_safe_default_limits():
    snap = build_effective_policy("legacy_posix", _LEGACY_CAPS, None)
    assert snap["limits"]["memory_max_bytes"] == 0
    assert snap["limits"]["wall_clock_limit"] == 0
    assert snap["cgroup_enabled"] == ""
    # enforced is still derived from caps, independent of policy presence
    assert snap["enforced"]["memory"] is True


def test_snapshot_is_json_serializable():
    snap = build_effective_policy("legacy_posix", _LEGACY_CAPS, AgentTaskPolicy())
    # Must round-trip through JSON (that is how it is persisted).
    json.loads(json.dumps(snap))


# ── orchestrator persistence ──


class _FakeRepo:
    def __init__(self) -> None:
        self.updates: list[tuple[str, dict]] = []

    def update_workflow(self, wf_id: str, updates: dict) -> None:
        self.updates.append((wf_id, dict(updates)))


def _make_orch() -> AutonomousOrchestrator:
    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch.repo = _FakeRepo()
    orch._workflow_id = "wf-123"
    return orch


def test_on_sandbox_created_persists_effective_policy_as_json():
    orch = _make_orch()
    snap = build_effective_policy(
        "legacy_posix", _LEGACY_CAPS, AgentTaskPolicy(memory_max_bytes=2048)
    )
    orch._on_sandbox_created("s1", "sandbox-abc", "legacy_posix", None, snap)
    _, updates = orch.repo.updates[0]
    assert "sandbox_effective_policy" in updates
    persisted = json.loads(updates["sandbox_effective_policy"])
    assert persisted["provider"] == "legacy_posix"
    assert persisted["limits"]["memory_max_bytes"] == 2048


def test_on_sandbox_created_omits_effective_policy_when_none():
    """A caller passing no snapshot (legacy 4-arg path) does not write the key."""
    orch = _make_orch()
    orch._on_sandbox_created("s1", "sandbox-abc", "legacy_posix", None)
    _, updates = orch.repo.updates[0]
    assert "sandbox_effective_policy" not in updates
