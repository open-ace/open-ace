"""SandboxSpec -> CreateSandboxRequest translation and capability derivation (#2023).

Two things this module must never do, both of which have burned this codebase
before:

* declare a capability whose enforcement mechanism does not hold (the #2082
  lesson — RemoteMachineProvider copied Legacy's capability set and enforced
  none of it);
* silently drop a policy field the caller asked for. Upstream's egress MVP
  cannot express IP/CIDR rules, so a spec carrying ``allow_cidrs`` must fail
  closed rather than run with those rules simply absent.
"""

from __future__ import annotations

import pytest

from app.modules.workspace.autonomous.sandbox.opensandbox.config import (
    Attestations,
    PoolConfig,
    parse_backend_config,
)
from app.modules.workspace.autonomous.sandbox.opensandbox.policy import (
    build_command_request,
    build_create_request,
    build_env,
    build_pty_command,
    build_resource_limits,
    derive_capabilities,
    map_state,
    synthesise_spec_fields,
    validate_spec_for_endpoint,
)
from app.modules.workspace.autonomous.sandbox.provider import CapabilityUnsupported, SandboxError
from app.modules.workspace.autonomous.sandbox.types import (
    NetworkEgressPolicy,
    RuntimeSpec,
    SandboxCapability,
    SandboxSpec,
    SandboxStatus,
    VolumeSpec,
)
from app.modules.workspace.autonomous.task_isolation import AgentTaskPolicy

pytestmark = [pytest.mark.regression, pytest.mark.issue(2023)]

_DIGEST = "ghcr.io/open-ace/agent@sha256:" + "a" * 64

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


def _cfg(attestations=None, **overrides):
    endpoint = {
        "base_url": "http://osb.open-ace.svc.cluster.local:8080/v1",
        "api_key_env": "OSB_KEY",
        "execd_token_env": "OSB_EXECD_TOKEN",
        "runtime_class": "gvisor",
        "default_image": _DIGEST,
        "egress_allow_hosts": ["api.anthropic.com"],
        "attestations": _FULL_ATTESTATIONS if attestations is None else attestations,
    }
    endpoint.update(overrides.pop("endpoint", {}))
    raw = {
        "installation_id": "openace-test",
        "default_tier": "gvisor",
        "endpoints": {"gvisor": endpoint},
        "image_allowlist": [_DIGEST],
        "resource_defaults": {"cpu": "2", "memory": "4Gi", "ephemeral-storage": "8Gi"},
        "sandbox_ttl_seconds": 3600,
    }
    raw.update(overrides)
    return parse_backend_config(raw)


def _endpoint(cfg):
    return cfg.endpoint_for(tenant=None, project_path=None)


def _spec(**overrides) -> SandboxSpec:
    base = {"task_id": "t-1", "project_path": "/workspace/repo", "cli_tool": "claude-code"}
    base.update(overrides)
    return SandboxSpec(**base)


def _create(spec=None, cfg=None, **kw):
    cfg = cfg or _cfg()
    return build_create_request(
        spec or _spec(), cfg, _endpoint(cfg), generation=kw.pop("generation", 1), **kw
    )


# ── egress ────────────────────────────────────────────────────────────


def test_default_egress_blocks_metadata_private_cidr_and_unknown_domain():
    policy = _create()["networkPolicy"]
    assert policy["defaultAction"] == "deny"
    targets = {rule["target"] for rule in policy["egress"]}
    assert targets == {"api.anthropic.com"}
    assert all(rule["action"] == "allow" for rule in policy["egress"])
    assert "169.254.169.254" not in targets
    assert not any(t.startswith(("10.", "192.168.", "172.16.", "127.")) for t in targets)
    assert "evil.example.com" not in targets


def test_allow_cidrs_fail_closed_because_upstream_egress_has_no_ip_rules():
    spec = _spec(
        network_egress=NetworkEgressPolicy(mode="allow_explicit", allow_cidrs=("10.0.0.0/8",))
    )
    with pytest.raises(SandboxError, match="CIDR"):
        _create(spec)


