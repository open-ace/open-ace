"""OpenSandboxProvider lifecycle over the frozen #2022 contract (#2023).

Driven entirely by FakeOpenSandboxApi, which models upstream's *real* behaviour
— synchronous create returning Running, delete going Stopping then Terminated,
a non-zero exit arriving as an SSE `error` with a numeric evalue and no
execution_complete, and a pod OOM taking execd down with the container.
"""

from __future__ import annotations

import pytest

from app.modules.workspace.autonomous.command_evidence.types import TerminalReason
from app.modules.workspace.autonomous.sandbox.opensandbox.config import parse_backend_config
from app.modules.workspace.autonomous.sandbox.opensandbox.fake_server import FakeOpenSandboxApi
from app.modules.workspace.autonomous.sandbox.opensandbox.provider import (
    _GIT_SYNTHESIS,
    OpenSandboxProvider,
    OpenSandboxTurnSpec,
)
from app.modules.workspace.autonomous.sandbox.provider import CapabilityUnsupported, SandboxError
from app.modules.workspace.autonomous.sandbox.types import (
    SandboxCapability,
    SandboxEventKind,
    SandboxSpec,
    SandboxStatus,
)

pytestmark = [pytest.mark.regression, pytest.mark.issue(2023)]

_DIGEST = "ghcr.io/open-ace/agent@sha256:" + "a" * 64

_FULL = {
    "egress_enforced": True,
    "egress_mode_dns_nft": True,
    "metadata_cidr_blocked": True,
    "execd_token_required": True,
    "secure_access_required": True,
    "nonroot_enforced": True,
    "readonly_rootfs": True,
    "seccomp_runtime_default": True,
    "dedicated_service_account": True,
    "pod_pids_limit": 512,
    "ephemeral_storage_enforced": True,
}


# The same deployment with the OTHER egress mechanism: the cluster NetworkPolicy
# alone (the only one gVisor can run). `egress_allow_hosts` must be empty
# alongside it, and exactly one of the two mechanisms may be attested.
_CNI = {k: v for k, v in _FULL.items() if k not in ("egress_enforced", "egress_mode_dns_nft")}
_CNI["egress_cni_default_deny"] = True


def _cfg(attestations=None, *, pool=None, tenant_tiers=None, endpoints=None, **overrides):
    endpoint = {
        "base_url": "http://osb.open-ace.svc.cluster.local:8080/v1",
        "api_key_env": "OSB_KEY",
        "execd_token_env": "OSB_EXECD_TOKEN",
        "runtime_class": "kata-qemu",
        "default_image": _DIGEST,
        "egress_allow_hosts": ["api.anthropic.com"],
        "attestations": _FULL if attestations is None else attestations,
    }
    if pool is not None:
        endpoint["pool"] = pool
    raw = {
        "installation_id": "openace-test",
        "default_tier": "gvisor",
        "endpoints": endpoints or {"gvisor": endpoint},
        "image_allowlist": [_DIGEST],
        "resource_defaults": {"cpu": "2", "memory": "4Gi"},
        "sandbox_ttl_seconds": 3600,
        "tenant_tiers": tenant_tiers or {},
    }
    raw.update(overrides)
    return parse_backend_config(raw)


def _cni_cfg(**overrides):
    """A CNI-enforced endpoint, still on a Kata runtime_class.

    The runtime class stays Kata so the kernel probe is not also under test
    here: a gVisor tier is what makes this mechanism mandatory, not what makes
    it work, and a Kata operator who declines to run the sidecar lands in
    exactly this configuration.
    """
    endpoint = {
        "base_url": "http://osb.open-ace.svc.cluster.local:8080/v1",
        "api_key_env": "OSB_KEY",
        "execd_token_env": "OSB_EXECD_TOKEN",
        "runtime_class": "kata-qemu",
        "default_image": _DIGEST,
        "egress_allow_hosts": [],
        "attestations": _CNI,
    }
    endpoint.update(overrides.pop("endpoint", {}))
    return _cfg(endpoints={"gvisor": endpoint}, **overrides)


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setenv("OSB_KEY", "k")
    monkeypatch.setenv("OSB_EXECD_TOKEN", "t")


def _spec(**overrides) -> SandboxSpec:
    base = {"task_id": "t-1", "project_path": "/workspace", "cli_tool": "claude-code"}
    base.update(overrides)
    return SandboxSpec(**base)


def _provider(api=None, cfg=None, **kw):
    api = api or FakeOpenSandboxApi()
    provider = OpenSandboxProvider(cfg or _cfg(), api_factory=lambda endpoint: api, **kw)
    return provider, api


# ── contract surface ──────────────────────────────────────────────────


def test_provider_satisfies_the_frozen_protocol_surface():
    provider, _ = _provider()
    for name in (
        "capabilities",
        "create",
        "upload_workspace",
        "exec",
        "stream",
        "pause",
        "resume",
        "stop",
        "collect_changes",
        "collect_execution_evidence",
        "destroy",
        "destroy_attribution",
        "inspect",
    ):
        assert callable(getattr(provider, name)), name


def test_create_mints_a_handle_with_provider_name_and_generation():
    provider, _ = _provider()
    handle = provider.create(_spec())
    assert handle.sandbox_id
    assert handle.provider_name == "opensandbox"
    assert handle.generation == 1


def test_create_rejects_a_capability_the_endpoint_does_not_attest():
    # A CNI tier enforces egress, but not per-sandbox — so it does not declare
    # NETWORK_EGRESS_POLICY, and a spec demanding it must fail closed.
    cfg = _cni_cfg()
    provider, _ = _provider(cfg=cfg)
    spec = _spec(required_capabilities=frozenset({SandboxCapability.NETWORK_EGRESS_POLICY}))
    with pytest.raises((CapabilityUnsupported, SandboxError)):
        provider.create(spec)


def test_stale_generation_handle_is_refused():
    provider, _ = _provider()
    handle = provider.create(_spec())
    stale = handle.__class__(
        sandbox_id=handle.sandbox_id,
        generation=handle.generation + 1,
        provider_name=handle.provider_name,
        spec=handle.spec,
    )
    with pytest.raises(SandboxError):
        provider.exec(stale, command=["ls"], env=None, exec_policy=None)


