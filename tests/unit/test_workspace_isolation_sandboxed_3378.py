"""Issue #3378: sandboxed isolation level — capability probe and gate.

Covers the zero-pod fail-closed probe (config/tier/image/proxy/TTL reason
codes), the snapshot restructure (sandboxed wins independently of platform
and multi_user_mode; kernel dimension vocabulary; runtime-unverified
honesty), and the evaluate_isolation_requirement sandboxed branch.
"""

import pytest

from app.services import workspace_isolation_contract as wic

pytestmark = [pytest.mark.issue(3378)]


_DIGEST = "sha256:" + "a" * 64
_VALID_IMAGE = f"ghcr.io/open-ace/webui@{_DIGEST}"
_DEFAULT_IMAGE = f"ghcr.io/open-ace/agent@{_DIGEST}"


class _Cfg:
    def __init__(self, **kwargs):
        self.enabled = True
        self.multi_user_mode = True
        self.sandbox_tier = ""
        self.webui_callback_url = ""
        self.required_isolation_level = ""
        for key, value in kwargs.items():
            setattr(self, key, value)


class _Attestations:
    egress_enforced = True


class _Endpoint:
    def __init__(self, webui_image=_VALID_IMAGE, egress_allow_hosts=None):
        self.tier = "kata"
        self.webui_image = webui_image
        self.egress_allow_hosts = (
            ("openace.open-ace.svc.cluster.local",)
            if egress_allow_hosts is None
            else tuple(egress_allow_hosts)
        )
        self.attestations = _Attestations()


class _BackendCfg:
    def __init__(self, endpoints=None, default_tier="kata"):
        self.default_tier = default_tier
        self.endpoints = endpoints or {"kata": _Endpoint()}
        self.image_allowlist = frozenset({_VALID_IMAGE, _DEFAULT_IMAGE})


@pytest.fixture
def no_sandbox_backend(monkeypatch):
    """Default deployments: no backend config, no probe side effects."""
    import app.modules.workspace.autonomous.sandbox.opensandbox.config as sbcfg

    monkeypatch.setattr(sbcfg, "load_backend_config", lambda explicit=None: None)


@pytest.fixture
def ready_backend(monkeypatch):
    """A fully-configured, healthy sandbox backend (probe passes)."""
    import app.modules.workspace.autonomous.sandbox.opensandbox.config as sbcfg

    monkeypatch.setattr(sbcfg, "load_backend_config", lambda explicit=None: _BackendCfg())
    monkeypatch.setenv("OPENACE_PROXY_TOKEN_TTL_WEBUI_MINUTES", "1440")


# --- probe reason codes ------------------------------------------------------


def test_probe_reports_unconfigured_when_no_backend(no_sandbox_backend):
    ok, tier, reason = wic._sandboxed_readiness(_Cfg())
    assert ok is False
    assert reason.code == "sandbox_backend_unconfigured"


def test_probe_reports_unconfigured_on_malformed_backend(monkeypatch):
    import app.modules.workspace.autonomous.sandbox.opensandbox.config as sbcfg

    def boom(explicit=None):
        raise sbcfg.SandboxConfigError("malformed")

    monkeypatch.setattr(sbcfg, "load_backend_config", boom)
    monkeypatch.setenv("OPENACE_PROXY_TOKEN_TTL_WEBUI_MINUTES", "1440")
    ok, tier, reason = wic._sandboxed_readiness(_Cfg())
    assert ok is False
    assert reason.code == "sandbox_backend_unconfigured"


def test_probe_reports_tier_missing(monkeypatch):
    import app.modules.workspace.autonomous.sandbox.opensandbox.config as sbcfg

    monkeypatch.setattr(
        sbcfg,
        "load_backend_config",
        lambda explicit=None: _BackendCfg(default_tier="sidecar", endpoints={}),
    )
    monkeypatch.setenv("OPENACE_PROXY_TOKEN_TTL_WEBUI_MINUTES", "1440")
    ok, tier, reason = wic._sandboxed_readiness(_Cfg())
    assert ok is False
    assert reason.code == "sandbox_tier_missing"


