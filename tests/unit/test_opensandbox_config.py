"""OpenSandbox backend config: tier routing and fail-closed validation (#2023).

gVisor vs Kata is a SERVER-level setting upstream, so an isolation tier selects
a separately configured endpoint rather than a request field. Everything the
provider cannot observe through the API — pod securityContext, kubelet
podPidsLimit, the cluster NetworkPolicy, the egress sidecar mode — is an
operator *attestation*, and a capability is declared only when its attestation
is present.
"""

from __future__ import annotations

import json

import pytest

from app.modules.workspace.autonomous.sandbox.opensandbox.config import (
    SandboxConfigError,
    load_backend_config,
    parse_backend_config,
)

pytestmark = [pytest.mark.regression, pytest.mark.issue(2023)]

_DIGEST = "ghcr.io/open-ace/agent@sha256:" + "a" * 64
_KATA_DIGEST = "ghcr.io/open-ace/agent@sha256:" + "b" * 64


def _endpoint(**overrides) -> dict:
    base = {
        "base_url": "http://osb.open-ace.svc.cluster.local:8080/v1",
        "api_key_env": "OSB_KEY_GVISOR",
        "runtime_class": "gvisor",
        "default_image": _DIGEST,
        "egress_allow_hosts": ["api.anthropic.com"],
        "attestations": {"egress_enforced": True, "egress_mode_dns_nft": True},
    }
    base.update(overrides)
    return base


def _raw(**overrides) -> dict:
    base = {
        "default_tier": "gvisor",
        "endpoints": {"gvisor": _endpoint()},
        "image_allowlist": [_DIGEST],
        "sandbox_ttl_seconds": 3600,
    }
    base.update(overrides)
    return base


# ── tier resolution ───────────────────────────────────────────────────


def test_resolves_default_tier_endpoint():
    cfg = parse_backend_config(_raw())
    assert cfg.endpoint_for(tenant=None, project_path=None).runtime_class == "gvisor"


def test_tenant_tier_overrides_default():
    cfg = parse_backend_config(
        _raw(
            endpoints={
                "gvisor": _endpoint(),
                "kata": _endpoint(
                    runtime_class="kata-qemu",
                    api_key_env="OSB_KEY_KATA",
                    default_image=_KATA_DIGEST,
                ),
            },
            image_allowlist=[_DIGEST, _KATA_DIGEST],
            tenant_tiers={"acme": "kata"},
        )
    )
    assert cfg.endpoint_for(tenant="acme", project_path=None).runtime_class == "kata-qemu"
    assert cfg.endpoint_for(tenant="other", project_path=None).runtime_class == "gvisor"


def test_project_tier_beats_tenant_tier():
    cfg = parse_backend_config(
        _raw(
            endpoints={
                "gvisor": _endpoint(),
                "kata": _endpoint(
                    runtime_class="kata-qemu",
                    api_key_env="OSB_KEY_KATA",
                    default_image=_KATA_DIGEST,
                ),
            },
            image_allowlist=[_DIGEST, _KATA_DIGEST],
            tenant_tiers={"acme": "gvisor"},
            project_tiers={"/srv/high-sec": "kata"},
        )
    )
    assert cfg.endpoint_for(tenant="acme", project_path="/srv/high-sec").tier == "kata"


def test_tier_pointing_at_missing_endpoint_fails_closed():
    # Never fall back to a weaker tier — that is the acceptance item
    # "production required policy cannot silently fall back".
    cfg = parse_backend_config(_raw(tenant_tiers={"acme": "kata"}))
    with pytest.raises(SandboxConfigError):
        cfg.endpoint_for(tenant="acme", project_path=None)


def test_default_tier_absent_from_endpoints_is_rejected():
    with pytest.raises(SandboxConfigError):
        parse_backend_config(_raw(default_tier="nope"))


# ── attestations ──────────────────────────────────────────────────────


def test_attestations_default_to_absent_not_present():
    # A missing attestation must read as "not enforced", so the capability is
    # dropped and any spec requiring it fails closed at create().
    cfg = parse_backend_config(_raw(endpoints={"gvisor": _endpoint(attestations={})}))
    att = cfg.endpoint_for(tenant=None, project_path=None).attestations
    assert att.nonroot_enforced is False
    assert att.pod_pids_limit == 0
    assert att.inode_quota_enforced is False


def test_inode_quota_attestation_defaults_off():
    cfg = parse_backend_config(_raw())
    assert (
        cfg.endpoint_for(tenant=None, project_path=None).attestations.inode_quota_enforced is False
    )


def test_negative_pod_pids_limit_is_rejected():
    with pytest.raises(SandboxConfigError):
        parse_backend_config(
            _raw(endpoints={"gvisor": _endpoint(attestations={"pod_pids_limit": -1})})
        )


def test_unknown_attestation_key_is_rejected():
    # A typo'd attestation would silently read as absent and quietly remove a
    # capability, so reject it loudly instead.
    with pytest.raises(SandboxConfigError):
        parse_backend_config(
            _raw(endpoints={"gvisor": _endpoint(attestations={"nonroot_enfoced": True})})
        )


