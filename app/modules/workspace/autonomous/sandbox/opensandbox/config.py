"""Backend configuration for the OpenSandbox provider (Issue #2023).

Upstream configures the secure container runtime at the *server* level —
``[secure_runtime] k8s_runtime_class`` in ``sandbox.toml``, with the guide
stating "All sandboxes on that server transparently use the configured runtime.
SDK users and API callers require no code changes". A caller therefore cannot
ask for gVisor on one request and Kata on the next against one server, so an
isolation *tier* selects a separately configured **endpoint**.

Everything the provider cannot observe through the API is an operator
*attestation* (see the package docstring for why in-band enforcement cannot be
trusted). Validation here is fail-closed throughout: a malformed file raises
rather than falling back to defaults, and a tier with no endpoint raises rather
than degrading to a weaker one.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.modules.workspace.autonomous.sandbox.provider import SandboxError

DEFAULT_BACKEND_CONFIG_PATH = "/etc/openace/sandbox-backends.json"
USER_BACKEND_CONFIG_PATH = os.path.expanduser("~/.open-ace/sandbox-backends.json")
BACKEND_CONFIG_ENV = "OPENACE_SANDBOX_BACKENDS"

# Upstream ``CreateSandboxRequest.timeout`` is seconds with ``minimum: 60``.
MIN_SANDBOX_TTL_SECONDS = 60

# A digest-pinned image reference: ``<name>@sha256:<64 hex>``. A tag-only
# reference is refused because a tag can be repointed after the allowlist was
# reviewed, which would defeat the allowlist entirely.
_DIGEST_PINNED = re.compile(r"@sha256:[0-9a-f]{64}$")

# Hostnames that resolve to cloud metadata services. Upstream's egress rules are
# name-based, so these must never appear in an allowlist.
_METADATA_HOSTS = frozenset(
    {"metadata", "metadata.google.internal", "instance-data", "metadata.goog"}
)

_HOSTNAME_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$", re.IGNORECASE)

# Metadata values travel to upstream as plain strings; keep the installation tag
# to a conservative, label-safe alphabet so it cannot inject separators into the
# metadata filter that reconciliation queries with.
_INSTALLATION_ID = re.compile(r"^[A-Za-z0-9._-]{1,63}$")


class SandboxConfigError(SandboxError):
    """Backend configuration is missing, malformed, or internally inconsistent.

    A subclass of :class:`SandboxError` so callers that catch the sandbox
    family (including the fail-closed paths in the provider) catch config
    problems too rather than letting them escape as a generic exception.
    """


@dataclass(frozen=True)
class ChangesetLimits:
    """Control-plane bounds applied to a ChangeSet before it touches the worktree."""

    # 2000 rejected open-ace itself (2274 tracked files) — and since a failed
    # apply now fails the run rather than degrading, the project's own primary
    # use case hit `too_many_files` on the default. Sized for a large working
    # tree; the byte limits below remain the real ceiling.
    max_files: int = 20000
    max_file_bytes: int = 10 * 1024 * 1024
    max_total_bytes: int = 100 * 1024 * 1024


@dataclass(frozen=True)
class Attestations:
    """Operator promises about properties the provider cannot observe.

    Each field corresponds to something set in the Kubernetes pod template, the
    kubelet, the cluster ``NetworkPolicy``, or the OpenSandbox server config —
    none of which is readable through the sandbox API. ``policy.derive_capabilities``
    turns these into the capability set, so an absent attestation removes a
    capability and makes any spec requiring it fail closed.

    Defaults are all "not attested" on purpose: a config that forgets a key must
    lose the capability, never gain it.
    """

    egress_enforced: bool = False
    egress_mode_dns_nft: bool = False
    metadata_cidr_blocked: bool = False
    execd_token_required: bool = False
    # secure_access_required is GONE, not merely optional. Upstream honours
    # `secureAccess` only when `[ingress] mode = "gateway"`, and the manifests
    # here ship "direct" — so the attestation could never be true. Leaving the
    # key accepted let an operator assert it (copied from an older config, or
    # reading the name as aspirational) and be granted CREDENTIAL_TOKEN_BINDING
    # with nothing enforcing it: the #2082 defect, one level down. Removing the
    # field makes _parse_attestations reject the key as unknown, which is loud.
    nonroot_enforced: bool = False
    readonly_rootfs: bool = False
    seccomp_runtime_default: bool = False
    dedicated_service_account: bool = False
    pod_pids_limit: int = 0
    ephemeral_storage_enforced: bool = False
    # Off by default and deliberately hard to turn on: ``ulimit -f`` caps a
    # single file's size and a Kubernetes ``ephemeral-storage`` limit is enforced
    # by kubelet eviction polling with no inode dimension, so neither is an inode
    # quota. Declaring the capability on those would write
    # ``"enforced": {"inode": true}`` to the workflow row via
    # ``build_effective_policy`` — a lie in the database.
    inode_quota_enforced: bool = False


@dataclass(frozen=True)
class PoolConfig:
    """Warm-pool settings, all of which must be attested before pool mode is used.

    Upstream rejects ``image``, ``resourceLimits``, ``networkPolicy`` and
    ``volumes`` alongside ``extensions.poolRef``, so in pool mode every one of
    those comes from the Pool CRD template — which the provider cannot read
    (there is no pool-inspection endpoint). ``RecycleStrategy.Type`` also allows
    ``Noop``, which hands the previous tenant's pod (and its ``/workspace`` and
    ``HOME``) to the next allocation unchanged.
    """

    pool_ref: str = ""
    egress_preapplied: bool = False
    recycle_delete: bool = False
    image_digest: str = ""

    def usable(self) -> bool:
        """True only when every guarantee pool mode bypasses has been attested."""
        return bool(
            self.pool_ref
            and self.egress_preapplied
            and self.recycle_delete
            and _DIGEST_PINNED.search(self.image_digest)
        )


@dataclass(frozen=True)
class RolloutConfig:
    """Which tenants and projects the OpenSandbox backend is switched on for.

    Without this, the only switch is whether the config file exists at all —
    which makes the backend all-or-nothing per deployment and leaves no way to
    run one tenant on it while everyone else stays on Legacy. The issue asks for
    a gradual rollout by tenant and project, so ``allowlist`` mode is what makes
    that possible.

    ``all`` is the default because a config file that says nothing about rollout
    most plausibly means "use this backend", and because it is what the backend
    did before this block existed.
    """

    mode: str = "all"  # "all" | "allowlist"
    tenants: frozenset[str] = field(default_factory=frozenset)
    projects: frozenset[str] = field(default_factory=frozenset)

    def includes(self, *, tenant: str | None, project_path: str | None) -> bool:
        """Whether this task should run on the OpenSandbox backend at all."""
        if self.mode != "allowlist":
            return True
        if tenant is not None and str(tenant) in self.tenants:
            return True
        return bool(project_path) and str(project_path) in self.projects


@dataclass(frozen=True)
class EndpointConfig:
    """One OpenSandbox server, tagged with the runtime class it was configured for.

    ``runtime_class`` is a *declaration* of what the operator set server-side, not
    a request field — upstream exposes no API that reports the effective runtime.
    It is cross-checked at runtime by the provider's boot probe rather than taken
    on trust.
    """

    tier: str
    base_url: str
    api_key_env: str
    runtime_class: str
    default_image: str
    execd_port: int = 44772
    # Egress sidecar is a SEPARATE service on its own port with its own auth
    # header — GET /policy against execd's port is a 404.
    egress_port: int = 18080
    # execd requires X-EXECD-ACCESS-TOKEN whenever the server sets one, and
    # refusal 9 makes execd_token_required mandatory, so every usable tier needs
    # this. Read from the environment, never stored in the JSON.
    execd_token_env: str = ""
    # execd may run as root; files uploaded root-owned under a restrictive mode
    # would leave the non-root agent unable to edit its own workspace.
    runtime_user: str = "openace"
    runtime_group: str = "openace"
    # uid/gid for POST /command. Never 0: refusing root here is defence in depth
    # only — the agent can reach execd itself and ask for uid 0 — but a control
    # plane that asks for root would be a bug worth failing on.
    exec_uid: int = 1000
    exec_gid: int = 1000
    execd_endpoint_host_allowlist: tuple[str, ...] = ()
    egress_allow_hosts: tuple[str, ...] = ()
    attestations: Attestations = field(default_factory=Attestations)
    pool: PoolConfig = field(default_factory=PoolConfig)

    def execd_token(self) -> str:
        """Read the execd access token, or "" when the tier declares none."""
        if not self.execd_token_env:
            return ""
        value = os.environ.get(self.execd_token_env, "")
        if not value:
            raise SandboxConfigError(
                f"endpoint {self.tier!r}: execd token env var "
                f"{self.execd_token_env!r} is unset or empty"
            )
        return value

    def api_key(self) -> str:
        """Read the API key from the configured environment variable.

        Keys are never stored in the JSON file. An unset or empty variable is a
        configuration error, not an anonymous request: upstream requires the
        ``OPEN-SANDBOX-API-KEY`` header on every lifecycle call.
        """
        value = os.environ.get(self.api_key_env, "")
        if not value:
            raise SandboxConfigError(
                f"endpoint {self.tier!r}: API key env var {self.api_key_env!r} is unset or empty"
            )
        return value


@dataclass(frozen=True)
class SandboxBackendConfig:
    """The whole backend configuration, already validated."""

    default_tier: str
    endpoints: Mapping[str, EndpointConfig]
    # This control plane's identity, stamped on every sandbox's metadata and
    # REQUIRED (parse refuses an empty one). Orphan reconciliation destroys
    # every sandbox it does not recognise, so on a lifecycle server shared by
    # two Open ACE installations a provider-only filter makes each one classify
    # the other's live sandboxes as unclaimed and delete them mid-run. The tag
    # must be stable across restarts and distinct per installation.
    installation_id: str = ""
    tenant_tiers: Mapping[str, str] = field(default_factory=dict)
    project_tiers: Mapping[str, str] = field(default_factory=dict)
    # Tenants for which Legacy is not an acceptable answer. This is the sole
    # input to isolation_tier.requires_production_isolation; without it that
    # predicate had no defined source and acceptance criterion 12 ("production
    # required policy must not silently fall back") rested on nothing.
    production_required_tenants: frozenset[str] = field(default_factory=frozenset)
    image_allowlist: frozenset[str] = field(default_factory=frozenset)
    image_signer_identity: str = ""
    resource_defaults: Mapping[str, str] = field(default_factory=dict)
    sandbox_ttl_seconds: int = 3600
    changeset_limits: ChangesetLimits = field(default_factory=ChangesetLimits)
    rollout: RolloutConfig = field(default_factory=RolloutConfig)

    def rollout_includes(self, *, tenant: str | int | None, project_path: str | None) -> bool:
        """Whether this task is in the OpenSandbox rollout.

        Separate from :meth:`requires_production_isolation`: rollout answers
        "may this task use the backend", the other answers "must it". A tenant
        that is required but not rolled out is a contradiction, and
        :func:`parse_backend_config` rejects that combination outright rather
        than letting one setting silently win.
        """
        tenant_key = None if tenant is None else str(tenant)
        return self.rollout.includes(tenant=tenant_key, project_path=project_path)

    def requires_production_isolation(self, tenant: str | None) -> bool:
        """True when this tenant may not fall back to a weaker backend.

        Tenant keys are the decimal string of the integer ``tenant_id`` this
        repository actually carries (``CommandExecutionEvidence.tenant_id``);
        there is no tenant-name→id mapping anywhere in the codebase, so a slug
        key would have been something nothing could supply.
        """
        return bool(tenant) and str(tenant) in self.production_required_tenants

    def tier_for(self, *, tenant: str | None, project_path: str | None) -> str:
        """Resolve the isolation tier: project override, then tenant, then default."""
        if project_path and str(project_path) in self.project_tiers:
            return self.project_tiers[str(project_path)]
        # str() here is load-bearing, not defensive: the runner holds an integer
        # tenant_id, config keys are strings, and requires_production_isolation
        # already coerces. Without the same coercion a production-required
        # tenant would be correctly flagged and then routed to the DEFAULT tier.
        if tenant and str(tenant) in self.tenant_tiers:
            return self.tenant_tiers[str(tenant)]
        return self.default_tier

    def endpoint_for(self, *, tenant: str | None, project_path: str | None) -> EndpointConfig:
        """Resolve the endpoint for a task, or raise.

        Raising is the point: a tenant mapped to a ``kata`` tier on a deployment
        with no Kata endpoint must not quietly receive the gVisor one. That is
        the acceptance item "production required policy cannot silently fall
        back".
        """
        tier = self.tier_for(tenant=tenant, project_path=project_path)
        endpoint = self.endpoints.get(tier)
        if endpoint is None:
            raise SandboxConfigError(
                f"isolation tier {tier!r} has no configured endpoint "
                f"(configured tiers: {sorted(self.endpoints)})"
            )
        return endpoint


def candidate_backend_config_paths(explicit: str | None = None) -> tuple[str, ...]:
    """Return config-path candidates in precedence order.

    Mirrors :func:`task_isolation.candidate_agent_task_policy_paths`:

    1. an explicit caller path or ``$OPENACE_SANDBOX_BACKENDS``
    2. the system config ``/etc/openace/sandbox-backends.json``
    3. the user fallback ``~/.open-ace/sandbox-backends.json``
    """
    candidates = [
        explicit,
        os.environ.get(BACKEND_CONFIG_ENV),
        DEFAULT_BACKEND_CONFIG_PATH,
        USER_BACKEND_CONFIG_PATH,
    ]
    ordered: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in ordered:
            ordered.append(candidate)
    return tuple(ordered)


def resolve_backend_config_path(explicit: str | None = None) -> str | None:
    """Return the first existing candidate path, else ``None``.

    An explicitly requested path — a caller argument or
    ``$OPENACE_SANDBOX_BACKENDS`` — that does not exist **raises**. Searching on
    would mean a typo'd env var or a config lost during a deploy silently
    resolves to the user-level file, or to ``None``; and ``None`` means "no
    OpenSandbox backend", which hands every non-required tenant back to Legacy
    with no signal at all.
    """
    requested = explicit or os.environ.get(BACKEND_CONFIG_ENV)
    if requested:
        if Path(requested).is_file():
            return requested
        raise SandboxConfigError(
            f"sandbox backend config {requested!r} was explicitly requested but does not exist"
        )
    for candidate in (DEFAULT_BACKEND_CONFIG_PATH, USER_BACKEND_CONFIG_PATH):
        if Path(candidate).is_file():
            return candidate
    return None


def load_backend_config(explicit: str | None = None) -> SandboxBackendConfig | None:
    """Load and validate the backend config, or ``None`` when none is configured.

    ``None`` means "this deployment has no OpenSandbox backend", which is a
    legitimate state — the local path then behaves exactly as it did before
    #2023. A file that *exists* but cannot be read or parsed is an error, never
    silently treated as absent.
    """
    path = resolve_backend_config_path(explicit)
    if path is None:
        return None
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise SandboxConfigError(f"cannot read sandbox backend config {path!r}: {exc}") from exc
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise SandboxConfigError(f"malformed sandbox backend config {path!r}: {exc}") from exc
    if not isinstance(raw, dict):
        raise SandboxConfigError(f"sandbox backend config {path!r} must be a JSON object")
    return parse_backend_config(raw)


def parse_backend_config(raw: Mapping[str, Any]) -> SandboxBackendConfig:
    """Validate a raw config mapping into a :class:`SandboxBackendConfig`."""
    image_allowlist = frozenset(_str_list(raw, "image_allowlist"))
    for image in sorted(image_allowlist):
        _require_digest_pinned(image, "image_allowlist entry")

    ttl = _int_or_raise(raw.get("sandbox_ttl_seconds", 3600), "sandbox_ttl_seconds")
    if ttl < MIN_SANDBOX_TTL_SECONDS:
        raise SandboxConfigError(
            f"sandbox_ttl_seconds must be >= {MIN_SANDBOX_TTL_SECONDS} "
            f"(upstream CreateSandboxRequest.timeout minimum), got {ttl}"
        )

    installation_id = str(raw.get("installation_id") or "").strip()
    if not installation_id:
        raise SandboxConfigError(
            "installation_id is required: it is stamped on every sandbox's metadata "
            "and is what stops this deployment's orphan reconciliation from destroying "
            "another Open ACE installation's live sandboxes on a shared lifecycle server"
        )
    if not _INSTALLATION_ID.match(installation_id):
        raise SandboxConfigError(
            f"installation_id {installation_id!r} must be 1-63 characters of "
            "[A-Za-z0-9._-] (it travels as an upstream metadata value)"
        )

    raw_endpoints = raw.get("endpoints") or {}
    if not isinstance(raw_endpoints, dict) or not raw_endpoints:
        raise SandboxConfigError("endpoints must be a non-empty object")
    endpoints = {
        tier: _parse_endpoint(tier, body, image_allowlist) for tier, body in raw_endpoints.items()
    }

    default_tier = str(raw.get("default_tier") or "").strip()
    if default_tier not in endpoints:
        raise SandboxConfigError(
            f"default_tier {default_tier!r} has no entry in endpoints "
            f"(configured tiers: {sorted(endpoints)})"
        )

    tenant_tiers = {str(k): str(v) for k, v in (raw.get("tenant_tiers") or {}).items()}
    project_tiers = {str(k): str(v) for k, v in (raw.get("project_tiers") or {}).items()}
    # A tier name with no endpoint parses cleanly and then raises at the first
    # create — for the very tenants an operator singled out, which are the ones
    # most likely to also be in production_required_tenants and so have no
    # fallback. Catch it while the config is being read.
    dangling = sorted(
        {
            f"{source}[{key!r}] -> {tier!r}"
            for source, mapping in (
                ("tenant_tiers", tenant_tiers),
                ("project_tiers", project_tiers),
            )
            for key, tier in mapping.items()
            if tier not in endpoints
        }
    )
    if dangling:
        raise SandboxConfigError(
            f"tier mappings reference tiers with no endpoint: {dangling}; "
            f"configured tiers are {sorted(endpoints)}"
        )

    rollout = _parse_rollout(raw.get("rollout") or {})
    required_tenants = frozenset(_str_list(raw, "production_required_tenants"))
    # A tenant that must use the backend but is excluded from the rollout is an
    # incoherent pair, and silently letting either one win would be the exact
    # kind of quiet downgrade this design exists to prevent.
    excluded_but_required = sorted(
        tenant
        for tenant in required_tenants
        if not rollout.includes(tenant=tenant, project_path=None)
    )
    if excluded_but_required:
        raise SandboxConfigError(
            f"tenants {excluded_but_required} are in production_required_tenants but are "
            "not covered by the rollout allowlist; add them to rollout.tenants or drop "
            "them from production_required_tenants"
        )

    return SandboxBackendConfig(
        default_tier=default_tier,
        endpoints=endpoints,
        installation_id=installation_id,
        tenant_tiers=tenant_tiers,
        project_tiers=project_tiers,
        production_required_tenants=required_tenants,
        rollout=rollout,
        image_allowlist=image_allowlist,
        image_signer_identity=str(raw.get("image_signer_identity") or ""),
        resource_defaults={str(k): str(v) for k, v in (raw.get("resource_defaults") or {}).items()},
        sandbox_ttl_seconds=ttl,
        changeset_limits=_parse_changeset_limits(raw.get("changeset_limits") or {}),
    )


# ── helpers ───────────────────────────────────────────────────────────


def _parse_endpoint(tier: str, body: Any, image_allowlist: frozenset[str]) -> EndpointConfig:
    if not isinstance(body, dict):
        raise SandboxConfigError(f"endpoint {tier!r} must be an object")

    base_url = str(body.get("base_url") or "").strip()
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise SandboxConfigError(
            f"endpoint {tier!r}: base_url must be an http(s) URL with a host, got {base_url!r}"
        )

    for key in ("api_key_env", "runtime_class", "default_image"):
        if not str(body.get(key) or "").strip():
            raise SandboxConfigError(f"endpoint {tier!r}: {key} is required")

    default_image = str(body["default_image"]).strip()
    _require_digest_pinned(default_image, f"endpoint {tier!r} default_image")
    if default_image not in image_allowlist:
        raise SandboxConfigError(f"endpoint {tier!r}: default_image is not in image_allowlist")

    for uid_key in ("exec_uid", "exec_gid"):
        if _int_or_raise(body.get(uid_key, 1000), f"endpoint {tier!r} {uid_key}") == 0:
            raise SandboxConfigError(
                f"endpoint {tier!r}: {uid_key} must not be 0; the control plane never "
                "asks for root inside a sandbox"
            )

    attestations = _parse_attestations(tier, body.get("attestations") or {})

    egress_hosts = tuple(_str_list(body, "egress_allow_hosts"))
    for host in egress_hosts:
        _require_egress_host(tier, host)
    if attestations.execd_token_required and not str(body.get("execd_token_env") or "").strip():
        raise SandboxConfigError(
            f"endpoint {tier!r}: execd_token_required is attested but execd_token_env "
            "is unset; the client would send no token and every execd call would 401"
        )
    if attestations.egress_enforced and not egress_hosts:
        raise SandboxConfigError(
            f"endpoint {tier!r}: egress_enforced is attested but egress_allow_hosts is empty; "
            "a deny-default policy with no allowlist cannot reach the LLM proxy"
        )

    host_allowlist = tuple(_str_list(body, "execd_endpoint_host_allowlist")) or (parsed.hostname,)

    return EndpointConfig(
        tier=tier,
        base_url=base_url,
        api_key_env=str(body["api_key_env"]).strip(),
        runtime_class=str(body["runtime_class"]).strip(),
        default_image=default_image,
        execd_port=_int_or_raise(body.get("execd_port", 44772), f"endpoint {tier!r} execd_port"),
        egress_port=_int_or_raise(body.get("egress_port", 18080), f"endpoint {tier!r} egress_port"),
        execd_token_env=str(body.get("execd_token_env") or "").strip(),
        runtime_user=str(body.get("runtime_user") or "openace").strip(),
        runtime_group=str(body.get("runtime_group") or "openace").strip(),
        exec_uid=_int_or_raise(body.get("exec_uid", 1000), f"endpoint {tier!r} exec_uid"),
        exec_gid=_int_or_raise(body.get("exec_gid", 1000), f"endpoint {tier!r} exec_gid"),
        execd_endpoint_host_allowlist=host_allowlist,
        egress_allow_hosts=egress_hosts,
        attestations=attestations,
        pool=_parse_pool(tier, body.get("pool") or {}),
    )


def _parse_attestations(tier: str, raw: Any) -> Attestations:
    if not isinstance(raw, dict):
        raise SandboxConfigError(f"endpoint {tier!r}: attestations must be an object")
    known = {f.name for f in fields(Attestations)}
    unknown = set(raw) - known
    if unknown:
        # A typo'd attestation would read as absent and quietly remove a
        # capability. Refuse it loudly instead of silently weakening the tier.
        raise SandboxConfigError(
            f"endpoint {tier!r}: unknown attestation key(s) {sorted(unknown)}; "
            f"known keys are {sorted(known)}"
        )
    values: dict[str, Any] = {}
    for f in fields(Attestations):
        if f.name not in raw:
            continue
        # bool is a subclass of int, so test for it first.
        if isinstance(f.default, bool):
            values[f.name] = _bool_or_raise(raw[f.name], f"endpoint {tier!r} attestation {f.name}")
        else:
            value = _int_or_raise(raw[f.name], f"endpoint {tier!r} attestation {f.name}")
            if value < 0:
                raise SandboxConfigError(
                    f"endpoint {tier!r}: attestation {f.name} must be >= 0, got {value}"
                )
            values[f.name] = value
    return Attestations(**values)


def _parse_pool(tier: str, raw: Any) -> PoolConfig:
    if not isinstance(raw, dict):
        raise SandboxConfigError(f"endpoint {tier!r}: pool must be an object")
    image_digest = str(raw.get("image_digest") or "")
    if image_digest:
        _require_digest_pinned(image_digest, f"endpoint {tier!r} pool.image_digest")
    return PoolConfig(
        pool_ref=str(raw.get("pool_ref") or ""),
        # Same strict coercion as the attestations: these two decide whether a
        # recycled pool sandbox keeps its egress policy and whether teardown
        # really deletes, so a string "false" reading as True is a security
        # downgrade, not a cosmetic one.
        egress_preapplied=_bool_or_raise(
            raw.get("egress_preapplied", False), f"endpoint {tier!r} pool.egress_preapplied"
        ),
        recycle_delete=_bool_or_raise(
            raw.get("recycle_delete", False), f"endpoint {tier!r} pool.recycle_delete"
        ),
        image_digest=image_digest,
    )


def _parse_rollout(raw: Any) -> RolloutConfig:
    if not isinstance(raw, dict):
        raise SandboxConfigError("rollout must be an object")
    mode = str(raw.get("mode") or "all").strip().lower()
    if mode not in ("all", "allowlist"):
        raise SandboxConfigError(f"rollout.mode must be 'all' or 'allowlist', got {mode!r}")
    return RolloutConfig(
        mode=mode,
        tenants=frozenset(_str_list(raw, "tenants")),
        projects=frozenset(_str_list(raw, "projects")),
    )


def _parse_changeset_limits(raw: Any) -> ChangesetLimits:
    if not isinstance(raw, dict):
        raise SandboxConfigError("changeset_limits must be an object")
    defaults = ChangesetLimits()
    values: dict[str, int] = {}
    for f in fields(ChangesetLimits):
        value = _int_or_raise(
            raw.get(f.name, getattr(defaults, f.name)), f"changeset_limits.{f.name}"
        )
        if value <= 0:
            raise SandboxConfigError(f"changeset_limits.{f.name} must be > 0, got {value}")
        values[f.name] = value
    return ChangesetLimits(**values)


def _require_digest_pinned(image: str, label: str) -> None:
    if not _DIGEST_PINNED.search(image):
        raise SandboxConfigError(
            f"{label} must be digest-pinned (name@sha256:<64 hex>), got {image!r}"
        )


def _require_egress_host(tier: str, host: str) -> None:
    """Reject anything upstream's name-based egress rules cannot express.

    ``NetworkRule.target`` is documented as "FQDN or wildcard domain … IP/CIDR
    not yet supported in the egress MVP", so an IP literal in the allowlist is
    not merely risky — it is meaningless, and would leave the operator believing
    a range was permitted (or, for a metadata address, deliberately allowed)
    when the sidecar cannot act on it at all.
    """
    candidate = host[2:] if host.startswith("*.") else host
    if not candidate:
        raise SandboxConfigError(f"endpoint {tier!r}: empty egress_allow_hosts entry")
    try:
        ipaddress.ip_address(candidate.strip("[]"))
    except ValueError:
        pass
    else:
        raise SandboxConfigError(
            f"endpoint {tier!r}: egress_allow_hosts entry {host!r} is an IP literal; "
            "upstream's egress MVP supports FQDN/wildcard targets only"
        )
    if candidate.lower() in _METADATA_HOSTS:
        raise SandboxConfigError(
            f"endpoint {tier!r}: egress_allow_hosts entry {host!r} is a cloud metadata host"
        )
    if not all(_HOSTNAME_LABEL.match(label) for label in candidate.split(".") if label):
        raise SandboxConfigError(
            f"endpoint {tier!r}: egress_allow_hosts entry {host!r} is not a valid hostname"
        )


def _str_list(raw: Mapping[str, Any], key: str) -> list[str]:
    value = raw.get(key) or []
    if not isinstance(value, (list, tuple)):
        raise SandboxConfigError(f"{key} must be a list")
    return [str(item).strip() for item in value if str(item).strip()]


def host_matches(host: str, pattern: str) -> bool:
    """Does *host* satisfy an allowlist *pattern* (exact, or ``*.suffix``)?

    ONE definition, shared by the execd endpoint allowlist (``client``) and the
    egress allowlist (``policy``). They are the same question over two lists,
    and this PR has already been bitten once by re-implementing a shared
    predicate in two places — the snapshot/deletion split that ``133111cb``
    fixed structurally. A second copy would let the two allowlists drift into
    disagreeing about what ``*.svc.cluster.local`` means.
    """
    pattern = pattern.lower().strip()
    host = host.lower().strip()
    if pattern.startswith("*."):
        suffix = pattern[1:]  # ".open-ace.svc.cluster.local"
        return host.endswith(suffix) or host == pattern[2:]
    return host == pattern


def _bool_or_raise(value: Any, label: str) -> bool:
    """Accept a real JSON boolean only.

    ``bool(value)`` is wrong for every field this guards: a templating layer
    that emits ``"false"`` (a non-empty string) would turn a *withheld*
    security attestation into a granted one, and the provider grants
    capabilities off these flags. The failure is silent and fails OPEN, so the
    coercion is refused rather than widened.
    """
    if not isinstance(value, bool):
        raise SandboxConfigError(
            f"{label} must be a JSON boolean (true/false), got {value!r}; "
            'strings such as "false" are refused because they would read as true'
        )
    return value


def _int_or_raise(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise SandboxConfigError(f"{label} must be an integer, got {value!r}")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise SandboxConfigError(f"{label} must be an integer, got {value!r}") from exc