# ── status overlay (spec §6.4) ────────────────────────────────────────


def test_inspect_returns_created_until_first_exec():
    # Upstream create returns Running; the #2022 contract test expects CREATED.
    provider, _ = _provider()
    handle = provider.create(_spec())
    assert provider.inspect(handle) == SandboxStatus.CREATED


def test_inspect_reports_running_after_first_exec():
    provider, _ = _provider()
    handle = provider.create(_spec())
    provider.exec(handle, command=["ls"], env=None, exec_policy=None)
    assert provider.inspect(handle) == SandboxStatus.RUNNING


def test_stop_transitions_to_stopped_even_though_upstream_stays_running():
    provider, _ = _provider()
    handle = provider.create(_spec())
    exec_handle = provider.exec(handle, command=["ls"], env=None, exec_policy=None)
    provider.stop(exec_handle)
    assert provider.inspect(handle) == SandboxStatus.STOPPED


def test_stopped_overlay_clears_on_next_exec():
    # Otherwise cancelling one turn would report STOPPED for the rest of the
    # sandbox's life, including while a later exec is running.
    provider, _ = _provider()
    handle = provider.create(_spec())
    exec_handle = provider.exec(handle, command=["ls"], env=None, exec_policy=None)
    provider.stop(exec_handle)
    provider.exec(handle, command=["ls"], env=None, exec_policy=None)
    assert provider.inspect(handle) == SandboxStatus.RUNNING


def test_destroy_marks_destroyed_and_is_idempotent():
    provider, api = _provider()
    handle = provider.create(_spec())
    provider.destroy(handle)
    provider.destroy(handle)
    assert provider.inspect(handle) == SandboxStatus.DESTROYED
    assert handle.sandbox_id in api.deleted


def test_destroy_that_never_confirms_does_not_report_destroyed():
    # Reporting DESTROYED for a sandbox still consuming quota and network is the
    # same lie as marking a workflow row destroyed without destroying anything.
    # A sandbox still Running after the poll budget is the genuinely
    # unconfirmed case.
    provider, api = _provider()
    handle = provider.create(_spec())
    api.stall_delete = True
    provider.destroy(handle)
    assert provider.inspect(handle) != SandboxStatus.DESTROYED


def test_destroy_confirms_on_stopping_rather_than_waiting_for_terminated():
    # Kubernetes pod deletion runs to terminationGracePeriodSeconds (30 by
    # default). Requiring `Terminated` would make every successful teardown
    # report unconfirmed and ask the reconciler to retry, training operators to
    # ignore the one signal that matters.
    events: list[tuple[str, dict]] = []
    provider, api = _provider(event_sink=lambda name, data: events.append((name, data)))
    handle = provider.create(_spec())
    api.linger_in_stopping = True
    provider.destroy(handle)
    assert provider.inspect(handle) == SandboxStatus.DESTROYED
    assert not any(name == "sandbox_destroy_unconfirmed" for name, _ in events)


# ── stream (spec §6.3) ────────────────────────────────────────────────


def test_stream_emits_the_canonical_lifecycle_sequence():
    provider, _ = _provider()
    handle = provider.create(_spec())
    exec_handle = provider.exec(handle, command=["ls"], env=None, exec_policy=None)
    events = list(provider.stream(exec_handle))
    kinds = [e.kind for e in events]
    assert kinds[0] == SandboxEventKind.PROCESS_STARTED
    assert SandboxEventKind.COMMAND_STARTED in kinds
    assert SandboxEventKind.STDOUT_CHUNK in kinds
    assert kinds[-1] == SandboxEventKind.PROCESS_EXITED
    assert all(e.sandbox_id for e in events)


def test_sse_error_with_numeric_evalue_is_a_normal_nonzero_exit():
    # Upstream emits `error` and NO execution_complete for any non-zero exit.
    # Mapping it to SANDBOX_ERROR would report every failing pytest run as an
    # infrastructure failure.
    provider, _ = _provider(FakeOpenSandboxApi(scripted_exit_code=1))
    handle = provider.create(_spec())
    exec_handle = provider.exec(handle, command=["pytest"], env=None, exec_policy=None)
    events = list(provider.stream(exec_handle))
    terminal = [e for e in events if e.kind == SandboxEventKind.COMMAND_COMPLETED]
    assert terminal and terminal[0].exit_code == 1
    assert not any(e.kind == SandboxEventKind.SANDBOX_ERROR for e in events)


def test_stream_never_reports_completed_for_a_non_completion():
    provider, _ = _provider(FakeOpenSandboxApi(scripted_timeout=True))
    handle = provider.create(_spec())
    exec_handle = provider.exec(handle, command=["sleep"], env=None, exec_policy=None)
    kinds = [e.kind for e in provider.stream(exec_handle)]
    assert SandboxEventKind.COMMAND_TIMED_OUT in kinds
    assert SandboxEventKind.COMMAND_COMPLETED not in kinds


# ── evidence (spec §7.3) ──────────────────────────────────────────────


def test_execution_evidence_matches_provider_contract():
    provider, _ = _provider()
    handle = provider.create(_spec())
    exec_handle = provider.exec(handle, command=["ls"], env=None, exec_policy=None)
    list(provider.stream(exec_handle))
    row = provider.collect_execution_evidence(handle)[0]
    assert row.sandbox_id == handle.sandbox_id
    assert row.sandbox_generation == handle.generation
    assert row.cwd == handle.spec.project_path
    assert row.exit_code == 0
    assert row.terminal_reason == TerminalReason.COMPLETED.value