# ── egress allowlist ──────────────────────────────────────────────────


def test_egress_enforced_without_allowlist_is_rejected():
    with pytest.raises(SandboxConfigError):
        parse_backend_config(_raw(endpoints={"gvisor": _endpoint(egress_allow_hosts=[])}))


@pytest.mark.parametrize(
    "host",
    [
        "169.254.169.254",
        "metadata.google.internal",
        "metadata",
        "instance-data",
        "10.0.0.5",
        "127.0.0.1",
        "192.168.1.1",
        "::1",
        "fd00::1",
    ],
)
def test_ip_literals_and_metadata_hosts_rejected_from_allowlist(host):
    # Upstream's egress MVP cannot express IP/CIDR rules at all, so an IP
    # literal in the allowlist is meaningless rather than merely risky.
    with pytest.raises(SandboxConfigError):
        parse_backend_config(
            _raw(endpoints={"gvisor": _endpoint(egress_allow_hosts=["api.anthropic.com", host])})
        )


def test_wildcard_and_fqdn_hosts_are_accepted():
    cfg = parse_backend_config(
        _raw(
            endpoints={
                "gvisor": _endpoint(egress_allow_hosts=["*.githubusercontent.com", "pypi.org"])
            }
        )
    )
    assert cfg.endpoint_for(tenant=None, project_path=None).egress_allow_hosts == (
        "*.githubusercontent.com",
        "pypi.org",
    )


def test_egress_allow_hosts_are_per_endpoint():
    cfg = parse_backend_config(
        _raw(
            endpoints={
                "gvisor": _endpoint(egress_allow_hosts=["api.anthropic.com", "pypi.org"]),
                "kata": _endpoint(
                    runtime_class="kata-qemu",
                    api_key_env="OSB_KEY_KATA",
                    default_image=_KATA_DIGEST,
                    egress_allow_hosts=["api.anthropic.com"],
                ),
            },
            image_allowlist=[_DIGEST, _KATA_DIGEST],
            tenant_tiers={"acme": "kata"},
        )
    )
    assert len(cfg.endpoint_for(tenant=None, project_path=None).egress_allow_hosts) == 2
    assert len(cfg.endpoint_for(tenant="acme", project_path=None).egress_allow_hosts) == 1


# ── images ────────────────────────────────────────────────────────────


def test_tag_only_image_rejected_from_allowlist():
    with pytest.raises(SandboxConfigError):
        parse_backend_config(_raw(image_allowlist=["ghcr.io/open-ace/agent:v1"]))


def test_short_digest_rejected_from_allowlist():
    with pytest.raises(SandboxConfigError):
        parse_backend_config(_raw(image_allowlist=["ghcr.io/open-ace/agent@sha256:abc"]))


def test_default_image_must_be_in_allowlist():
    with pytest.raises(SandboxConfigError):
        parse_backend_config(_raw(endpoints={"gvisor": _endpoint(default_image=_KATA_DIGEST)}))


# ── misc validation ───────────────────────────────────────────────────


def test_ttl_below_upstream_minimum_60_is_rejected():
    with pytest.raises(SandboxConfigError):
        parse_backend_config(_raw(sandbox_ttl_seconds=30))


def test_non_http_base_url_is_rejected():
    with pytest.raises(SandboxConfigError):
        parse_backend_config(_raw(endpoints={"gvisor": _endpoint(base_url="ftp://osb.svc/v1")}))


def test_execd_endpoint_host_allowlist_defaults_to_the_base_url_host():
    # The execd URL is SERVER-supplied; without a default allowlist the guard
    # in client.py would have nothing to check against.
    cfg = parse_backend_config(_raw())
    endpoint = cfg.endpoint_for(tenant=None, project_path=None)
    assert endpoint.execd_endpoint_host_allowlist == ("osb.open-ace.svc.cluster.local",)


def test_api_key_read_from_named_env_var(monkeypatch):
    monkeypatch.setenv("OSB_KEY_GVISOR", "secret-key")
    cfg = parse_backend_config(_raw())
    assert cfg.endpoint_for(tenant=None, project_path=None).api_key() == "secret-key"


def test_api_key_missing_from_env_fails_closed(monkeypatch):
    monkeypatch.delenv("OSB_KEY_GVISOR", raising=False)
    cfg = parse_backend_config(_raw())
    with pytest.raises(SandboxConfigError):
        cfg.endpoint_for(tenant=None, project_path=None).api_key()


# ── loading ───────────────────────────────────────────────────────────


def test_missing_config_file_returns_none(tmp_path):
    assert load_backend_config(str(tmp_path / "absent.json")) is None


def test_malformed_config_raises_rather_than_defaulting(tmp_path):
    path = tmp_path / "sandbox-backends.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SandboxConfigError):
        load_backend_config(str(path))


def test_explicit_env_path_wins(tmp_path, monkeypatch):
    path = tmp_path / "sandbox-backends.json"
    path.write_text(json.dumps(_raw()), encoding="utf-8")
    monkeypatch.setenv("OPENACE_SANDBOX_BACKENDS", str(path))
    cfg = load_backend_config()
    assert cfg is not None and cfg.default_tier == "gvisor"