def test_probe_reports_webui_image_missing(monkeypatch):
    import app.modules.workspace.autonomous.sandbox.opensandbox.config as sbcfg

    backend = _BackendCfg(endpoints={"kata": _Endpoint(webui_image="")})
    monkeypatch.setattr(sbcfg, "load_backend_config", lambda explicit=None: backend)
    monkeypatch.setenv("OPENACE_PROXY_TOKEN_TTL_WEBUI_MINUTES", "1440")
    ok, tier, reason = wic._sandboxed_readiness(_Cfg())
    assert ok is False
    assert reason.code == "webui_image_missing"


def test_probe_reports_webui_image_not_pinned(monkeypatch):
    import app.modules.workspace.autonomous.sandbox.opensandbox.config as sbcfg

    backend = _BackendCfg(
        endpoints={"kata": _Endpoint(webui_image="ghcr.io/open-ace/webui:latest")}
    )
    monkeypatch.setattr(sbcfg, "load_backend_config", lambda explicit=None: backend)
    monkeypatch.setenv("OPENACE_PROXY_TOKEN_TTL_WEBUI_MINUTES", "1440")
    ok, tier, reason = wic._sandboxed_readiness(_Cfg())
    assert ok is False
    assert reason.code == "webui_image_not_pinned"


def test_probe_reports_webui_image_not_allowed(monkeypatch):
    import app.modules.workspace.autonomous.sandbox.opensandbox.config as sbcfg

    other = f"ghcr.io/other/webui@{_DIGEST}"
    backend = _BackendCfg(endpoints={"kata": _Endpoint(webui_image=other)})
    monkeypatch.setattr(sbcfg, "load_backend_config", lambda explicit=None: backend)
    monkeypatch.setenv("OPENACE_PROXY_TOKEN_TTL_WEBUI_MINUTES", "1440")
    ok, tier, reason = wic._sandboxed_readiness(_Cfg())
    assert ok is False
    assert reason.code == "webui_image_not_allowed"


def test_probe_reports_proxy_unreachable_without_callback_url(ready_backend):
    ok, tier, reason = wic._sandboxed_readiness(_Cfg())
    assert ok is False
    assert reason.code == "sandbox_proxy_unreachable"


def test_probe_reports_proxy_unreachable_when_egress_blocks_host(monkeypatch):
    import app.modules.workspace.autonomous.sandbox.opensandbox.config as sbcfg

    backend = _BackendCfg(endpoints={"kata": _Endpoint(egress_allow_hosts=("other.internal",))})
    monkeypatch.setattr(sbcfg, "load_backend_config", lambda explicit=None: backend)
    monkeypatch.setenv("OPENACE_PROXY_TOKEN_TTL_WEBUI_MINUTES", "1440")
    cfg = _Cfg(webui_callback_url="http://openace.open-ace.svc.cluster.local:8080")
    ok, tier, reason = wic._sandboxed_readiness(cfg)
    assert ok is False
    assert reason.code == "sandbox_proxy_unreachable"


def test_probe_reports_proxy_token_ttl_too_short(monkeypatch):
    import app.modules.workspace.autonomous.sandbox.opensandbox.config as sbcfg

    monkeypatch.setattr(sbcfg, "load_backend_config", lambda explicit=None: _BackendCfg())
    # 240m < 24h pod TTL: the pod would outlive its baked-in LLM credentials.
    monkeypatch.setenv("OPENACE_PROXY_TOKEN_TTL_WEBUI_MINUTES", "240")
    cfg = _Cfg(webui_callback_url="http://openace.open-ace.svc.cluster.local:8080")
    ok, tier, reason = wic._sandboxed_readiness(cfg)
    assert ok is False
    assert reason.code == "sandbox_proxy_token_ttl_too_short"


