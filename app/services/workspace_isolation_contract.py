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
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

# Revision 2 (2026-09-11.2, PR review round 2): the snapshot derives os_user
# from the launch-path readiness probe instead of the Docker layout,
# entry_points became a conditional key, and new reason codes were added
# (launch_path_degraded / launch_path_unverified).
POLICY_REVISION = "2026-09-11.2"

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

ALL_DIMENSIONS = (
    DIMENSION_IDENTITY,
    DIMENSION_FILESYSTEM,
    DIMENSION_ENVIRONMENT,
    DIMENSION_PROCESS,
    DIMENSION_RESOURCES,
    DIMENSION_NETWORK_EGRESS,
)

_OS_USER_ENFORCED = (
    DIMENSION_IDENTITY,
    DIMENSION_FILESYSTEM,
    DIMENSION_ENVIRONMENT,
    DIMENSION_PROCESS,
)
_OS_USER_UNSUPPORTED = (DIMENSION_RESOURCES, DIMENSION_NETWORK_EGRESS)

BACKEND_PER_USER = "qwen-code-webui-per-user"
BACKEND_SHARED = "qwen-code-webui-shared"

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
            "WebUI manager is disabled; no interactive workspace runtime " "is available.",
        )

    # Linux only: macOS skips system-user creation (utils/workspace.py), so
    # Open ACE cannot establish or verify the identity mapping there; Windows
    # forces a single shared instance.
    if _current_platform() != "linux":
        return _unsupported(
            "platform_unsupported",
            "Per-user workspace isolation is supported on Linux deployments "
            "only; this platform runs a single shared WebUI instance.",
        )

    if not getattr(config, "multi_user_mode", False):
        return _unsupported(
            "multi_user_mode_disabled",
            "Multi-user workspace mode is disabled; the interactive workspace "
            "intentionally runs one shared instance.",
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
            return _unsupported(
                "launch_path_degraded",
                "This deployment's WebUI launch path cannot host per-user "
                f"instances ({degradation}); see the workspace isolation "
                "documentation.",
            )
        return IsolationCapabilitySnapshot(
            supported=True,
            backend=BACKEND_PER_USER,
            isolation_level=ISOLATION_LEVEL_OS_USER,
            enforced=_OS_USER_ENFORCED,
            unsupported=_OS_USER_UNSUPPORTED,
            reasons=(),
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
        ),
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
    if explicit:
        if is_valid_isolation_level(explicit):
            return explicit
        # Late import avoids a logging dependency at module import time.
        import logging

        logging.getLogger(__name__).warning(
            "Invalid workspace.required_isolation_level %r; using derived default",
            explicit,
        )
    return snapshot.isolation_level


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
            f"Cannot launch the WebUI under system account "
            f"'{system_account}' ({launch_reason}).",
        )
    return None
