"""Sandboxed interactive WebUI lifecycle over an OpenSandbox backend (#3378).

``SandboxedWebuiLauncher`` owns one qwen-code-webui process per user inside an
OpenSandbox pod, plus the session-history snapshot machinery that survives pod
recycling. It deliberately does NOT reuse ``OpenSandboxProvider.create`` — the
provider's create path carries autonomous-task semantics (workspace snapshot
upload, git repo synthesis, turn execution) that have no meaning for a
long-lived interactive pod. What IS reused, by name:

* ``policy.build_create_request`` — the ONE create-request shape (T2 added the
  entrypoint-suffix / timeout / extra-metadata overrides this module passes);
* ``policy._ENV_NEVER`` — the control-plane credential denylist, so the webui
  env merge can never smuggle a GitHub write credential into a pod;
* ``OpenSandboxProvider._run_probes`` — the boot probes whose success is the
  only evidence the declared runtime class and egress enforcement are real.
  A probe failure destroys the pod, exactly as the provider does for agent pods.

Design decisions pinned by docs/superpowers/plans/2026-09-12-issue-3378-*.md
(v5): D2 (bootstrap-prefixed entrypoint + restore gate + per-instance secret),
D5 (renew clamped to the proxy-token expiry so a pod never outlives its baked-in
LLM credentials), D6 (export guarded on the restore-done ground truth).
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import secrets
import shlex
import tarfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.modules.workspace.autonomous.sandbox.opensandbox import config as sandbox_config_mod
from app.modules.workspace.autonomous.sandbox.opensandbox import policy as sandbox_policy_mod
from app.modules.workspace.autonomous.sandbox.opensandbox.client import OpenSandboxApiError
from app.modules.workspace.autonomous.sandbox.provider import SandboxError
from app.modules.workspace.autonomous.sandbox.types import RuntimeSpec, SandboxSpec

if TYPE_CHECKING:  # pragma: no cover - annotations only
    from app.modules.workspace.autonomous.sandbox.opensandbox.client import OpenSandboxApi
    from app.modules.workspace.autonomous.sandbox.opensandbox.config import (
        EndpointConfig,
        SandboxBackendConfig,
    )

logger = logging.getLogger(__name__)

# The webui port inside the pod (D2; the local per-instance port proxy in D1
# forwards to this).
WEBUI_POD_PORT = 3100

# Restore gate ground truth (D5/D6, review round 1 T-E). The entrypoint
# blocks on the in-pod marker; the control plane touches it only after a
# confirmed snapshot restore. The in-pod marker is NOT trusted as an export
# gate, though — the supervised process can forge it — so the control plane
# ALSO persists a confirmation record under the state root, and both the
# export guard and the orphan reconciler key on THAT (the pod-side marker
# survives only as the entrypoint's unblock signal).
RESTORE_MARKER_PATH = "/workspace/.openace-restore-done"
RESTORE_CONFIRMED_DIRNAME = "restore-confirmed"

# Where the webui keeps its session tree inside the pod (the CLI's cwd encoding
# of /workspace — same constant the provider pins for qwen transcripts).
WEBUI_STATE_POD_DIR = "/home/agent/.qwen/projects/-workspace"

# In-pod staging path for the snapshot tar (both directions).
WEBUI_STATE_TAR_PATH = "/tmp/webui-state.tar"

# Snapshot size ceiling (D6/FEAS-Q2): over the limit the export is skipped with
# a WARNING and the last good snapshot stays — a user's history freezes rather
# than being truncated or ballooning control-plane memory.
DEFAULT_STATE_MAX_BYTES = 16 * 1024 * 1024
STATE_MAX_BYTES_ENV = "OPENACE_WEBUI_STATE_MAX_BYTES"

# Independent snapshot root (D6: the reaper never scans it). Chosen INSIDE the
# config directory (alongside config.json), because that is the directory Docker
# deployments mount for persistence — a sibling of CONFIG_DIR itself would be
# container-local and lose every snapshot on container recreation.
STATE_ROOT_ENV = "OPENACE_WEBUI_STATE_ROOT"

# Restore-gate timing (D2): 300 polls x 0.2s = 60s before the entrypoint
# degrades and execs the webui without a restored history.
RESTORE_GATE_ATTEMPTS = 300
RESTORE_GATE_SLEEP_SECONDS = 0.2

# Metadata keys for the webui process namespace (D5/FEAS-Q1). Defined ONCE in
# policy.py (the provider's orphan sweep needs them too — N7 exclusion
# contract); re-exported here for the launcher's callers.
WEBUI_METADATA_KIND = sandbox_policy_mod.WEBUI_METADATA_KIND
WEBUI_METADATA_GENERATION = sandbox_policy_mod.WEBUI_METADATA_GENERATION
WEBUI_METADATA_OWNER = sandbox_policy_mod.WEBUI_METADATA_OWNER
WEBUI_METADATA_KIND_VALUE = sandbox_policy_mod.WEBUI_METADATA_KIND_VALUE

# Per-process generation: reconcile destroys webui pods whose generation is not
# this value (D5). Module-level so every launcher in this process agrees with
# the reconciler.
_PROCESS_GENERATION = uuid.uuid4().hex

# ── T-D (review round 1): multi-replica reconcile mutex via heartbeats ──────
#
# The k8s reference manifest ships 3 replicas (HPA 3-10). Generation-based
# orphan discrimination alone let every newly started replica sweep every
# OTHER replica's live pods. Each web process now keeps a heartbeat file in
# the shared state root (filename carries boot_id/pid, content carries
# pid/boot_id/ts); the reconcile destroys nothing while another FRESH
# heartbeat exists. The maintenance loop that refreshes the heartbeat lives
# in webui_manager (SANDBOX_MAINTENANCE_INTERVAL_SECONDS = 300); the
# freshness window below is 2x that plus a 60s scheduling grace, duplicated
# here (not imported) to keep this module's import graph one-directional.
HEARTBEAT_FILENAME_PREFIX = "webui-heartbeat-"
HEARTBEAT_FRESH_WINDOW_SECONDS = 2 * 300.0 + 60.0
# Ancient heartbeat files (dead processes from previous deploys) are pruned
# on every write so a long-lived mounted volume cannot accumulate them.
HEARTBEAT_MAX_AGE_SECONDS = 24 * 3600.0


def _boot_id() -> str:
    """The host's boot id (Linux), or "" where /proc is unavailable."""
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def write_webui_heartbeat(state_root_override: str | Path | None = None) -> Path | None:
    """Write/refresh THIS process's heartbeat file (T-D, entirely fail-soft).

    Called at reconcile time (web-process boot) and by the manager's
    maintenance cycle every ~5 minutes. Returns the path written, or None
    when the state root is unwritable — a missing heartbeat can only make a
    PEER's sweep more conservative toward skipping, never less.
    """
    payload = {"pid": os.getpid(), "boot_id": _boot_id(), "ts": time.time()}
    try:
        base = state_root(state_root_override)
        base.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = (
            base / f"{HEARTBEAT_FILENAME_PREFIX}{payload['boot_id'] or 'nb'}-{payload['pid']}.json"
        )
        temp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.replace(temp, path)
    except OSError as exc:
        logger.debug("webui heartbeat write failed (fail-soft): %s", exc)
        return None
    _prune_stale_heartbeat_files(state_root_override)
    return path


