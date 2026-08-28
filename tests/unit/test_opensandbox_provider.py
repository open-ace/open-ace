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


def _cfg(attestations=None, *, pool=None, tenant_tiers=None, endpoints=None, **overrides):
    endpoint = {
        "base_url": "http://osb.open-ace.svc.cluster.local:8080/v1",
        "api_key_env": "OSB_KEY",
        "execd_token_env": "OSB_EXECD_TOKEN",
        "runtime_class": "gvisor",
        "default_image": _DIGEST,
        "egress_allow_hosts": ["api.anthropic.com"],
        "attestations": _FULL if attestations is None else attestations,
    }
    if pool is not None:
        endpoint["pool"] = pool
    raw = {
        "default_tier": "gvisor",
        "endpoints": endpoints or {"gvisor": endpoint},
        "image_allowlist": [_DIGEST],
        "resource_defaults": {"cpu": "2", "memory": "4Gi"},
        "sandbox_ttl_seconds": 3600,
        "tenant_tiers": tenant_tiers or {},
    }
    raw.update(overrides)
    return parse_backend_config(raw)


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
    cfg = _cfg(attestations={**_FULL, "egress_enforced": False})
    provider, _ = _provider(cfg=cfg)
    with pytest.raises((CapabilityUnsupported, SandboxError)):
        provider.create(_spec())


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
    provider, api = _provider()
    handle = provider.create(_spec())
    api.stall_delete = True
    provider.destroy(handle)
    assert provider.inspect(handle) != SandboxStatus.DESTROYED


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
    provider, _ = _provider(FakeOpenSandboxApi(runtime_kernel="Linux 4.4.0 gVisor"))
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
    git_commands = [b["command"] for b in api.command_bodies if "git init" in b["command"]]
    assert git_commands, "repo synthesis did not run"
    assert "user.email" in git_commands[0]  # self-contained identity
    assert api.uploaded[handle.sandbox_id]  # files landed first


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
    provider, _ = _provider()
    provider.destroy_attribution("sb-unknown", None)


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
