"""#2022 P4 ④ — unified contract tests over Legacy + Remote providers.

The #2022 acceptance: "Legacy / Remote / 未来 OpenSandbox 复用 contract tests".
Both providers must satisfy the SAME contract assertions (capability fail-closed,
create/exec/stream/stop/destroy lifecycle, evidence sandbox attribution). A
#2023 gVisor provider reuses this suite by adding its harness to the parametrize
list. ``FakeSandboxProvider`` stays the fast in-memory regression for the shape.
"""

from __future__ import annotations

import tempfile
from typing import Any

import pytest

from app.modules.workspace.autonomous.sandbox.legacy_posix import LegacyPosixProvider
from app.modules.workspace.autonomous.sandbox.provider import CapabilityUnsupported
from app.modules.workspace.autonomous.sandbox.remote_machine import (
    RemoteMachineProvider,
    RemoteTurnSpec,
)
from app.modules.workspace.autonomous.sandbox.types import (
    SandboxCapability,
    SandboxEventKind,
    SandboxSpec,
    SandboxStatus,
)
from app.modules.workspace.autonomous.task_isolation import AgentTaskPolicy

_LEGACY_CAPS = frozenset(
    {
        SandboxCapability.PRIVATE_HOME_TMP_XDG,
        SandboxCapability.FILESYSTEM_ACL,
        SandboxCapability.CPU_MEM_PIDS_TIME_QUOTA,
        SandboxCapability.CREDENTIAL_TOKEN_BINDING,
    }
)


class _FakeRemoteSessionManager:
    def __init__(self) -> None:
        self._polls = 0

    def create_remote_session(self, **kwargs: Any) -> dict[str, Any]:
        return {"session_id": "rsess-1"}

    def send_message(self, **kwargs: Any) -> bool:
        return True

    def get_session_status(self, session_id: str) -> dict[str, Any]:
        self._polls += 1
        return {"output": [{"is_complete": True, "stream": "stdout"}], "exit_code": 0}

    def stop_session(self, session_id: str) -> bool:
        return True


class _LegacyHarness:
    name = "legacy_posix"
    provider_name = "legacy_posix"
    expected_caps = _LEGACY_CAPS

    def __init__(self) -> None:
        self.provider = LegacyPosixProvider()

    def spec(self, **overrides: Any) -> SandboxSpec:
        base: dict[str, Any] = {
            "task_id": "t",
            "project_path": tempfile.gettempdir(),
            "cli_tool": "c",
        }
        base.update(overrides)
        return SandboxSpec(**base)

    def exec(self, handle) -> Any:
        return self.provider.exec(handle, command=["/bin/echo", "hi"], env=None, exec_policy=None)

    def drive_to_completion(self, eh) -> list:
        return list(self.provider.stream(eh))

    def realism_probes(self) -> dict[SandboxCapability, Any]:
        """Per-cap enforcement-wiring probes (#2020 Phase B / #2082 defense).

        Each declared cap maps to a probe that fails if the provider does not
        actually wire it. The Legacy caps are all enforced through the isolated
        ``openace-run-as`` launcher on the cross-user path, so each probe
        inspects a distinct facet of ``build_launch_argv`` for a cross-user spec
        — the launcher entry point is the signal that ACL/cgroup/HOME-TMP/
        privilege-drop are wired (the cgroup write itself is integration-tested
        in tests/issues/2020 Phase A). Non-flaky: build_launch_argv is pure, no
        spawn, no root.
        """

        def cross_user_argv() -> list[str]:
            spec = self.spec(
                system_account="agent_x",
                policy=AgentTaskPolicy(memory_max_bytes=2048, pids_max=10, cpu_max="100000/100000"),
            )
            handle = self.provider.create(spec)
            return self.provider.build_launch_argv(
                handle, command=["/bin/true"], env={"HOME": "/tmp"}
            )

        def probe_private_home_tmp_xdg() -> None:
            argv = cross_user_argv()
            assert (
                "--task-id" in argv
            ), "PRIVATE_HOME_TMP_XDG: launcher must key per-task HOME/TMP/XDG off --task-id"

        def probe_filesystem_acl() -> None:
            argv = cross_user_argv()
            assert argv[:4] == [
                "sudo",
                "-n",
                "-u",
                "root",
            ], "FILESYSTEM_ACL: cross-user launch must route through the root launcher"

        def probe_cpu_mem_pids_time_quota() -> None:
            argv = cross_user_argv()
            assert (
                "--isolated" in argv
            ), "CPU_MEM_PIDS_TIME_QUOTA: resource quota must go through the isolated launcher"

        def probe_credential_token_binding() -> None:
            argv = cross_user_argv()
            assert (
                argv[0] == "sudo"
            ), "CREDENTIAL_TOKEN_BINDING: credential isolation requires privilege drop"

        return {
            SandboxCapability.PRIVATE_HOME_TMP_XDG: probe_private_home_tmp_xdg,
            SandboxCapability.FILESYSTEM_ACL: probe_filesystem_acl,
            SandboxCapability.CPU_MEM_PIDS_TIME_QUOTA: probe_cpu_mem_pids_time_quota,
            SandboxCapability.CREDENTIAL_TOKEN_BINDING: probe_credential_token_binding,
        }