def test_unrestricted_egress_mode_refused():
    with pytest.raises(SandboxError, match="unrestricted"):
        _create(_spec(network_egress=NetworkEgressPolicy(mode="unrestricted")))


def test_spec_can_narrow_the_operator_allowlist_but_never_widen_it():
    cfg = _cfg(endpoint={"egress_allow_hosts": ["api.anthropic.com", "pypi.org"]})
    spec = _spec(
        network_egress=NetworkEgressPolicy(
            mode="allow_explicit", allow_hosts=("pypi.org", "evil.example.com")
        )
    )
    targets = {r["target"] for r in _create(spec, cfg)["networkPolicy"]["egress"]}
    assert targets == {"pypi.org"}


# ── synthesis: production specs arrive with everything None ───────────


def test_production_shaped_spec_is_synthesised_from_the_tier():
    # agent_runner builds SandboxSpec(task_id, project_path, cli_tool,
    # system_account, policy) — runtime/network_egress/volumes are all None.
    # Without synthesis the refusals below would be dead code.
    cfg = _cfg()
    spec = synthesise_spec_fields(_spec(), cfg, _endpoint(cfg))
    assert spec.runtime is not None and spec.runtime.image == _DIGEST
    assert spec.network_egress is not None and spec.network_egress.mode == "deny_all"


def test_tier_without_egress_attestation_fails_closed_even_when_spec_asks_nothing():
    # The synthesised policy implies NETWORK_EGRESS_POLICY, which an
    # unattested tier does not declare -> CapabilityUnsupported.
    cfg = _cfg(
        attestations={k: v for k, v in _FULL_ATTESTATIONS.items() if k != "egress_enforced"},
        endpoint={"egress_allow_hosts": ["api.anthropic.com"]},
    )
    with pytest.raises(CapabilityUnsupported):
        _create(cfg=cfg)


# ── other refusals ────────────────────────────────────────────────────


def test_host_backed_volume_refused():
    spec = _spec(volumes=(VolumeSpec(name="host", mount_path="/host", kind="host"),))
    with pytest.raises(SandboxError, match="host"):
        _create(spec)


def test_volume_mounted_outside_the_workspace_root_refused():
    spec = _spec(volumes=(VolumeSpec(name="etc", mount_path="/etc", kind="ephemeral"),))
    with pytest.raises(SandboxError):
        _create(spec)


def test_image_outside_allowlist_refused():
    spec = _spec(runtime=RuntimeSpec(image="docker.io/evil@sha256:" + "b" * 64))
    with pytest.raises(SandboxError, match="allowlist"):
        _create(spec)


def test_tag_only_image_refused():
    spec = _spec(runtime=RuntimeSpec(image="ghcr.io/open-ace/agent:v1"))
    with pytest.raises(SandboxError):
        _create(spec)


def test_a_tier_without_secure_access_still_runs_but_loses_the_capability():
    """secureAccess needs gateway-mode ingress upstream; ours is direct.

    Requiring the attestation demanded a promise the shipped manifests cannot
    keep, and granted CREDENTIAL_TOKEN_BINDING off it — a capability asserted
    with nothing enforcing it (#2082). The tier now runs, and simply does not
    claim per-sandbox credential binding.
    """
    cfg = _cfg(
        attestations={k: v for k, v in _FULL_ATTESTATIONS.items() if k != "secure_access_required"}
    )
    _create(cfg=cfg)  # no refusal


def test_credential_token_binding_is_not_granted_without_secure_access():
    from app.modules.workspace.autonomous.sandbox.opensandbox.policy import derive_capabilities
    from app.modules.workspace.autonomous.sandbox.types import SandboxCapability

    cfg = _cfg(
        attestations={k: v for k, v in _FULL_ATTESTATIONS.items() if k != "secure_access_required"}
    )
    caps = derive_capabilities(cfg.endpoints["gvisor"], probes_passed=True)
    assert SandboxCapability.CREDENTIAL_TOKEN_BINDING not in caps