def _iter_heartbeat_entries(
    state_root_override: str | Path | None = None,
):
    """Yield ``(path, entry)`` for every parseable heartbeat file (fail-soft)."""
    try:
        base = state_root(state_root_override)
        for path in sorted(base.glob(f"{HEARTBEAT_FILENAME_PREFIX}*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                yield path, data
    except OSError:
        return


def _prune_stale_heartbeat_files(state_root_override: str | Path | None = None) -> None:
    """Unlink heartbeat files older than HEARTBEAT_MAX_AGE_SECONDS (fail-soft)."""
    cutoff = time.time() - HEARTBEAT_MAX_AGE_SECONDS
    for path, data in list(_iter_heartbeat_entries(state_root_override)):
        try:
            if float(data.get("ts") or 0) < cutoff:
                path.unlink(missing_ok=True)
        except (OSError, TypeError, ValueError):
            continue


def fresh_peer_heartbeats(
    state_root_override: str | Path | None = None,
) -> list[dict]:
    """Heartbeat entries of OTHER live web processes (T-D).

    An entry counts when its ts is within HEARTBEAT_FRESH_WINDOW_SECONDS of
    now (a future ts — clock skew — reads as "just written" and counts as
    fresh; fail-safe in the skip direction). This process's own entry never
    counts.
    """
    own_boot, own_pid = _boot_id(), os.getpid()
    now = time.time()
    peers: list[dict] = []
    for _path, data in _iter_heartbeat_entries(state_root_override):
        if data.get("pid") == own_pid and data.get("boot_id") == own_boot:
            continue
        try:
            if now - float(data.get("ts") or 0) <= HEARTBEAT_FRESH_WINDOW_SECONDS:
                peers.append(data)
        except (TypeError, ValueError):
            continue
    return peers


def current_process_generation() -> str:
    """Return this process's webui-pod generation tag (D5 reconcile key)."""
    return _PROCESS_GENERATION


class SandboxWebuiError(RuntimeError):
    """A fail-closed launcher refusal with a machine-readable reason code.

    Codes are the §5 vocabulary (``sandbox_create_failed``,
    ``sandbox_endpoint_unresolved``, ...) plus passthrough probe failures.
    """

    def __init__(self, message: str, *, reason_code: str = "") -> None:
        """Store the machine-readable reason code (§5 vocabulary)."""
        super().__init__(message)
        self.reason_code = reason_code


class SnapshotUnreadableError(RuntimeError):
    """A stored snapshot exists but cannot be read (permissions/EIO).

    Review round 1 (T-E): this is NOT "no snapshot". The launch degrades to
    an empty history with exports suspended, and the unreadable file is left
    on disk for an operator to repair — never treated as a first launch that
    may freely overwrite the slot.
    """


@dataclass(frozen=True)
class SandboxedWebuiLaunchResult:
    """Everything the manager needs after a successful sandboxed launch."""

    sandbox_id: str
    tier: str
    # Per-instance webui token secret (D2/B1): the pod's webui validates tokens
    # end-to-end against this; the manager stores it on the WebUIInstance.
    token_secret: str
    restore_confirmed: bool
    # Expiry of the proxy token baked into the pod env — renew clamps to this.
    proxy_token_expires_at: datetime
    proxy_token: str
    webui_port: int = WEBUI_POD_PORT


def state_max_bytes(explicit: int | None = None) -> int:
    """Resolve the snapshot size ceiling (env-configurable, FEAS-Q2)."""
    if explicit is not None and explicit > 0:
        return explicit
    raw = os.environ.get(STATE_MAX_BYTES_ENV, "").strip()
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            logger.warning(
                "Invalid %s=%r; using %d default", STATE_MAX_BYTES_ENV, raw, DEFAULT_STATE_MAX_BYTES
            )
    return DEFAULT_STATE_MAX_BYTES


def state_root(explicit: str | Path | None = None) -> Path:
    """Resolve the independent snapshot root (see STATE_ROOT_ENV)."""
    if explicit:
        return Path(explicit)
    env_root = os.environ.get(STATE_ROOT_ENV, "").strip()
    if env_root:
        return Path(env_root)
    from app.repositories.database import CONFIG_DIR

    return Path(CONFIG_DIR) / "webui-agent-state"


def snapshot_path_for_user(user_id: int, root: str | Path | None = None) -> Path:
    """Return the on-disk snapshot slot for *user_id* (``webui-<user_id>.tar``)."""
    base = state_root(root) if root is not None else state_root()
    return base / f"webui-{int(user_id)}.tar"


def build_webui_entrypoint_suffix(
    token_secret: str,
    *,
    port: int = WEBUI_POD_PORT,
    attempts: int = RESTORE_GATE_ATTEMPTS,
    sleep_seconds: float = RESTORE_GATE_SLEEP_SECONDS,
    extra_args: tuple[str, ...] = (),
) -> str:
    """Build the restore gate + webui exec appended after the create bootstrap (D2).

    Appended verbatim by ``policy.build_create_request(entrypoint_suffix=...)``
    after the bootstrap half of ``_build_entrypoint``. POSIX sh only (Ubuntu
    dash: no brace expansion, F34), explicit paths, and every argv word
    shlex-quoted — the secret included.
    """
    webui_argv = [
        "qwen-code-webui",
        "--host",
        "0.0.0.0",
        "--port",
        str(port),
        "--token-secret",
        token_secret,
        "--quota-check-enabled",
        # The pod authenticates with the proxy token under the OpenAI env key,
        # exactly like the local per-user instances (--auth-type parity).
        "--auth-type",
        "openai",
        *extra_args,
    ]
    quoted = " ".join(shlex.quote(part) for part in webui_argv)
    return (
        f"i=0; "
        f'until [ -f {RESTORE_MARKER_PATH} ] || [ "$i" -ge {attempts} ]; do '
        f"sleep {sleep_seconds}; i=$((i+1)); done; "
        f"[ -f {RESTORE_MARKER_PATH} ] || "
        f'echo "restore marker missing; degrading" >&2; '
        f"exec {quoted}"
    )


def mint_instance_token(user_id: int, port: int, token_secret: str) -> str:
    """Mint a v2 webui token signed with a per-instance secret.

    Same wire format as ``WebUIManager.generate_token`` (qwen-code-webui PR
    #210) — ``v2:{user_id}:{port}:{timestamp}:{random}:{signature}`` — but
    signed with the pod's per-instance secret so instance A's tokens stop
    validating the moment instance A is destroyed (D2/B1, N8).
    """
    timestamp = int(time.time())
    random_part = secrets.token_hex(8)
    payload = f"v2:{user_id}:{port}:{timestamp}:{random_part}"
    signature = hashlib.sha256(f"{payload}:{token_secret}".encode()).hexdigest()[:16]
    return f"{payload}:{signature}"


def proxy_token_expiry(token: str) -> datetime:
    """Read the minted proxy token's expiry out of its signed payload.

    ``generate_proxy_token`` returns only the token string; the expiry lives
    in the base64 JSON payload under ``exp`` (ISO datetime). Renew clamps to
    this moment. Returns an aware UTC datetime (a naive payload — the mint
    writes local time — is normalized via astimezone).

    Review round 1 (T-F): decode failures and EMPTY tokens (the
    ``WebUIInstance.proxy_token`` default) fail CLOSED — 'now' (UTC) — so a
    renew clamps the pod to immediate expiry instead of re-arming a full
    fallback TTL every maintenance cycle (previously each 5-minute pass moved
    the pod's deadline 4 hours further out: an unbounded lifetime for a
    credential whose real expiry cannot be known). An unreadable token is a
    credential problem, never 'valid for another TTL'.
    """
    import base64
    import json as _json

    if token:
        try:
            payload_b64 = token.split(".", 1)[0]
            payload = _json.loads(base64.b64decode(payload_b64))
            parsed = datetime.fromisoformat(str(payload["exp"]))
            if parsed.tzinfo is None:
                # The mint wrote naive local time (datetime.now().isoformat()).
                parsed = parsed.astimezone()
            return parsed
        except Exception:  # noqa: BLE001 - fail closed below
            logger.warning(
                "webui proxy token expiry could not be decoded; treating the "
                "credential as expired (renew will clamp the pod to now)"
            )
    else:
        logger.warning(
            "webui proxy token is empty; treating the credential as expired "
            "(renew will clamp the pod to now)"
        )
    return datetime.now(timezone.utc)


def empty_state_tar() -> bytes:
    """Return a VALID empty tar archive (D2): the no-snapshot restore marker upload.

    ``tar -xf`` succeeds on it and extracts nothing, so the restore command
    sequence is identical whether or not a snapshot exists.
    """
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w"):
        pass
    return buffer.getvalue()


class SandboxedWebuiLauncher:
    """Launch and manage one interactive webui pod per user (Issue #3378).

    The manager (T5) owns instance bookkeeping and the local port proxy; this
    class is the OpenSandbox-facing half: create with the bootstrap-prefixed
    entrypoint, restore the session snapshot, health-check through the proxy,
    renew with the TTL clamp, export/persist snapshots, and destroy idempotently.
    """

    def __init__(
        self,
        *,
        backend_config: SandboxBackendConfig | None = None,
        tier: str = "",
        endpoint: EndpointConfig | None = None,
        api_factory: Callable[[EndpointConfig], OpenSandboxApi] | None = None,
        proxy_service_factory: Callable[[], Any] | None = None,
        restore_timeout_seconds: float = 90.0,
        poll_interval_seconds: float = 0.2,
        max_state_bytes: int | None = None,
        state_root_override: str | None = None,
    ) -> None:
        """Store injection points; the backend resolves lazily on first use."""
        self._backend_config = backend_config
        self._tier = (tier or "").strip()
        self._api_factory = api_factory
        self._proxy_service_factory = proxy_service_factory
        self._restore_timeout_seconds = restore_timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._max_state_bytes = state_max_bytes(max_state_bytes)
        self._state_root_override = state_root_override
        self._api: OpenSandboxApi | None = None
        # Issue #3378 review (m5): an explicit endpoint can be injected for
        # callers that never resolve one themselves (the orphan exporter) —
        # _run_background_command's uid/gid credential switch keys on it.
        self._endpoint: EndpointConfig | None = endpoint
        # sandbox_id -> (url, headers) for the pod's webui port (D1 upstream).
        self._webui_endpoints: dict[str, tuple[str, dict[str, str]]] = {}

    # ── resolution ───────────────────────────────────────────────────

    def _resolve_endpoint(self) -> tuple[SandboxBackendConfig, EndpointConfig]:
        """Resolve (backend config, endpoint) or raise a fail-closed refusal."""
        cfg = self._backend_config
        if cfg is None:
            cfg = sandbox_config_mod.load_backend_config()
        if cfg is None:
            raise SandboxWebuiError(
                "No OpenSandbox backend is configured; the sandboxed isolation "
                "level is unavailable.",
                reason_code="sandbox_backend_unconfigured",
            )
        tier = self._tier or cfg.default_tier
        endpoint = cfg.endpoints.get(tier)
        if endpoint is None:
            raise SandboxWebuiError(
                f"Sandbox endpoint tier {tier!r} does not exist in the backend config.",
                reason_code="sandbox_tier_missing",
            )
        return cfg, endpoint

    def _api_for(self, cfg: SandboxBackendConfig, endpoint: EndpointConfig) -> OpenSandboxApi:
        if self._api_factory is not None:
            return self._api_factory(endpoint)
        from app.modules.workspace.autonomous.sandbox.opensandbox.client import HttpOpenSandboxApi

        return HttpOpenSandboxApi(endpoint)

    def _proxy_service(self) -> Any:
        if self._proxy_service_factory is not None:
            return self._proxy_service_factory()
        from app.modules.workspace.api_key_proxy import get_api_key_proxy_service

        return get_api_key_proxy_service()

    def _effective_proxy_ttl_minutes(self) -> int:
        """Return the effective webui proxy-token TTL (one definition, no drift)."""
        return int(self._proxy_service().effective_proxy_token_ttl_minutes("webui"))

    # ── launch ───────────────────────────────────────────────────────

    def launch(
        self,
        *,
        user_id: int,
        callback_url: str,
        snapshot: bytes | None = None,
    ) -> SandboxedWebuiLaunchResult:
        """Create a webui pod, probe it, and restore its session snapshot.

        ``callback_url`` is the control-plane URL the pod reaches the LLM proxy
        through (``workspace.webui_callback_url`` — the static probe already
        verified the tier's egress policy admits it). ``snapshot`` is the
        user's previous session-history tar, or None for a first launch.
        """
        from app.auth.decorators import WEBUI_TOKEN_TTL_SECONDS

        cfg, endpoint = self._resolve_endpoint()
        if not (endpoint.webui_image or "").strip():
            # The readiness probe gates on this too; the launcher re-checks so
            # a race (config changed between gate and launch) cannot create a
            # pod from the autonomous default image.
            raise SandboxWebuiError(
                f"endpoint tier {endpoint.tier!r} has no webui_image configured",
                reason_code="webui_image_missing",
            )
        api = self._api_for(cfg, endpoint)
        self._endpoint = endpoint
        self._api = api

        # LLM credentials: minted per instance, baked into the pod env, and
        # renewable only by recreating the pod — so the pod TTL may never
        # exceed the token TTL (D5).
        proxy_token, token_expiry, ttl_minutes = self._mint_proxy_token(user_id)
        timeout_seconds = min(int(WEBUI_TOKEN_TTL_SECONDS), ttl_minutes * 60)

        token_secret = secrets.token_hex(32)
        spec = SandboxSpec(
            task_id=f"webui-user-{user_id}",
            project_path="/workspace",
            cli_tool="qwen-code-webui",
            runtime=RuntimeSpec(
                image=endpoint.webui_image, runtime=endpoint.runtime_class, toolchain=""
            ),
        )
        try:
            body = sandbox_policy_mod.build_create_request(
                spec,
                cfg,
                endpoint,
                generation=1,
                entrypoint_suffix=build_webui_entrypoint_suffix(token_secret),
                timeout_seconds=timeout_seconds,
                extra_metadata={
                    WEBUI_METADATA_KIND: WEBUI_METADATA_KIND_VALUE,
                    WEBUI_METADATA_GENERATION: current_process_generation(),
                    WEBUI_METADATA_OWNER: str(user_id),
                },
            )
        except SandboxError as exc:
            raise SandboxWebuiError(
                f"webui sandbox create request refused: {exc}",
                reason_code="sandbox_create_failed",
            ) from exc
        body["env"].update(self._build_pod_env(user_id, callback_url, proxy_token))

        try:
            record = api.create_sandbox(body)
        except Exception as exc:  # noqa: BLE001 - any create failure is fatal here
            raise SandboxWebuiError(
                f"webui sandbox create failed: {exc}", reason_code="sandbox_create_failed"
            ) from exc
        sandbox_id = str(record.get("id") or "")
        if not sandbox_id:
            raise SandboxWebuiError(
                "webui sandbox create returned no sandbox id",
                reason_code="sandbox_create_failed",
            )

        # Boot probes BEFORE the snapshot upload: a runtime-class mismatch must
        # not waste an upload, and the probe outcome feeds the contract memo.
        self._run_boot_probes(api, endpoint, spec, sandbox_id)

        restore_confirmed = self.restore_sequence(api, sandbox_id, snapshot)

        return SandboxedWebuiLaunchResult(
            sandbox_id=sandbox_id,
            tier=endpoint.tier,
            token_secret=token_secret,
            restore_confirmed=restore_confirmed,
            proxy_token_expires_at=token_expiry,
            proxy_token=proxy_token,
        )

    def _mint_proxy_token(self, user_id: int) -> tuple[str, datetime, int]:
        """Mint the instance's LLM proxy token; return (token, expiry, ttl min).

        A freshly minted token that does not decode is a BUG in the mint
        (T-F's fail-closed 'now' would create a zero-lifetime pod) — surface
        it instead of silently clamping.
        """
        service = self._proxy_service()
        ttl_minutes = service.effective_proxy_token_ttl_minutes("webui")
        token = service.generate_proxy_token(
            user_id=user_id,
            session_id=f"webui:{user_id}",
            tenant_id=1,
            provider="openai",
            session_type="webui",
            extra_payload={"scope": "local", "tool_name": "qwen-code"},
        )
        expiry = proxy_token_expiry(token)
        if token and (expiry - datetime.now(timezone.utc)).total_seconds() <= 0:
            raise SandboxWebuiError(
                "minted webui proxy token carries an already-expired or "
                "undecodable payload; refusing to create a pod whose LLM "
                "credential is dead at birth",
                reason_code="sandbox_create_failed",
            )
        return token, expiry, ttl_minutes

    def _build_pod_env(self, user_id: int, callback_url: str, proxy_token: str) -> dict[str, str]:
        """Build the webui-specific env merged over build_create_request's base env.

        Same key set the local per-user path builds in
        ``WebUIManager._configure_local_openai_proxy`` (OPENAI_BASE_URL shape
        included), minus everything the sandbox env denylist forbids: the merge
        filters through ``policy._ENV_NEVER`` so no control-plane write
        credential can ride along under any name.
        """
        openace_api_url = callback_url.rstrip("/")
        env: dict[str, str] = {
            "OPENAI_BASE_URL": f"{openace_api_url}/api/workspace/llm-proxy/v1",
            "OPENAI_API_KEY": proxy_token,
            "OPENACE_PROXY_TOKEN": proxy_token,
            "OPENACE_PROXY_URL": f"{openace_api_url}/api/workspace/llm-proxy",
        }
        try:
            pool = self._proxy_service().get_tool_model_pool(
                tenant_id=1,
                tool_name="qwen-code",
                scope="local",
                provider="openai",
            )
            for model in pool.get("models", []):
                env_key = model.get("envKey")
                if env_key and env_key not in env:
                    env[str(env_key)] = proxy_token
        except Exception as exc:  # noqa: BLE001 - pool is an optimisation, not a gate
            logger.warning("webui sandbox: model pool unavailable for user %s: %s", user_id, exc)
        return {
            key: value for key, value in env.items() if key not in sandbox_policy_mod._ENV_NEVER
        }

    def _run_boot_probes(
        self,
        api: OpenSandboxApi,
        endpoint: EndpointConfig,
        spec: SandboxSpec,
        sandbox_id: str,
    ) -> bool:
        """Run the provider's boot probes on the webui pod (D3 upgrade hook).

        Reuses ``OpenSandboxProvider._run_probes`` rather than re-implementing
        the kernel/egress classification — the same checks, drifted copies are
        how capabilities get declared without enforcement. On success the
        contract memo upgrades kernel/network_egress (gVisor positive → kernel
        enforced; Kata negative-only → kernel stays unverified, D3). On failure
        the pod is destroyed — an unverifiable webui pod must not survive, the
        same rule the provider applies to agent pods.
        """
        # The provider resolves its endpoint via the config's default_tier
        # (tenant/project unset); re-point that at THIS pod's tier so the probe
        # verdict matches the runtime class the pod was actually created with
        # — otherwise a sandbox_tier different from default_tier probes the
        # wrong tier's declaration (review Q1, multi-tier deployments).
        import dataclasses

        from app.modules.workspace.autonomous.sandbox.opensandbox.provider import (
            OpenSandboxProvider,
        )
        from app.modules.workspace.autonomous.sandbox.types import SandboxHandle

        provider = OpenSandboxProvider(
            dataclasses.replace(self._require_backend_config(), default_tier=endpoint.tier),
            api_factory=lambda _endpoint: api,
        )
        handle = SandboxHandle(
            sandbox_id=sandbox_id,
            generation=1,
            provider_name="opensandbox",
            spec=spec,
        )
        try:
            provider._run_probes(handle)
        except SandboxError as exc:
            logger.error("webui sandbox %s boot probe failed: %s", sandbox_id, exc)
            self._destroy_raw(api, sandbox_id)
            raise SandboxWebuiError(
                f"webui sandbox boot probe failed: {exc}",
                reason_code=getattr(exc, "reason_code", "") or "sandbox_create_failed",
            ) from exc
        family = sandbox_config_mod.runtime_family(endpoint.runtime_class)
        kernel_enforced = family == "gvisor"
        from app.services.workspace_isolation_contract import register_sandbox_runtime_verified

        # Per-tier memo (review Q1): this pod's verification upgrades only the
        # tier it was launched against — a Kata pod starting later must not
        # downgrade an already-verified gVisor tier's snapshot (or vice versa).
        register_sandbox_runtime_verified(tier=endpoint.tier, kernel_enforced=kernel_enforced)
        logger.info(
            "webui sandbox %s boot probes passed (runtime family %r; kernel_enforced=%s)",
            sandbox_id,
            family,
            kernel_enforced,
        )
        return kernel_enforced

    def _require_backend_config(self) -> SandboxBackendConfig:
        cfg = self._backend_config
        if cfg is None:
            cfg = sandbox_config_mod.load_backend_config()
        if cfg is None:
            raise SandboxWebuiError(
                "No OpenSandbox backend is configured.",
                reason_code="sandbox_backend_unconfigured",
            )
        return cfg

    # ── restore (D2/N3) ──────────────────────────────────────────────

    def restore_sequence(
        self, api: OpenSandboxApi, sandbox_id: str, snapshot: bytes | None
    ) -> bool:
        """Upload + extract the snapshot, then touch the restore marker.

        Order is load-bearing (N3): the entrypoint's webui exec is gated on the
        marker, so the history lands BEFORE the webui can read it. Only a
        confirmed successful ``touch`` PLUS a persisted control-plane
        confirmation record sets restore-confirmed — the D6 export guard keys
        on that state, and a degraded (timeout/failed-extract/unwritable
        record) start must leave it False so an empty tree can never overwrite
        a good snapshot. On degrade the entrypoint's own 60s counter lets the
        webui start anyway.
        """
        tar_bytes = snapshot if snapshot is not None else empty_state_tar()
        try:
            api.upload_file(sandbox_id, WEBUI_STATE_TAR_PATH, tar_bytes, 0o600)
            extract = (
                f"mkdir -p {shlex.quote(WEBUI_STATE_POD_DIR)} && "
                f"tar -xf {shlex.quote(WEBUI_STATE_TAR_PATH)} "
                f"-C {shlex.quote(WEBUI_STATE_POD_DIR)}"
            )
            if not self._run_background_command(api, sandbox_id, extract):
                logger.warning(
                    "webui sandbox %s: snapshot extract did not confirm; "
                    "degrading without restore marker",
                    sandbox_id,
                )
                return False
            touch = f"touch {shlex.quote(RESTORE_MARKER_PATH)}"
            if not self._run_background_command(api, sandbox_id, touch):
                logger.warning("webui sandbox %s: restore marker touch did not confirm", sandbox_id)
                return False
        except OpenSandboxApiError as exc:
            logger.warning(
                "webui sandbox %s: restore sequence failed (%s); degrading", sandbox_id, exc
            )
            return False
        if not self.mark_restore_confirmed(sandbox_id):
            # Crash-window guard (T-E): the in-pod marker is confirmed but the
            # control-plane record is not durable. Refusing to mark confirmed
            # only ever SKIPS exports — the safe direction.
            logger.warning(
                "webui sandbox %s: control-plane restore-confirmation record "
                "could not be written; exports stay disabled for this pod",
                sandbox_id,
            )
            return False
        logger.info("webui sandbox %s: snapshot restore confirmed", sandbox_id)
        return True

    # ── control-plane restore confirmation (T-E) ─────────────────────

    def _restore_confirmed_dir(self) -> Path:
        return state_root(self._state_root_override) / RESTORE_CONFIRMED_DIRNAME

    def mark_restore_confirmed(self, sandbox_id: str) -> bool:
        """Persist the CP-side restore confirmation record (fail-soft False)."""
        try:
            root = self._restore_confirmed_dir()
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            (root / sandbox_id).write_text(
                json.dumps({"sandbox_id": sandbox_id, "ts": time.time()}),
                encoding="utf-8",
            )
            return True
        except OSError as exc:
            logger.warning(
                "webui sandbox %s: restore-confirmation record write failed: %s",
                sandbox_id,
                exc,
            )
            return False

    def restore_confirmed_on_cp(self, sandbox_id: str) -> bool:
        """Whether the CP record says this pod completed a confirmed restore."""
        try:
            return (self._restore_confirmed_dir() / sandbox_id).is_file()
        except OSError:
            return False

    def clear_restore_confirmation(self, sandbox_id: str) -> None:
        """Drop the CP record (pod destroyed; the id never returns)."""
        try:
            (self._restore_confirmed_dir() / sandbox_id).unlink(missing_ok=True)
        except OSError:
            pass

    def _run_background_command(self, api: OpenSandboxApi, sandbox_id: str, command: str) -> bool:
        """Run one ``POST /command background:true`` and confirm exit 0.

        Background semantics (policy.py:652-657): execution_complete fires at
        launch and proves nothing about the outcome — the exit code is read
        from ``/command/status`` polls, bounded by the restore timeout.
        """
        body: dict[str, Any] = {
            "command": command,
            "cwd": "/workspace",
            "background": True,
            "envs": {},
        }
        endpoint = self._endpoint
        if endpoint is not None and not endpoint.attestations.execd_runs_as_exec_identity:
            # Same rule as provider._run_foreground: an execd already running
            # as the exec identity cannot switch credentials to it.
            body["uid"] = endpoint.exec_uid
            body["gid"] = endpoint.exec_gid
        events = list(api.run_command(sandbox_id, body))
        command_id = ""
        for event in events:
            if event.get("type") == "init" and event.get("text"):
                command_id = str(event["text"])
                break
        if not command_id:
            logger.warning("webui sandbox %s: background command returned no id", sandbox_id)
            return False
        exit_code = self._poll_command(api, sandbox_id, command_id)
        return exit_code == 0

    def _poll_command(self, api: OpenSandboxApi, sandbox_id: str, command_id: str) -> int | None:
        """Poll /command/status until terminal; None on timeout/unknown."""
        deadline = time.monotonic() + self._restore_timeout_seconds
        while time.monotonic() < deadline:
            status = api.command_status(sandbox_id, command_id)
            if status is None:
                return None
            if not status.get("running"):
                code = status.get("exit_code")
                return int(code) if isinstance(code, int) else None
            time.sleep(self._poll_interval_seconds)
        return None

    # ── health / renew (D5) ──────────────────────────────────────────

    def health_check(self, *, proxy_port: int, token: str) -> bool:
        """Probe the pod's webui THROUGH the local D1 proxy (not the pod).

        Same raw-socket shape as ``WebUIInstance._check_http_health``:
        ``GET /api/version?token=...`` with ``Connection: close``, bypassing
        any system proxy. The token is minted from the per-instance secret, so
        a pass also proves the end-to-end token path, not just that a socket
        answered.
        """
        import socket
        import urllib.parse

        try:
            sock = socket.create_connection(("localhost", proxy_port), timeout=5)
            try:
                path = f"/api/version?token={urllib.parse.quote(token)}"
                request = f"GET {path} HTTP/1.0\r\nHost: localhost\r\nConnection: close\r\n\r\n"
                sock.sendall(request.encode())
                response = b""
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    response += chunk
            finally:
                sock.close()
        except OSError:
            return False
        try:
            status_line = response.split(b"\r\n", 1)[0].decode()
            return int(status_line.split(" ")[1]) == 200
        except (IndexError, ValueError):
            return False

    def renew_expiration(self, sandbox_id: str, *, proxy_token: str) -> str:
        """Renew the pod TTL clamped to the proxy-token expiry (D5).

        Target = min(now + webui token TTL, proxy token expiry): the pod must
        die with its credentials rather than outlive them — after the clamp
        point the normal idle reaper tears it down and the next /user-url
        recreates it with a fresh token and a snapshot restore.

        Review round 1 (T-F): both clock reads are aware UTC datetimes and
        the returned ISO string carries +00:00 — a naive local 'now' next to
        the token's (normalized) expiry compared apples to oranges on any
        host not running UTC. An unreadable/empty token clamps to 'now'
        (see proxy_token_expiry): the pod expires with its unusable
        credential instead of being re-armed forever.
        """
        from app.auth.decorators import WEBUI_TOKEN_TTL_SECONDS

        token_expiry = proxy_token_expiry(proxy_token)
        target = min(
            datetime.now(timezone.utc) + timedelta(seconds=int(WEBUI_TOKEN_TTL_SECONDS)),
            token_expiry,
        )
        # Normalize to UTC before serialization: the token side of the min()
        # may be aware in the mint host's local offset (+08:00 etc.) — the
        # upstream API gets one unambiguous representation.
        expires_at = target.astimezone(timezone.utc).isoformat()
        api = self._current_api()
        api.renew_expiration(sandbox_id, expires_at)
        return expires_at

    def _current_api(self) -> OpenSandboxApi:
        """Return the API instance for the resolved endpoint (created lazily)."""
        if self._api is not None:
            return self._api
        cfg, endpoint = self._resolve_endpoint()
        self._endpoint = endpoint
        self._api = self._api_for(cfg, endpoint)
        return self._api

    # ── upstream endpoint resolution (D1) ────────────────────────────

    def resolve_webui_endpoint(self, sandbox_id: str) -> tuple[str, dict[str, str]]:
        """Resolve the pod's webui endpoint via the client's own mechanism.

        ``HttpOpenSandboxApi._resolve_port`` is reused verbatim (client.py is
        frozen by the plan): it performs the ``GET /sandboxes/{id}/endpoints/
        {port}`` call, enforces the execd host allowlist, and filters the
        server-supplied headers. Resolution failures surface as
        ``sandbox_endpoint_unresolved`` — the D1/N6 documented runtime failure
        for a cluster whose gateway cannot answer the 3100 endpoint.
        """
        cached = self._webui_endpoints.get(sandbox_id)
        if cached is not None:
            return cached
        api = self._current_api()
        resolve = getattr(api, "_resolve_port", None)
        if not callable(resolve):
            raise SandboxWebuiError(
                "sandbox webui endpoint cannot be resolved on this API",
                reason_code="sandbox_endpoint_unresolved",
            )
        try:
            url, headers = resolve(sandbox_id, WEBUI_POD_PORT, self._webui_endpoints)
        except Exception as exc:  # noqa: BLE001 - any resolution failure is N6
            raise SandboxWebuiError(
                f"sandbox {sandbox_id}: webui endpoint (port {WEBUI_POD_PORT}) "
                f"could not be resolved: {exc}",
                reason_code="sandbox_endpoint_unresolved",
            ) from exc
        if not url:
            raise SandboxWebuiError(
                f"sandbox {sandbox_id}: empty webui endpoint returned",
                reason_code="sandbox_endpoint_unresolved",
            )
        return url, headers

    # ── snapshot export / import / destroy (D6) ──────────────────────

    def export_snapshot(
        self,
        sandbox_id: str,
        *,
        restore_confirmed: bool,
        user_id: int | None = None,
    ) -> bytes | None:
        """Read the pod's session tree as a bounded tar, or None.

        The export guard (FEAS-M2/FEAS-R4-1): an instance whose restore was
        never confirmed — degraded start, the CP touch never succeeded — never
        exports. Its empty tree must not overwrite the last good snapshot.
        Over-ceiling exports are skipped with a dedicated WARNING naming the
        user, the ceiling env var, and the sizes (T-I: the freeze must be
        audible and diagnosable — the user's history freezes at the old
        snapshot until an operator trims it; documented semantics, FEAS-Q2).
        """
        if not restore_confirmed:
            logger.warning(
                "webui sandbox %s: export skipped (restore never confirmed — "
                "guarding the last good snapshot)",
                sandbox_id,
            )
            return None
        api = self._current_api()
        command = (
            f"tar -cf {shlex.quote(WEBUI_STATE_TAR_PATH)} -C {shlex.quote(WEBUI_STATE_POD_DIR)} ."
        )
        if not self._run_background_command(api, sandbox_id, command):
            logger.warning("webui sandbox %s: snapshot tar did not confirm", sandbox_id)
            return None
        try:
            return api.download_file(
                sandbox_id, WEBUI_STATE_TAR_PATH, max_bytes=self._max_state_bytes
            )
        except OpenSandboxApiError as exc:
            if getattr(exc, "code", "") == "FILE_TOO_LARGE":
                subject = f"user {user_id}" if user_id is not None else f"sandbox {sandbox_id}"
                logger.warning(
                    "snapshot for %s exceeds %s (%s); keeping previous "
                    "snapshot; history is frozen until trimmed",
                    subject,
                    STATE_MAX_BYTES_ENV,
                    exc,
                )
                return None
            logger.warning(
                "webui sandbox %s: snapshot download failed (%s); keeping the previous snapshot",
                sandbox_id,
                exc,
            )
            return None

    def persist_snapshot(self, user_id: int, blob: bytes) -> Path:
        """Write the snapshot to its independent slot, atomically and 0o600.

        Review round 1 (T-E) hardening:

        * never overwrite an existing-but-UNREADABLE snapshot — its bytes may
          be recoverable by an operator; a fresh (possibly degraded) export
          must not destroy them;
        * validate the blob is a legal tar BEFORE replacing the last good
          file — a truncated or corrupt transfer must never win;
        * write via ``O_EXCL`` at 0600 (no 0644 window), ``fsync`` the file,
          ``os.replace`` onto the slot, then ``fsync`` the directory so the
          rename itself is durable.
        """
        path = snapshot_path_for_user(user_id, state_root(self._state_root_override))
        if path.exists():
            try:
                with open(path, "rb"):
                    pass
            except OSError as exc:
                logger.warning(
                    "webui snapshot for user %s exists but is unreadable (%s); "
                    "keeping the previous snapshot; exports stay suspended "
                    "until the file is repaired",
                    user_id,
                    exc,
                )
                return path
        try:
            with tarfile.open(fileobj=io.BytesIO(blob), mode="r:"):
                pass
        except tarfile.TarError as exc:
            logger.warning(
                "webui snapshot for user %s is not a valid tar archive (%s); "
                "keeping the previous snapshot",
                user_id,
                exc,
            )
            return path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                view = memoryview(blob)
                while view:
                    view = view[os.write(fd, view) :]
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(temp, path)
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            if temp.exists():
                temp.unlink(missing_ok=True)
        return path

    def load_snapshot(self, user_id: int) -> bytes | None:
        """Read the user's stored snapshot; None when none exists.

        Review round 1 (T-E): a snapshot that exists but cannot be read
        (permissions, EIO) is NOT a first launch — it raises
        :class:`SnapshotUnreadableError` so the caller can degrade honestly
        (empty history, exports suspended) instead of silently treating the
        user's history as absent.
        """
        path = snapshot_path_for_user(user_id, state_root(self._state_root_override))
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.warning("webui snapshot read failed for user %s: %s", user_id, exc)
            raise SnapshotUnreadableError(
                f"snapshot for user {user_id} exists but is unreadable: {exc}"
            ) from exc

    def destroy(
        self,
        sandbox_id: str,
        user_id: int,
        *,
        restore_confirmed: bool,
        final_export: bool = True,
    ) -> None:
        """Best-effort final export, then an idempotent sandbox delete.

        404 from delete is success (the desired end state). Export failures
        NEVER block the destroy — an idle webui pod is cheaper than a leak
        (T-I: the final-export block catches Exception wholesale, the same
        posture as restore_sequence's degrade path; only the delete frees the
        pod, so it must always run).
        """
        api = self._current_api()
        if final_export:
            try:
                blob = self.export_snapshot(
                    sandbox_id, restore_confirmed=restore_confirmed, user_id=user_id
                )
                if blob is not None:
                    try:
                        self.persist_snapshot(user_id, blob)
                    except OSError as exc:
                        logger.warning(
                            "webui snapshot persist failed for user %s: %s", user_id, exc
                        )
            except Exception as exc:  # noqa: BLE001 - destroy must always run
                logger.warning(
                    "webui sandbox %s: final export failed (%s); destroying anyway",
                    sandbox_id,
                    exc,
                )
        self._destroy_raw(api, sandbox_id)
        self.clear_restore_confirmation(sandbox_id)
        self._webui_endpoints.pop(sandbox_id, None)

    def _destroy_raw(self, api: OpenSandboxApi, sandbox_id: str) -> None:
        """delete_sandbox, treating 404/unavailable as already-gone."""
        try:
            api.delete_sandbox(sandbox_id)
        except OpenSandboxApiError as exc:
            if exc.status_code == 404:
                return
            logger.warning("webui sandbox %s delete failed: %s", sandbox_id, exc)
        except Exception as exc:  # noqa: BLE001 - destroy stays idempotent
            logger.warning("webui sandbox %s delete failed: %s", sandbox_id, exc)


# ── D1: the per-instance browser proxy ──────────────────────────────────────

# Client headers never forwarded upstream (beyond the credential prefixes):
# hop-by-hop headers per RFC 7230 §6.1 plus Expect (a 100-continue the proxy
# cannot answer would wedge the upstream).
_HOP_BY_HOP_REQUEST_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "expect",
    }
)

