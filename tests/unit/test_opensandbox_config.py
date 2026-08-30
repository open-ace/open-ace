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
from dataclasses import replace

import pytest

from app.modules.workspace.autonomous.sandbox.opensandbox import config as config_module
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
        "execd_token_env": "OSB_EXECD_TOKEN",
        "base_url": "http://osb.open-ace.svc.cluster.local:8080/v1",
        "api_key_env": "OSB_KEY_GVISOR",
        "runtime_class": "kata-qemu",
        "default_image": _DIGEST,
        "egress_allow_hosts": ["api.anthropic.com"],
        "attestations": {"egress_enforced": True, "egress_mode_dns_nft": True},
    }
    base.update(overrides)
    return base


def _raw(**overrides) -> dict:
    base = {
        "installation_id": "openace-test",
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
    assert cfg.endpoint_for(tenant=None, project_path=None).runtime_class == "kata-qemu"


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
            tenant_tiers={"42": "kata"},
        )
    )
    assert cfg.endpoint_for(tenant="42", project_path=None).runtime_class == "kata-qemu"
    assert cfg.endpoint_for(tenant="7", project_path=None).runtime_class == "kata-qemu"


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
            tenant_tiers={"42": "gvisor"},
            project_tiers={"/srv/high-sec": "kata"},
        )
    )
    assert cfg.endpoint_for(tenant="42", project_path="/srv/high-sec").tier == "kata"


def test_tier_pointing_at_missing_endpoint_is_refused_at_parse_time():
    """Caught when the file is read, not at the first create.

    Deferring it meant the config loaded cleanly and then raised for exactly
    the tenants an operator had singled out — who are also the likeliest
    members of production_required_tenants, and so have no fallback.
    """
    with pytest.raises(SandboxConfigError, match="no endpoint"):
        parse_backend_config(_raw(tenant_tiers={"42": "kata"}))
    with pytest.raises(SandboxConfigError, match="no endpoint"):
        parse_backend_config(_raw(project_tiers={"/srv/repo": "kata"}))


def test_endpoint_for_still_fails_closed_on_a_hand_built_config():
    # The parser now blocks this, but a config constructed directly must not
    # fall back to a weaker tier either.
    cfg = parse_backend_config(_raw())
    cfg = replace(cfg, tenant_tiers={"42": "kata"})
    with pytest.raises(SandboxConfigError):
        cfg.endpoint_for(tenant="42", project_path=None)


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
            tenant_tiers={"42": "kata"},
        )
    )
    assert len(cfg.endpoint_for(tenant=None, project_path=None).egress_allow_hosts) == 2
    assert len(cfg.endpoint_for(tenant="42", project_path=None).egress_allow_hosts) == 1


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


def test_explicitly_requested_config_that_is_absent_raises(tmp_path, monkeypatch):
    # Falling through to the system/user config would mean a typo'd env var, or
    # a config lost during a deploy, silently resolves to None — and None means
    # "no OpenSandbox backend", handing every non-required tenant to Legacy with
    # no signal. That is acceptance criterion 12's failure mode.
    monkeypatch.delenv("OPENACE_SANDBOX_BACKENDS", raising=False)
    with pytest.raises(SandboxConfigError):
        load_backend_config(str(tmp_path / "absent.json"))


def test_absent_env_var_path_also_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENACE_SANDBOX_BACKENDS", str(tmp_path / "absent.json"))
    with pytest.raises(SandboxConfigError):
        load_backend_config()


def test_no_config_anywhere_returns_none(tmp_path, monkeypatch):
    # Pin the search paths at tmp_path so the result does not depend on whether
    # the developer's machine happens to have /etc/openace/sandbox-backends.json.
    monkeypatch.delenv("OPENACE_SANDBOX_BACKENDS", raising=False)
    monkeypatch.setattr(config_module, "DEFAULT_BACKEND_CONFIG_PATH", str(tmp_path / "etc.json"))
    monkeypatch.setattr(config_module, "USER_BACKEND_CONFIG_PATH", str(tmp_path / "user.json"))
    assert load_backend_config() is None


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