@pytest.mark.parametrize(
    "missing",
    [
        "nonroot_enforced",
        "readonly_rootfs",
        "seccomp_runtime_default",
        "dedicated_service_account",
        "execd_token_required",
    ],
)
def test_tier_without_pod_hardening_attestations_is_refused(missing):
    # Nothing in implied_required_capabilities requires FILESYSTEM_ACL,
    # CPU_MEM_PIDS_TIME_QUOTA or CREDENTIAL_TOKEN_BINDING, and production specs
    # carry required_capabilities=frozenset(). Without an explicit refusal the
    # provider would correctly decline to DECLARE those capabilities and then
    # run the agent anyway — as root, on a writable rootfs, against an
    # unauthenticated execd. Honest declaration, silently degraded execution.
    cfg = _cfg(attestations={k: v for k, v in _FULL_ATTESTATIONS.items() if k != missing})
    with pytest.raises(SandboxError):
        _create(cfg=cfg)


def test_zero_pod_pids_limit_is_refused():
    cfg = _cfg(attestations={**_FULL_ATTESTATIONS, "pod_pids_limit": 0})
    with pytest.raises(SandboxError):
        _create(cfg=cfg)


def test_pids_max_above_the_attested_pod_limit_is_refused():
    policy = AgentTaskPolicy(pids_max=4096)
    with pytest.raises(SandboxError, match="pids"):
        _create(_spec(policy=policy))


# ── capabilities ──────────────────────────────────────────────────────


def test_capabilities_track_attestations_not_a_constant():
    full = derive_capabilities(_endpoint(_cfg()), probes_passed=True)
    assert SandboxCapability.NETWORK_EGRESS_POLICY in full
    assert SandboxCapability.FILESYSTEM_ACL in full
    assert SandboxCapability.CPU_MEM_PIDS_TIME_QUOTA in full

    bare = derive_capabilities(
        _endpoint(_cfg(attestations={}, endpoint={"egress_allow_hosts": ["api.anthropic.com"]})),
        probes_passed=True,
    )
    assert SandboxCapability.NETWORK_EGRESS_POLICY not in bare
    assert SandboxCapability.FILESYSTEM_ACL not in bare
    assert SandboxCapability.CPU_MEM_PIDS_TIME_QUOTA not in bare


def test_namespace_isolation_requires_the_runtime_probe_to_have_passed():
    # The runtime class is an operator declaration with no API that reports it,
    # so it is only claimed once the boot probe has confirmed the kernel.
    assert SandboxCapability.NAMESPACE_ISOLATION not in derive_capabilities(
        _endpoint(_cfg()), probes_passed=False
    )


def test_egress_capability_requires_all_three_egress_attestations():
    for missing in ("egress_enforced", "egress_mode_dns_nft", "metadata_cidr_blocked"):
        attestations = {k: v for k, v in _FULL_ATTESTATIONS.items() if k != missing}
        caps = derive_capabilities(_endpoint(_cfg(attestations=attestations)), probes_passed=True)
        assert SandboxCapability.NETWORK_EGRESS_POLICY not in caps, missing


def test_storage_quota_capability_needs_at_least_one_storage_attestation():
    # Gating on inode_quota_enforced ALONE made the ephemeral-storage branch
    # unreachable: implied_required_capabilities demands STORAGE_INODE_QUOTA
    # whenever policy.ephemeral_storage_limit > 0, so such a spec fail-closed
    # before the branch could run and the attestation was inert.
    neither = {k: v for k, v in _FULL_ATTESTATIONS.items() if k != "ephemeral_storage_enforced"}
    assert SandboxCapability.STORAGE_INODE_QUOTA not in derive_capabilities(
        _endpoint(_cfg(attestations=neither)), probes_passed=True
    )
    assert SandboxCapability.STORAGE_INODE_QUOTA in derive_capabilities(
        _endpoint(_cfg()), probes_passed=True
    )