def test_probe_passes_with_full_config(monkeypatch):
    import app.modules.workspace.autonomous.sandbox.opensandbox.config as sbcfg

    monkeypatch.setattr(sbcfg, "load_backend_config", lambda explicit=None: _BackendCfg())
    monkeypatch.setenv("OPENACE_PROXY_TOKEN_TTL_WEBUI_MINUTES", "1440")
    cfg = _Cfg(webui_callback_url="http://openace.open-ace.svc.cluster.local:8080")
    ok, tier, reason = wic._sandboxed_readiness(cfg)
    assert ok is True
    assert tier == "kata"
    assert reason is None


def test_probe_honors_explicit_sandbox_tier(monkeypatch):
    import app.modules.workspace.autonomous.sandbox.opensandbox.config as sbcfg

    backend = _BackendCfg(
        endpoints={"kata": _Endpoint(), "gvisor": _Endpoint()},
        default_tier="kata",
    )
    monkeypatch.setattr(sbcfg, "load_backend_config", lambda explicit=None: backend)
    monkeypatch.setenv("OPENACE_PROXY_TOKEN_TTL_WEBUI_MINUTES", "1440")
    cfg = _Cfg(
        sandbox_tier="gvisor",
        webui_callback_url="http://openace.open-ace.svc.cluster.local:8080",
    )
    ok, tier, reason = wic._sandboxed_readiness(cfg)
    assert ok is True
    assert tier == "gvisor"


# --- snapshot structure ------------------------------------------------------


class _OsUserManager:
    """Manager stub with a healthy os_user launch probe (linux, multi-user)."""

    def __init__(self, multi_user_mode=True):
        self.config = _Cfg(multi_user_mode=multi_user_mode)
        self.per_user_launch_readiness = lambda: None


def test_snapshot_reports_sandboxed_when_probe_passes(monkeypatch):
    import app.modules.workspace.autonomous.sandbox.opensandbox.config as sbcfg

    monkeypatch.setattr(sbcfg, "load_backend_config", lambda explicit=None: _BackendCfg())
    monkeypatch.setenv("OPENACE_PROXY_TOKEN_TTL_WEBUI_MINUTES", "1440")
    manager = _OsUserManager()
    manager.config.webui_callback_url = "http://openace.open-ace.svc.cluster.local:8080"
    snap = wic.build_workspace_isolation_snapshot(manager)
    assert snap.supported is True
    assert snap.isolation_level == wic.ISOLATION_LEVEL_SANDBOXED
    assert snap.backend == f"{wic.BACKEND_OPENSANDBOX}:kata"
    assert snap.enforced == wic._SANDBOXED_ENFORCED
    assert wic.DIMENSION_KERNEL in snap.unsupported
    assert wic.DIMENSION_NETWORK_EGRESS in snap.unsupported
    assert [r.code for r in snap.reasons] == ["sandbox_runtime_unverified"]
    assert "entry_points" in snap.public_dict()


def test_snapshot_os_user_unchanged_without_backend(monkeypatch, no_sandbox_backend):
    monkeypatch.setattr(wic, "_current_platform", lambda: "linux")
    snap = wic.build_workspace_isolation_snapshot(_OsUserManager())
    assert snap.isolation_level == wic.ISOLATION_LEVEL_OS_USER
    assert snap.reasons == ()


def test_snapshot_appends_sandbox_reason_when_configured_but_failing(monkeypatch):
    import app.modules.workspace.autonomous.sandbox.opensandbox.config as sbcfg

    backend = _BackendCfg(endpoints={"kata": _Endpoint(webui_image="")})
    monkeypatch.setattr(sbcfg, "load_backend_config", lambda explicit=None: backend)
    monkeypatch.setenv("OPENACE_PROXY_TOKEN_TTL_WEBUI_MINUTES", "1440")
    monkeypatch.setattr(wic, "_current_platform", lambda: "linux")
    snap = wic.build_workspace_isolation_snapshot(_OsUserManager())
    # os_user still wins; the sandbox failure rides along for the gate.
    assert snap.isolation_level == wic.ISOLATION_LEVEL_OS_USER
    assert [r.code for r in snap.reasons] == ["webui_image_missing"]


