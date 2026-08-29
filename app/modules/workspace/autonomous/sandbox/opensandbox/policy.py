"""Spec/policy translation and capability derivation for OpenSandbox (#2023).

This module is pure: given a :class:`SandboxSpec`, an :class:`AgentTaskPolicy`
and the resolved endpoint config, it produces the request dicts the provider
sends and the capability set the provider declares. Keeping it free of I/O is
what lets the fail-closed rules be tested exhaustively.

Two rules govern everything here.

**A capability is declared only when something actually enforces it.** The
mechanisms available *inside* the sandbox — a ``ulimit`` prefix on the command
string, the ``uid`` argument to ``POST /command`` — are all reachable by the
agent itself, because execd hands every command its own environment (including
``EXECD_ACCESS_TOKEN``) and accepts ``uid: 0``. So enforcement lives at the
pod/kernel layer and reaches this module as operator attestations.

**Declaring honestly is not the same as running safely.** Production specs
arrive with ``required_capabilities=frozenset()``, and
``implied_required_capabilities`` derives only ``NETWORK_EGRESS_POLICY``,
``NAMESPACE_ISOLATION`` and ``STORAGE_INODE_QUOTA`` from spec fields. Nothing
implies ``FILESYSTEM_ACL``, ``CPU_MEM_PIDS_TIME_QUOTA`` or
``CREDENTIAL_TOKEN_BINDING``, so a tier attesting no pod hardening would
correctly decline to declare them — and then run the agent as root on a
writable rootfs anyway. :func:`validate_spec_for_endpoint` therefore *refuses*
such a tier rather than treating those attestations as advisory.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from app.modules.workspace.autonomous.sandbox.opensandbox import config as config_mod
from app.modules.workspace.autonomous.sandbox.provider import (
    SandboxError,
    validate_spec_capabilities,
)
from app.modules.workspace.autonomous.sandbox.types import (
    NetworkEgressPolicy,
    RuntimeSpec,
    SandboxCapability,
    SandboxSpec,
    SandboxStatus,
)

if TYPE_CHECKING:  # pragma: no cover - annotations only
    from app.modules.workspace.autonomous.sandbox.opensandbox.config import (
        EndpointConfig,
        SandboxBackendConfig,
    )
    from app.modules.workspace.autonomous.task_isolation import AgentTaskPolicy

PROVIDER_NAME = "opensandbox"

# The metadata key carrying this control plane's identity. Shared with the
# provider's orphan sweep so the tag written and the tag filtered on cannot
# drift apart.
INSTALLATION_METADATA_KEY = "openace.installation"

# Upstream CreateSandboxRequest.timeout is seconds with minimum 60.
_MIN_TTL_SECONDS = 60

# Digest-pinned image reference. A tag can be repointed after the allowlist was
# reviewed, so a tag-only reference defeats the allowlist entirely.
_DIGEST_PINNED = re.compile(r"@sha256:[0-9a-f]{64}$")

# The workspace root every mount and every command must stay under.
_WORKSPACE_ROOT = "/workspace"
_AGENT_HOME = "/home/agent"

# The container's PID 1. It must create the HOME/TMP/XDG tree :func:`build_env`
# points the agent at: the pod runs with a read-only rootfs and /workspace is an
# empty emptyDir, so nothing else brings those directories into existence and
# pip, npm, pre-commit and Python's own tempfile all fail without them.
#
# This is why the create body sets `entrypoint` explicitly rather than relying on
# the image's own — a script baked into the image would be overridden here.
_ENTRYPOINT = [
    "/bin/sh",
    "-c",
    f"mkdir -p {_AGENT_HOME}/tmp {_AGENT_HOME}/.cache {_AGENT_HOME}/.config "
    f"{_AGENT_HOME}/.local/share {_WORKSPACE_ROOT} && exec tail -f /dev/null",
]

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Linux caps a single execve argument at MAX_ARG_STRLEN (128 KiB) regardless of
# ARG_MAX. Leave headroom for the shell wrapper upstream adds.
_MAX_COMMAND_BYTES = 120 * 1024

# Upstream's SandboxState -> our SandboxStatus. Unknown states map to ERROR
# rather than to a benign default: a state we do not recognise is not a state
# we can claim is healthy.
_STATE_MAP: dict[str, SandboxStatus] = {
    "Pending": SandboxStatus.CREATED,
    "Running": SandboxStatus.RUNNING,
    "Resuming": SandboxStatus.RUNNING,
    "Pausing": SandboxStatus.PAUSED,
    "Paused": SandboxStatus.PAUSED,
    "Stopping": SandboxStatus.STOPPED,
    "Terminated": SandboxStatus.DESTROYED,
    "Failed": SandboxStatus.ERROR,
}

# The only environment variables that ever reach the sandbox. Built as an
# allowlist rather than a denylist so a newly-introduced secret in the control
# plane's environment cannot leak by default.
# PATH is deliberately ABSENT. The passthrough loop runs after the defaults, so
# leaving it here let extra["PATH"] — the control plane's own PATH, prefixed
# with a host-only guard_bin directory — overwrite _DEFAULT_PATH. `exec claude`
# inside the container then resolved against nvm/homebrew directories that do
# not exist in the image, and /usr/local/bin (where an npm -g install lands)
# was not on it: exit 127, reported as an opaque crash. The container's own
# PATH is the only authority on where its CLI lives, which is what the argv0
# basename rewrite in _exec_agent_turn assumes.
_ENV_PASSTHROUGH = (
    "OPENACE_PROXY_URL",
    "OPENACE_PROXY_TOKEN",
    "OPENACE_MODEL",
    "ANTHROPIC_BASE_URL",
    "OPENAI_BASE_URL",
    "GEMINI_BASE_URL",
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
    "GIT_TERMINAL_PROMPT",
)

# Never forwarded even if a caller passes them: these are the control plane's
# write credentials, and commit/push stay control-plane side by design.
_ENV_NEVER = frozenset(
    {"GITHUB_TOKEN", "GH_TOKEN", "GH_CONFIG_DIR", "GITHUB_API_TOKEN", "GH_ENTERPRISE_TOKEN"}
)

_DEFAULT_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def derive_capabilities(
    endpoint: EndpointConfig, *, probes_passed: bool
) -> frozenset[SandboxCapability]:
    """Capabilities this endpoint can honestly claim.

    Computed from the resolved config, never a module constant — the ``#2082``
    lesson, where ``RemoteMachineProvider`` copied Legacy's capability set and
    enforced none of it.

    ``probes_passed`` gates ``NAMESPACE_ISOLATION`` and
    ``NETWORK_EGRESS_POLICY`` on the boot probes having confirmed the runtime
    kernel and the sidecar's enforcement mode. Upstream exposes no API that
    reports the effective secure runtime, so without the probe the runtime class
    is only an operator's word.
    """
    att = endpoint.attestations
    caps: set[SandboxCapability] = {SandboxCapability.PRIVATE_HOME_TMP_XDG}

    if probes_passed:
        caps.add(SandboxCapability.NAMESPACE_ISOLATION)
        if att.egress_enforced and att.egress_mode_dns_nft and att.metadata_cidr_blocked:
            # All three: a deny-default policy enforced in dns-only mode does
            # not stop a connection made to a bare IP, and the cluster
            # NetworkPolicy is what closes that.
            caps.add(SandboxCapability.NETWORK_EGRESS_POLICY)

    if (
        att.nonroot_enforced
        and att.readonly_rootfs
        and att.seccomp_runtime_default
        and att.dedicated_service_account
    ):
        caps.add(SandboxCapability.FILESYSTEM_ACL)

    if att.pod_pids_limit > 0:
        caps.add(SandboxCapability.CPU_MEM_PIDS_TIME_QUOTA)

    # CREDENTIAL_TOKEN_BINDING is NEVER granted by this backend.
    #
    # It means "this sandbox's credential is bound to this sandbox", and only
    # `secureAccess` provides that — which upstream honours solely under
    # gateway-mode ingress (see validate_spec_for_endpoint note 7). Under the
    # direct-ingress manifests shipped here every sandbox authenticates to execd
    # with the same static token, which any agent can read out of execd's
    # environment. That is a deployment-wide shared secret, the opposite of
    # per-sandbox binding, so the capability is unreachable rather than
    # conditional. Restoring it means implementing gateway ingress first.

    if att.inode_quota_enforced or att.ephemeral_storage_enforced:
        # A disjunction, because implied_required_capabilities demands this
        # capability for EITHER dimension. Gating on inode alone made the
        # ephemeral-storage path unreachable. The inode dimension specifically
        # is still refused in validate_spec_for_endpoint unless truly attested.
        caps.add(SandboxCapability.STORAGE_INODE_QUOTA)

    return frozenset(caps)


def synthesise_spec_fields(
    spec: SandboxSpec, cfg: SandboxBackendConfig, endpoint: EndpointConfig
) -> SandboxSpec:
    """Fill in the container dimensions a production spec leaves ``None``.

    ``agent_runner`` builds ``SandboxSpec(task_id, project_path, cli_tool,
    system_account, policy)`` — ``runtime``, ``network_egress`` and ``volumes``
    are always unset. Without this the fail-closed refusals below would key off
    fields that are never populated (dead code), and
    ``implied_required_capabilities`` would not demand
    ``NETWORK_EGRESS_POLICY``, letting a tier with no egress enforcement run
    unchecked. Synthesising first means the refusals evaluate the request that
    will actually be sent.
    """
    runtime = spec.runtime
    if runtime is None or not runtime.image:
        runtime = RuntimeSpec(
            image=endpoint.default_image, runtime=endpoint.runtime_class, toolchain=""
        )
    egress = spec.network_egress
    if egress is None:
        egress = NetworkEgressPolicy(mode="deny_all", allow_hosts=endpoint.egress_allow_hosts)
    return replace_spec(spec, runtime=runtime, network_egress=egress)


def replace_spec(spec: SandboxSpec, **changes: Any) -> SandboxSpec:
    """``dataclasses.replace`` for :class:`SandboxSpec` (frozen value object)."""
    import dataclasses

    return dataclasses.replace(spec, **changes)


def validate_spec_for_endpoint(
    spec: SandboxSpec,
    cfg: SandboxBackendConfig,
    endpoint: EndpointConfig,
    *,
    probes_passed: bool = True,
) -> None:
    """Fail-closed gate for the OpenSandbox backend.

    Runs the backend-specific refusals, then delegates to the shared ``#2022``
    :func:`validate_spec_capabilities`. Distinct from that function by design —
    do not collapse the two.
    """
    att = endpoint.attestations

    # 9. Pod hardening is enforced, not advisory. See the module docstring.
    missing = [
        name
        for name, present in (
            ("nonroot_enforced", att.nonroot_enforced),
            ("readonly_rootfs", att.readonly_rootfs),
            ("seccomp_runtime_default", att.seccomp_runtime_default),
            ("dedicated_service_account", att.dedicated_service_account),
            ("execd_token_required", att.execd_token_required),
            ("pod_pids_limit", att.pod_pids_limit > 0),
        )
        if not present
    ]
    if missing:
        raise SandboxError(
            f"endpoint {endpoint.tier!r} is missing required pod-hardening "
            f"attestations {missing}; refusing to run an agent without them"
        )

    # 7. secureAccess is NOT required, and deliberately so.
    #
    # The provider sets `"secureAccess": True` on every create, but upstream
    # honours it only for Kubernetes sandboxes when `[ingress] mode = "gateway"`
    # (server/configuration.md: "currently supported only for Kubernetes
    # sandboxes when ingress.mode = 'gateway'"; the OpenAPI adds "When omitted
    # or false, endpoints remain accessible without the additional access
    # token"). The manifests this repository ships run `mode = "direct"`, so
    # per-sandbox tokens are never minted and the flag has no effect.
    #
    # Making the attestation mandatory therefore demanded an operator promise
    # that the deployment cannot keep, and granted CREDENTIAL_TOKEN_BINDING off
    # it — a capability asserted with nothing enforcing it, which is exactly the
    # #2082 defect this package exists to avoid repeating. Requiring it is
    # dropped rather than faked.
    #
    # KNOWN LIMITATION, documented in docs/sandbox-backends.md: under direct
    # ingress every sandbox authenticates to execd with the same static
    # EXECD_ACCESS_TOKEN, and an agent can read that token out of execd's
    # inherited environment. A compromised agent can therefore reach a peer
    # sandbox's execd. Closing it needs gateway-mode ingress, which is
    # deployment work outside this backend.

    egress = spec.network_egress
    if egress is not None:
        # 1. Upstream's NetworkRule.target is documented as "FQDN or wildcard
        # domain ... IP/CIDR not yet supported in the egress MVP". Silently
        # dropping a CIDR allowlist would run a restrictive-looking spec with
        # those rules simply absent.
        if egress.allow_cidrs:
            raise SandboxError(
                "network_egress.allow_cidrs cannot be honored: upstream's egress "
                "MVP supports FQDN/wildcard targets only, so a CIDR rule would be "
                "silently dropped"
            )
        # 2. The issue mandates default-deny egress.
        if egress.mode == "unrestricted":
            raise SandboxError("network_egress.mode 'unrestricted' is refused by this backend")

    # 3. No host path is ever exposed; the trusted Git common-dir must be
    # unreachable from the sandbox.
    for volume in spec.volumes:
        if volume.kind == "host":
            raise SandboxError(f"volume {volume.name!r}: host-backed volumes are refused")
        if not _under_workspace(volume.mount_path):
            raise SandboxError(
                f"volume {volume.name!r}: mount_path {volume.mount_path!r} is outside "
                f"{_WORKSPACE_ROOT}"
            )

    # 4. Image allowlist + digest pinning.
    image = spec.runtime.image if spec.runtime else ""
    if image:
        if not _DIGEST_PINNED.search(image):
            raise SandboxError(f"image {image!r} is not digest-pinned")
        if image not in cfg.image_allowlist:
            raise SandboxError(f"image {image!r} is not in the configured image_allowlist")

    policy = spec.policy
    if policy is not None:
        # 8. The ephemeral-storage attestation bounds bytes, not inode count.
        if policy.inode_limit > 0 and not att.inode_quota_enforced:
            raise SandboxError(
                "policy.inode_limit requires the inode_quota_enforced attestation; "
                "an ephemeral-storage limit is eviction-polled and has no inode dimension"
            )
        if policy.pids_max > 0 and policy.pids_max > att.pod_pids_limit:
            raise SandboxError(
                f"policy.pids_max={policy.pids_max} exceeds the attested pod "
                f"podPidsLimit={att.pod_pids_limit}"
            )

    # 6. Finally the shared #2022 gate, over explicit + field-implied caps.
    validate_spec_capabilities(derive_capabilities(endpoint, probes_passed=probes_passed), spec)


def build_network_policy(spec: SandboxSpec, endpoint: EndpointConfig) -> dict:
    """Deny-default egress with the operator allowlist, optionally narrowed.

    A spec may intersect the operator's allowlist but never extend it: the
    operator decides what this deployment may reach, and a task may only ask
    for less.
    """
    allowed = tuple(endpoint.egress_allow_hosts)
    requested = spec.network_egress.allow_hosts if spec.network_egress else ()
    if requested:
        allowed = tuple(host for host in allowed if host in set(requested))
    return {
        "defaultAction": "deny",
        "egress": [{"action": "allow", "target": host} for host in allowed],
    }


def assert_proxy_reachable(env: Mapping[str, str], endpoint: EndpointConfig) -> None:
    """Refuse a turn whose LLM proxy the sandbox's egress policy would block.

    The agent reaches Anthropic/OpenAI/etc. only through Open ACE's own proxy,
    so the proxy host is the one destination a run cannot work without. Egress
    is deny-default, and the proxy URL comes from the control plane's
    ``server_url`` — which defaults to ``http://localhost:<port>``, a name that
    inside the sandbox pod resolves to the sandbox itself.

    Both misconfigurations produce the same symptom: the agent starts, every
    request hangs or is refused, and the run dies with no indication that the
    network policy was the cause. Refusing here names the host and the setting
    that has to change.
    """
    for key in ("OPENACE_PROXY_URL", "ANTHROPIC_BASE_URL", "OPENAI_BASE_URL", "GEMINI_BASE_URL"):
        raw = str(env.get(key) or "")
        if not raw:
            continue
        host = (urlparse(raw).hostname or "").lower()
        if not host:
            continue
        if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):  # noqa: S104 - comparison
            raise SandboxError(
                f"{key}={raw!r} points at the control plane's loopback address; inside the "
                "sandbox pod that resolves to the sandbox itself. Set the control plane's "
                "server_url to an address reachable from the cluster."
            )
        if not any(
            config_mod.host_matches(host, pattern) for pattern in endpoint.egress_allow_hosts
        ):
            raise SandboxError(
                f"{key} host {host!r} is not in endpoint {endpoint.tier!r} egress_allow_hosts "
                f"{sorted(endpoint.egress_allow_hosts)}; egress is deny-default, so the agent "
                "could not reach its own LLM proxy. Add the host to egress_allow_hosts."
            )


def build_resource_limits(
    policy: AgentTaskPolicy | None, cfg: SandboxBackendConfig, endpoint: EndpointConfig
) -> dict[str, str]:
    """Translate the #2020 policy into Kubernetes resource quantities.

    The policy is authoritative; ``resource_defaults`` fills only the dimensions
    it leaves at ``0``. Recording the *applied* values rather than the requested
    ones is what keeps the effective-policy snapshot honest.
    """
    defaults = cfg.resource_defaults
    limits: dict[str, str] = {}

    memory = getattr(policy, "memory_max_bytes", 0) if policy else 0
    limits["memory"] = str(memory) if memory > 0 else str(defaults.get("memory", "4Gi"))

    cpu = _cpu_from_cgroup(getattr(policy, "cpu_max", "") if policy else "")
    limits["cpu"] = cpu or str(defaults.get("cpu", "2"))

    if endpoint.attestations.ephemeral_storage_enforced:
        storage = getattr(policy, "ephemeral_storage_limit", 0) if policy else 0
        value = str(storage) if storage > 0 else str(defaults.get("ephemeral-storage", ""))
        if value:
            limits["ephemeral-storage"] = value
    return limits


def build_env(
    spec: SandboxSpec,
    cfg: SandboxBackendConfig,
    endpoint: EndpointConfig,
    *,
    proxy_token: str = "",
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Construct the agent's environment from scratch.

    Never ``dict(os.environ)``: the control plane's environment holds the
    GitHub write credentials that must not exist inside the sandbox, and an
    allowlist means a secret added to the control plane later cannot leak by
    default.
    """
    # Deliberately OUTSIDE the workspace. With HOME under /workspace the repo
    # synthesis (`git add -A` at /workspace) stages the agent's entire home tree
    # into the initial commit, and every later ~/.cache write — pip wheels, npm,
    # pre-commit environments, easily tens of thousands of files — shows up as a
    # repo modification and blows through the ChangeSet file/byte limits.
    home = _AGENT_HOME
    env: dict[str, str] = {
        "HOME": home,
        "TMPDIR": f"{home}/tmp",
        "XDG_CACHE_HOME": f"{home}/.cache",
        "XDG_CONFIG_HOME": f"{home}/.config",
        "XDG_DATA_HOME": f"{home}/.local/share",
        "PATH": _DEFAULT_PATH,
        "GIT_TERMINAL_PROMPT": "0",
    }
    for key, value in (extra or {}).items():
        if key in _ENV_NEVER:
            continue
        if key in _ENV_PASSTHROUGH:
            env[key] = str(value)
            continue
        # Credential slots, forwarded on VALUE rather than name.
        #
        # The agent authenticates with whatever variable its CLI reads —
        # ANTHROPIC_API_KEY for claude-code, OPENAI_API_KEY / GEMINI_API_KEY /
        # BAILIAN_CODING_PLAN_API_KEY elsewhere, plus any name a model
        # provider's `envKeys` config invents at runtime. A name allowlist
        # cannot cover the dynamic ones, and omitting them entirely is what
        # left the agent holding OPENACE_PROXY_TOKEN under a name nothing reads
        # — it started, could not authenticate, and died.
        #
        # Matching on the value keeps the allowlist's guarantee intact and in
        # fact tightens it: this forwards a variable only when it carries THIS
        # run's short-lived proxy token, so a raw upstream API key present in
        # the control plane's environment can never be forwarded under any
        # name. _build_agent_env fails closed when proxy setup fails, so there
        # is no path where these slots hold a real key.
        if proxy_token and str(value) == proxy_token:
            env[key] = str(value)
    if proxy_token:
        env["OPENACE_PROXY_TOKEN"] = proxy_token
    return env