def test_inode_dimension_is_refused_even_when_ephemeral_storage_is_attested():
    # ulimit -f caps ONE file and a k8s ephemeral-storage limit is eviction-
    # polled with no inode dimension, so the capability being declared for the
    # storage dimension must not let an inode_limit through.
    with pytest.raises(SandboxError, match="inode"):
        _create(_spec(policy=AgentTaskPolicy(inode_limit=50_000)))


def test_inode_limit_allowed_once_a_real_inode_quota_is_attested():
    cfg = _cfg(attestations={**_FULL_ATTESTATIONS, "inode_quota_enforced": True})
    body = _create(_spec(policy=AgentTaskPolicy(inode_limit=50_000)), cfg)
    assert body["resourceLimits"]


def test_credential_binding_requires_execd_token_and_secure_access():
    caps = derive_capabilities(
        _endpoint(
            _cfg(
                attestations={
                    k: v for k, v in _FULL_ATTESTATIONS.items() if k != "execd_token_required"
                }
            )
        ),
        probes_passed=True,
    )
    assert SandboxCapability.CREDENTIAL_TOKEN_BINDING not in caps


def test_every_declared_capability_has_an_observable_artifact():
    # Capability-realism probe: each declared capability must correspond to
    # something actually present in the request or the attestations.
    cfg = _cfg()
    endpoint = _endpoint(cfg)
    body = _create(cfg=cfg)
    caps = derive_capabilities(endpoint, probes_passed=True)
    if SandboxCapability.NETWORK_EGRESS_POLICY in caps:
        assert body["networkPolicy"]["defaultAction"] == "deny"
    if SandboxCapability.CPU_MEM_PIDS_TIME_QUOTA in caps:
        assert body["resourceLimits"]["cpu"] and body["resourceLimits"]["memory"]
        assert endpoint.attestations.pod_pids_limit > 0
    if SandboxCapability.CREDENTIAL_TOKEN_BINDING in caps:
        assert body["secureAccess"] is True
    if SandboxCapability.PRIVATE_HOME_TMP_XDG in caps:
        assert body["env"]["HOME"].startswith("/")


# ── resource translation ──────────────────────────────────────────────


def test_cgroup_cpu_max_converts_to_millicores():
    # cgroup v2 cpu.max is "<quota_us> <period_us>": 200000/100000 == 2 CPUs.
    cfg = _cfg()
    limits = build_resource_limits(AgentTaskPolicy(cpu_max="200000 100000"), cfg, _endpoint(cfg))
    assert limits["cpu"] == "2000m"


def test_cgroup_cpu_max_unlimited_falls_back_to_resource_defaults():
    cfg = _cfg()
    limits = build_resource_limits(AgentTaskPolicy(cpu_max="max 100000"), cfg, _endpoint(cfg))
    assert limits["cpu"] == "2"


def test_memory_bytes_become_a_plain_byte_quantity():
    cfg = _cfg()
    limits = build_resource_limits(
        AgentTaskPolicy(memory_max_bytes=4294967296), cfg, _endpoint(cfg)
    )
    assert limits["memory"] == "4294967296"


def test_policy_wins_over_defaults_and_defaults_fill_only_zeros():
    cfg = _cfg()
    limits = build_resource_limits(AgentTaskPolicy(memory_max_bytes=1024), cfg, _endpoint(cfg))
    assert limits["memory"] == "1024"
    assert limits["cpu"] == "2"  # policy left cpu_max empty -> default


def test_ephemeral_storage_only_sent_when_attested():
    cfg = _cfg(
        attestations={
            k: v for k, v in _FULL_ATTESTATIONS.items() if k != "ephemeral_storage_enforced"
        }
    )
    limits = build_resource_limits(
        AgentTaskPolicy(ephemeral_storage_limit=2048), cfg, _endpoint(cfg)
    )
    assert "ephemeral-storage" not in limits