def test_resource_limits_return_structured_terminal_reason():
    # A child killed under the cgroup surfaces as 128+n through execd...
    provider, _ = _provider(FakeOpenSandboxApi(scripted_exit_code=137))
    handle = provider.create(_spec())
    exec_handle = provider.exec(handle, command=["forkbomb"], env=None, exec_policy=None)
    list(provider.stream(exec_handle))
    row = provider.collect_execution_evidence(handle)[0]
    assert row.terminal_reason == TerminalReason.SIGNAL.value
    assert row.signal == 9

    # ...and a timeout is never COMPLETED.
    provider, _ = _provider(FakeOpenSandboxApi(scripted_timeout=True))
    handle = provider.create(_spec())
    exec_handle = provider.exec(handle, command=["sleep"], env=None, exec_policy=None)
    list(provider.stream(exec_handle))
    row = provider.collect_execution_evidence(handle)[0]
    assert row.terminal_reason == TerminalReason.TIMEOUT.value


def test_pod_level_oom_maps_to_signal_via_the_sandbox_state():
    # A memory-limit breach OOM-kills the container INCLUDING execd, so there is
    # no tidy exit 137 to read — /command/status is simply unreachable.
    api = FakeOpenSandboxApi()
    provider, _ = _provider(api)
    handle = provider.create(_spec())
    provider.exec(handle, command=["hog"], env=None, exec_policy=None)
    api.set_pod_oom(True)
    row = provider.collect_execution_evidence(handle)[0]
    assert row.terminal_reason == TerminalReason.SIGNAL.value


# ── probes (spec §5.3) ────────────────────────────────────────────────


def test_runtime_probe_rejects_a_gvisor_kernel_on_a_kata_endpoint():
    cfg = _cfg(
        endpoints={
            "kata": {
                "base_url": "http://osb-kata.open-ace.svc.cluster.local:8080/v1",
                "api_key_env": "OSB_KEY",
                "execd_token_env": "OSB_EXECD_TOKEN",
                "runtime_class": "kata-qemu",
                "default_image": _DIGEST,
                "egress_allow_hosts": ["api.anthropic.com"],
                "attestations": _FULL,
            }
        },
        default_tier="kata",
    )
    api = FakeOpenSandboxApi(runtime_kernel="Linux version 4.4.0 #1 SMP gVisor")
    provider, _ = _provider(api, cfg=cfg)
    with pytest.raises(SandboxError, match="runtime"):
        provider.create(_spec())


def test_runtime_probe_accepts_a_matching_kernel():
    # The shared fixture declares a Kata tier — the only kind that can run these
    # workloads, since upstream rejects every networkPolicy under gVisor — so a
    # kernel that does NOT identify as gVisor is the match here.
    provider, _ = _provider(FakeOpenSandboxApi(runtime_kernel="Linux 5.15.0 #1 SMP"))
    assert provider.create(_spec())


def test_egress_probe_fails_closed_when_the_sidecar_reports_dns_only():
    # dns-only enforcement does not stop a connection made to a bare IP, which
    # is exactly what the metadata endpoint is reached by.
    api = FakeOpenSandboxApi(egress_enforcement_mode="dns")
    provider, _ = _provider(api)
    with pytest.raises(SandboxError, match="egress"):
        provider.create(_spec())


def test_egress_probe_fails_closed_on_an_allow_default():
    provider, _ = _provider(FakeOpenSandboxApi(egress_default_action="allow"))
    with pytest.raises(SandboxError, match="egress"):
        provider.create(_spec())


# ── the CNI enforcement mechanism (the only one gVisor can run) ───────


def test_a_cni_tier_creates_when_the_cluster_policy_is_in_force():
    api = FakeOpenSandboxApi()  # default: both destinations BLOCKED
    provider, _ = _provider(api, cfg=_cni_cfg())
    assert provider.create(_spec())


@pytest.mark.parametrize("destination", ["METADATA", "CLUSTER"])
def test_a_cni_tier_fails_closed_when_the_sandbox_can_still_reach_out(destination):
    """The attestation's whole content is that this policy is applied.

    On a gVisor tier it is the ONLY egress control, so an unapplied manifest — or
    one whose podSelector matches nothing, which this repository shipped — must
    stop the run rather than be taken on the operator's word.
    """
    api = FakeOpenSandboxApi()
    api.cluster_egress = {**api.cluster_egress, destination: "REACHABLE"}
    provider, _ = _provider(api, cfg=_cni_cfg())
    with pytest.raises(SandboxError, match="cluster NetworkPolicy is not restricting"):
        provider.create(_spec())


def test_a_cni_tier_fails_closed_when_the_probe_call_itself_errors():
    """execd refusing the command is not a pass either.

    The one path that reaches this is an execd error, which the fake models by
    raising from run_command — the same shape a 500 or a dropped connection
    takes at the client boundary.
    """

    class Exploding(FakeOpenSandboxApi):
        def run_command(self, sandbox_id, body):
            if "OPENACE_METADATA=" in str(body.get("command") or ""):
                raise SandboxError("execd said no")
            return super().run_command(sandbox_id, body)

    provider, _ = _provider(Exploding(), cfg=_cni_cfg())
    with pytest.raises(SandboxError, match="could not run the cluster-egress check"):
        provider.create(_spec())


def test_a_cni_tier_fails_closed_when_dns_leaves_the_check_undecided():
    """A blocked packet and a failed lookup must not read the same.

    Folding UNRESOLVED into BLOCKED would grant the attestation for free on any
    cluster with a broken resolver — a false PASS on the one check a gVisor
    tier's egress rests on.
    """
    api = FakeOpenSandboxApi()
    api.cluster_egress = {"METADATA": "BLOCKED", "CLUSTER": "UNRESOLVED"}
    provider, _ = _provider(api, cfg=_cni_cfg())
    with pytest.raises(SandboxError, match="did not resolve"):
        provider.create(_spec())


def test_a_cni_tier_fails_closed_when_the_probe_cannot_run():
    """No python3 in the image means no verdict, and no verdict is a refusal.

    Unlike the kernel probe's optional sources, there is no second opinion here:
    passing anyway would grant the tier unverified egress enforcement.
    """
    api = FakeOpenSandboxApi()
    api.cluster_egress = None
    provider, _ = _provider(api, cfg=_cni_cfg())
    with pytest.raises(SandboxError, match="no verdict"):
        provider.create(_spec())