# ── production-required tenants (acceptance criterion 12) ─────────────


def test_production_required_tenant_is_recognised():
    cfg = parse_backend_config(_raw(production_required_tenants=["42"]))
    assert cfg.requires_production_isolation("42") is True
    assert cfg.requires_production_isolation("7") is False
    assert cfg.requires_production_isolation(None) is False


def test_tenant_keys_are_the_integer_tenant_id_as_a_string():
    # The repo's tenant identity is an integer tenant_id; there is no name->id
    # mapping anywhere, so a slug key would be unsuppliable.
    cfg = parse_backend_config(_raw(tenant_tiers={42: "gvisor"}))
    assert cfg.tier_for(tenant="42", project_path=None) == "gvisor"


def test_no_production_required_list_means_no_tenant_is_required():
    cfg = parse_backend_config(_raw())
    assert cfg.requires_production_isolation("42") is False


# ── tenant key coercion (B7) ──────────────────────────────────────────


def test_int_and_str_tenant_ids_resolve_to_the_same_tier():
    # The runner holds an integer tenant_id and config keys are strings. Without
    # coercion in tier_for, a production-required tenant passed as an int is
    # correctly flagged by requires_production_isolation and then routed to the
    # DEFAULT tier — flagged as needing Kata, given gVisor.
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
            tenant_tiers={"42": "kata"},
            production_required_tenants=["42"],
        )
    )
    assert cfg.tier_for(tenant=42, project_path=None) == "kata"
    assert cfg.tier_for(tenant="42", project_path=None) == "kata"
    assert cfg.requires_production_isolation(42) is True


# ── execd token, egress port, runtime user (B4/B5/B15) ────────────────


def test_execd_token_required_without_an_env_var_is_rejected():
    # Refusal 9 makes execd_token_required mandatory for a usable tier, so a
    # tier that attests it but names no env var would send no token and 401 on
    # every execd call.
    with pytest.raises(SandboxConfigError):
        parse_backend_config(
            _raw(
                endpoints={
                    "gvisor": _endpoint(
                        attestations={"execd_token_required": True}, execd_token_env=""
                    )
                }
            )
        )


def test_execd_token_read_from_its_env_var(monkeypatch):
    monkeypatch.setenv("OSB_EXECD_TOKEN", "execd-secret")
    cfg = parse_backend_config(
        _raw(
            endpoints={
                "gvisor": _endpoint(
                    attestations={"execd_token_required": True},
                    execd_token_env="OSB_EXECD_TOKEN",
                )
            }
        )
    )
    assert cfg.endpoint_for(tenant=None, project_path=None).execd_token() == "execd-secret"


def test_execd_token_missing_from_env_fails_closed(monkeypatch):
    monkeypatch.delenv("OSB_EXECD_TOKEN", raising=False)
    cfg = parse_backend_config(
        _raw(
            endpoints={
                "gvisor": _endpoint(
                    attestations={"execd_token_required": True},
                    execd_token_env="OSB_EXECD_TOKEN",
                )
            }
        )
    )
    with pytest.raises(SandboxConfigError):
        cfg.endpoint_for(tenant=None, project_path=None).execd_token()


def test_egress_port_defaults_to_the_sidecar_not_execd():
    # The egress sidecar is a separate service; GET /policy on execd's port 404s.
    endpoint = parse_backend_config(_raw()).endpoint_for(tenant=None, project_path=None)
    assert endpoint.egress_port == 18080
    assert endpoint.execd_port == 44772


def test_runtime_user_and_group_have_defaults():
    endpoint = parse_backend_config(_raw()).endpoint_for(tenant=None, project_path=None)
    assert endpoint.runtime_user and endpoint.runtime_group


def test_root_exec_uid_is_rejected():
    with pytest.raises(SandboxConfigError):
        parse_backend_config(_raw(endpoints={"gvisor": _endpoint(exec_uid=0)}))