class _RemoteHarness:
    name = "remote_machine"
    provider_name = "remote_machine"
    # Remote provides NO verifiable isolation today (#2078 P1#1): the remote-agent
    # executor runs from dict(os.environ) + plain Popen. Declaring any cap would
    # be a fail-closed lie.
    expected_caps = frozenset()

    def __init__(self) -> None:
        self.provider = RemoteMachineProvider(_FakeRemoteSessionManager(), poll_interval=0)

    def spec(self, **overrides: Any) -> SandboxSpec:
        base: dict[str, Any] = {
            "task_id": "t",
            "project_path": "/repo",
            "cli_tool": "c",
            "machine_id": "m",
            "user_id": 1,
        }
        base.update(overrides)
        return SandboxSpec(**base)

    def exec(self, handle) -> Any:
        return self.provider.exec(
            handle, command=[], env=None, exec_policy=RemoteTurnSpec(prompt="hi")
        )

    def drive_to_completion(self, eh) -> list:
        return list(self.provider.stream(eh))

    def realism_probes(self) -> dict[SandboxCapability, Any]:
        """Remote declares no caps → no probes (the honest-empty assertion in
        ``test_capabilities_match_expected`` IS the realism check). #2082
        defense: if a future change makes Remote claim a cap, it must add a
        probe here or ``test_every_declared_capability_has_a_realism_probe``
        fails."""
        return {}


# ── #2023 plug-in point ──
# To conformance-test a new backend (gVisor/OpenSandbox), add a `_GvisorHarness`
# here with `expected_caps` (the caps it REALLY enforces — e.g.
# STORAGE_INODE_QUOTA, NAMESPACE_ISOLATION, NETWORK_EGRESS_POLICY) and a matching
# `realism_probes()` entry per declared cap. Every test below then runs against
# it automatically. The realism-probe framework (track 3) ensures the new
# harness cannot claim a cap without a probe backing it (#2082 defense).
HARNESSES = [_LegacyHarness, _RemoteHarness]


@pytest.fixture(params=HARNESSES, ids=[h.name for h in HARNESSES])
def harness(request):
    return request.param()


# ── shared contract assertions ──


def test_capabilities_match_expected(harness):
    # Each provider declares only what it actually enforces (#2078 P1#1):
    # Legacy the four POSIX caps; Remote none (remote-agent has no isolation).
    assert harness.provider.capabilities() == harness.expected_caps
    assert SandboxCapability.NAMESPACE_ISOLATION not in harness.provider.capabilities()
    assert SandboxCapability.NETWORK_EGRESS_POLICY not in harness.provider.capabilities()


