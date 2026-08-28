"""The gate that refuses to downgrade a production-required tenant (#2023)."""

from __future__ import annotations

import pytest

from app.modules.workspace.autonomous.sandbox.isolation_tier import (
    requires_production_isolation,
    select_provider,
)
from app.modules.workspace.autonomous.sandbox.legacy_posix import LegacyPosixProvider
from app.modules.workspace.autonomous.sandbox.opensandbox.config import parse_backend_config
from app.modules.workspace.autonomous.sandbox.opensandbox.fake_server import FakeOpenSandboxApi
from app.modules.workspace.autonomous.sandbox.opensandbox.provider import OpenSandboxProvider
from app.modules.workspace.autonomous.sandbox.provider import SandboxError

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
}


def _cfg(**overrides):
    raw = {
        "default_tier": "gvisor",
        "endpoints": {
            "gvisor": {
                "base_url": "http://osb.open-ace.svc.cluster.local:8080/v1",
                "api_key_env": "OSB_KEY",
                "execd_token_env": "OSB_EXECD_TOKEN",
                "runtime_class": "gvisor",
                "default_image": _DIGEST,
                "egress_allow_hosts": ["api.anthropic.com"],
                "attestations": _FULL,
            }
        },
        "image_allowlist": [_DIGEST],
        "production_required_tenants": ["42"],
    }
    raw.update(overrides)
    return parse_backend_config(raw)


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setenv("OSB_KEY", "k")
    monkeypatch.setenv("OSB_EXECD_TOKEN", "t")


def _select(**kw):
    defaults = {
        "tenant": None,
        "project_path": None,
        "config": _cfg(),
        "fallback": LegacyPosixProvider(),
        "api_factory": lambda endpoint: FakeOpenSandboxApi(),
    }
    defaults.update(kw)
    return select_provider(**defaults)


def test_required_production_policy_cannot_fallback_to_legacy():
    # A required tenant resolves to OpenSandbox...
    assert isinstance(_select(tenant="42"), OpenSandboxProvider)

    # ...and when the tier it needs has no endpoint, it RAISES rather than
    # quietly running somewhere weaker.
    cfg = _cfg(tenant_tiers={"42": "kata"})
    with pytest.raises(SandboxError):
        _select(tenant="42", config=cfg)


def test_int_tenant_id_is_recognised_as_required():
    # The runner holds an integer tenant_id.
    assert requires_production_isolation(42, _cfg()) is True
    assert requires_production_isolation(7, _cfg()) is False


def test_unrequired_tenant_still_gets_opensandbox_when_configured():
    assert isinstance(_select(tenant="7"), OpenSandboxProvider)


def test_gate_returns_the_injected_provider_when_no_config_is_present():
    # AgentRunner accepts an injected provider and several suites rely on it;
    # constructing a fresh LegacyPosixProvider() here would silently discard it.
    injected = LegacyPosixProvider()
    assert _select(tenant="7", config=None, fallback=injected) is injected


def test_misconfigured_tier_does_not_silently_downgrade_a_non_required_tenant():
    # A tier with no endpoint is an operator mistake, and running the whole
    # deployment on Legacy because of it — with no audit event, since the
    # provider is never constructed — is exactly the quiet downgrade this gate
    # exists to prevent. It must be loud for everyone, not only for tenants on
    # the required list.
    cfg = _cfg(tenant_tiers={"7": "kata"}, production_required_tenants=[])
    with pytest.raises(SandboxError):
        _select(tenant="7", config=cfg, fallback=LegacyPosixProvider())


def test_a_missing_api_key_is_loud_rather_than_a_silent_legacy_downgrade(monkeypatch):
    monkeypatch.delenv("OSB_KEY", raising=False)
    cfg = _cfg(production_required_tenants=[])
    provider = _select(tenant="7", config=cfg)
    # Construction succeeds; the key is read on first use and raises there.
    with pytest.raises(SandboxError):
        provider._endpoint.api_key()


def test_no_tenant_and_no_config_returns_the_fallback():
    injected = LegacyPosixProvider()
    assert _select(config=None, fallback=injected) is injected


# ── rollout: the per-tenant/project switch between Legacy and sandbox ──


def test_tenant_outside_the_rollout_allowlist_stays_on_legacy():
    # This is the gradual-rollout knob. Without it the only switch was whether
    # the config file exists, which is all-or-nothing per deployment.
    injected = LegacyPosixProvider()
    cfg = _cfg(
        rollout={"mode": "allowlist", "tenants": ["42"]},
        production_required_tenants=["42"],
    )
    assert _select(tenant="7", config=cfg, fallback=injected) is injected


def test_tenant_inside_the_rollout_allowlist_gets_opensandbox():
    cfg = _cfg(
        rollout={"mode": "allowlist", "tenants": ["42"]},
        production_required_tenants=["42"],
    )
    assert isinstance(_select(tenant="42", config=cfg), OpenSandboxProvider)


def test_project_allowlist_routes_a_single_repository_to_the_sandbox():
    cfg = _cfg(
        rollout={"mode": "allowlist", "projects": ["/srv/repos/pilot"]},
        production_required_tenants=[],
    )
    assert isinstance(
        _select(tenant="7", project_path="/srv/repos/pilot", config=cfg), OpenSandboxProvider
    )
    injected = LegacyPosixProvider()
    assert (
        _select(tenant="7", project_path="/srv/repos/other", config=cfg, fallback=injected)
        is injected
    )


def test_default_all_mode_keeps_every_tenant_on_the_sandbox():
    assert isinstance(_select(tenant="7"), OpenSandboxProvider)