# The credential/routing header namespace. Everything the client sends under
# these prefixes is stripped — the browser is the untrusted party here, and
# the ONLY legitimate source of these headers is the server-resolved endpoint
# map injected below.
_INJECTED_HEADER_PREFIXES = ("opensandbox-", "x-execd-")

# The four names the endpoint resolution may legitimately inject (mirrors
# client._ALLOWED_ENDPOINT_HEADER_KEYS — the complete set upstream ever sends).
_INJECTABLE_HEADER_NAMES = frozenset(
    {
        "opensandbox-secure-access",
        "opensandbox-ingress-to",
        "x-execd-access-token",
        "opensandbox-egress-auth",
    }
)

# Bounded buffering: a runaway head or chunked body fails loudly instead of
# being absorbed into control-plane memory.
_MAX_HEAD_BYTES = 64 * 1024
_MAX_DECHUNK_BYTES = 16 * 1024 * 1024

# RFC 7230 §3.2.6 token (header field-name): the only legal shape for a
# header NAME crossing the proxy. Anything else (a smuggled space, a colon,
# a bare CR/LF) would re-serialize into a different header set than the one
# parsed — the request/response-splitting vector T-A closes.
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


def _parse_head(head: bytes) -> tuple[str, list[tuple[str, str]]]:
    r"""Split a raw request/response head into its start line + header pairs.

    Raises :class:`_MalformedHead` (not a bare ValueError) so the handler can
    classify: a malformed CLIENT head is a 400, a malformed UPSTREAM head a
    502 (m2).

    Whole-head control-character check (T-A): splitting on ``\\r\\n`` removes
    every legitimate CRLF, so any CR/LF/NUL left anywhere — the start line, a
    header name, or a header value — is a bare one smuggled inside a field.
    Re-serializing such a head lets a lenient upstream read injected headers
    (``GET /x\\nOpenSandbox-Secure-Access: ...`` parses as one request line
    here but as two lines for a parser that accepts bare LF), so the entire
    head is refused, not repaired.
    """
    text = head.decode("iso-8859-1")
    lines = text.split("\r\n")
    for line in lines:
        if "\r" in line or "\n" in line or "\0" in line:
            raise _MalformedHead(f"bare CR/LF or NUL inside head line {line!r}")
    start_line = lines[0]
    headers: list[tuple[str, str]] = []
    for line in lines[1:]:
        if not line:
            continue
        if ":" not in line:
            raise _MalformedHead(f"malformed header line {line!r}")
        name, _, value = line.partition(":")
        name = name.strip()
        if not _HEADER_NAME_RE.fullmatch(name):
            raise _MalformedHead(f"header name {name!r} is not an RFC 7230 token")
        headers.append((name, value.strip()))
    return start_line, headers