def test_a_sidecar_tier_also_probes_the_cluster_policy_it_attests():
    """`metadata_cidr_blocked` is what closes the sidecar's bare-IP gap.

    It names the same manifest, so it gets the same check — otherwise the flag
    that upgrades a Kata tier to NETWORK_EGRESS_POLICY is the one flag nothing
    verifies.
    """
    api = FakeOpenSandboxApi()
    api.cluster_egress = {**api.cluster_egress, "METADATA": "REACHABLE"}
    provider, _ = _provider(api)  # the shared fixture is a sidecar tier
    with pytest.raises(SandboxError, match="cluster NetworkPolicy is not restricting"):
        provider.create(_spec())


# ── isolation (acceptance criterion 5) ────────────────────────────────


def test_sandbox_cannot_read_host_or_peer_workspace(tmp_path):
    provider, api = _provider()
    handle = provider.create(_spec())
    body = api.created_bodies[0]
    # secureAccess is what stops a peer reaching this sandbox's endpoint.
    assert body["secureAccess"] is True
    assert not any(v.get("host") for v in body.get("volumes", []))
    # A peer without the token is refused.
    with pytest.raises(SandboxError):
        api.peer_request(handle.sandbox_id, token=None)
    assert api.peer_request(handle.sandbox_id, token=f"tok-{handle.sandbox_id}")["ok"]


def test_the_per_sandbox_credential_actually_reaches_execd():
    """The other half of peer isolation: WE must send the token.

    The check above only proves the server would refuse an unauthenticated
    peer. It asserts against the fake directly and never touches the client —
    so it stayed green while the client's endpoint-header allowlist silently
    stripped the real `OpenSandbox-Secure-Access` header, which would have left
    every one of OUR execd calls uncredentialed under gateway mode.
    """
    from app.modules.workspace.autonomous.sandbox.opensandbox.client import (
        SECURE_ACCESS_HEADER,
        HttpOpenSandboxApi,
    )

    class _Session:
        def __init__(self):
            self.calls = []

        def request(self, method, url, **kw):
            self.calls.append(kw)

            class _R:
                status_code = 200
                text = "{}"
                content = b""
                headers: dict = {}

                @staticmethod
                def json():
                    return {
                        "endpoint": "http://osb.open-ace.svc.cluster.local/p",
                        "headers": {SECURE_ACCESS_HEADER: "per-sandbox-abc"},
                    }

            return _R()

    cfg = _cfg()
    session = _Session()
    real = HttpOpenSandboxApi(cfg.endpoints["gvisor"], session=session)
    assert real.execd_headers("sb-1").get(SECURE_ACCESS_HEADER) == "per-sandbox-abc"


