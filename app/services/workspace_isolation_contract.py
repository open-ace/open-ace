"""Versioned capability contract for local interactive workspace multi-user
isolation (Issue #3374).

The contract reports what this deployment actually enforces, derived from
runtime state only (webui manager config, environment, euid, platform).
It deliberately never upgrades the reported isolation level: ``os_user``
means per-user OS accounts on a shared host kernel, not strong runtime
isolation (no namespaces, no egress policy). Reason messages stay free of
deployment specifics; see docs/workspace-isolation-capabilities.md for
deployment requirements. Bump POLICY_REVISION whenever derivation semantics
or the entry-point matrix change.

Issue #3378 adds the ``sandboxed`` level (WebUI pods on an OpenSandbox
backend). Its kernel/egress enforcement is only verifiable per-pod via boot
probes, so the sandboxed snapshot reports those dimensions as unsupported
with an explicit ``sandbox_runtime_unverified`` reason until the first
successful pod probe memoizes the upgrade (in-process; resets on restart).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

# Revision 3 (2026-09-12.1, Issue #3378): new sandboxed level with a zero-pod
# fail-closed probe; new ``kernel`` dimension (os_user reports it unsupported
# — shared host kernel); sandboxed snapshots report
# enforced=(identity, filesystem, environment, process, resources) with
# kernel/network_egress unverified-until-probed; evaluate_isolation_requirement
# gates sandboxed requests on the probe reasons instead of the OS-account
# chain.
POLICY_REVISION = "2026-09-12.1"

ISOLATION_LEVEL_NONE = "none"
ISOLATION_LEVEL_OS_USER = "os_user"
ISOLATION_LEVEL_SANDBOXED = "sandboxed"

_LEVEL_ORDER = {
    ISOLATION_LEVEL_NONE: 0,
    ISOLATION_LEVEL_OS_USER: 1,
    ISOLATION_LEVEL_SANDBOXED: 2,
}
SUPPORTED_ISOLATION_LEVELS = tuple(_LEVEL_ORDER)

DIMENSION_IDENTITY = "identity"
DIMENSION_FILESYSTEM = "filesystem"
DIMENSION_ENVIRONMENT = "environment"
DIMENSION_PROCESS = "process"
DIMENSION_RESOURCES = "resources"
DIMENSION_NETWORK_EGRESS = "network_egress"
DIMENSION_KERNEL = "kernel"

ALL_DIMENSIONS = (
    DIMENSION_IDENTITY,
    DIMENSION_FILESYSTEM,
    DIMENSION_ENVIRONMENT,
    DIMENSION_PROCESS,
    DIMENSION_RESOURCES,
    DIMENSION_NETWORK_EGRESS,
    DIMENSION_KERNEL,
)

_OS_USER_ENFORCED = (
    DIMENSION_IDENTITY,
    DIMENSION_FILESYSTEM,
    DIMENSION_ENVIRONMENT,
    DIMENSION_PROCESS,
)
# Issue #3378: os_user shares the host kernel by definition (module docstring),
# so the kernel dimension is honestly reported as unsupported.
_OS_USER_UNSUPPORTED = (DIMENSION_RESOURCES, DIMENSION_NETWORK_EGRESS, DIMENSION_KERNEL)

# Config-derived facts for sandboxed WebUI pods: one pod per instance with a
# per-instance token secret, an image_allowlisted digest-pinned image, and
# resource limits that build_create_request always attaches (defaults are
# bounds too). Kernel and egress enforcement need a live pod probe (D3).
_SANDBOXED_ENFORCED = (
    DIMENSION_IDENTITY,
    DIMENSION_FILESYSTEM,
    DIMENSION_ENVIRONMENT,
    DIMENSION_PROCESS,
    DIMENSION_RESOURCES,
)
_SANDBOXED_UNSUPPORTED = (DIMENSION_KERNEL, DIMENSION_NETWORK_EGRESS)

BACKEND_PER_USER = "qwen-code-webui-per-user"
BACKEND_SHARED = "qwen-code-webui-shared"
BACKEND_OPENSANDBOX = "opensandbox"

# Reason codes produced by the sandboxed readiness probe; the user-url gate
# surfaces one of these when a sandboxed request outruns the snapshot level.
SANDBOX_PROBE_REASON_CODES = (
    "sandbox_backend_unconfigured",
    "sandbox_tier_missing",
    "webui_image_missing",
    "webui_image_not_pinned",
    "webui_image_not_allowed",
    "sandbox_proxy_unreachable",
    "sandbox_proxy_token_ttl_too_short",
)

# Issue #3378 (D3): the boot-probe upgrade memo, keyed per tier. Kernel and
# egress enforcement are only verifiable per-pod, so the sandboxed snapshot
# reports them unverified until the WebUI launcher's first successful pod probe
# on THAT tier registers the upgrade here. Per-tier keying (conformance review
# Q1): in a multi-tier deployment (gVisor + Kata) a later Kata pod must not
# overwrite a gVisor tier's earlier positive kernel upgrade (and vice versa) —
# each snapshot consults only its own tier's memo. Still a process-lifetime
# memo — a restart reverts every tier to the static (unverified) view, which is
# the documented honesty contract.
_SANDBOX_RUNTIME_MEMO: dict[str, dict[str, bool]] = {}


def register_sandbox_runtime_verified(*, tier: str, kernel_enforced: bool) -> None:
    """Record that a sandbox pod's boot probes passed on ``tier`` (launcher hook).

    Called by ``SandboxedWebuiLauncher`` after the runtime-class and egress
    probes confirm the pod on the tier the pod was launched against.
    ``kernel_enforced`` distinguishes the two probe directions (provider
    ``_run_probes``): gVisor identifies itself positively in the kernel probe,
    while a Kata result is negative-only — it rules out gVisor but cannot
    distinguish Kata from an unisolated runc container, so the kernel dimension
    must NOT upgrade on it (reason ``sandbox_runtime_kata_negative_only``).
    """
    _SANDBOX_RUNTIME_MEMO[str(tier)] = {
        "verified": True,
        "kernel_enforced": bool(kernel_enforced),
    }


def sandbox_runtime_verification(tier: str) -> tuple[bool, bool]:
    """Return ``(verified, kernel_enforced)`` for *tier* — see register hook."""
    state = _SANDBOX_RUNTIME_MEMO.get(str(tier))
    if state is None:
        return (False, False)
    return (state["verified"], state["kernel_enforced"])


def _reset_sandbox_runtime_verification() -> None:
    """Reset the boot-probe memo for every tier (test isolation only)."""
    _SANDBOX_RUNTIME_MEMO.clear()


# Static, revision-gated audit result (design doc §2.4). Values:
# enforced | partial | separate_contract.
ENTRY_POINT_STATUSES = {
    "webui": "enforced",
    "filesystem_api": "partial",
    "session_history": "enforced",
    "terminal": "partial",
    "vscode": "partial",
    "autonomous": "separate_contract",
}


def _current_platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    return sys.platform


def is_valid_isolation_level(value: str) -> bool:
    return value in _LEVEL_ORDER


def isolation_level_at_least(have: str, want: str) -> bool:
    return _LEVEL_ORDER[have] >= _LEVEL_ORDER[want]


@dataclass(frozen=True)
class IsolationReason:
    """A machine-readable cause code with a human-readable explanation."""

    code: str
    message: str

    def public_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class IsolationCapabilitySnapshot:
    """Immutable view of what this deployment enforces for local workspaces."""

    supported: bool
    backend: str
    isolation_level: str
    enforced: tuple[str, ...]
    unsupported: tuple[str, ...]
    reasons: tuple[IsolationReason, ...]
    policy_revision: str = POLICY_REVISION

    def public_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "local_workspace_multi_user": "supported" if self.supported else "unsupported",
            "backend": self.backend,
            "isolation_level": self.isolation_level,
            "enforced": list(self.enforced),
            "unsupported": list(self.unsupported),
            "reasons": [r.public_dict() for r in self.reasons],
            "policy_revision": self.policy_revision,
        }
        # Issue #3374 review #11: the entry-point matrix describes multi-user
        # enforcement; emitting it on an unsupported snapshot would claim
        # "webui: enforced" next to "no isolation at all".
        if self.supported:
            data["entry_points"] = dict(ENTRY_POINT_STATUSES)
        return data


def _unsupported(reason_code: str, message: str) -> IsolationCapabilitySnapshot:
    return IsolationCapabilitySnapshot(
        supported=False,
        backend=BACKEND_SHARED,
        isolation_level=ISOLATION_LEVEL_NONE,
        enforced=(),
        unsupported=ALL_DIMENSIONS,
        reasons=(IsolationReason(reason_code, message),),
    )


def _sandboxed_readiness(config: Any) -> tuple[bool, str, IsolationReason | None]:
    """Zero-pod fail-closed probe for the sandboxed level (Issue #3378).

    Verifies the configuration plane only — backend config parses, a tier
    exists, a digest-pinned allowlisted webui image is set, the control
    plane's LLM-proxy URL would be reachable under the tier's egress policy,
    and the effective webui proxy-token TTL covers the pod TTL. Never creates
    a pod; kernel/egress enforcement stays ``sandbox_runtime_unverified``
    until the first successful pod boot probe upgrades it (launcher-side,
    in-process memo).

    Returns ``(ok, tier, reason)``; on failure ``reason`` carries one of
    :data:`SANDBOX_PROBE_REASON_CODES`.
    """
    from app.modules.workspace.autonomous.sandbox.opensandbox.config import load_backend_config

    try:
        backend_cfg = load_backend_config()
    except Exception as exc:  # noqa: BLE001 - fail closed on any config error
        return (
            False,
            "",
            IsolationReason(
                "sandbox_backend_unconfigured",
                f"The sandbox backend config cannot be loaded ({exc}); the "
                "sandboxed isolation level is unavailable until it is repaired.",
            ),
        )
    if backend_cfg is None:
        return (
            False,
            "",
            IsolationReason(
                "sandbox_backend_unconfigured",
                "No OpenSandbox backend is configured on this deployment; the "
                "sandboxed isolation level is unavailable.",
            ),
        )

    tier = (getattr(config, "sandbox_tier", "") or "").strip() or backend_cfg.default_tier
    endpoint = backend_cfg.endpoints.get(tier)
    if endpoint is None:
        return (
            False,
            tier,
            IsolationReason(
                "sandbox_tier_missing",
                f"Sandbox endpoint tier {tier!r} does not exist in the backend "
                "config; the sandboxed isolation level is unavailable.",
            ),
        )

    image = (endpoint.webui_image or "").strip()
    if not image:
        return (
            False,
            tier,
            IsolationReason(
                "webui_image_missing",
                f"Sandbox endpoint tier {tier!r} has no webui_image configured; "
                "the sandboxed isolation level needs an image containing "
                "qwen-code-webui.",
            ),
        )
    from app.modules.workspace.autonomous.sandbox.opensandbox.config import SandboxConfigError

    try:
        # Same digest-pinning discipline as default_image, but validated here
        # so a bad value degrades only the sandboxed level, not the shared
        # backend config autonomous tasks depend on.
        from app.modules.workspace.autonomous.sandbox.opensandbox.config import (
            _require_digest_pinned,
        )

        _require_digest_pinned(image, f"endpoint {tier!r} webui_image")
    except SandboxConfigError as exc:
        return False, tier, IsolationReason("webui_image_not_pinned", str(exc))
    if backend_cfg.image_allowlist and image not in backend_cfg.image_allowlist:
        return (
            False,
            tier,
            IsolationReason(
                "webui_image_not_allowed",
                f"webui_image for tier {tier!r} is not in image_allowlist; "
                "refusing to launch interactive pods from an unlisted image.",
            ),
        )

    # Proxy reachability: the pod must reach the control plane's LLM proxy.
    # The static probe needs a URL without a request in hand, so it uses the
    # configured webui_callback_url — deployments wanting the sandboxed level
    # must set it (the per-request host is only known at launch time).
    callback_url = (getattr(config, "webui_callback_url", "") or "").strip()
    if not callback_url:
        return (
            False,
            tier,
            IsolationReason(
                "sandbox_proxy_unreachable",
                "workspace.webui_callback_url is not set; the control-plane URL "
                "sandbox pods must reach back for the LLM proxy cannot be "
                "verified without it.",
            ),
        )
    from app.modules.workspace.autonomous.sandbox.opensandbox import policy as sandbox_policy

    try:
        sandbox_policy.assert_proxy_reachable({"OPENACE_PROXY_URL": callback_url}, endpoint)
    except Exception as exc:  # noqa: BLE001 - fail closed on any reachability error
        return (
            False,
            tier,
            IsolationReason(
                "sandbox_proxy_unreachable",
                f"The sandbox egress policy would block the control-plane LLM proxy ({exc}).",
            ),
        )

    # Proxy-token TTL must cover the pod TTL: the token is baked into the pod
    # env at create time and cannot be refreshed without a restart, so a
    # shorter effective TTL would strand sessions with dead LLM auth while
    # health checks still pass.
    from app.auth.decorators import WEBUI_TOKEN_TTL_SECONDS
    from app.modules.workspace.api_key_proxy import get_api_key_proxy_service

    effective_ttl_minutes = get_api_key_proxy_service().effective_proxy_token_ttl_minutes("webui")
    if effective_ttl_minutes * 60 < WEBUI_TOKEN_TTL_SECONDS:
        return (
            False,
            tier,
            IsolationReason(
                "sandbox_proxy_token_ttl_too_short",
                f"The effective webui proxy-token TTL ({effective_ttl_minutes}m) "
                f"is shorter than the WebUI token TTL "
                f"({WEBUI_TOKEN_TTL_SECONDS}s) a sandboxed pod is created with; "
                "raise OPENACE_PROXY_TOKEN_TTL_WEBUI_MINUTES to at least the "
                "pod TTL or the pod outlives its LLM credentials.",
            ),
        )
    return True, tier, None


def build_workspace_isolation_snapshot(
    manager: Any = None,
) -> IsolationCapabilitySnapshot:
    """Derive the isolation capability snapshot from live runtime state.

    When a manager is available the snapshot also consults the launch-path
    readiness probe, so the contract and the /user-url gate can never
    disagree about the same host (Issue #3374 review #4). Without a manager
    (read-only capability GET before any workspace activity) the snapshot is
    derived from disk config only and cannot verify the launch path — that
    limitation is documented in the capability docs.

    Issue #3378: the sandboxed probe runs first and independently — it does
    not check platform or multi_user_mode (the pods live on a remote
    cluster; single-user + sandboxed is a legitimate hardening). When it
    passes the snapshot reports the sandboxed level with kernel/egress
    unverified-until-probed. When it fails the os_user chain below runs
    exactly as before, with the sandbox failure reason attached whenever a
    sandbox backend is actually configured (an unconfigured backend adds no
    noise — that is every default deployment).
    """
    readiness_probe = None
    config: Any = None
    if manager is None:
        from app.services.webui_manager import peek_webui_manager

        manager = peek_webui_manager()
    if manager is None:
        from app.services.webui_manager import read_workspace_config

        config = read_workspace_config()
    else:
        config = getattr(manager, "config", None)
        readiness_probe = getattr(manager, "per_user_launch_readiness", None)

    if config is None or not getattr(config, "enabled", False):
        return _unsupported(
            "webui_disabled",
            "WebUI manager is disabled; no interactive workspace runtime is available.",
        )

    sandbox_ok, sandbox_tier, sandbox_reason = _sandboxed_readiness(config)
    if sandbox_ok:
        # D3 two-state mapping: the static view reports kernel/egress
        # unverified; once the launcher's first pod boot probe succeeded the
        # in-process memo upgrades them (egress always — its enforcement plane
        # was read live; kernel only for a positive gVisor identification).
        # The memo is keyed per tier (review Q1): a pod verified on another tier
        # never upgrades THIS tier's snapshot.
        verified, kernel_enforced = sandbox_runtime_verification(sandbox_tier)
        if verified:
            enforced: tuple[str, ...] = _SANDBOXED_ENFORCED + (DIMENSION_NETWORK_EGRESS,)
            reasons: tuple[IsolationReason, ...] = ()
            if kernel_enforced:
                enforced = enforced + (DIMENSION_KERNEL,)
            else:
                reasons = (
                    IsolationReason(
                        "sandbox_runtime_kata_negative_only",
                        "The runtime boot probe passed in the negative "
                        "direction only (Kata rules out gVisor but no "
                        "hypervisor signal is observable), so the kernel "
                        "dimension stays unverified; network egress is "
                        "verified against the tier's enforcement plane.",
                    ),
                )
            return IsolationCapabilitySnapshot(
                supported=True,
                backend=f"{BACKEND_OPENSANDBOX}:{sandbox_tier}",
                isolation_level=ISOLATION_LEVEL_SANDBOXED,
                enforced=enforced,
                unsupported=tuple(d for d in ALL_DIMENSIONS if d not in enforced),
                reasons=reasons,
            )
        return IsolationCapabilitySnapshot(
            supported=True,
            backend=f"{BACKEND_OPENSANDBOX}:{sandbox_tier}",
            isolation_level=ISOLATION_LEVEL_SANDBOXED,
            enforced=_SANDBOXED_ENFORCED,
            unsupported=_SANDBOXED_UNSUPPORTED,
            reasons=(
                IsolationReason(
                    "sandbox_runtime_unverified",
                    "The sandboxed level is verified on the configuration "
                    "plane only; kernel and egress enforcement are confirmed "
                    "per-pod by boot probes after the first launch (resets "
                    "on control-plane restart).",
                ),
            ),
        )
    # A configured-but-failing sandbox backend is observable on the os_user
    # snapshot so the gate can surface the exact reason for sandboxed
    # requests; an absent backend (every default deployment) stays silent.
    sandbox_reasons: tuple[IsolationReason, ...] = ()
    if sandbox_reason is not None and sandbox_reason.code != "sandbox_backend_unconfigured":
        sandbox_reasons = (sandbox_reason,)

    # Linux only: macOS skips system-user creation (utils/workspace.py), so
    # Open ACE cannot establish or verify the identity mapping there; Windows
    # forces a single shared instance.
    if _current_platform() != "linux":
        return _with_extra_reasons(
            _unsupported(
                "platform_unsupported",
                "Per-user workspace isolation is supported on Linux deployments "
                "only; this platform runs a single shared WebUI instance.",
            ),
            sandbox_reasons,
        )

    if not getattr(config, "multi_user_mode", False):
        return _with_extra_reasons(
            _unsupported(
                "multi_user_mode_disabled",
                "Multi-user workspace mode is disabled; the interactive workspace "
                "intentionally runs one shared instance.",
            ),
            sandbox_reasons,
        )

    # The launch-path readiness probe is the verification: it checks the
    # actual per-user launch mechanism (WebUI resolution, dev-directory mode,
    # launch wrapper, sudo) regardless of Docker vs package installation —
    # a package-method host that really runs `sudo -u` reports os_user.
    # Without a manager (read-only capability GET on a cold worker) the
    # probe cannot run; the snapshot is reported as provisional via an
    # explicit reason instead of silently claiming a verified level.
    if readiness_probe is not None:
        degradation = readiness_probe()
        if degradation:
            return _with_extra_reasons(
                _unsupported(
                    "launch_path_degraded",
                    "This deployment's WebUI launch path cannot host per-user "
                    f"instances ({degradation}); see the workspace isolation "
                    "documentation.",
                ),
                sandbox_reasons,
            )
        return IsolationCapabilitySnapshot(
            supported=True,
            backend=BACKEND_PER_USER,
            isolation_level=ISOLATION_LEVEL_OS_USER,
            enforced=_OS_USER_ENFORCED,
            unsupported=_OS_USER_UNSUPPORTED,
            reasons=sandbox_reasons,
        )

    return IsolationCapabilitySnapshot(
        supported=True,
        backend=BACKEND_PER_USER,
        isolation_level=ISOLATION_LEVEL_OS_USER,
        enforced=_OS_USER_ENFORCED,
        unsupported=_OS_USER_UNSUPPORTED,
        reasons=(
            IsolationReason(
                "launch_path_unverified",
                "The WebUI launch path has not been probed on this worker "
                "yet; treat this level as provisional until the manager is "
                "initialized.",
            ),
        )
        + sandbox_reasons,
    )


def _with_extra_reasons(
    snapshot: IsolationCapabilitySnapshot, extra: tuple[IsolationReason, ...]
) -> IsolationCapabilitySnapshot:
    if not extra:
        return snapshot
    return IsolationCapabilitySnapshot(
        supported=snapshot.supported,
        backend=snapshot.backend,
        isolation_level=snapshot.isolation_level,
        enforced=snapshot.enforced,
        unsupported=snapshot.unsupported,
        reasons=snapshot.reasons + extra,
    )


def resolve_required_floor(config: Any, snapshot: IsolationCapabilitySnapshot) -> str:
    """Resolve the server-side isolation floor (Issue #3374 PR review round 2).

    An explicitly configured ``workspace.required_isolation_level`` wins when
    valid; unset — or a hand-edited invalid value (warned) — derives from what
    the deployment actually verifies (``snapshot.isolation_level``), NOT from
    a flat mode default: a package-method or launch-degraded host must not
    have every default workspace request rejected by an unreachable floor.
    The request parameter can only raise above this floor, never lower it.
    """
    explicit = (getattr(config, "required_isolation_level", "") or "").strip()
    degraded = snapshot.isolation_level == ISOLATION_LEVEL_NONE and _reason_is_degraded(snapshot)
    if degraded:
        # The floor follows verified capability — it can only mirror a
        # degradation downward, never detect one. Make BOTH directions
        # audible (PR review round 5): a pinned floor above a degraded
        # probe rejects every launch and must not fail silently, while a
        # derived multi-user floor keeps serving on the shared account.
        # Late import avoids a logging dependency at module import time.
        import logging

        # PR review round 6: only a VALID pin above the degraded probe
        # actually rejects; an invalid value falls through to the derived
        # floor below and must not claim rejections that are not happening.
        if explicit and is_valid_isolation_level(explicit) and explicit != ISOLATION_LEVEL_NONE:
            logging.getLogger(__name__).warning(
                "Workspace launch path is degraded (%s); the pinned "
                "required_isolation_level '%s' is above what this host can "
                "verify and will REJECT all launches until the launch path "
                "is repaired",
                _first_degradation(snapshot),
                explicit,
            )
        elif getattr(config, "multi_user_mode", False):
            logging.getLogger(__name__).warning(
                "Multi-user workspace isolation floor derived as 'none' "
                "(launch path degraded: %s); default launches will NOT be "
                "per-user isolated — set workspace.required_isolation_level "
                "to pin a hard floor",
                _first_degradation(snapshot),
            )
    if explicit:
        if is_valid_isolation_level(explicit):
            return explicit
        import logging

        logging.getLogger(__name__).warning(
            "Invalid workspace.required_isolation_level %r; using derived default",
            explicit,
        )
    return snapshot.isolation_level


def _reason_is_degraded(snapshot: IsolationCapabilitySnapshot) -> bool:
    return any(r.code in ("launch_path_degraded",) for r in snapshot.reasons)


def _first_degradation(snapshot: IsolationCapabilitySnapshot) -> str:
    for r in snapshot.reasons:
        if r.code == "launch_path_degraded":
            return r.message
    return "unknown"


def evaluate_isolation_requirement(
    required_level: str,
    *,
    snapshot: IsolationCapabilitySnapshot,
    system_account: str | None,
    manager: Any,
) -> IsolationReason | None:
    """Gate an explicit isolation requirement before launching a workspace.

    Returns None when the requirement is satisfiable, else the structured
    rejection reason. ``system_account`` must be the explicit DB mapping
    (no username fallback) — a missing mapping is a rejection, not a
    silent convention-derived account (Issue #3374).
    """
    if not is_valid_isolation_level(required_level):
        return IsolationReason(
            "invalid_isolation_level",
            f"Unknown isolation level '{required_level}'; expected one of "
            f"{', '.join(SUPPORTED_ISOLATION_LEVELS)}.",
        )
    if required_level == ISOLATION_LEVEL_NONE:
        return None
    if required_level == ISOLATION_LEVEL_SANDBOXED:
        # Issue #3378: identity for sandboxed pods is the per-instance webui
        # token, not an OS account — the identity_mapping / per-user-launch
        # chain below is os_user-specific and does not apply. The gate is the
        # capability snapshot itself: when the level is unmet, surface the
        # sandbox probe's exact reason instead of the generic level message.
        if not isolation_level_at_least(snapshot.isolation_level, ISOLATION_LEVEL_SANDBOXED):
            for reason in snapshot.reasons:
                if reason.code in SANDBOX_PROBE_REASON_CODES:
                    return reason
            return IsolationReason(
                "isolation_level_unsupported",
                f"Requested isolation level 'sandboxed' exceeds what this "
                f"deployment enforces ('{snapshot.isolation_level}'); refusing to "
                "silently launch with weaker isolation.",
            )
        return None
    if not isolation_level_at_least(snapshot.isolation_level, required_level):
        return IsolationReason(
            "isolation_level_unsupported",
            f"Requested isolation level '{required_level}' exceeds what this "
            f"deployment enforces ('{snapshot.isolation_level}'); refusing to "
            "silently launch with weaker isolation.",
        )
    if not system_account:
        return IsolationReason(
            "identity_mapping_missing",
            "User has no system_account mapping; refusing to fall back to a "
            "convention-derived or shared account under an explicit isolation "
            "requirement.",
        )
    launch_ok, launch_reason = manager.supports_per_user_launch(system_account)
    if not launch_ok:
        return IsolationReason(
            "per_user_launch_unavailable",
            f"Cannot launch the WebUI under system account '{system_account}' ({launch_reason}).",
        )
    return None