def _serialize_head(start_line: str, headers: list[tuple[str, str]]) -> bytes:
    """Assemble a start line + headers back into wire bytes."""
    lines = [start_line] + [f"{name}: {value}" for name, value in headers]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("iso-8859-1")


class _BufferedSock:
    """A gevent socket plus the bytes overread past the last head/line read.

    ``recv`` semantics differ from a raw socket on purpose: ``read_head`` /
    ``read_line`` buffer until their terminator, and everything they read past
    it stays buffered so the body reader never blocks on bytes it already has.
    """

    def __init__(self, sock: Any) -> None:
        """Wrap *sock*; all I/O cooperates because *sock* is a gevent socket."""
        self._sock = sock
        self._pending = bytearray()

    def read_head(self, max_bytes: int = _MAX_HEAD_BYTES) -> bytes:
        """Read through the blank line; return the head WITHOUT its terminator."""
        while b"\r\n\r\n" not in self._pending:
            if len(self._pending) > max_bytes:
                raise _BadRequest("head over the proxy size limit")
            chunk = self._sock.recv(65536)
            if not chunk:
                raise OSError("connection closed before the head completed")
            self._pending.extend(chunk)
        head, _, rest = bytes(self._pending).partition(b"\r\n\r\n")
        self._pending = bytearray(rest)
        return head

    def read_line(self, max_bytes: int = 8192) -> bytes:
        r"""Read one ``\n``-terminated line, returning it WITH the newline."""
        while b"\n" not in self._pending:
            if len(self._pending) > max_bytes:
                raise _BadRequest("overlong framing line")
            chunk = self._sock.recv(4096)
            if not chunk:
                raise OSError("connection closed mid-line")
            self._pending.extend(chunk)
        line, _, rest = bytes(self._pending).partition(b"\n")
        self._pending = bytearray(rest)
        return line + b"\n"

    def read_exact(self, count: int) -> bytes:
        """Read exactly *count* bytes (short only on peer close).

        A non-positive *count* returns ``b""`` immediately — depth-in-depth
        against a caller that computes a negative length (T-A): the recv loop
        below would otherwise block forever waiting for bytes nobody will
        send.
        """
        if count <= 0:
            return b""
        while len(self._pending) < count:
            chunk = self._sock.recv(min(65536, count - len(self._pending)))
            if not chunk:
                break
            self._pending.extend(chunk)
        out = bytes(self._pending[:count])
        del self._pending[:count]
        return out

    def pending_bytes(self) -> int:
        """How many buffered bytes are already in hand."""
        return len(self._pending)

    def drain_pending(self) -> bytes:
        """Return and forget the buffered overread."""
        out = bytes(self._pending)
        self._pending = bytearray()
        return out

    def sendall(self, data: bytes) -> None:
        """Write bytes to the peer."""
        self._sock.sendall(data)

    def pump_from(self) -> bytes:
        """One receive step: buffered overread first, then a fresh recv."""
        if self._pending:
            return self.drain_pending()
        try:
            chunk: bytes = self._sock.recv(65536)
            return chunk
        except OSError:
            return b""

    def close(self) -> None:
        """Close the underlying socket (best effort)."""
        try:
            self._sock.close()
        except Exception:  # noqa: BLE001 - teardown is best effort
            pass