def test_upload_workspace_sends_no_git_or_credential_files(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[remote]", encoding="utf-8")
    (tmp_path / ".git-credentials").write_text("tok", encoding="utf-8")
    (tmp_path / "main.py").write_text("print(1)", encoding="utf-8")
    provider, api = _provider()
    handle = provider.create(_spec(project_path=str(tmp_path)))
    provider.upload_workspace(handle, str(tmp_path))
    uploaded = set(api.uploaded[handle.sandbox_id])
    assert not any(".git" in path for path in uploaded)
    assert any(path.endswith("main.py") for path in uploaded)


def test_repo_synthesis_runs_after_upload_and_produces_a_commit(tmp_path):
    # An entrypoint-time git init would run against an empty /workspace, commit
    # nothing, and leave the snapshot as untracked files.
    (tmp_path / "main.py").write_text("print(1)", encoding="utf-8")
    provider, api = _provider()
    handle = provider.create(_spec(project_path=str(tmp_path)))
    provider.upload_workspace(handle, str(tmp_path))
    git_commands = [b["command"] for b in api.command_bodies if " init -q" in b["command"]]
    assert git_commands, "repo synthesis did not run"
    assert "user.email" in git_commands[0]  # self-contained identity
    assert api.uploaded[handle.sandbox_id]  # files landed first


def test_every_git_invocation_in_the_synthesis_carries_safe_directory():
    """Without this the synthesis dies `dubious ownership`, exit 128, every run.

    The shipped template pins the sandbox container to uid 1000 while
    /workspace is a root-owned emptyDir the entrypoint's chown — running as that
    same uid 1000 — cannot change. git then refuses to operate on the tree.
    Found on a live gVisor cluster; it affects BOTH tiers.

    Asserted per-invocation rather than once on the whole string: the original
    bug was that only the `commit` carried its own `-c` overrides, so a fix that
    covered `commit` alone would still fail at `add`.
    """
    invocations = [part.strip() for part in _GIT_SYNTHESIS.split("&&")]
    assert len(invocations) >= 3, invocations
    missing = [inv for inv in invocations if "safe.directory=/workspace" not in inv]
    assert not missing, f"git invocations without safe.directory: {missing}"


# ── warm pool (acceptance criterion 10) ───────────────────────────────


def test_warm_pool_refused_unless_egress_recycle_and_image_all_attested():
    cfg = _cfg(pool={"pool_ref": "agents", "egress_preapplied": True, "recycle_delete": False})
    provider, _ = _provider(cfg=cfg)
    with pytest.raises(SandboxError, match="pool"):
        provider.create(_spec(), use_pool=True)


def test_warm_pool_does_not_reuse_tenant_state():
    cfg = _cfg(
        pool={
            "pool_ref": "agents",
            "egress_preapplied": True,
            "recycle_delete": True,
            "image_digest": _DIGEST,
        }
    )
    provider, api = _provider(cfg=cfg)
    first = provider.create(_spec(task_id="t-1"), use_pool=True)
    provider.exec(first, command=["ls"], env=None, exec_policy=None)
    provider.destroy(first)
    second = provider.create(_spec(task_id="t-2"), use_pool=True)
    assert second.sandbox_id != first.sandbox_id
    # No evidence, no env and no workspace carries across.
    assert provider.collect_execution_evidence(second) == []
    assert api.created_bodies[-1]["metadata"]["openace.task_id"] == "t-2"


# ── audit ─────────────────────────────────────────────────────────────


def test_refusals_emit_an_audit_event_and_carry_a_reason_code():
    events: list[tuple[str, dict]] = []
    cfg = _cfg(attestations={**_FULL, "nonroot_enforced": False})
    provider, _ = _provider(cfg=cfg, event_sink=lambda name, data: events.append((name, data)))
    with pytest.raises(SandboxError) as exc:
        provider.create(_spec())
    assert getattr(exc.value, "reason_code", "")
    assert any(name == "sandbox_refused" for name, _ in events)


def test_lifecycle_calls_emit_audit_events():
    events: list[tuple[str, dict]] = []
    provider, _ = _provider(event_sink=lambda name, data: events.append((name, data)))
    handle = provider.create(_spec())
    provider.destroy(handle)
    names = [name for name, _ in events]
    assert "sandbox_created" in names
    assert "sandbox_destroyed" in names


# ── reconciliation ────────────────────────────────────────────────────


def test_destroy_attribution_works_without_a_live_handle():
    provider, api = _provider()
    handle = provider.create(_spec())
    fresh, _ = _provider(api)
    fresh.destroy_attribution(handle.sandbox_id, None)
    assert handle.sandbox_id in api.deleted


def test_destroy_attribution_never_raises():
    provider, api = _provider()
    provider.destroy_attribution("sb-unknown", None)
    # Locate-before-delete: an unknown attribution is a reporting no-op — a
    # blind DELETE whose 404 counts as success would be indistinguishable
    # from destroying the wrong server's sandbox.
    assert api.deleted == set()


def test_reconcile_orphans_destroys_only_unclaimed_sandboxes():
    provider, api = _provider()
    keep = provider.create(_spec(task_id="keep"))
    orphan = provider.create(_spec(task_id="orphan"))
    destroyed = provider.reconcile_orphans(live_sandbox_ids={keep.sandbox_id})
    assert destroyed == [orphan.sandbox_id]
    assert keep.sandbox_id not in api.deleted


def test_reconcile_orphans_filters_on_our_provider_metadata():
    provider, api = _provider()
    provider.create(_spec())
    provider.reconcile_orphans(live_sandbox_ids=set())
    assert api.list_filters and api.list_filters[-1]["openace.provider"] == "opensandbox"


# ── PTY branch discrimination ─────────────────────────────────────────


class _FakePtyConnection:
    def __init__(self):
        self.sent = []

    def send(self, data):
        self.sent.append(data)

    def recv(self, timeout=None):
        import json as _json

        return _json.dumps({"type": "exit", "exit_code": 0})

    def close(self):
        pass


def test_turn_spec_selects_the_pty_branch():
    provider, api = _provider(connect_factory=lambda url, headers: _FakePtyConnection())
    handle = provider.create(_spec())
    exec_handle = provider.exec(
        handle,
        command=["claude", "--input-format", "stream-json"],
        env={"HOME": "/workspace/home"},
        exec_policy=OpenSandboxTurnSpec(prompt="hi"),
    )
    assert api.pty_sessions
    assert provider.get_transport(exec_handle) is not None


def test_plain_command_uses_the_foreground_command_branch():
    provider, api = _provider()
    handle = provider.create(_spec())
    provider.exec(handle, command=["ls"], env=None, exec_policy=None)
    assert api.command_bodies
    assert api.command_bodies[-1]["background"] is False


def test_capabilities_reflect_the_resolved_endpoint():
    provider, _ = _provider()
    provider.create(_spec())
    caps = provider.capabilities()
    assert SandboxCapability.NAMESPACE_ISOLATION in caps
    assert SandboxCapability.NETWORK_EGRESS_POLICY in caps


def test_evidence_timestamps_are_datetimes_not_strings():
    # execd reports RFC3339 strings; CommandExecutionEvidence types these as
    # datetime, so handing the string through would put the wrong type in the
    # evidence row.
    from datetime import datetime

    provider, _ = _provider()
    handle = provider.create(_spec())
    exec_handle = provider.exec(handle, command=["ls"], env=None, exec_policy=None)
    list(provider.stream(exec_handle))
    row = provider.collect_execution_evidence(handle)[0]
    assert isinstance(row.started_at, datetime)
    assert isinstance(row.completed_at, datetime)


# ── acceptance criterion 8: the remaining two fail-closed classes ──────


def test_fork_bomb_produces_a_structured_signal_not_a_silent_pass():
    # The pids bound is the kubelet's podPidsLimit, which the tier must attest;
    # a process killed under it surfaces as 128+9 through execd. What matters
    # here is that it never reads back as a clean completion.
    provider, _ = _provider(FakeOpenSandboxApi(scripted_exit_code=137))
    handle = provider.create(_spec())
    exec_handle = provider.exec(handle, command=[":(){ :|:& };:"], env=None, exec_policy=None)
    list(provider.stream(exec_handle))
    row = provider.collect_execution_evidence(handle)[0]
    assert row.terminal_reason == TerminalReason.SIGNAL.value
    assert row.terminal_reason != TerminalReason.COMPLETED.value


def test_tier_without_an_attested_pids_limit_is_refused():
    # Without the attestation there is no fork-bomb bound at all, so the tier
    # must not run an agent rather than run one unprotected.
    cfg = _cfg(attestations={**_FULL, "pod_pids_limit": 0})
    provider, _ = _provider(cfg=cfg)
    with pytest.raises(SandboxError):
        provider.create(_spec())


def test_network_scan_target_is_not_reachable_through_the_generated_policy():
    # A scan works by connecting to hosts nobody allowlisted. The generated
    # policy is deny-default with only the operator's hosts allowed, and the
    # sidecar is verified to be enforcing in dns+nft mode before the sandbox is
    # used at all.
    provider, api = _provider()
    provider.create(_spec())
    policy = api.created_bodies[0]["networkPolicy"]
    assert policy["defaultAction"] == "deny"
    allowed = {rule["target"] for rule in policy["egress"]}
    for target in ("10.0.0.1", "169.254.169.254", "scanner.example.com", "*"):
        assert target not in allowed


def test_sandbox_is_refused_when_the_sidecar_is_not_enforcing():
    # The scan defence rests on the sidecar actually enforcing; an unverified
    # boolean would leave it resting on nothing.
    provider, _ = _provider(FakeOpenSandboxApi(egress_enforcement_mode="dns"))
    with pytest.raises(SandboxError):
        provider.create(_spec())


# ── the agent-turn path (previously untested end to end) ──────────────


class _ScriptedPtyConnection:
    """A PTY socket that emits scripted frames then an exit frame."""

    def __init__(self, frames=(), exit_code=0):
        import json as _json
        import struct as _struct

        self.sent: list = []
        self._frames = list(frames) + [_json.dumps({"type": "exit", "exit_code": exit_code})]
        self._struct = _struct

    def send(self, data):
        self.sent.append(data)

    def recv(self, timeout=None):
        if not self._frames:
            raise ConnectionError("closed")
        return self._frames.pop(0)

    def close(self):
        pass


def _turn(frames=(), exit_code=0, api=None):
    conn = _ScriptedPtyConnection(frames, exit_code)
    provider, api = _provider(api, connect_factory=lambda url, headers: conn)
    handle = provider.create(_spec())
    exec_handle = provider.exec(
        handle,
        command=["claude", "--input-format", "stream-json"],
        env={},
        exec_policy=OpenSandboxTurnSpec(prompt="hi", proxy_token="tok"),
    )
    return provider, api, handle, exec_handle


def test_stream_emits_the_canonical_sequence_for_an_agent_turn():
    # Every previous stream() test went through the /command branch. The PTY
    # branch raised TypeError because the transport is not iterable — the whole
    # agent path was untested.
    provider, _, _, exec_handle = _turn([b"\x01hello\n"])
    kinds = [e.kind for e in provider.stream(exec_handle)]
    assert kinds[0] == SandboxEventKind.PROCESS_STARTED
    assert SandboxEventKind.COMMAND_STARTED in kinds
    assert SandboxEventKind.STDOUT_CHUNK in kinds
    assert SandboxEventKind.COMMAND_COMPLETED in kinds
    assert kinds[-1] == SandboxEventKind.PROCESS_EXITED


def test_agent_turn_evidence_carries_the_exit_frames_code():
    # A PTY session is not a command, so execd 404s the local uuid and every
    # turn recorded MISSING_RESULT. The exit frame is the only source of truth.
    provider, _, handle, exec_handle = _turn(exit_code=0)
    list(provider.stream(exec_handle))
    row = provider.collect_execution_evidence(handle)[0]
    assert row.exit_code == 0
    assert row.terminal_reason == TerminalReason.COMPLETED.value


def test_agent_turn_nonzero_exit_is_recorded_as_a_failure_not_missing_result():
    provider, _, handle, exec_handle = _turn(exit_code=1)
    list(provider.stream(exec_handle))
    row = provider.collect_execution_evidence(handle)[0]
    assert row.exit_code == 1
    assert row.terminal_reason == TerminalReason.COMPLETED.value


def test_agent_turn_signal_exit_decodes_to_signal():
    provider, _, handle, exec_handle = _turn(exit_code=137)
    list(provider.stream(exec_handle))
    row = provider.collect_execution_evidence(handle)[0]
    assert (row.signal, row.terminal_reason) == (9, TerminalReason.SIGNAL.value)


def test_exec_runs_in_the_sandbox_workspace_not_the_host_path():
    # spec.project_path is the CONTROL PLANE's path. Inside the container the
    # tree is at /workspace, so a command started in the host path runs in a
    # directory that does not exist.
    provider, api = _provider()
    handle = provider.create(_spec(project_path="/srv/repos/open-ace"))
    provider.exec(handle, command=["ls"], env=None, exec_policy=None)
    assert api.command_bodies[-1]["cwd"] == "/workspace"


def test_evidence_cwd_is_where_the_command_actually_ran():
    provider, _ = _provider()
    handle = provider.create(_spec(project_path="/srv/repos/open-ace"))
    exec_handle = provider.exec(handle, command=["ls"], env=None, exec_policy=None)
    list(provider.stream(exec_handle))
    assert provider.collect_execution_evidence(handle)[0].cwd == "/workspace"


def test_entrypoint_creates_the_directories_build_env_points_at():
    provider, api = _provider()
    provider.create(_spec())
    body = api.created_bodies[0]
    entrypoint = " ".join(body["entrypoint"])
    assert "mkdir -p" in entrypoint
    # Every directory build_env points the agent at must be created, or on a
    # read-only rootfs with an empty /workspace none of them exist and pip, npm,
    # pre-commit and tempfile all fail.
    for key in ("TMPDIR", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
        assert body["env"][key] in entrypoint, key


def test_the_entrypoint_marks_the_workspace_safe_for_the_agents_own_git():
    """`-c safe.directory` on OUR git does nothing for the git the AGENT runs.

    Under the shipped template /workspace is a root-owned emptyDir and the agent
    is uid 1000, so every `git status` / `git diff` the agent runs trips the same
    ownership check that killed the repo synthesis — an autonomous coding agent
    that cannot run git is not much use. Confirmed locally with git's
    GIT_TEST_ASSUME_DIFFERENT_OWNER hook: `git status` kept failing with the
    synthesis's own `-c` flags in place, and passed only once a GLOBAL
    safe.directory was set.

    So this is a distinct mechanism from
    test_every_git_invocation_in_the_synthesis_carries_safe_directory, not a
    duplicate of it, and it needs its own guard.
    """
    provider, api = _provider()
    provider.create(_spec())
    entrypoint = " ".join(api.created_bodies[0]["entrypoint"])
    assert "git config --global --add safe.directory /workspace" in entrypoint


def test_agent_home_is_outside_the_workspace():
    # With HOME under /workspace, the repo synthesis `git add -A` stages the
    # agent's entire home tree — pip wheels, npm, pre-commit envs — into the
    # initial commit.
    provider, api = _provider()
    provider.create(_spec())
    assert not api.created_bodies[0]["env"]["HOME"].startswith("/workspace")


def test_non_oom_failed_sandbox_is_a_crash_not_missing_result():
    # MISSING_RESULT maps to NOT_RUN, a materially more forgiving verdict than
    # FAILED for a sandbox that crashed.
    api = FakeOpenSandboxApi()
    provider, _ = _provider(api)
    handle = provider.create(_spec())
    provider.exec(handle, command=["x"], env=None, exec_policy=None)
    api.set_failed("Error", "scheduling failed")
    row = provider.collect_execution_evidence(handle)[0]
    assert row.terminal_reason == TerminalReason.CRASH.value


def test_evidence_carries_a_stderr_digest():
    provider, _ = _provider(FakeOpenSandboxApi(stderr_text="boom\n"))
    handle = provider.create(_spec())
    exec_handle = provider.exec(handle, command=["x"], env=None, exec_policy=None)
    list(provider.stream(exec_handle))
    row = provider.collect_execution_evidence(handle)[0]
    assert row.stderr_digest


def test_upload_workspace_fails_closed_when_git_synthesis_fails(tmp_path):
    # A silent failure here leaves the agent running against a tree that was
    # never prepared — the failure repo synthesis exists to prevent.
    (tmp_path / "main.py").write_text("x", encoding="utf-8")
    provider, _ = _provider(FakeOpenSandboxApi(scripted_exit_code=1))
    handle = provider.create(_spec(project_path=str(tmp_path)))
    with pytest.raises(SandboxError, match="setup"):
        provider.upload_workspace(handle, str(tmp_path))


def test_upload_workspace_refuses_a_non_path_snapshot():
    provider, _ = _provider()
    handle = provider.create(_spec())
    with pytest.raises(SandboxError):
        provider.upload_workspace(handle, {"not": "a path"})


# ── ChangeSet round trip (previously unreachable) ─────────────────────


def test_collect_changes_produces_and_returns_a_manifest(tmp_path):
    provider, api = _provider()
    handle = provider.create(_spec())
    api.set_manifest({"src/main.py": b"print(1)"})
    entries, deleted = provider.collect_changes(handle)
    assert [e.path for e in entries] == ["src/main.py"]
    assert deleted == []
    # The producer itself is uploaded outside the workspace so it cannot appear
    # in the manifest it generates.
    assert any(p.startswith("/tmp/") for p in api.uploaded[handle.sandbox_id])


def test_collect_changes_raises_rather_than_reporting_no_changes():
    # Returning None here would be indistinguishable from "the agent changed
    # nothing", silently discarding a run's work product.
    provider, api = _provider()
    handle = provider.create(_spec())
    api.set_manifest(None)
    with pytest.raises(SandboxError, match="manifest"):
        provider.collect_changes(handle)


def test_apply_changes_writes_the_agent_edits_into_the_trusted_worktree(tmp_path):
    provider, api = _provider()
    handle = provider.create(_spec())
    api.set_manifest({"src/main.py": b"print(1)"})
    provider.apply_changes(handle, str(tmp_path))
    assert (tmp_path / "src" / "main.py").read_bytes() == b"print(1)"


def test_a_handle_from_a_previous_generation_is_refused_after_a_bump():
    # The reconciler bumps sandbox_generation on every restart sweep. Comparing
    # against a literal 1 accepted exactly the stale handles this check exists
    # to reject, and refused the legitimate ones.
    provider, api = _provider()
    old = provider.create(_spec())
    assert old.generation == 1

    bumped, _ = _provider(api, generation=2)
    fresh = bumped.create(_spec())
    assert fresh.generation == 2
    with pytest.raises(SandboxError, match="stale"):
        bumped.exec(old, command=["ls"], env=None, exec_policy=None)
    bumped.exec(fresh, command=["ls"], env=None, exec_policy=None)


def test_generation_is_recorded_in_the_sandbox_metadata():
    provider, api = _provider(generation=3)
    provider.create(_spec())
    assert api.created_bodies[0]["metadata"]["openace.generation"] == "3"


def test_a_file_deleted_in_the_sandbox_is_removed_from_the_worktree(tmp_path):
    # A removal is a normal outcome of a refactor. The producer cannot report
    # one, so without derivation the stale file survives in the trusted worktree
    # and the commit that follows looks correct.
    (tmp_path / "kept.py").write_text("old", encoding="utf-8")
    (tmp_path / "gone.py").write_text("old", encoding="utf-8")
    provider, api = _provider()
    handle = provider.create(_spec())
    api.set_manifest({"kept.py": b"new"})
    provider.apply_changes(handle, str(tmp_path))
    assert (tmp_path / "kept.py").read_bytes() == b"new"
    assert not (tmp_path / "gone.py").exists()


# ── the agent's command and credentials reach it correctly (§5.4) ─────


def test_the_host_resolved_cli_path_is_not_execd_inside_the_container():
    """`shutil.which` runs on the CONTROL PLANE; that path means nothing in the image.

    The runner resolves the agent CLI on the host and passes an absolute path.
    Exec'ing it verbatim required the image to carry the binary at a
    byte-identical absolute path — undocumented, unvalidated, and false for any
    image not built to mirror the control plane's filesystem.
    """
    provider, api = _provider(connect_factory=lambda url, headers: _FakePtyConnection())
    handle = provider.create(_spec())
    provider.exec(
        handle,
        command=["/opt/homebrew/bin/claude", "--input-format", "stream-json"],
        env={},
        exec_policy=OpenSandboxTurnSpec(prompt="hi"),
    )
    started = api.pty_sessions[next(iter(api.pty_sessions))]["command"]
    assert "/opt/homebrew/bin/claude" not in started
    assert "exec 'claude'" in started or "exec claude" in started


def test_a_turn_is_refused_when_the_proxy_host_is_not_egress_allowed():
    """Wired, not merely defined.

    The check exists in policy.py, but a test that only calls it directly would
    stay green if the provider stopped calling it — and the symptom in
    production is an agent that hangs on every request with nothing naming the
    network policy as the cause.
    """
    provider, _ = _provider(connect_factory=lambda url, headers: _FakePtyConnection())
    handle = provider.create(_spec())
    with pytest.raises(SandboxError, match="egress_allow_hosts"):
        provider.exec(
            handle,
            command=["claude"],
            env={"OPENACE_PROXY_URL": "https://proxy.not-allowlisted.example/api"},
            exec_policy=OpenSandboxTurnSpec(prompt="hi"),
        )


def test_a_setup_command_with_no_events_fails_closed():
    """Zero events is not an observed success.

    Real execd always emits at least an `init` frame, so an empty stream means
    the protocol was not read — which is exactly what happened while the SSE
    parser understood only `data:` lines. Every setup command "succeeded"
    without evidence and the agent ran against a tree that may never have been
    prepared. Defense in depth against a future protocol drift.
    """
    provider, api = _provider()
    handle = provider.create(_spec())
    api.run_command = lambda sandbox_id, body: iter(())  # type: ignore[assignment]
    with pytest.raises(SandboxError, match="no events"):
        provider.upload_workspace(handle, None)


def test_the_runtime_probe_is_one_directional_for_kata():
    """Pins a KNOWN limitation so it cannot be mistaken for verification.

    gVisor's kernel identifies itself, so a gVisor claim is positively checked.
    Kata boots a real kernel in a VM whose /proc/version looks like plain runc's,
    so a Kata claim is only confirmed not-gVisor. That asymmetry sets the actual
    strength of NAMESPACE_ISOLATION on a Kata tier, and is documented in
    docs/sandbox-backends.md §5 rather than overstated.
    """
    from app.modules.workspace.autonomous.sandbox.types import SandboxCapability

    # A generic kernel — what plain runc would report — passes a Kata tier.
    provider, _ = _provider(FakeOpenSandboxApi(runtime_kernel="Linux version 5.15.0-91-generic"))
    provider.create(_spec())
    assert SandboxCapability.NAMESPACE_ISOLATION in provider.capabilities()

    # The one direction that IS positive: a gVisor kernel under a Kata tier is
    # still refused.
    provider2, _ = _provider(FakeOpenSandboxApi(runtime_kernel="Linux 4.4.0 gVisor"))
    with pytest.raises(SandboxError, match="runtime"):
        provider2.create(_spec())


def test_the_kernel_probe_reads_more_than_proc_version():
    """Current runsc puts no gVisor marker in /proc/version.

    On release-20260112 it reports a plain `Linux version 4.4.0 #1 SMP ...`, so
    probing that file alone REFUSED a correctly-deployed gVisor tier with
    runtime_class_mismatch — verified on a live gVisor cluster. The markers live
    in /proc/cmdline (BOOT_IMAGE=/vmlinuz-4.4.0-gvisor) and dmesg
    ("Starting gVisor..."), so the probe reads all three.
    """
    commands: list[str] = []

    class _Api(FakeOpenSandboxApi):
        def run_command(self, sandbox_id, body):
            commands.append(str(body.get("command") or ""))
            return iter(({"type": "init", "text": "c"}, {"type": "execution_complete"}))

    api = _Api()
    # Force the exec path rather than the proc_version shortcut.
    api.proc_version = None  # type: ignore[assignment]
    provider, _ = _provider(api)
    provider._probe_kernel("sb-1")
    joined = " ".join(commands)
    assert "/proc/version" in joined
    assert "/proc/cmdline" in joined, "the marker current runsc DOES emit is not read"
    assert "dmesg" in joined


def test_an_unavailable_probe_source_does_not_refuse_the_run():
    """dmesg is often unavailable to an unprivileged process."""
    from app.modules.workspace.autonomous.sandbox.provider import SandboxError as _SE

    class _Api(FakeOpenSandboxApi):
        def run_command(self, sandbox_id, body):
            if "dmesg" in str(body.get("command") or ""):
                raise _SE("dmesg: operation not permitted")
            return iter(({"type": "stdout", "text": "Linux version 4.4.0 gVisor"},))

    api = _Api()
    api.proc_version = None  # type: ignore[assignment]
    provider, _ = _provider(api)
    assert "gVisor" in provider._probe_kernel("sb-1")


def test_setup_commands_omit_credentials_when_execd_is_the_exec_identity():
    """This is the call that died on the live cluster.

    _run_foreground runs the repo synthesis. With the shipped template pinning
    the sandbox container to uid 1000, execd runs as 1000 and cannot switch to
    1000, so a body carrying uid/gid fails
    `fork/exec /usr/bin/bash: operation not permitted` — no run got past
    upload_workspace. Covered separately from _exec_command because it builds
    its body inline rather than through build_command_request.
    """
    cfg = _cfg(attestations={**_FULL, "execd_runs_as_exec_identity": True})
    provider, api = _provider(cfg=cfg)
    handle = provider.create(_spec())
    api.command_bodies.clear()
    provider.upload_workspace(handle, None)
    setup = api.command_bodies[-1]
    assert "uid" not in setup and "gid" not in setup, setup


def test_setup_commands_send_credentials_when_execd_runs_as_root():
    cfg = _cfg(attestations=dict(_FULL))
    provider, api = _provider(cfg=cfg)
    handle = provider.create(_spec())
    api.command_bodies.clear()
    provider.upload_workspace(handle, None)
    setup = api.command_bodies[-1]
    assert setup["uid"] == 1000 and setup["gid"] == 1000


def test_foreground_exec_omits_credentials_when_execd_is_the_exec_identity():
    """The /command branch needs the same rule as the setup commands.

    Covered separately because _exec_command routes through
    build_command_request while _run_foreground builds its body inline — the
    two are easy to fix one at a time and leave the other broken.
    """
    cfg = _cfg(attestations={**_FULL, "execd_runs_as_exec_identity": True})
    provider, api = _provider(cfg=cfg)
    handle = provider.create(_spec())
    api.command_bodies.clear()
    provider.exec(handle, command=["ls"], env=None, exec_policy=None)
    body = api.command_bodies[-1]
    assert "uid" not in body and "gid" not in body, body


def test_foreground_exec_sends_credentials_when_execd_runs_as_root():
    provider, api = _provider()
    handle = provider.create(_spec())
    api.command_bodies.clear()
    provider.exec(handle, command=["ls"], env=None, exec_policy=None)
    body = api.command_bodies[-1]
    assert body["uid"] == 1000 and body["gid"] == 1000
