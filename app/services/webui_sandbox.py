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
LLLM credentials), D6 (export guarded on the restore-done ground truth).
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import secrets
import shlex
import tarfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
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

# Restore gate ground truth (D5/D6). The entrypoint blocks on this file; the
# control plane touches it only after a confirmed snapshot restore, and both
# the export guard and the orphan reconciler read the same file as the single
# source of truth for "this pod ever completed a restore".
RESTORE_MARKER_PATH = "/workspace/.openace-restore-done"

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

# Metadata keys for the webui process namespace (D5/FEAS-Q1). Deliberately
# independent of openace.generation, which carries the workflow generation.
WEBUI_METADATA_KIND = "openace.webui.kind"
WEBUI_METADATA_GENERATION = "openace.webui.process_generation"
WEBUI_METADATA_OWNER = "openace.webui.owner"
WEBUI_METADATA_KIND_VALUE = "webui"

# Per-process generation: reconcile destroys webui pods whose generation is not
# this value (D5). Module-level so every launcher in this process agrees with
# the reconciler.
_PROCESS_GENERATION = uuid.uuid4().hex


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


def proxy_token_expiry(token: str, *, fallback_ttl_minutes: int) -> datetime:
    """Read the minted proxy token's expiry out of its signed payload.

    ``generate_proxy_token`` returns only the token string; the expiry lives in
    the base64 JSON payload under ``exp`` (ISO datetime). Renew clamps to this
    moment. Decode failures fall back to the effective TTL the mint used — the
    same duration, recomputed rather than trusted from an unreadable token.
    """
    import base64
    import json as _json

    try:
        payload_b64 = token.split(".", 1)[0]
        payload = _json.loads(base64.b64decode(payload_b64))
        return datetime.fromisoformat(str(payload["exp"]))
    except Exception:  # noqa: BLE001 - conservative fallback, same duration
        return datetime.now() + timedelta(minutes=fallback_ttl_minutes)


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
        self._endpoint: EndpointConfig | None = None
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
        """Mint the instance's LLM proxy token; return (token, expiry, ttl min)."""
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
        expiry = proxy_token_expiry(token, fallback_ttl_minutes=ttl_minutes)
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
        from app.modules.workspace.autonomous.sandbox.opensandbox.provider import (
            OpenSandboxProvider,
        )
        from app.modules.workspace.autonomous.sandbox.types import SandboxHandle

        provider = OpenSandboxProvider(
            self._require_backend_config(), api_factory=lambda _endpoint: api
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
        from app.services.workspace_isolation_contract import (
            register_sandbox_runtime_verified,
        )

        register_sandbox_runtime_verified(kernel_enforced=kernel_enforced)
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
        confirmed successful ``touch`` sets restore-confirmed — the D6 export
        guard keys on that state, and a degraded (timeout/failed-extract) start
        must leave it False so an empty tree can never overwrite a good
        snapshot. On degrade the entrypoint's own 60s counter lets the webui
        start anyway.
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
        logger.info("webui sandbox %s: snapshot restore confirmed", sandbox_id)
        return True

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
        """
        from app.auth.decorators import WEBUI_TOKEN_TTL_SECONDS

        ttl_minutes = self._effective_proxy_ttl_minutes()
        token_expiry = proxy_token_expiry(proxy_token, fallback_ttl_minutes=ttl_minutes)
        target = min(
            datetime.now() + timedelta(seconds=int(WEBUI_TOKEN_TTL_SECONDS)),
            token_expiry,
        )
        expires_at = target.isoformat()
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

    def export_snapshot(self, sandbox_id: str, *, restore_confirmed: bool) -> bytes | None:
        """Read the pod's session tree as a bounded tar, or None.

        The export guard (FEAS-M2/FEAS-R4-1): an instance whose restore was
        never confirmed — degraded start, the CP touch never succeeded — never
        exports. Its empty tree must not overwrite the last good snapshot.
        Over-ceiling exports are skipped with a WARNING (the user's history
        freezes at the old snapshot; documented semantics, FEAS-Q2).
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
            f"tar -cf {shlex.quote(WEBUI_STATE_TAR_PATH)} "
            f"-C {shlex.quote(WEBUI_STATE_POD_DIR)} ."
        )
        if not self._run_background_command(api, sandbox_id, command):
            logger.warning("webui sandbox %s: snapshot tar did not confirm", sandbox_id)
            return None
        try:
            return api.download_file(
                sandbox_id, WEBUI_STATE_TAR_PATH, max_bytes=self._max_state_bytes
            )
        except OpenSandboxApiError as exc:
            logger.warning(
                "webui sandbox %s: snapshot download failed (%s); keeping the " "previous snapshot",
                sandbox_id,
                exc,
            )
            return None

    def persist_snapshot(self, user_id: int, blob: bytes) -> Path:
        """Write the snapshot to its independent slot, atomically and 0o600."""
        path = snapshot_path_for_user(user_id, state_root(self._state_root_override))
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            with open(temp, "wb") as handle:
                handle.write(blob)
            os.chmod(temp, 0o600)
            os.replace(temp, path)
        finally:
            if temp.exists():
                temp.unlink(missing_ok=True)
        return path

    def load_snapshot(self, user_id: int) -> bytes | None:
        """Read the user's stored snapshot, or None when none exists."""
        path = snapshot_path_for_user(user_id, state_root(self._state_root_override))
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.warning("webui snapshot read failed for user %s: %s", user_id, exc)
            return None

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
        never block the destroy — an idle webui pod is cheaper than a leak.
        """
        api = self._current_api()
        if final_export:
            blob = self.export_snapshot(sandbox_id, restore_confirmed=restore_confirmed)
            if blob is not None:
                try:
                    self.persist_snapshot(user_id, blob)
                except OSError as exc:
                    logger.warning("webui snapshot persist failed for user %s: %s", user_id, exc)
        self._destroy_raw(api, sandbox_id)
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