class SandboxWebuiProxy:
    """Dumb byte pipe from one local port to one pod's webui endpoint (D1).

    Port-shaped on purpose: a path-shaped proxy would depend on the webui
    frontend using relative paths everywhere — an external application whose
    behavior we cannot verify. Port-shaped, the origin is the root path and no
    rewriting assumption exists.

    HTTP mode is one request per connection (``Connection: close`` on both
    legs; SSE is unaffected because the response body streams until the
    upstream closes). WS mode forwards the client's Upgrade with the injected
    headers and, on 101, splices the two sockets in both directions.

    Authentication is NOT the proxy's business (D1: a dumb pipe): the pod's
    webui validates tokens end-to-end against the per-instance secret, so a
    local open port is equivalent to today's local webui port. Every
    successful forward reports through ``on_activity`` — this is the only
    heartbeat the single-user form has.
    """

    def __init__(
        self,
        *,
        sandbox_id: str,
        upstream_resolver: Callable[[], tuple[str, dict[str, str]]],
        on_activity: Callable[[], None] | None = None,
        bind_host: str = "0.0.0.0",  # noqa: S104 - same exposure as the local webui port
        upstream_connect_timeout_seconds: float = 15.0,
        upstream_head_timeout_seconds: float = 15.0,
    ) -> None:
        """Store wiring; nothing binds until :meth:`start`.

        The two upstream timeouts are phase-scoped on purpose (M2): the
        connect budget applies only while dialing, the head budget only while
        waiting for the upstream's answer. Neither may leak into the body
        pump — a streaming response (SSE/WS) may legitimately idle between
        bytes far longer than either budget.
        """
        self.sandbox_id = sandbox_id
        self._upstream_resolver = upstream_resolver
        self._on_activity = on_activity
        self._bind_host = bind_host
        self._upstream_connect_timeout_seconds = upstream_connect_timeout_seconds
        self._upstream_head_timeout_seconds = upstream_head_timeout_seconds
        self._server: Any = None
        self.port: int | None = None
        self._greenlets: set[Any] = set()

    # ── lifecycle ────────────────────────────────────────────────────

    def start(self, port: int = 0) -> int:
        """Bind and start serving; returns the bound port."""
        import gevent.server

        if self._server is not None:
            return int(self.port or 0)
        self._server = gevent.server.StreamServer((self._bind_host, port), self._handle)
        self._server.start()  # cooperative: serves on this thread's hub
        self.port = int(self._server.address[1])
        logger.info(
            "webui proxy for sandbox %s listening on %s:%s",
            self.sandbox_id,
            self._bind_host,
            self.port,
        )
        return self.port

    def stop(self) -> None:
        """Close the listening socket and kill every in-flight greenlet."""
        import gevent

        greenlets = list(self._greenlets)
        self._greenlets.clear()
        for greenlet in greenlets:
            try:
                greenlet.kill()
            except Exception:  # noqa: BLE001 - stop must always succeed
                pass
        if self._server is not None:
            try:
                self._server.stop()
            except Exception:  # noqa: BLE001 - stop must always succeed
                pass
            self._server = None
        gevent.sleep(0)
        self.port = None

    # ── connection handling ──────────────────────────────────────────

    def _handle(self, client_sock: Any, _address: Any) -> None:
        """Serve exactly one client connection (HTTP or WS upgrade)."""
        import gevent

        greenlet = gevent.getcurrent()
        self._greenlets.add(greenlet)
        client = _BufferedSock(client_sock)
        upstream: _BufferedSock | None = None
        try:
            head = client.read_head()
            try:
                start_line, headers = _parse_head(head)
            except _MalformedHead as exc:
                # Client garbage (m2): 400, not 502 — the upstream was never
                # contacted.
                raise _BadRequest(f"malformed request head: {exc}") from exc
            self._validate_request(headers)
            method, path, _version = self._split_request_line(start_line)

            url, inject_headers = self._upstream_resolver()
            outbound = self._build_outbound_headers(headers, inject_headers)

            # A chunked request body is de-chunked BEFORE the head is sent:
            # its TE header is stripped (hop-by-hop), so the upstream needs a
            # real Content-Length instead of framing nothing.
            request_body: bytes | None = None
            lowered = {name.lower(): value for name, value in headers}
            if "transfer-encoding" in lowered:
                request_body = self._dechunk(client)
                outbound.append(("Content-Length", str(len(request_body))))
            elif "content-length" in lowered:
                # T-A: the value must be a non-empty run of ASCII digits.
                # int() alone would accept "-1", "5_0" (→50!), "+5" and
                # " 5" — every one of those frames a different body than
                # the upstream will read after re-serialization.
                raw_length = lowered["content-length"]
                if not raw_length or not raw_length.isdigit():
                    raise _BadRequest("invalid Content-Length")
                length = int(raw_length)
                if length < 0:  # unreachable post-isdigit; depth in depth
                    raise _BadRequest("invalid Content-Length")
                request_body = client.read_exact(length) if length else b""

            if self._is_websocket_upgrade(headers):
                outbound = [
                    (name, value) for name, value in outbound if name.lower() != "connection"
                ] + [("Connection", "Upgrade")]

            sock, base_path = self._connect_upstream(url)
            upstream = _BufferedSock(sock)
            target = f"{base_path.rstrip('/')}{path}" if base_path.rstrip("/") else path
            upstream.sendall(_serialize_head(f"{method} {target} HTTP/1.1", outbound))
            if request_body:
                upstream.sendall(request_body)

            # M2: a bounded window for the upstream's ANSWER only. A gateway
            # that accepted the connection but never responds must not pin a
            # greenlet forever; once the head is in, the budget is cleared so
            # the body pump (SSE included) can idle between bytes unboundedly.
            sock.settimeout(self._upstream_head_timeout_seconds)
            try:
                response_head = upstream.read_head()
            except OSError as exc:
                raise _UpstreamError(f"upstream did not answer in time: {exc}") from exc
            finally:
                sock.settimeout(None)
            try:
                status_line, response_headers = _parse_head(response_head)
            except _MalformedHead as exc:
                # Upstream garbage (m2): 502 — the client's request was fine.
                raise _UpstreamError(f"malformed upstream response head: {exc}") from exc
            is_101 = status_line.split(" ", 2)[1:2] == ["101"]
            if self._is_websocket_upgrade(headers) and is_101:
                client.sendall(
                    _serialize_head(status_line, self._clean_response_headers(response_headers))
                )
                self._notify_activity()
                self._splice(client, upstream)
                upstream = None  # splice owns both sockets now
                return
            cleaned = [
                (name, value)
                for name, value in self._clean_response_headers(response_headers)
                if name.lower() != "connection"
            ] + [("Connection", "close")]
            client.sendall(_serialize_head(status_line, cleaned))
            self._notify_activity()
            # Single request per connection: relay the body raw (SSE included)
            # until the upstream closes, then the finally block closes client.
            self._pump(upstream, client)
        except _BadRequest as exc:
            self._send_simple(client, 400, "Bad Request", str(exc))
        except (_UpstreamError, OSError):
            self._send_simple(client, 502, "Bad Gateway", "upstream unavailable")
        except Exception:  # noqa: BLE001 - a dead connection must not kill the hub
            logger.exception("webui proxy error for sandbox %s", self.sandbox_id)
            self._send_simple(client, 502, "Bad Gateway", "proxy error")
        finally:
            self._greenlets.discard(greenlet)
            client.close()
            if upstream is not None:
                upstream.close()

    def _validate_request(self, headers: list[tuple[str, str]]) -> None:
        """Reject the malformed shapes D1 names: CL+TE, duplicate CL/injects.

        Duplicate Content-Length is a MUST-reject (RFC 7230 §3.3.2): a
        request smuggling vector, refused before the upstream is contacted —
        same 400 treatment as duplicated injection names (m2).
        """
        names = [name.lower() for name, _ in headers]
        if names.count("content-length") > 1:
            raise _BadRequest("duplicate Content-Length header")
        if "content-length" in names and "transfer-encoding" in names:
            raise _BadRequest("Content-Length and Transfer-Encoding are mutually exclusive")
        duplicated = sorted(name for name in _INJECTABLE_HEADER_NAMES if names.count(name) > 1)
        if duplicated:
            raise _BadRequest(f"duplicated injection headers: {duplicated}")

    def _build_outbound_headers(
        self, headers: list[tuple[str, str]], inject_headers: dict[str, str]
    ) -> list[tuple[str, str]]:
        """Strip client prefixes/hop-by-hop, then force-append the inject set.

        The inject map is appended AFTER the strip, so an injected name always
        overwrites whatever the client tried to send under the same name — and
        every client header in the credential prefixes is dropped regardless.
        """
        outbound: list[tuple[str, str]] = []
        for name, value in headers:
            lowered = name.lower()
            if lowered in _HOP_BY_HOP_REQUEST_HEADERS:
                continue
            if lowered.startswith(_INJECTED_HEADER_PREFIXES):
                continue
            outbound.append((name, value))
        outbound.append(("Connection", "close"))
        outbound.extend((str(key), str(value)) for key, value in inject_headers.items())
        return outbound

    def _clean_response_headers(self, headers: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """Strip credential-namespace headers the browser never needs.

        The four injectable names are allowlisted through (the gateway may
        legitimately set them); anything else under the OpenSandbox-/
        X-EXECD-/OPENSANDBOX- prefixes is dropped before the bytes reach the
        browser. Framing headers (Content-Length / Transfer-Encoding) are kept
        verbatim because the body is relayed raw behind them.
        """
        cleaned: list[tuple[str, str]] = []
        for name, value in headers:
            lowered = name.lower()
            if lowered.startswith(_INJECTED_HEADER_PREFIXES) and (
                lowered not in _INJECTABLE_HEADER_NAMES
            ):
                continue
            cleaned.append((name, value))
        return cleaned

    def _dechunk(self, client: _BufferedSock) -> bytes:
        """Read one chunked body to its terminator, bounded."""
        body = bytearray()
        while True:
            size_line = client.read_line()
            try:
                size = int(size_line.split(b";", 1)[0].strip() or b"0", 16)
            except ValueError as exc:
                raise _BadRequest("malformed chunk size") from exc
            if size < 0:
                # int(hex) happily parses "-5"; a negative size is framing
                # garbage and must be refused, not "read" (m3).
                raise _BadRequest("negative chunk size")
            if size == 0:
                while True:  # trailers up to the blank line
                    line = client.read_line()
                    if line.strip() in (b"", b"\r\n"):
                        break
                break
            if len(body) + size > _MAX_DECHUNK_BYTES:
                raise _BadRequest("chunked request body over the proxy limit")
            body.extend(client.read_exact(size))
            client.read_line()  # the CRLF after each chunk
        return bytes(body)

    def _splice(self, client: _BufferedSock, upstream: _BufferedSock) -> None:
        """Two-direction raw pump for an established 101 connection."""
        import gevent

        def pump(source: _BufferedSock, destination: _BufferedSock) -> None:
            try:
                self._pump(source, destination)
            finally:
                source.close()
                destination.close()

        up_greenlet = gevent.spawn(pump, client, upstream)
        down_greenlet = gevent.spawn(pump, upstream, client)
        self._greenlets.add(up_greenlet)
        self._greenlets.add(down_greenlet)
        try:
            gevent.joinall([up_greenlet, down_greenlet], raise_error=True)
        finally:
            self._greenlets.discard(up_greenlet)
            self._greenlets.discard(down_greenlet)

    def _pump(self, source: _BufferedSock, destination: _BufferedSock) -> None:
        """Relay raw bytes until the source closes or a write fails."""
        while True:
            chunk = source.pump_from()
            if not chunk:
                return
            destination.sendall(chunk)

    @staticmethod
    def _split_request_line(start_line: str) -> tuple[str, str, str]:
        """Split ``METHOD PATH HTTP/x``; refuse anything else."""
        parts = start_line.split(" ", 2)
        if len(parts) != 3:
            raise _BadRequest(f"malformed request line {start_line!r}")
        return parts[0], parts[1], parts[2]

    @staticmethod
    def _is_websocket_upgrade(headers: list[tuple[str, str]]) -> bool:
        """Return True when the request asks for a websocket Upgrade.

        Connection tokens are comma-separated with OPTIONAL whitespace (RFC
        7230 §6.1 list syntax), so ``Upgrade,keep-alive`` (no space) must
        still register (m7).
        """
        lowered = {name.lower(): value.lower() for name, value in headers}
        connection_tokens = [token.strip() for token in lowered.get("connection", "").split(",")]
        return lowered.get("upgrade", "") == "websocket" and "upgrade" in connection_tokens

    def _connect_upstream(self, url: str) -> tuple[Any, str]:
        """Open the upstream connection (url from the endpoint resolver)."""
        from urllib.parse import urlparse

        from gevent import socket as gevent_socket

        parsed = urlparse(url)
        host = parsed.hostname or ""
        if not host:
            raise _UpstreamError(f"upstream url {url!r} has no host")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            sock = gevent_socket.create_connection(
                (host, port), timeout=self._upstream_connect_timeout_seconds
            )
        except OSError as exc:
            raise _UpstreamError(f"upstream {host}:{port} unreachable: {exc}") from exc
        if parsed.scheme == "https":
            from gevent import ssl as gevent_ssl

            sock = gevent_ssl.wrap_socket(sock, server_hostname=host)
        # Issue #3378 review (M2): create_connection leaves its connect
        # timeout installed on the socket; it must be cleared or it becomes
        # a whole-session read timeout, and pump_from treats a read timeout
        # as peer-close — truncating any stream that idles longer than the
        # connect budget. The response head keeps its own bounded window in
        # _handle; the body pump is deliberately unbounded.
        sock.settimeout(None)
        return sock, parsed.path or ""

    def _notify_activity(self) -> None:
        """Report one successful forward to the activity callback."""
        if self._on_activity is None:
            return
        try:
            self._on_activity()
        except Exception:  # noqa: BLE001 - bookkeeping must never break the pipe
            logger.exception("webui proxy activity callback failed")

    def _send_simple(self, sock: _BufferedSock, status: int, phrase: str, reason: str) -> None:
        """Write a minimal error response (best effort)."""
        body = reason.encode()
        head = (
            f"HTTP/1.1 {status} {phrase}\r\n"
            "Content-Type: text/plain\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode()
        try:
            sock.sendall(head + body)
        except Exception:  # noqa: BLE001 - the client may already be gone
            pass


class _BadRequest(Exception):
    """A client request the D1 rules refuse with 400."""


class _MalformedHead(Exception):
    """A head that does not parse as start-line + colon-separated headers."""


class _UpstreamError(Exception):
    """The upstream endpoint could not be reached (502)."""


# ── D5: orphan reconcile (positive-trigger, web-process-hosted) ──────────────

# The ONLY production host of the reconcile is the gunicorn worker startup
# hook (app/gunicorn_worker.py), env-gated below; server.py's dev __main__
# path spawns the same helper on its own greenlet. Management scripts import
# create_app without ever setting this env, so they can never reconcile.
RECONCILE_ENV = "OPENACE_WEBUI_ORPHAN_RECONCILE"


def reconcile_webui_orphans(
    *,
    backend_config: SandboxBackendConfig | None = None,
    api_factory: Callable[[EndpointConfig], OpenSandboxApi] | None = None,
    state_root_override: str | None = None,
) -> list[str]:
    """Destroy webui pods whose process generation is not this process's.

    Belt-and-braces filtering (D5): the list query carries the provider +
    installation keys AND ``openace.webui.kind=webui``; the client-side pass
    re-checks all of them and skips anything stamped with THIS process's
    generation — a live webui pod can never be its own orphan.

    Export-before-destroy (FEAS-R4-1): each orphan's export is gated on the
    control-plane restore-confirmation record (T-E; the in-pod marker is
    writable by the supervised process and proves nothing). The destroy
    itself is idempotent (404 = success) and never blocked by an export
    failure.

    Multi-replica mutex (T-D): the sweep first writes this process's
    heartbeat, then refuses to destroy ANYTHING while another FRESH
    heartbeat exists — with the shipped 3-replica manifest, generation
    discrimination alone made every new replica destroy its siblings' live
    pods. Only when every other heartbeat is stale does the sweep run.
    """
    try:
        cfg = (
            backend_config
            if backend_config is not None
            else sandbox_config_mod.load_backend_config()
        )
    except Exception:  # noqa: BLE001 - fail-soft: a corrupt sandbox-backends.json
        # must never break the web boot that triggered this sweep.
        logger.exception("webui orphan reconcile: backend config unreadable (fail-soft)")
        return []
    if cfg is None:
        return []
    # T-D: register this process, then honour the peer mutex. Both steps are
    # fail-soft; a heartbeat root that cannot be written simply means peers
    # also cannot see us, and the sweep degrades to the previous behavior.
    write_webui_heartbeat(state_root_override)
    peers = fresh_peer_heartbeats(state_root_override)
    if peers:
        logger.info(
            "webui orphan reconcile skipped: %d other web process(es) hold fresh "
            "heartbeats (pids %s); their pods are not orphans",
            len(peers),
            sorted(str(p.get("pid")) for p in peers),
        )
        return []
    destroyed: list[str] = []
    mine = current_process_generation()
    query = {
        "openace.provider": sandbox_policy_mod.PROVIDER_NAME,
        sandbox_policy_mod.INSTALLATION_METADATA_KEY: cfg.installation_id,
        WEBUI_METADATA_KIND: WEBUI_METADATA_KIND_VALUE,
    }
    for endpoint in cfg.endpoints.values():
        api = api_factory(endpoint) if api_factory is not None else _default_api_factory(endpoint)
        try:
            rows = api.list_sandboxes(query)
        except Exception as exc:  # noqa: BLE001 - one bad tier must not stop the sweep
            logger.warning("webui orphan reconcile: list failed on %s: %s", endpoint.tier, exc)
            continue
        for row in rows:
            metadata = row.get("metadata") or {}
            sandbox_id = str(row.get("id") or "")
            # Client-side re-check: a server that ignored the query filters
            # must not be able to hand us another installation's pods.
            if metadata.get("openace.provider") != sandbox_policy_mod.PROVIDER_NAME:
                continue
            if metadata.get(sandbox_policy_mod.INSTALLATION_METADATA_KEY) != cfg.installation_id:
                continue
            if metadata.get(WEBUI_METADATA_KIND) != WEBUI_METADATA_KIND_VALUE:
                continue
            if metadata.get(WEBUI_METADATA_GENERATION) == mine:
                continue  # ours: a live pod of this very process
            if not sandbox_id:
                continue
            _export_orphan(api, sandbox_id, metadata, state_root_override, endpoint)
            _delete_orphan(api, sandbox_id, state_root_override)
            destroyed.append(sandbox_id)
    if destroyed:
        logger.info(
            "webui orphan reconcile destroyed %d stale pod(s): %s", len(destroyed), destroyed
        )
    return destroyed


def _export_orphan(
    api: OpenSandboxApi,
    sandbox_id: str,
    metadata: dict,
    state_root_override: str | None,
    endpoint: EndpointConfig,
) -> None:
    """Best-effort export of one orphan, gated on the CP restore record.

    Review round 1 (T-E): the gate is the CONTROL-PLANE confirmation record
    under the state root, never the in-pod ``/workspace/.openace-restore-done``
    marker — the supervised process can forge that, and a forged marker would
    let an empty (or attacker-shaped) tree overwrite the user's stored
    snapshot. A missing record (degraded launch, unwritable state root in the
    crash window, or a pre-T-E pod) skips the export: conservative.
    """
    owner_raw = str(metadata.get(WEBUI_METADATA_OWNER) or "").strip()
    try:
        owner = int(owner_raw)
    except ValueError:
        logger.warning(
            "webui orphan %s: owner %r is not a user id; skipping export",
            sandbox_id,
            owner_raw,
        )
        return
    launcher = SandboxedWebuiLauncher(
        backend_config=None,
        # m5: the tier's endpoint must be injected explicitly — the exporter
        # never resolves one itself, and without it _run_background_command
        # skips the uid/gid credential switch the endpoint's attestations
        # require for the in-pod tar.
        endpoint=endpoint,
        state_root_override=state_root_override,
    )
    if not launcher.restore_confirmed_on_cp(sandbox_id):
        logger.info(
            "webui orphan %s: no control-plane restore-confirmation record "
            "(degraded launch, forged in-pod marker, or pre-upgrade pod); "
            "skipping export, keeping the stored snapshot",
            sandbox_id,
        )
        return
    command = f"tar -cf {shlex.quote(WEBUI_STATE_TAR_PATH)} -C {shlex.quote(WEBUI_STATE_POD_DIR)} ."
    # Reuse the launcher's background-command + bounded-download machinery.
    if not launcher._run_background_command(api, sandbox_id, command):  # noqa: SLF001 - same module
        logger.warning("webui orphan %s: snapshot tar did not confirm", sandbox_id)
        return
    try:
        blob = api.download_file(
            sandbox_id,
            WEBUI_STATE_TAR_PATH,
            max_bytes=launcher._max_state_bytes,  # noqa: SLF001
        )
    except OpenSandboxApiError as exc:
        logger.warning(
            "webui orphan %s: snapshot download failed (%s); keeping the previous snapshot",
            sandbox_id,
            exc,
        )
        return
    try:
        launcher.persist_snapshot(owner, blob)
    except OSError as exc:
        logger.warning("webui orphan %s: snapshot persist failed: %s", sandbox_id, exc)


def _delete_orphan(
    api: OpenSandboxApi,
    sandbox_id: str,
    state_root_override: str | None = None,
) -> None:
    """Idempotent delete; failures are logged, never raised (retry next boot).

    The CP restore-confirmation record goes with the pod (the id never
    returns); fail-soft — a leftover record only makes a future export-gate
    check stale-harmless for an id that no longer exists.
    """
    try:
        api.delete_sandbox(sandbox_id)
    except OpenSandboxApiError as exc:
        if exc.status_code != 404:
            logger.warning("webui orphan %s: delete failed: %s", sandbox_id, exc)
    except Exception as exc:  # noqa: BLE001 - the sweep must go on
        logger.warning("webui orphan %s: delete failed: %s", sandbox_id, exc)
    try:
        record = state_root(state_root_override) / RESTORE_CONFIRMED_DIRNAME / sandbox_id
        record.unlink(missing_ok=True)
    except OSError:
        pass


def _default_api_factory(endpoint: EndpointConfig) -> OpenSandboxApi:
    """Build the production HTTP API for *endpoint*."""
    from app.modules.workspace.autonomous.sandbox.opensandbox.client import HttpOpenSandboxApi

    return HttpOpenSandboxApi(endpoint)


def maybe_spawn_webui_orphan_reconcile() -> bool:
    """Env-gated, TESTING-guarded, fail-soft reconcile spawn (D5/FEAS-R4-3).

    Returns True when a reconcile greenlet was spawned. The reconcile work
    (list + per-orphan export + destroy + polling) NEVER runs inline in the
    caller's greenlet — the worker boot hook and server.py's dev path both
    spawn it separately, so a slow sweep cannot block request serving.
    """
    if os.environ.get(RECONCILE_ENV) != "1":
        return False
    # TESTING guard (FEAS-M3), matching the app/__init__.py precedent: a test
    # process must never sweep real sandbox backends.
    if os.environ.get("PYTEST_VERSION") or os.environ.get("TESTING"):
        logger.info("webui orphan reconcile skipped (test process)")
        return False

    def _run() -> None:
        try:
            reconcile_webui_orphans()
        except Exception:  # noqa: BLE001 - fail-soft: web boot must not fail
            logger.exception("webui orphan reconcile failed (fail-soft)")

    import gevent

    gevent.spawn(_run)
    logger.info("webui orphan reconcile spawned (worker startup)")
    return True
