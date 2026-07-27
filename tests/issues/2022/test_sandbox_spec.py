"""SandboxProvider contract — SandboxCapability + SandboxSpec (Issue #2022 Phase 1).

Phase 1 fixes the *contract surface* the later phases (LegacyPosixProvider,
RemoteMachineProvider, persistence, reconciliation) build on. These tests pin
the capability taxonomy and the ``SandboxSpec`` value object before any
production provider exists.

Scope (#2022 comment, 2026-07-26): this contract abstracts ONLY the autonomous
agent execution path. ``SandboxSpec.policy`` reuses the per-task isolation
policy from #2020 (``AgentTaskPolicy``) rather than redefining HOME/TMP/quota.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

from app.modules.workspace.autonomous.sandbox.types import SandboxCapability, SandboxSpec
from app.modules.workspace.autonomous.task_isolation import AgentTaskPolicy

# The capability taxonomy a spec may require. LegacyPosixProvider (Phase 3)
# satisfies the first four; namespace/network isolation is reserved for the
# OpenSandbox/Kubernetes backend (#2023) and MUST make Legacy refuse creation
# (fail-closed, tested in test_sandbox_provider.py).
_EXPECTED_CAPABILITIES = {
    "private_home_tmp_xdg",
    "filesystem_acl",
    "cpu_mem_pids_time_quota",
    "credential_token_binding",
    "namespace_isolation",
    "network_egress_policy",
}


def test_sandbox_capability_enum_has_documented_values():
    values = {cap.value for cap in SandboxCapability}
    assert values == _EXPECTED_CAPABILITIES


def test_sandbox_capability_is_string_enum():
    # str-enum so capability sets serialize to JSON for sandbox_policy_digest.
    assert SandboxCapability.PRIVATE_HOME_TMP_XDG == "private_home_tmp_xdg"
    assert isinstance(SandboxCapability.FILESYSTEM_ACL.value, str)


def test_sandbox_spec_is_frozen_dataclass():
    spec = SandboxSpec(task_id="t-1", project_path="/repo", cli_tool="claude-code")
    try:
        spec.task_id = "tampered"  # type: ignore[misc]
    except FrozenInstanceError:
        return
    raise AssertionError("SandboxSpec must be frozen")


def test_sandbox_spec_required_capabilities_defaults_empty():
    spec = SandboxSpec(task_id="t-1", project_path="/repo", cli_tool="claude-code")
    assert spec.required_capabilities == frozenset()
    # system_account / policy are optional — a local same-user task has neither.
    assert spec.system_account is None
    assert spec.policy is None


def test_sandbox_spec_round_trips_fields():
    caps = frozenset({SandboxCapability.PRIVATE_HOME_TMP_XDG, SandboxCapability.FILESYSTEM_ACL})
    spec = SandboxSpec(
        task_id="task-abc",
        project_path="/srv/worktrees/x",
        cli_tool="qwen-code",
        system_account="openace-agent",
        required_capabilities=caps,
    )
    assert spec.task_id == "task-abc"
    assert spec.project_path == "/srv/worktrees/x"
    assert spec.cli_tool == "qwen-code"
    assert spec.system_account == "openace-agent"
    assert spec.required_capabilities is caps


def test_sandbox_spec_reuses_agent_task_policy_from_issue_2020():
    # The spec does NOT redefine HOME/TMP/quota; it carries the #2020 policy.
    policy = AgentTaskPolicy(memory_max_bytes=536870912, pids_max=256, max_concurrent_workflows=2)
    spec = SandboxSpec(
        task_id="t-1",
        project_path="/repo",
        cli_tool="claude-code",
        policy=policy,
    )
    assert spec.policy is policy
    assert spec.policy.memory_max_bytes == 536870912
    assert spec.policy.pids_max == 256
    assert spec.policy.max_concurrent_workflows == 2


# ── #2022 P4 ①: gVisor-facing isolation dimensions on the spec ──
#
# The minimal P1 spec carried only identity + #2020 policy. A gVisor/container
# backend (#2023) needs to express network egress, runtime/image and volumes
# THROUGH THE SPEC — otherwise it would extend the contract itself, which is
# exactly the backend-detail-leak #2022 exists to prevent. AgentTaskPolicy
# already owns HOME/TMP/cgroup/quota, so these new dimensions do not duplicate it.

from app.modules.workspace.autonomous.sandbox.types import (  # noqa: E402
    NetworkEgressPolicy,
    RuntimeSpec,
    VolumeSpec,
)


def test_network_egress_policy_is_frozen_value_object():
    egress = NetworkEgressPolicy(
        mode="allow_explicit",
        allow_cidrs=("10.0.0.0/8",),
        allow_hosts=("github.com",),
    )
    assert egress.mode == "allow_explicit"
    assert egress.allow_cidrs == ("10.0.0.0/8",)
    assert egress.allow_hosts == ("github.com",)
    try:
        egress.mode = "unrestricted"  # type: ignore[misc]
    except FrozenInstanceError:
        return
    raise AssertionError("NetworkEgressPolicy must be frozen")


def test_runtime_and_volume_specs_are_frozen_value_objects():
    runtime = RuntimeSpec(image="openace/agent:1", runtime="runsc")
    assert runtime.image == "openace/agent:1"
    assert runtime.runtime == "runsc"
    vol = VolumeSpec(name="repo", mount_path="/workspace", kind="ephemeral")
    assert vol.mount_path == "/workspace"
    assert vol.kind == "ephemeral"
    try:
        runtime.runtime = "runc"  # type: ignore[misc]
    except FrozenInstanceError:
        return
    raise AssertionError("RuntimeSpec must be frozen")


def test_sandbox_spec_carries_gvisor_isolation_dimensions():
    egress = NetworkEgressPolicy(mode="deny_all")
    runtime = RuntimeSpec(image="openace/agent:1", runtime="runsc")
    volumes = (VolumeSpec(name="repo", mount_path="/workspace", kind="ephemeral"),)
    spec = SandboxSpec(
        task_id="t-1",
        project_path="/repo",
        cli_tool="claude-code",
        machine_id="machine-7",
        user_id=42,
        network_egress=egress,
        runtime=runtime,
        volumes=volumes,
        required_capabilities=frozenset(
            {SandboxCapability.NAMESPACE_ISOLATION, SandboxCapability.NETWORK_EGRESS_POLICY}
        ),
    )
    assert spec.machine_id == "machine-7"
    assert spec.user_id == 42
    assert spec.network_egress is egress
    assert spec.runtime is runtime
    assert spec.volumes == volumes


def test_sandbox_spec_gvisor_fields_default_none_for_legacy():
    # Legacy/Remote providers ignore the gVisor dimensions; they default to
    # None/empty so existing two-arg construction stays backward-compatible.
    spec = SandboxSpec(task_id="t-1", project_path="/repo", cli_tool="claude-code")
    assert spec.machine_id is None
    assert spec.user_id is None
    assert spec.network_egress is None
    assert spec.runtime is None
    assert spec.volumes == ()
    assert spec.transcript_profile == ""
    assert spec.evidence_profile == ""