def test_zero_wall_clock_falls_back_to_configured_ttl_not_60s():
    # wall_clock_limit defaults to 0, and read_agent_task_policy returns
    # all-defaults when no agent-launcher.conf exists — the common case, and
    # exactly the "absent config" state the rollout targets. max(0, 60) would
    # have killed every agent run one minute in.
    assert _create(_spec(policy=AgentTaskPolicy()))["timeout"] == 3600
    assert _create()["timeout"] == 3600


def test_wall_clock_below_the_configured_ttl_does_not_shorten_the_sandbox():
    assert _create(_spec(policy=AgentTaskPolicy(wall_clock_limit=30)))["timeout"] == 3600


def test_wall_clock_above_the_configured_ttl_extends_it():
    assert _create(_spec(policy=AgentTaskPolicy(wall_clock_limit=7200)))["timeout"] == 7200


def test_command_timeout_is_milliseconds():
    body = build_command_request(
        ["pytest"], cwd="/workspace", envs={}, wall_clock_limit=90, uid=1000, gid=1000
    )
    assert body["timeout"] == 90_000


def test_zero_wall_clock_omits_command_timeout_rather_than_sending_zero():
    # Upstream: "If omitted, the server will not enforce any timeout." Sending
    # 0 is not omitting, and its behaviour is undefined.
    body = build_command_request(
        ["pytest"], cwd="/workspace", envs={}, wall_clock_limit=0, uid=1000, gid=1000
    )
    assert "timeout" not in body


# ── environment ───────────────────────────────────────────────────────


def test_env_is_built_from_an_allowlist_and_never_inherits_process_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    monkeypatch.setenv("GH_TOKEN", "ghp_secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws")
    cfg = _cfg()
    env = build_env(_spec(), cfg, _endpoint(cfg), proxy_token="tok")
    assert "GITHUB_TOKEN" not in env
    assert "GH_TOKEN" not in env
    assert "GH_CONFIG_DIR" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env


def test_env_sets_private_home_tmp_and_xdg():
    cfg = _cfg()
    env = build_env(_spec(), cfg, _endpoint(cfg))
    for key in ("HOME", "TMPDIR", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
        assert env[key].startswith("/")


# ── command construction ──────────────────────────────────────────────


def test_argv_is_shlex_quoted_into_the_shell_string():
    # RunCommandRequest.command is a string run via `bash -c`; a branch name or
    # path carrying shell metacharacters would otherwise be injection.
    body = build_command_request(
        ["echo", "a; rm -rf /"], cwd="/workspace", envs={}, wall_clock_limit=0, uid=1000, gid=1000
    )
    assert "'a; rm -rf /'" in body["command"]
    assert body["command"].count("rm -rf") == 1


def test_command_runs_in_the_foreground():
    # background:true emits no stdout/stderr SSE, merges the two streams, fires
    # execution_complete immediately and pipes stdin from /dev/null.
    body = build_command_request(
        ["ls"], cwd="/workspace", envs={}, wall_clock_limit=0, uid=1000, gid=1000
    )
    assert body["background"] is False


def test_command_refuses_root():
    with pytest.raises(SandboxError, match="root"):
        build_command_request(["ls"], cwd="/workspace", envs={}, wall_clock_limit=0, uid=0, gid=0)


# ── request shape ─────────────────────────────────────────────────────


def test_image_is_an_object_not_a_string():
    assert _create()["image"] == {"uri": _DIGEST}


def test_metadata_values_are_all_strings_including_generation():
    metadata = _create(generation=7)["metadata"]
    assert metadata["openace.generation"] == "7"
    assert all(isinstance(v, str) for v in metadata.values())
    assert metadata["openace.provider"] == "opensandbox"


def test_secure_access_is_always_true():
    assert _create()["secureAccess"] is True


def test_pool_mode_is_not_used_unless_fully_attested():
    assert "extensions" not in _create() or "poolRef" not in _create().get("extensions", {})


# ── state mapping ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "state,expected",
    [
        ("Pending", SandboxStatus.CREATED),
        ("Running", SandboxStatus.RUNNING),
        ("Resuming", SandboxStatus.RUNNING),
        ("Pausing", SandboxStatus.PAUSED),
        ("Paused", SandboxStatus.PAUSED),
        ("Stopping", SandboxStatus.STOPPED),
        ("Terminated", SandboxStatus.DESTROYED),
        ("Failed", SandboxStatus.ERROR),
        ("SomethingNew", SandboxStatus.ERROR),
        ("", SandboxStatus.ERROR),
    ],
)
def test_sandbox_state_mapping_is_total(state, expected):
    assert map_state(state) == expected


