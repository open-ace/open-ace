"""The gate that refuses to downgrade a production-required tenant (#2023)."""

from __future__ import annotations

from dataclasses import replace

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
        "installation_id": "openace-test",
        "default_tier": "gvisor",
        "endpoints": {
            "gvisor": {
                "base_url": "http://osb.open-ace.svc.cluster.local:8080/v1",
                "api_key_env": "OSB_KEY",
                "execd_token_env": "OSB_EXECD_TOKEN",
                "runtime_class": "kata-qemu",
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
    # quietly running somewhere weaker. parse_backend_config now rejects a
    # dangling tier outright, so build the broken config directly to keep
    # testing the GATE's behaviour rather than the parser's.
    cfg = replace(_cfg(), tenant_tiers={"42": "kata"})
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
    # Built directly: the parser refuses a dangling tier now, and this test is
    # about the gate refusing rather than downgrading.
    cfg = replace(_cfg(production_required_tenants=[]), tenant_tiers={"7": "kata"})
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


# ── the gate the protocol dispatch used to skip (spec §6.4) ───────────


def _runner_with_config(monkeypatch, tmp_path, required_tenants):
    """A runner whose backend config marks *required_tenants* no-downgrade."""
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

    config = _cfg(production_required_tenants=list(required_tenants))
    runner = AutonomousAgentRunner.__new__(AutonomousAgentRunner)
    runner._load_backend_config = lambda: config
    runner._resolve_tenant_id_strict = lambda user_id: 42
    runner._resolve_tenant_id = lambda user_id: 42
    return runner


class _NoStdinAdapter:
    @staticmethod
    def supports_stdin_input() -> bool:
        return False


class _StdinAdapter:
    @staticmethod
    def supports_stdin_input() -> bool:
        return True


def test_zcode_is_refused_for_a_production_required_tenant(monkeypatch, tmp_path):
    """ZCode returns from the protocol dispatch, above the provider gate.

    Acceptance criterion 12 says a required policy must not silently fall back.
    Before this check the run simply took the app-server path and spawned a
    local process, with nothing recorded to say the policy had been bypassed.
    """
    runner = _runner_with_config(monkeypatch, tmp_path, ["42"])
    with pytest.raises(SandboxError, match="production isolation"):
        runner._resolve_tenant_for_isolation(7, cli_tool="zcode", adapter=_StdinAdapter())


def test_a_single_shot_tool_is_refused_for_a_production_required_tenant(monkeypatch, tmp_path):
    runner = _runner_with_config(monkeypatch, tmp_path, ["42"])
    with pytest.raises(SandboxError, match="production isolation"):
        runner._resolve_tenant_for_isolation(7, cli_tool="codex", adapter=_NoStdinAdapter())


def test_a_stream_json_tool_is_allowed_through_to_the_provider_gate(monkeypatch, tmp_path):
    runner = _runner_with_config(monkeypatch, tmp_path, ["42"])
    assert (
        runner._resolve_tenant_for_isolation(7, cli_tool="claude-code", adapter=_StdinAdapter())
        == 42
    )


def test_an_unrequired_tenant_may_still_use_zcode(monkeypatch, tmp_path):
    """The refusal is scoped to the policy, not a blanket ban on the tool."""
    runner = _runner_with_config(monkeypatch, tmp_path, ["99"])
    assert runner._resolve_tenant_for_isolation(7, cli_tool="zcode", adapter=_StdinAdapter()) == 42


def test_a_tenant_lookup_failure_is_fatal_only_when_a_policy_exists(monkeypatch, tmp_path):
    """A DB blip must not downgrade a protected tenant — nor break everyone else.

    With a production-required list, guessing tenant 1 could silently answer
    "not required" for a tenant that is. With no such list, nothing can be
    downgraded, so failing every local run over a users-table hiccup would be a
    far larger outage than the one being guarded against.
    """
    runner = _runner_with_config(monkeypatch, tmp_path, ["42"])

    def _boom(user_id):
        raise SandboxError("users table unreadable")

    runner._resolve_tenant_id_strict = _boom
    runner._resolve_tenant_id = lambda user_id: 1
    with pytest.raises(SandboxError, match="users table"):
        runner._resolve_tenant_for_isolation(7, cli_tool="claude-code", adapter=_StdinAdapter())

    # Same failure, no policy configured: lenient.
    lenient = _runner_with_config(monkeypatch, tmp_path, [])
    lenient._resolve_tenant_id_strict = _boom
    lenient._resolve_tenant_id = lambda user_id: 1
    assert (
        lenient._resolve_tenant_for_isolation(7, cli_tool="claude-code", adapter=_StdinAdapter())
        == 1
    )
