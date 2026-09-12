"""Issue #3378: SandboxedWebuiLauncher core (create body, restore, renew, destroy).

Fake-provider unit tests for the launcher halves of D2/D5: the create body the
launcher assembles via policy.build_create_request (bootstrap-prefixed
entrypoint, webui env, timeout alignment, webui metadata keys, no host volume),
the restore sequencing (marker only after a confirmed extract; empty-marker
first launch; timeout degrade leaves restore_confirmed False), the renew clamp,
idempotent destroy, and the boot-probe contract upgrade (gVisor positive vs
Kata negative-only).
"""

from __future__ import annotations

import json
import tarfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.modules.workspace.autonomous.sandbox.opensandbox.config import parse_backend_config
from app.modules.workspace.autonomous.sandbox.opensandbox.fake_server import FakeOpenSandboxApi
from app.services import webui_sandbox as ws
from app.services import workspace_isolation_contract as wic

pytestmark = [pytest.mark.issue(3378)]

_AGENT_IMAGE = "ghcr.io/open-ace/agent@sha256:" + "a" * 64
_WEBUI_IMAGE = "ghcr.io/open-ace/webui@sha256:" + "b" * 64

_FULL_ATTESTATIONS = {
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


def _backend(runtime_class: str = "kata-qemu"):
    return parse_backend_config(
        {
            "installation_id": "openace-test",
            "default_tier": "kata",
            "endpoints": {
                "kata": {
                    "base_url": "http://osb.open-ace.svc.cluster.local:8080/v1",
                    "api_key_env": "OSB_KEY",
                    "execd_token_env": "OSB_EXECD_TOKEN",
                    "runtime_class": runtime_class,
                    "default_image": _AGENT_IMAGE,
                    "webui_image": _WEBUI_IMAGE,
                    "egress_allow_hosts": ["api.anthropic.com"],
                    "attestations": _FULL_ATTESTATIONS,
                }
            },
            "image_allowlist": [_AGENT_IMAGE, _WEBUI_IMAGE],
            "sandbox_ttl_seconds": 3600,
        }
    )


class _FakeProxyService:
    """Stands in for APIKeyProxyService: TTL + token mint with a payload exp."""

    def __init__(self, ttl_minutes: int = 1440):
        self.ttl_minutes = ttl_minutes
        self.minted: list[dict] = []

    def effective_proxy_token_ttl_minutes(self, session_type: str) -> int:
        assert session_type == "webui"
        return self.ttl_minutes

    def generate_proxy_token(self, **kwargs) -> str:
        import base64
        import json

        self.minted.append(kwargs)
        expires_at = datetime.now() + timedelta(minutes=self.ttl_minutes)
        payload = {"exp": expires_at.isoformat(), "session_type": "webui"}
        return base64.b64encode(json.dumps(payload).encode()).decode() + ".sig"

    def get_tool_model_pool(self, **kwargs):
        return {"models": [{"envKey": "BAILIAN_CODING_PLAN_API_KEY"}]}


@pytest.fixture(autouse=True)
def _reset_contract_memo():
    wic._reset_sandbox_runtime_verification()
    yield
    wic._reset_sandbox_runtime_verification()


@pytest.fixture(autouse=True)
def _isolated_state_root(tmp_path, monkeypatch):
    """T-E: launch/restore now persists control-plane records under the
    state root; keep every launcher test off the real config directory."""
    monkeypatch.setenv(ws.STATE_ROOT_ENV, str(tmp_path / "webui-agent-state"))


def _launcher(
    fake: FakeOpenSandboxApi,
    *,
    ttl_minutes: int = 1440,
    backend=None,
    restore_timeout: float = 5.0,
) -> tuple[ws.SandboxedWebuiLauncher, _FakeProxyService]:
    proxy_service = _FakeProxyService(ttl_minutes)
    launcher = ws.SandboxedWebuiLauncher(
        backend_config=backend or _backend(),
        api_factory=lambda endpoint: fake,
        proxy_service_factory=lambda: proxy_service,
        restore_timeout_seconds=restore_timeout,
        poll_interval_seconds=0.01,
    )
    return launcher, proxy_service


def _launch(launcher, user_id=7, snapshot=None):
    return launcher.launch(
        user_id=user_id,
        callback_url="http://openace.open-ace.svc.cluster.local:8080",
        snapshot=snapshot,
    )


# ── create body ───────────────────────────────────────────────────────


def test_create_body_carries_bootstrap_entrypoint_and_webui_metadata():
    fake = FakeOpenSandboxApi()
    launcher, _svc = _launcher(fake)
    _launch(launcher)
    body = fake.created_bodies[0]

    entrypoint = body["entrypoint"]
    assert entrypoint[:2] == ["/bin/sh", "-c"]
    script = entrypoint[2]
    # Bootstrap preserved verbatim (D2), then restore gate, then webui exec.
    assert script.startswith("mkdir -p /home/agent/tmp /home/agent/.cache")
    assert "git config --global --add safe.directory /workspace || true; " in script
    assert ".openace-restore-done" in script
    assert "exec qwen-code-webui" in script
    assert "exec tail -f /dev/null" not in script

    meta = body["metadata"]
    assert meta[ws.WEBUI_METADATA_KIND] == "webui"
    assert meta[ws.WEBUI_METADATA_GENERATION] == ws.current_process_generation()
    assert meta[ws.WEBUI_METADATA_OWNER] == "7"
    assert meta["openace.generation"] == "1"  # workflow-generation key intact

    assert body["image"] == {"uri": _WEBUI_IMAGE}
    # No host volume ever (D2/refusal 3): the body carries none at all.
    assert "volumes" not in body


def test_create_body_env_has_llm_proxy_keys_and_no_github_credentials():
    fake = FakeOpenSandboxApi()
    launcher, _svc = _launcher(fake)
    result = _launch(launcher)
    env = fake.created_bodies[0]["env"]
    assert env["OPENAI_BASE_URL"] == (
        "http://openace.open-ace.svc.cluster.local:8080/api/workspace/llm-proxy/v1"
    )
    assert env["OPENAI_API_KEY"] == result.proxy_token
    assert env["OPENACE_PROXY_TOKEN"] == result.proxy_token
    assert env["OPENACE_PROXY_URL"] == (
        "http://openace.open-ace.svc.cluster.local:8080/api/workspace/llm-proxy"
    )
    # Dynamic model envKeys ride along (parity with the local path).
    assert env["BAILIAN_CODING_PLAN_API_KEY"] == result.proxy_token
    # _ENV_NEVER: no GitHub credential names ever reach the pod.
    for banned in ("GITHUB_TOKEN", "GH_TOKEN", "GH_CONFIG_DIR", "GH_ENTERPRISE_TOKEN"):
        assert banned not in env
    # Base env from build_create_request survives the merge.
    assert env["HOME"] == "/home/agent"
    assert env["PATH"]


def test_create_body_timeout_is_min_of_pod_and_proxy_token_ttl(monkeypatch):
    fake = FakeOpenSandboxApi()
    # 240m proxy TTL < 24h webui TTL → pod timeout clamps to 240m.
    launcher, _svc = _launcher(fake, ttl_minutes=240)
    monkeypatch.setattr("app.auth.decorators.WEBUI_TOKEN_TTL_SECONDS", 86400)
    _launch(launcher)
    assert fake.created_bodies[0]["timeout"] == 240 * 60


def test_create_body_timeout_bounded_by_webui_token_ttl(monkeypatch):
    fake = FakeOpenSandboxApi()
    # Proxy token outlives the webui TTL → the webui TTL wins.
    launcher, _svc = _launcher(fake, ttl_minutes=4320)
    monkeypatch.setattr("app.auth.decorators.WEBUI_TOKEN_TTL_SECONDS", 86400)
    _launch(launcher)
    assert fake.created_bodies[0]["timeout"] == 86400


def test_token_secret_is_shlex_quoted_and_returned():
    import shlex

    fake = FakeOpenSandboxApi()
    launcher, _svc = _launcher(fake)
    result = _launch(launcher)
    assert result.token_secret
    script = fake.created_bodies[0]["entrypoint"][2]
    # The secret travels as one quoted argv word next to --token-secret.
    assert f"--token-secret {shlex.quote(result.token_secret)}" in script


def test_launch_fails_closed_without_webui_image():
    fake = FakeOpenSandboxApi()
    backend = parse_backend_config(
        {
            "installation_id": "openace-test",
            "default_tier": "kata",
            "endpoints": {
                "kata": {
                    "base_url": "http://osb:8080/v1",
                    "api_key_env": "OSB_KEY",
                    "execd_token_env": "OSB_EXECD_TOKEN",
                    "runtime_class": "kata-qemu",
                    "default_image": _AGENT_IMAGE,
                    "attestations": _FULL_ATTESTATIONS,
                    "egress_allow_hosts": ["api.anthropic.com"],
                }
            },
            "image_allowlist": [_AGENT_IMAGE],
            "sandbox_ttl_seconds": 3600,
        }
    )
    launcher, _svc = _launcher(fake, backend=backend)
    with pytest.raises(ws.SandboxWebuiError) as excinfo:
        _launch(launcher)
    assert excinfo.value.reason_code == "webui_image_missing"
    assert fake.created_bodies == []


# ── restore sequencing (D2/N3) ────────────────────────────────────────


def _state_tar() -> bytes:
    import io

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        info = tarfile.TarInfo("chats/session-1.jsonl")
        data = b"[]"
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def test_restore_uploads_extracts_then_touches_marker_in_order():
    fake = FakeOpenSandboxApi()
    launcher, _svc = _launcher(fake)
    snapshot = _state_tar()
    result = _launch(launcher, snapshot=snapshot)

    assert result.restore_confirmed is True
    assert fake.uploaded[result.sandbox_id][ws.WEBUI_STATE_TAR_PATH] == snapshot
    commands = [b["command"] for b in fake.command_bodies]
    extract = next(c for c in commands if "tar -xf" in c)
    touch = next(c for c in commands if c.startswith("touch /workspace/.openace-restore-done"))
    # mkdir -p precedes tar (FEAS-M4c); the marker lands only after extraction.
    assert extract.index("mkdir -p") < extract.index("tar -xf")
    assert commands.index(extract) < commands.index(touch)
    # All restore commands run background:true (the D2 alternate path); the
    # probe commands interleaved from _run_boot_probes are not restore ones.
    for body in fake.command_bodies:
        if "tar -xf" in body["command"] or body["command"].startswith("touch "):
            assert body["background"] is True


def test_first_launch_without_snapshot_uploads_empty_marker_tar():
    fake = FakeOpenSandboxApi()
    launcher, _svc = _launcher(fake)
    result = _launch(launcher, snapshot=None)

    assert result.restore_confirmed is True
    uploaded = fake.uploaded[result.sandbox_id][ws.WEBUI_STATE_TAR_PATH]
    # A VALID empty tar (extraction succeeds, extracts nothing) — not 0 bytes.
    assert uploaded == ws.empty_state_tar()
    assert len(uploaded) >= 1024
    with tarfile.open(fileobj=__import__("io").BytesIO(uploaded)):
        pass


def test_restore_timeout_degrades_and_leaves_restore_unconfirmed():
    fake = FakeOpenSandboxApi(scripted_timeout=True)
    launcher, _svc = _launcher(fake, restore_timeout=0.05)
    result = _launch(launcher, snapshot=_state_tar())

    assert result.restore_confirmed is False
    # The pod is left running (the entrypoint degrades on its own counter).
    assert result.sandbox_id in fake.sandboxes
    # No touch command was issued after the unconfirmed extract.
    commands = [b["command"] for b in fake.command_bodies]
    assert not any(c.startswith("touch /workspace/.openace-restore-done") for c in commands)


def test_failed_extract_never_touches_the_marker():
    fake = FakeOpenSandboxApi(scripted_exit_code=2)
    launcher, _svc = _launcher(fake)
    result = _launch(launcher, snapshot=_state_tar())

    assert result.restore_confirmed is False
    commands = [b["command"] for b in fake.command_bodies]
    assert not any(c.startswith("touch") for c in commands)


# ── boot probes → contract memo (D3) ──────────────────────────────────


def test_kata_probes_do_not_upgrade_kernel_dimension():
    fake = FakeOpenSandboxApi()  # default kernel string is not gVisor
    launcher, _svc = _launcher(fake)  # kata-qemu tier
    _launch(launcher)
    verified, kernel_enforced = wic.sandbox_runtime_verification("kata")
    assert verified is True
    assert kernel_enforced is False


def _gvisor_backend():
    """A gVisor/CNI tier: gVisor cannot run the egress sidecar (config refuses
    the pair), so this exercises the cluster-egress probe direction instead."""
    return parse_backend_config(
        {
            "installation_id": "openace-test",
            "default_tier": "kata",
            "endpoints": {
                "kata": {
                    "base_url": "http://osb:8080/v1",
                    "api_key_env": "OSB_KEY",
                    "execd_token_env": "OSB_EXECD_TOKEN",
                    "runtime_class": "gvisor",
                    "default_image": _AGENT_IMAGE,
                    "webui_image": _WEBUI_IMAGE,
                    "egress_allow_hosts": [],
                    "attestations": {
                        **{
                            k: v
                            for k, v in _FULL_ATTESTATIONS.items()
                            if k not in ("egress_enforced", "egress_mode_dns_nft")
                        },
                        "egress_cni_default_deny": True,
                    },
                }
            },
            "image_allowlist": [_AGENT_IMAGE, _WEBUI_IMAGE],
            "sandbox_ttl_seconds": 3600,
        }
    )


def test_gvisor_probes_upgrade_kernel_and_egress():
    fake = FakeOpenSandboxApi(runtime_kernel="Linux version 4.4.0 (gvisor) #1 SMP")
    launcher, _svc = _launcher(fake, backend=_gvisor_backend())
    _launch(launcher)
    verified, kernel_enforced = wic.sandbox_runtime_verification("kata")
    assert verified is True
    assert kernel_enforced is True


def test_probe_failure_destroys_pod_and_raises():
    # gVisor-declared tier but the kernel identifies as plain Linux.
    fake = FakeOpenSandboxApi(runtime_kernel="Linux version 5.15.0 #1 SMP")
    launcher, _svc = _launcher(fake, backend=_gvisor_backend())
    with pytest.raises(ws.SandboxWebuiError) as excinfo:
        _launch(launcher)
    assert excinfo.value.reason_code == "runtime_class_mismatch"
    assert fake.deleted, "an unverifiable webui pod must not survive"
    assert wic.sandbox_runtime_verification("kata") == (False, False)


def test_contract_snapshot_upgrades_after_gvisor_probe(monkeypatch):
    """The memo drives build_workspace_isolation_snapshot (enforced/reasons)."""
    import app.modules.workspace.autonomous.sandbox.opensandbox.config as sbcfg

    class _Endpoint:
        tier = "kata"
        webui_image = _WEBUI_IMAGE
        api_key_env = "OSB_KEY"
        egress_allow_hosts = ("openace.open-ace.svc.cluster.local",)

        class attestations:
            egress_enforced = True

    class _BackendCfg:
        default_tier = "kata"
        endpoints = {"kata": _Endpoint()}
        image_allowlist = frozenset({_WEBUI_IMAGE, _AGENT_IMAGE})

    monkeypatch.setattr(sbcfg, "load_backend_config", lambda explicit=None: _BackendCfg())
    monkeypatch.setenv("OSB_KEY", "unit-test-tier-key")

    class _Cfg:
        enabled = True
        multi_user_mode = True
        sandbox_tier = ""
        webui_callback_url = "http://openace.open-ace.svc.cluster.local:8080"
        required_isolation_level = ""

    class _StubManager:
        config = _Cfg()
        per_user_launch_readiness = lambda: None  # noqa: E731

    wic._reset_sandbox_runtime_verification()
    # Static (no probe yet): kernel/egress unsupported with sandbox_runtime_unverified.
    snap = wic.build_workspace_isolation_snapshot(_StubManager())
    assert snap.isolation_level == wic.ISOLATION_LEVEL_SANDBOXED
    assert [r.code for r in snap.reasons] == ["sandbox_runtime_unverified"]

    wic.register_sandbox_runtime_verified(tier="kata", kernel_enforced=True)
    snap = wic.build_workspace_isolation_snapshot(_StubManager())
    assert wic.DIMENSION_KERNEL in snap.enforced
    assert wic.DIMENSION_NETWORK_EGRESS in snap.enforced
    assert snap.reasons == ()

    wic._reset_sandbox_runtime_verification()
    wic.register_sandbox_runtime_verified(tier="kata", kernel_enforced=False)
    snap = wic.build_workspace_isolation_snapshot(_StubManager())
    assert wic.DIMENSION_KERNEL in snap.unsupported
    assert wic.DIMENSION_NETWORK_EGRESS in snap.enforced
    assert [r.code for r in snap.reasons] == ["sandbox_runtime_kata_negative_only"]


def _two_tier_backend():
    """gVisor (CNI egress) + Kata (sidecar egress) endpoints side by side."""
    return parse_backend_config(
        {
            "installation_id": "openace-test",
            "default_tier": "gvisor",
            "endpoints": {
                "gvisor": {
                    "base_url": "http://osb:8080/v1",
                    "api_key_env": "OSB_KEY",
                    "execd_token_env": "OSB_EXECD_TOKEN",
                    "runtime_class": "gvisor",
                    "default_image": _AGENT_IMAGE,
                    "webui_image": _WEBUI_IMAGE,
                    "egress_allow_hosts": [],
                    "attestations": {
                        **{
                            k: v
                            for k, v in _FULL_ATTESTATIONS.items()
                            if k not in ("egress_enforced", "egress_mode_dns_nft")
                        },
                        "egress_cni_default_deny": True,
                    },
                },
                "kata": {
                    "base_url": "http://osb:8080/v1",
                    "api_key_env": "OSB_KEY",
                    "execd_token_env": "OSB_EXECD_TOKEN",
                    "runtime_class": "kata-qemu",
                    "default_image": _AGENT_IMAGE,
                    "webui_image": _WEBUI_IMAGE,
                    "egress_allow_hosts": ["api.anthropic.com"],
                    "attestations": _FULL_ATTESTATIONS,
                },
            },
            "image_allowlist": [_AGENT_IMAGE, _WEBUI_IMAGE],
            "sandbox_ttl_seconds": 3600,
        }
    )


def test_runtime_memo_is_per_tier_kata_launch_does_not_downgrade_gvisor(monkeypatch):
    """Q1: the boot-probe memo is keyed by tier. A gVisor pod's positive kernel
    upgrade must survive a LATER Kata launch on the sibling tier — the old
    process-global memo let the last probe win, silently un-verifying the
    gVisor tier's kernel dimension for every snapshot built after it."""
    backend = _two_tier_backend()
    fakes = {
        "gvisor": FakeOpenSandboxApi(runtime_kernel="Linux version 4.4.0 (gvisor) #1 SMP"),
        "kata": FakeOpenSandboxApi(),  # plain Linux guest kernel
    }
    proxy_service = _FakeProxyService()

    def _launcher_for(tier: str):
        return ws.SandboxedWebuiLauncher(
            backend_config=backend,
            tier=tier,
            api_factory=lambda endpoint: fakes[endpoint.tier],
            proxy_service_factory=lambda: proxy_service,
            restore_timeout_seconds=5.0,
            poll_interval_seconds=0.01,
        )

    gvisor_launcher = _launcher_for("gvisor")
    gvisor_launcher.launch(
        user_id=7,
        callback_url="http://openace.open-ace.svc.cluster.local:8080",
        snapshot=None,
    )
    assert wic.sandbox_runtime_verification("gvisor") == (True, True)
    assert wic.sandbox_runtime_verification("kata") == (False, False)

    # The Kata pod starts afterwards — its negative-only probes must register
    # on the kata key ONLY, never touch the gVisor tier's memo entry.
    kata_launcher = _launcher_for("kata")
    kata_launcher.launch(
        user_id=7,
        callback_url="http://openace.open-ace.svc.cluster.local:8080",
        snapshot=None,
    )
    assert wic.sandbox_runtime_verification("gvisor") == (True, True)
    assert wic.sandbox_runtime_verification("kata") == (True, False)

    # And the contract snapshot consults its OWN tier's entry (review Q1): a
    # gvisor-configured deployment still reports kernel enforced, while the
    # kata tier stays kernel-unverified (negative-only direction).
    import app.modules.workspace.autonomous.sandbox.opensandbox.config as sbcfg

    class _Endpoint:
        tier = "unused"
        webui_image = _WEBUI_IMAGE
        api_key_env = "OSB_KEY"
        egress_allow_hosts = ("openace.open-ace.svc.cluster.local",)

        class attestations:
            egress_enforced = True

    class _TwoTierCfg:
        default_tier = "gvisor"
        endpoints = {"gvisor": _Endpoint(), "kata": _Endpoint()}
        image_allowlist = frozenset({_WEBUI_IMAGE, _AGENT_IMAGE})

    monkeypatch.setattr(sbcfg, "load_backend_config", lambda explicit=None: _TwoTierCfg())
    monkeypatch.setenv("OSB_KEY", "unit-test-tier-key")

    class _Cfg:
        enabled = True
        multi_user_mode = True
        webui_callback_url = "http://openace.open-ace.svc.cluster.local:8080"
        required_isolation_level = ""

        def __init__(self, sandbox_tier: str):
            self.sandbox_tier = sandbox_tier

    class _StubManager:
        def __init__(self, sandbox_tier: str):
            self.config = _Cfg(sandbox_tier)

        per_user_launch_readiness = lambda: None  # noqa: E731

    snap_gvisor = wic.build_workspace_isolation_snapshot(_StubManager("gvisor"))
    assert wic.DIMENSION_KERNEL in snap_gvisor.enforced
    assert wic.DIMENSION_NETWORK_EGRESS in snap_gvisor.enforced
    assert snap_gvisor.reasons == ()

    snap_kata = wic.build_workspace_isolation_snapshot(_StubManager("kata"))
    assert wic.DIMENSION_KERNEL in snap_kata.unsupported
    assert [r.code for r in snap_kata.reasons] == ["sandbox_runtime_kata_negative_only"]


# ── renew clamp (D5) ──────────────────────────────────────────────────


def test_renew_clamps_to_min_of_24h_and_proxy_token_expiry(monkeypatch):
    fake = FakeOpenSandboxApi()
    monkeypatch.setattr("app.auth.decorators.WEBUI_TOKEN_TTL_SECONDS", 86400)
    launcher, _svc = _launcher(fake, ttl_minutes=1440)  # token lives 24h
    result = _launch(launcher)

    before = datetime.now(timezone.utc)
    expires_at = launcher.renew_expiration(result.sandbox_id, proxy_token=result.proxy_token)
    after = datetime.now(timezone.utc)
    assert fake.renewed == [(result.sandbox_id, expires_at)]
    target = datetime.fromisoformat(expires_at)
    # T-F: the renew target is UTC-aware (+00:00 in the ISO string) — a naive
    # local 'now' would compare apples to oranges on non-UTC hosts.
    assert target.tzinfo is not None
    assert expires_at.endswith("+00:00")
    # Clamp = min(now+24h, token expiry). Token was minted moments ago with a
    # 24h TTL, so now+24h is the smaller side (mint skew included).
    assert before + timedelta(seconds=86400) - timedelta(seconds=5) <= target
    assert target <= after + timedelta(seconds=86400) + timedelta(seconds=5)
    assert target <= result.proxy_token_expires_at + timedelta(seconds=5)


def test_renew_clamps_to_shorter_proxy_token(monkeypatch):
    fake = FakeOpenSandboxApi()
    monkeypatch.setattr("app.auth.decorators.WEBUI_TOKEN_TTL_SECONDS", 86400)
    # Proxy token TTL 60m « 24h: the pod may not outlive its credentials.
    launcher, _svc = _launcher(fake, ttl_minutes=60)
    result = _launch(launcher)
    expires_at = launcher.renew_expiration(result.sandbox_id, proxy_token=result.proxy_token)
    target = datetime.fromisoformat(expires_at)
    assert target <= datetime.now(timezone.utc) + timedelta(minutes=61)
    assert target <= result.proxy_token_expires_at + timedelta(seconds=5)


# ── T-F: proxy-token expiry decode is fail-closed, not re-arming ──────


def test_proxy_token_expiry_fail_closed_on_empty_and_garbage():
    before = datetime.now(timezone.utc)
    empty = ws.proxy_token_expiry("")
    garbage = ws.proxy_token_expiry("not-a-token")
    after = datetime.now(timezone.utc)
    for parsed in (empty, garbage):
        assert parsed.tzinfo is not None
        assert before <= parsed <= after  # 'now', never now + fallback TTL


def test_renew_clamps_undecodable_token_to_now(monkeypatch):
    """T-F: an undecodable credential must not re-arm the pod's lifetime.
    The old fallback returned now+TTL on every 5-minute maintenance pass —
    a pod with a broken token lived forever."""
    fake = FakeOpenSandboxApi()
    monkeypatch.setattr("app.auth.decorators.WEBUI_TOKEN_TTL_SECONDS", 86400)
    launcher, _svc = _launcher(fake, ttl_minutes=1440)
    before = datetime.now(timezone.utc)
    expires_at = launcher.renew_expiration("sb-1", proxy_token="%%%%garbage")
    target = datetime.fromisoformat(expires_at)
    assert target.tzinfo is not None
    assert before <= target <= datetime.now(timezone.utc) + timedelta(seconds=5)
    assert fake.renewed == [("sb-1", expires_at)]


def test_proxy_token_expiry_normalizes_naive_payload_to_aware():
    """The mint writes naive local time; the expiry reader must return an
    aware datetime (naive-vs-aware min() in renew would raise TypeError)."""
    import base64

    naive_local = datetime.now() + timedelta(minutes=30)  # no tzinfo, local
    payload = base64.b64encode(json.dumps({"exp": naive_local.isoformat()}).encode())
    token = payload.decode() + ".sig"
    parsed = ws.proxy_token_expiry(token)
    assert parsed.tzinfo is not None
    # Same wall-clock instant, expressed aware.
    assert abs((parsed - naive_local.astimezone()).total_seconds()) < 1


# ── destroy (D5) ──────────────────────────────────────────────────────


def test_destroy_is_idempotent_and_404_tolerant():
    fake = FakeOpenSandboxApi()
    launcher, _svc = _launcher(fake)
    result = _launch(launcher)
    launcher.destroy(result.sandbox_id, 7, restore_confirmed=False, final_export=False)
    assert result.sandbox_id in fake.deleted
    # Second destroy: already gone — must not raise.
    launcher.destroy(result.sandbox_id, 7, restore_confirmed=False, final_export=False)


def test_token_minting_uses_webui_session_type():
    fake = FakeOpenSandboxApi()
    launcher, svc = _launcher(fake)
    _launch(launcher, user_id=7)
    mint = svc.minted[0]
    assert mint["session_type"] == "webui"
    assert mint["session_id"] == "webui:7"
    assert mint["extra_payload"] == {"scope": "local", "tool_name": "qwen-code"}