def test_validate_spec_for_endpoint_runs_the_shared_2022_gate():
    # A tier that attests pod hardening (so refusal 9 passes) but not egress
    # cannot supply NETWORK_EGRESS_POLICY, and the shared #2022 gate must be the
    # thing that catches it — not a bespoke check in this module.
    cfg = _cfg(attestations={k: v for k, v in _FULL_ATTESTATIONS.items() if k != "egress_enforced"})
    spec = _spec(required_capabilities=frozenset({SandboxCapability.NETWORK_EGRESS_POLICY}))
    with pytest.raises(CapabilityUnsupported):
        validate_spec_for_endpoint(spec, cfg, _endpoint(cfg), probes_passed=True)


def test_namespace_isolation_gate_engages_when_the_runtime_probe_failed():
    cfg = _cfg()
    spec = _spec(required_capabilities=frozenset({SandboxCapability.NAMESPACE_ISOLATION}))
    with pytest.raises(CapabilityUnsupported):
        validate_spec_for_endpoint(spec, cfg, _endpoint(cfg), probes_passed=False)


# ── PTY command construction (env delivery, spec §6.2) ────────────────


def test_pty_command_carries_the_env_because_pty_create_takes_none():
    # CreatePTYSessionRequest is {cwd, command} only, and pty_session.go sets
    # cmd.Env = os.Environ() with no merge — so the only way the agent's env
    # (including the per-run proxy token) reaches the process is the command
    # string, which buildPTYCommand runs as `bash -c "<command>"`.
    command = build_pty_command(
        ["claude", "--input-format", "stream-json"],
        env={"HOME": "/workspace/home", "OPENACE_PROXY_TOKEN": "tok"},
    )
    assert "export " in command
    assert "HOME=/workspace/home" in command
    assert command.rstrip().endswith("claude --input-format stream-json")
    assert "exec " in command


def test_pty_command_quotes_every_env_value():
    command = build_pty_command(["claude"], env={"EVIL": "a'; rm -rf /; echo '"})
    assert "rm -rf /" in command  # present, but quoted
    assert command.count("rm -rf") == 1
    assert "'a'\"'\"'; rm -rf /; echo '\"'\"''" in command or "'" in command


def test_pty_command_rejects_a_newline_bearing_env_value():
    # A newline would end the export statement and inject a second command that
    # no amount of quoting downstream would contain.
    with pytest.raises(SandboxError):
        build_pty_command(["claude"], env={"BAD": "a\nrm -rf /"})


def test_pty_command_rejects_an_invalid_env_var_name():
    with pytest.raises(SandboxError):
        build_pty_command(["claude"], env={"BAD NAME": "x"})


# ── the agent's LLM credential (spec §5.4) ────────────────────────────


def test_the_agent_receives_the_credential_variable_its_cli_actually_reads():
    """Without this the backend cannot complete a run at all.

    _build_agent_env mints a short-lived proxy token and hands it to the
    adapter, which returns it as ANTHROPIC_API_KEY — the only variable
    claude-code reads. A name-based allowlist that kept OPENACE_PROXY_TOKEN but
    dropped ANTHROPIC_API_KEY left the agent holding the credential under a
    name nothing consumes: it started, could not authenticate, and died. Every
    provider test passed, because none of them assembled the runner's env.
    """
    cfg = _cfg()
    # Exactly the shape _build_agent_env produces (agent_runner.py:1658-1665).
    runner_env = {
        "ANTHROPIC_API_KEY": "PROXYTOKEN-abc",
        "ANTHROPIC_BASE_URL": "https://proxy.open-ace.example/api/remote/llm-proxy",
        "OPENACE_PROXY_URL": "https://proxy.open-ace.example/api/remote/llm-proxy",
        "OPENACE_PROXY_TOKEN": "PROXYTOKEN-abc",
    }
    env = build_env(_spec(), cfg, _endpoint(cfg), proxy_token="PROXYTOKEN-abc", extra=runner_env)
    assert env["ANTHROPIC_API_KEY"] == "PROXYTOKEN-abc"