def test_create_rejects_namespace_requirement(harness):
    spec = harness.spec(required_capabilities=frozenset({SandboxCapability.NAMESPACE_ISOLATION}))
    with pytest.raises(CapabilityUnsupported):
        harness.provider.create(spec)


def test_explicit_network_egress_fails_closed(harness):
    # #2078 P1#1: setting network_egress on the spec implies
    # NETWORK_EGRESS_POLICY — neither Legacy nor Remote provides it, so create
    # must fail closed rather than silently ignore the policy field.
    from app.modules.workspace.autonomous.sandbox.types import NetworkEgressPolicy

    spec = harness.spec(network_egress=NetworkEgressPolicy(mode="deny_all"))
    with pytest.raises(CapabilityUnsupported):
        harness.provider.create(spec)


def test_create_mints_named_handle(harness):
    handle = harness.provider.create(harness.spec())
    assert handle.sandbox_id
    assert handle.provider_name == harness.provider_name
    assert handle.generation == 1


def test_exec_then_stream_emits_lifecycle_events(harness):
    handle = harness.provider.create(harness.spec())
    eh = harness.exec(handle)
    events = harness.drive_to_completion(eh)
    kinds = [e.kind for e in events]
    # The common lifecycle spine every provider must emit (Legacy also emits
    # STDOUT_CHUNK; Remote does not — the spine is the contract).
    for required in (
        SandboxEventKind.PROCESS_STARTED,
        SandboxEventKind.COMMAND_STARTED,
        SandboxEventKind.COMMAND_COMPLETED,
        SandboxEventKind.PROCESS_EXITED,
    ):
        assert required in kinds, f"{harness.name} missing {required}"
    completed = next(e for e in events if e.kind == SandboxEventKind.COMMAND_COMPLETED)
    assert completed.exit_code == 0


def test_stop_marks_stopped(harness):
    handle = harness.provider.create(harness.spec())
    eh = harness.exec(handle)
    harness.provider.stop(eh)
    assert harness.provider.inspect(handle) == SandboxStatus.STOPPED


def test_destroy_is_idempotent(harness):
    handle = harness.provider.create(harness.spec())
    harness.provider.destroy(handle)
    harness.provider.destroy(handle)  # no raise
    assert harness.provider.inspect(handle) == SandboxStatus.DESTROYED


def test_collect_execution_evidence_fills_sandbox_attribution(harness):
    handle = harness.provider.create(harness.spec())
    eh = harness.exec(handle)
    harness.drive_to_completion(eh)
    rows = harness.provider.collect_execution_evidence(handle)
    assert rows
    for row in rows:
        assert row.sandbox_id == handle.sandbox_id
        assert row.sandbox_generation == handle.generation


# ── capability realism (#2020 Phase B track 3 / #2082 defense) ──


def test_every_declared_capability_has_a_realism_probe(harness):
    """A provider must back EVERY declared cap with a probe (#2082 defense).

    The #2082 bug was Remote copy-pasting Legacy's 4 caps it could not enforce.
    ``test_capabilities_match_expected`` catches the claim-vs-reality mismatch
    for the providers we know; THIS test is the forward-looking gate: when #2023
    adds a gVisor harness declaring STORAGE_INODE_QUOTA / NAMESPACE_ISOLATION /
    NETWORK_EGRESS_POLICY, it MUST add a realism probe per cap or this fails —
    no provider can declare a cap without a test exercising its wiring.
    """
    declared = set(harness.provider.capabilities())
    probed = set(harness.realism_probes().keys())
    assert declared == probed, (
        f"{harness.name}: declared caps {declared} != probed caps {probed}; "
        "every declared capability needs a realism probe"
    )


def test_realism_probes_pass(harness):
    """Each declared cap's enforcement wiring is exercised and holds."""
    for cap, probe in harness.realism_probes().items():
        try:
            probe()
        except AssertionError as e:
            raise AssertionError(f"{harness.name} realism probe for {cap.value} failed: {e}") from e