def build_pty_command(command: Sequence[str], *, env: Mapping[str, str]) -> str:
    """Build the ``bash -c`` string that starts the agent with its environment.

    ``CreatePTYSessionRequest`` carries only ``{cwd, command}`` — no ``envs`` —
    and ``pty_session.go`` starts the shell with ``cmd.Env = os.Environ()`` and
    no merge. The command string is therefore the only channel through which the
    agent's environment, including the short-lived per-run proxy token, can
    reach the process. ``buildPTYCommand`` runs it as ``bash -c "<command>"``.

    Every name is validated and every value is ``shlex.quote``d, argv included.

    A value containing a newline is refused rather than quoted. ``shlex.quote``
    would in fact handle it correctly — bash keeps an embedded newline inside
    single quotes — so this is a policy choice, not a quoting necessity: no
    legitimate agent environment value contains a newline, and refusing keeps
    the assembled command auditable as a single line.

    The assembled string is one ``execve`` argument, capped by Linux
    ``MAX_ARG_STRLEN`` (128 KiB) regardless of ``ARG_MAX``. Exceeding it fails
    with a bare ``E2BIG`` that is very hard to diagnose, so it is refused here
    with a reason instead.
    """
    exports: list[str] = []
    for name, value in env.items():
        if value is None or str(value) == "":
            # An empty export is not the same as an unset variable, and some CLI
            # adapters distinguish them (an empty ANTHROPIC_API_KEY is not "no
            # key"). Omit rather than assert emptiness.
            continue
        if not _ENV_NAME.match(str(name)):
            raise SandboxError(f"invalid environment variable name {name!r}")
        text = str(value)
        if "\n" in text or "\r" in text:
            raise SandboxError(
                f"environment value for {name!r} contains a newline; refusing to "
                "build a shell command around it"
            )
        exports.append(f"export {name}={shlex.quote(text)}")
    argv = " ".join(shlex.quote(part) for part in command)
    prefix = "; ".join(exports)
    assembled = f"{prefix}; exec {argv}" if prefix else f"exec {argv}"
    if len(assembled.encode("utf-8")) > _MAX_COMMAND_BYTES:
        raise SandboxError(
            f"assembled PTY command is {len(assembled)} bytes, over the "
            f"{_MAX_COMMAND_BYTES}-byte limit (Linux MAX_ARG_STRLEN); trim the environment"
        )
    return assembled