def test_a_dynamic_provider_env_key_carrying_the_token_is_forwarded():
    """Model providers invent env-var names at runtime (`envKeys`).

    A static name allowlist cannot enumerate them, which is why forwarding is
    decided on the VALUE being this run's proxy token.
    """
    cfg = _cfg()
    env = build_env(
        _spec(),
        cfg,
        _endpoint(cfg),
        proxy_token="PROXYTOKEN-abc",
        extra={"BAILIAN_CODING_PLAN_API_KEY": "PROXYTOKEN-abc"},
    )
    assert env["BAILIAN_CODING_PLAN_API_KEY"] == "PROXYTOKEN-abc"


def test_a_real_api_key_is_never_forwarded_under_a_credential_name():
    """The value-match is what keeps the allowlist's guarantee.

    A raw upstream key sitting in a credential-shaped variable is NOT this
    run's proxy token, so it does not travel — which is a stronger guarantee
    than the name allowlist gave.
    """
    cfg = _cfg()
    env = build_env(
        _spec(),
        cfg,
        _endpoint(cfg),
        proxy_token="PROXYTOKEN-abc",
        extra={"ANTHROPIC_API_KEY": "sk-ant-REAL-KEY", "OPENAI_API_KEY": "sk-REAL"},
    )
    assert "sk-ant-REAL-KEY" not in env.values()
    assert env.get("ANTHROPIC_API_KEY") != "sk-ant-REAL-KEY"
    assert "OPENAI_API_KEY" not in env


def test_no_credential_travels_when_proxy_setup_failed():
    """_build_agent_env fails closed; nothing here may re-open it."""
    cfg = _cfg()
    env = build_env(_spec(), cfg, _endpoint(cfg), extra={"ANTHROPIC_API_KEY": "whatever"})
    assert "ANTHROPIC_API_KEY" not in env


def test_a_proxy_url_the_egress_policy_would_block_is_refused():
    """Egress is deny-default; the proxy is the one host a run cannot work without.

    Both misconfigurations below produce the same symptom — the agent starts,
    every request hangs, the run dies with nothing pointing at the network
    policy. Refusing names the host and the setting to change.
    """
    from app.modules.workspace.autonomous.sandbox.opensandbox.policy import assert_proxy_reachable

    cfg = _cfg()
    endpoint = _endpoint(cfg)  # egress_allow_hosts = api.anthropic.com
    with pytest.raises(SandboxError, match="egress_allow_hosts"):
        assert_proxy_reachable({"OPENACE_PROXY_URL": "https://proxy.internal/x"}, endpoint)


def test_a_loopback_proxy_url_is_refused():
    """Inside the pod, localhost is the sandbox itself — not the control plane."""
    from app.modules.workspace.autonomous.sandbox.opensandbox.policy import assert_proxy_reachable

    cfg = _cfg()
    with pytest.raises(SandboxError, match="loopback"):
        assert_proxy_reachable({"ANTHROPIC_BASE_URL": "http://localhost:5000/api"}, _endpoint(cfg))


def test_an_allowlisted_proxy_url_passes():
    from app.modules.workspace.autonomous.sandbox.opensandbox.policy import assert_proxy_reachable

    cfg = _cfg()
    assert_proxy_reachable({"ANTHROPIC_BASE_URL": "https://api.anthropic.com/v1"}, _endpoint(cfg))