def test_exec_uid_defaults_to_non_root():
    endpoint = parse_backend_config(_raw()).endpoint_for(tenant=None, project_path=None)
    assert endpoint.exec_uid != 0 and endpoint.exec_gid != 0


# ── rollout allowlist: the tenant/project gradual-rollout knob ─────────


def test_rollout_defaults_to_all_so_a_bare_config_means_use_the_backend():
    cfg = parse_backend_config(_raw())
    assert cfg.rollout_includes(tenant="7", project_path=None) is True
    assert cfg.rollout_includes(tenant=None, project_path=None) is True


def test_allowlist_mode_covers_only_listed_tenants():
    cfg = parse_backend_config(_raw(rollout={"mode": "allowlist", "tenants": ["42"]}))
    assert cfg.rollout_includes(tenant="42", project_path=None) is True
    assert cfg.rollout_includes(tenant=42, project_path=None) is True
    assert cfg.rollout_includes(tenant="7", project_path=None) is False


def test_allowlist_mode_covers_listed_projects():
    cfg = parse_backend_config(
        _raw(rollout={"mode": "allowlist", "projects": ["/srv/repos/pilot"]})
    )
    assert cfg.rollout_includes(tenant="7", project_path="/srv/repos/pilot") is True
    assert cfg.rollout_includes(tenant="7", project_path="/srv/repos/other") is False


def test_a_required_tenant_excluded_from_the_rollout_is_a_config_error():
    # "must use the sandbox" and "not rolled out to the sandbox" is an incoherent
    # pair. Letting either silently win would be the quiet downgrade this design
    # exists to prevent.
    with pytest.raises(SandboxConfigError, match="rollout"):
        parse_backend_config(
            _raw(
                rollout={"mode": "allowlist", "tenants": ["7"]},
                production_required_tenants=["42"],
            )
        )


def test_a_required_tenant_inside_the_rollout_is_accepted():
    cfg = parse_backend_config(
        _raw(
            rollout={"mode": "allowlist", "tenants": ["42"]},
            production_required_tenants=["42"],
        )
    )
    assert cfg.requires_production_isolation("42") is True
    assert cfg.rollout_includes(tenant="42", project_path=None) is True


def test_unknown_rollout_mode_is_rejected():
    with pytest.raises(SandboxConfigError):
        parse_backend_config(_raw(rollout={"mode": "sometimes"}))


@pytest.mark.parametrize("value", ["false", "False", "0", "no", "", 0, 1, None, []])
def test_a_non_boolean_attestation_is_refused_rather_than_coerced(value):
    """`bool("false")` is True, and these flags grant security capabilities.

    A templating layer that renders booleans as strings would have turned a
    withheld attestation into a granted one — silently, and failing OPEN. Only
    a real JSON boolean is accepted.
    """
    raw = _raw()
    raw["endpoints"]["gvisor"]["attestations"] = dict(
        raw["endpoints"]["gvisor"]["attestations"], nonroot_enforced=value
    )
    with pytest.raises(SandboxConfigError, match="boolean"):
        parse_backend_config(raw)


@pytest.mark.parametrize("flag", ["egress_preapplied", "recycle_delete"])
def test_a_string_pool_flag_is_refused(flag):
    """Same coercion hazard on the warm-pool guarantees.

    These decide whether a recycled sandbox keeps its egress policy and whether
    teardown really deletes; "false" reading as True is a security downgrade.
    """
    raw = _raw()
    raw["endpoints"]["gvisor"]["pool"] = {"pool_ref": "p", flag: "false"}
    with pytest.raises(SandboxConfigError, match="boolean"):
        parse_backend_config(raw)


def test_a_real_boolean_attestation_still_parses():
    raw = _raw()
    raw["endpoints"]["gvisor"]["attestations"] = dict(
        raw["endpoints"]["gvisor"]["attestations"], nonroot_enforced=False
    )
    cfg = parse_backend_config(raw)
    assert cfg.endpoints["gvisor"].attestations.nonroot_enforced is False