def test_policy_revision_bumped():
    assert wic.POLICY_REVISION == "2026-09-12.1"


def test_kernel_dimension_in_vocabulary():
    assert wic.DIMENSION_KERNEL in wic.ALL_DIMENSIONS
    assert wic.DIMENSION_KERNEL in wic._OS_USER_UNSUPPORTED


# --- evaluate gate -----------------------------------------------------------


def _sandboxed_snapshot():
    return wic.IsolationCapabilitySnapshot(
        supported=True,
        backend="opensandbox:kata",
        isolation_level=wic.ISOLATION_LEVEL_SANDBOXED,
        enforced=wic._SANDBOXED_ENFORCED,
        unsupported=wic._SANDBOXED_UNSUPPORTED,
        reasons=(wic.IsolationReason("sandbox_runtime_unverified", "stub"),),
    )


def _os_user_snapshot_with(reason_code):
    return wic.IsolationCapabilitySnapshot(
        supported=True,
        backend=wic.BACKEND_PER_USER,
        isolation_level=wic.ISOLATION_LEVEL_OS_USER,
        enforced=wic._OS_USER_ENFORCED,
        unsupported=wic._OS_USER_UNSUPPORTED,
        reasons=(wic.IsolationReason(reason_code, "stubbed sandbox probe failure"),),
    )


def test_gate_accepts_sandboxed_without_system_account():
    verdict = wic.evaluate_isolation_requirement(
        "sandboxed",
        snapshot=_sandboxed_snapshot(),
        system_account=None,
        manager=None,
    )
    assert verdict is None


def test_gate_surfaces_probe_reason_for_sandboxed_request():
    snap = _os_user_snapshot_with("webui_image_missing")
    verdict = wic.evaluate_isolation_requirement(
        "sandboxed", snapshot=snap, system_account=None, manager=None
    )
    assert verdict is not None
    assert verdict.code == "webui_image_missing"


def test_gate_generic_rejection_without_probe_reason():
    snap = wic.IsolationCapabilitySnapshot(
        supported=True,
        backend=wic.BACKEND_PER_USER,
        isolation_level=wic.ISOLATION_LEVEL_OS_USER,
        enforced=wic._OS_USER_ENFORCED,
        unsupported=wic._OS_USER_UNSUPPORTED,
        reasons=(),
    )
    verdict = wic.evaluate_isolation_requirement(
        "sandboxed", snapshot=snap, system_account=None, manager=None
    )
    assert verdict is not None
    assert verdict.code == "isolation_level_unsupported"


def test_gate_os_user_requirement_satisfied_by_sandboxed_capability():
    """T-B (pin=floor): an os_user requirement on a sandboxed-capable
    deployment is satisfied by the pod form — the strongest VERIFIED form
    that meets the floor. The os_user identity chain (system_account,
    supports_per_user_launch) describes the LOCAL launch path, which is not
    the path taken; without this, an entrypoint-pinned `os_user` floor made
    every mapping-less user's default request a 400 on a deployment whose
    launches were pods anyway."""
    snap = _sandboxed_snapshot()
    verdict = wic.evaluate_isolation_requirement(
        "os_user", snapshot=snap, system_account=None, manager=None
    )
    assert verdict is None


def test_gate_os_user_chain_unchanged_without_sandbox_capability():
    snap = wic.IsolationCapabilitySnapshot(
        supported=True,
        backend=wic.BACKEND_PER_USER,
        isolation_level=wic.ISOLATION_LEVEL_OS_USER,
        enforced=wic._OS_USER_ENFORCED,
        unsupported=wic._OS_USER_UNSUPPORTED,
        reasons=(),
    )
    # An os_user request on an os_user deployment still walks the identity
    # chain (level is satisfied; system_account still required).
    verdict = wic.evaluate_isolation_requirement(
        "os_user", snapshot=snap, system_account=None, manager=None
    )
    assert verdict is not None
    assert verdict.code == "identity_mapping_missing"
    assert verdict.code == "identity_mapping_missing"