def build_command_request(
    command: Sequence[str],
    *,
    cwd: str,
    envs: Mapping[str, str],
    wall_clock_limit: int,
    uid: int,
    gid: int,
) -> dict:
    """Build a ``POST /command`` body for a discrete (non-agent) command.

    Foreground, always. ``background: true`` fires ``execution_complete``
    immediately after launch, merges stdout and stderr into one descriptor,
    emits no stdout/stderr SSE events at all, and pipes stdin from
    ``/dev/null`` — none of which the evidence contract can work with.
    """
    if uid == 0 or gid == 0:
        raise SandboxError("refusing to exec as root inside the sandbox")
    body: dict[str, Any] = {
        "command": " ".join(shlex.quote(part) for part in command),
        "cwd": cwd,
        "background": False,
        "uid": uid,
        "gid": gid,
        "envs": dict(envs),
    }
    if wall_clock_limit > 0:
        body["timeout"] = wall_clock_limit * 1000
    # Otherwise the key is OMITTED: upstream's contract is "if omitted, the
    # server will not enforce any timeout", and sending 0 is not omitting.
    return body


def build_create_request(
    spec: SandboxSpec,
    cfg: SandboxBackendConfig,
    endpoint: EndpointConfig,
    *,
    generation: int,
    tenant: str | None = None,
    probes_passed: bool = True,
) -> dict:
    """Build the ``POST /v1/sandboxes`` body, refusing anything unenforceable."""
    spec = synthesise_spec_fields(spec, cfg, endpoint)
    validate_spec_for_endpoint(spec, cfg, endpoint, probes_passed=probes_passed)

    policy = spec.policy
    wall_clock = getattr(policy, "wall_clock_limit", 0) if policy else 0
    ttl = (
        max(wall_clock, cfg.sandbox_ttl_seconds, _MIN_TTL_SECONDS)
        if wall_clock > 0
        else max(cfg.sandbox_ttl_seconds, _MIN_TTL_SECONDS)
    )

    return {
        # image is an ImageSpec object, not a bare string.
        "image": {"uri": spec.runtime.image if spec.runtime else endpoint.default_image},
        "entrypoint": _ENTRYPOINT,
        "resourceLimits": build_resource_limits(policy, cfg, endpoint),
        "networkPolicy": build_network_policy(spec, endpoint),
        "timeout": ttl,
        # Without this, sandbox endpoints are reachable with no access token.
        "secureAccess": True,
        "env": build_env(spec, cfg, endpoint),
        # metadata values must all be strings upstream.
        "metadata": {
            "openace.provider": PROVIDER_NAME,
            # WHICH Open ACE. Reconciliation destroys every sandbox carrying our
            # provider tag that no local workflow row claims; on a lifecycle
            # server shared by two installations that filter alone is mutual
            # destruction. Required by parse_backend_config, so it is never "".
            INSTALLATION_METADATA_KEY: cfg.installation_id,
            "openace.task_id": str(spec.task_id),
            "openace.tenant": str(tenant or ""),
            "openace.generation": str(generation),
        },
    }