def test_a_config_without_an_installation_id_is_refused():
    """Reconciliation deletes what it does not recognise; it must know who we are."""
    raw = _raw()
    raw.pop("installation_id", None)
    with pytest.raises(SandboxConfigError, match="installation_id"):
        parse_backend_config(raw)


@pytest.mark.parametrize("value", ["has space", "a" * 64, "semi;colon", "comma,tag"])
def test_a_malformed_installation_id_is_refused(value):
    raw = _raw()
    raw["installation_id"] = value
    with pytest.raises(SandboxConfigError, match="installation_id"):
        parse_backend_config(raw)


def test_a_gvisor_tier_cannot_attest_egress_enforcement():
    """gVisor and the egress sidecar are mutually exclusive UPSTREAM.

    Verified against a real OpenSandbox server, which answers:
      "networkPolicy is not compatible with runtime 'gvisor': gVisor does not
       support the iptables nat table required by the egress sidecar."
    This provider always sends a networkPolicy, so such a tier could not create
    a single sandbox. Nothing short of running a real server surfaced it — the
    shipped gVisor tier had been the DEFAULT tier all along.
    """
    raw = _raw(
        endpoints={
            "gvisor": _endpoint(
                runtime_class="gvisor",
                attestations={"egress_enforced": True, "egress_mode_dns_nft": True},
            )
        }
    )
    with pytest.raises(SandboxConfigError, match="cannot enforce egress"):
        parse_backend_config(raw)


def test_a_kata_tier_may_attest_egress_enforcement():
    """Kata supports the sidecar, so it is the tier that satisfies #2023."""
    cfg = parse_backend_config(
        _raw(
            endpoints={
                "kata": _endpoint(
                    runtime_class="kata-qemu",
                    attestations={"egress_enforced": True, "egress_mode_dns_nft": True},
                )
            },
            default_tier="kata",
        )
    )
    assert cfg.endpoints["kata"].attestations.egress_enforced is True


# `runsc` is NOT here: it is gVisor's handler name and is now correctly
# classified as gVisor family, so it is refused by the egress rule below.
@pytest.mark.parametrize("runtime_class", ["totally-made-up", "runc", "sysbox-runc"])
def test_an_unverifiable_runtime_class_is_refused(runtime_class):
    """A RuntimeClass name this backend cannot classify buys no capability.

    `runsc` is gVisor's OWN handler name and a common RuntimeClass. Matching on
    the substring "gvisor" let it past the config refusal AND past both branches
    of the boot probe — which then set probes_passed having verified nothing,
    while NAMESPACE_ISOLATION is granted off exactly that flag. A capability
    declared with nothing enforcing it is the defect this package exists to
    prevent.
    """
    with pytest.raises(SandboxConfigError, match="cannot verify|not a runtime"):
        parse_backend_config(_raw(endpoints={"t": _endpoint(runtime_class=runtime_class)}))


@pytest.mark.parametrize("runtime_class", ["gvisor", "gVisor", "runsc", "gvisor-nvidia"])
def test_every_gvisor_family_name_is_refused_with_egress(runtime_class):
    """The gVisor/egress incompatibility is about the runtime, not the spelling."""
    raw = _raw(
        endpoints={
            "t": _endpoint(
                runtime_class=runtime_class,
                attestations={"egress_enforced": True, "egress_mode_dns_nft": True},
            )
        }
    )
    with pytest.raises(SandboxConfigError, match="cannot enforce egress"):
        parse_backend_config(raw)


def test_the_runtime_family_classifier_is_shared_with_the_probe():
    """One definition, so config and probe cannot disagree about a name."""
    from app.modules.workspace.autonomous.sandbox.opensandbox.config import runtime_family

    assert runtime_family("runsc") == "gvisor"
    assert runtime_family("gvisor") == "gvisor"
    assert runtime_family("kata-qemu") == "kata"
    assert runtime_family("kata-fc") == "kata"
    assert runtime_family("nonsense") == ""