def map_state(state: str) -> SandboxStatus:
    """Map an upstream ``SandboxState`` to a contract :class:`SandboxStatus`."""
    return _STATE_MAP.get(state, SandboxStatus.ERROR)


# ── helpers ───────────────────────────────────────────────────────────


def _cpu_from_cgroup(cpu_max: str) -> str:
    """Convert a cgroup-v2 ``cpu.max`` value to a Kubernetes CPU quantity.

    ``AgentTaskPolicy.cpu_max`` is cgroup-v2 syntax — ``"<quota_us> <period_us>"``
    (``scripts/setup-cgroup-v2.sh`` builds it as ``"${CPU_CORES}00000 100000"``),
    while ``resourceLimits["cpu"]`` wants millicores. ``"max"`` means no limit,
    which has no Kubernetes equivalent, so the caller falls back to the
    configured default.
    """
    parts = (cpu_max or "").split()
    if len(parts) != 2 or parts[0] == "max":
        return ""
    try:
        quota, period = int(parts[0]), int(parts[1])
    except ValueError:
        return ""
    if period <= 0 or quota <= 0:
        return ""
    return f"{round(quota / period * 1000)}m"


def _under_workspace(path: str) -> bool:
    normalized = (path or "").rstrip("/")
    return normalized == _WORKSPACE_ROOT or normalized.startswith(_WORKSPACE_ROOT + "/")
