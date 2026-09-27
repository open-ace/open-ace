"""Workspace isolation configuration: one ``level`` + ``backend`` vocabulary (#3446).

The admin states the isolation of interactive workspaces in ONE place,
``workspace.isolation`` in config.json::

    "isolation": {
      "level": "sandboxed",
      "backend": "local-kata",
      "limits": {"memory": "4G", "cpu_percent": 200, "tasks": 512},
      "egress_allow": ["pypi.org:443"]
    }

The same backend names are what the capability snapshot reports, what the
root policy file keys its sections by and what the confine wrapper's
``--backend`` takes. The keys this block replaces are rejected (no aliases),
with an error naming the replacement; so is a level/backend pair that does not
belong together, and a backend the install method cannot provide.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

LEVEL_NONE = "none"
LEVEL_OS_USER = "os_user"
LEVEL_SANDBOXED = "sandboxed"

BACKEND_SHARED = "shared"
BACKEND_PLAIN = "plain"
BACKEND_BWRAP = "bwrap"
BACKEND_LOCAL_GVISOR = "local-gvisor"
BACKEND_LOCAL_KATA = "local-kata"
BACKEND_OPENSANDBOX = "opensandbox"

# Every backend belongs to exactly one level.
BACKEND_LEVEL = {
    BACKEND_SHARED: LEVEL_NONE,
    BACKEND_PLAIN: LEVEL_OS_USER,
    BACKEND_BWRAP: LEVEL_OS_USER,
    BACKEND_LOCAL_GVISOR: LEVEL_SANDBOXED,
    BACKEND_LOCAL_KATA: LEVEL_SANDBOXED,
    BACKEND_OPENSANDBOX: LEVEL_SANDBOXED,
}
ALL_BACKENDS = tuple(BACKEND_LEVEL)
# Backends whose WebUIs run as the user's OS account on this host.
OS_ACCOUNT_BACKENDS = frozenset(
    {BACKEND_PLAIN, BACKEND_BWRAP, BACKEND_LOCAL_GVISOR, BACKEND_LOCAL_KATA}
)
# Backends launched through the root confine wrapper (limits + egress proxy).
CONFINED_BACKENDS = frozenset({BACKEND_BWRAP, BACKEND_LOCAL_GVISOR, BACKEND_LOCAL_KATA})
CONTAINER_BACKENDS = frozenset({BACKEND_LOCAL_GVISOR, BACKEND_LOCAL_KATA})

INSTALL_METHOD_ENV = "OPENACE_INSTALL_METHOD"
INSTALL_PACKAGE = "package"
INSTALL_DOCKER = "docker"
INSTALL_DEV = "dev"
INSTALL_METHODS = (INSTALL_PACKAGE, INSTALL_DOCKER, INSTALL_DEV)

DEFAULT_MEMORY = "4G"
DEFAULT_CPU_PERCENT = 200
DEFAULT_TASKS = 512
DEFAULT_CONTAINER_WEBUI = "/usr/bin/qwen-code-webui"

# Keys this block replaced: rejected with the replacement named.
REMOVED_KEYS = {
    "multi_user_mode": 'workspace.isolation.backend ("shared" for one shared WebUI, '
    '"plain" or another backend for per-user isolation)',
    "required_isolation_level": "workspace.isolation.level",
    "os_user_confinement": 'workspace.isolation.backend ("bwrap", "local-gvisor" or "local-kata")',
    "sandbox_tier": "workspace.isolation.tier",
    "confinement_memory_max": "workspace.isolation.limits.memory",
    "confinement_cpu_quota": "workspace.isolation.limits.cpu_percent",
    "confinement_tasks_max": "workspace.isolation.limits.tasks",
    "confinement_egress_allow": "workspace.isolation.egress_allow",
    "confinement_container_webui": "workspace.isolation.container_webui",
}
_BLOCK_KEYS = frozenset({"level", "backend", "tier", "limits", "egress_allow", "container_webui"})
_LIMIT_KEYS = frozenset({"memory", "cpu_percent", "tasks"})
_MEMORY_RE = re.compile(r"^[1-9][0-9]{0,12}[KMGT]?$")


class IsolationConfigError(ValueError):
    """The ``workspace`` configuration cannot be used; the message says how to fix it."""


@dataclass(frozen=True)
class IsolationConfig:
    """The validated ``workspace.isolation`` block."""

    level: str = LEVEL_NONE
    backend: str = BACKEND_SHARED
    tier: str = ""
    memory: str = DEFAULT_MEMORY
    cpu_percent: int = DEFAULT_CPU_PERCENT
    tasks: int = DEFAULT_TASKS
    egress_allow: tuple[str, ...] = ()
    container_webui: str = DEFAULT_CONTAINER_WEBUI


def _allowed_backends(level: str) -> list[str]:
    return [b for b, lvl in BACKEND_LEVEL.items() if lvl == level]


def parse_isolation(workspace: dict[str, Any]) -> IsolationConfig:
    """Validate ``workspace`` (the config.json section) and return its isolation.

    No ``isolation`` block means the default: one shared WebUI (``none``).
    """
    removed = [key for key in REMOVED_KEYS if key in workspace]
    if removed:
        lines = "; ".join(f"{key} -> {REMOVED_KEYS[key]}" for key in removed)
        raise IsolationConfigError(
            f"workspace.{removed[0]} is no longer supported; isolation is configured in "
            f"workspace.isolation. Replace: {lines}"
        )
    block = workspace.get("isolation")
    if block is None:
        return IsolationConfig()
    if not isinstance(block, dict):
        raise IsolationConfigError("workspace.isolation must be an object")
    unknown = sorted(set(block) - _BLOCK_KEYS)
    if unknown:
        raise IsolationConfigError(
            f"workspace.isolation has unknown key(s) {unknown}; allowed: {sorted(_BLOCK_KEYS)}"
        )
    level = block.get("level")
    backend = block.get("backend")
    if level not in (LEVEL_NONE, LEVEL_OS_USER, LEVEL_SANDBOXED):
        raise IsolationConfigError(
            'workspace.isolation.level must be "none", "os_user" or "sandboxed"'
        )
    if backend not in BACKEND_LEVEL:
        raise IsolationConfigError(
            f"workspace.isolation.backend must be one of {list(ALL_BACKENDS)}"
        )
    if BACKEND_LEVEL[backend] != level:
        raise IsolationConfigError(
            f'workspace.isolation.backend "{backend}" provides level '
            f'"{BACKEND_LEVEL[backend]}", not "{level}"; for level "{level}" use one of '
            f"{_allowed_backends(level)}"
        )

    tier = block.get("tier", "")
    if not isinstance(tier, str):
        raise IsolationConfigError("workspace.isolation.tier must be a string")
    if tier.strip() and backend != BACKEND_OPENSANDBOX:
        raise IsolationConfigError('workspace.isolation.tier only applies to backend "opensandbox"')

    limits = block.get("limits", {})
    if not isinstance(limits, dict) or set(limits) - _LIMIT_KEYS:
        raise IsolationConfigError(
            f"workspace.isolation.limits must be an object with keys {sorted(_LIMIT_KEYS)}"
        )
    egress = block.get("egress_allow", [])
    if not isinstance(egress, list) or not all(isinstance(item, str) for item in egress):
        raise IsolationConfigError("workspace.isolation.egress_allow must be a list of host:port")
    if (limits or egress) and backend not in CONFINED_BACKENDS:
        where = (
            "the OpenSandbox tier (sandbox-backends.json) sets them for backend " '"opensandbox"'
            if backend == BACKEND_OPENSANDBOX
            else f'backend "{backend}" does not enforce them'
        )
        raise IsolationConfigError(
            "workspace.isolation.limits / egress_allow apply to backends "
            f"{sorted(CONFINED_BACKENDS)} only; {where}"
        )
    memory = str(limits.get("memory", DEFAULT_MEMORY)).strip()
    if not _MEMORY_RE.fullmatch(memory):
        raise IsolationConfigError(
            'workspace.isolation.limits.memory must look like "4G", "512M" or bytes'
        )
    cpu = limits.get("cpu_percent", DEFAULT_CPU_PERCENT)
    tasks = limits.get("tasks", DEFAULT_TASKS)
    if isinstance(cpu, bool) or not isinstance(cpu, int) or not 1 <= cpu <= 6400:
        raise IsolationConfigError("workspace.isolation.limits.cpu_percent must be 1..6400")
    if isinstance(tasks, bool) or not isinstance(tasks, int) or not 16 <= tasks <= 65535:
        raise IsolationConfigError("workspace.isolation.limits.tasks must be 16..65535")

    container_webui = block.get("container_webui", DEFAULT_CONTAINER_WEBUI)
    if not isinstance(container_webui, str) or not os.path.isabs(container_webui):
        raise IsolationConfigError("workspace.isolation.container_webui must be an absolute path")
    if "container_webui" in block and backend not in CONTAINER_BACKENDS:
        raise IsolationConfigError(
            "workspace.isolation.container_webui only applies to backends "
            f"{sorted(CONTAINER_BACKENDS)}"
        )
    return IsolationConfig(
        level=level,
        backend=backend,
        tier=tier.strip(),
        memory=memory,
        cpu_percent=cpu,
        tasks=tasks,
        egress_allow=tuple(item.strip() for item in egress if item.strip()),
        container_webui=container_webui,
    )


# ── Install method ──────────────────────────────────────────────────────────


def install_method() -> str:
    """How this deployment was installed (``OPENACE_INSTALL_METHOD``), or "" if unknown.

    The Docker entrypoint sets ``docker``; the package installer's service
    units set ``package``. An unknown method restricts nothing.
    """
    value = os.environ.get(INSTALL_METHOD_ENV, "").strip().lower()
    return value if value in INSTALL_METHODS else ""


_DOCKER_REASON = (
    "the image ships no confine wrapper, bubblewrap needs systemd and unprivileged user "
    "namespaces that a container does not have, and per-user containers would need the "
    "host's docker.sock (host root)"
)
_PLATFORM_REASON = "it needs a Linux host"


def backend_unavailability(method: str, platform: str) -> dict[str, tuple[str, str]]:
    """Backends this deployment can never provide: backend -> (code, reason).

    Static facts about the install method and platform only; whether an
    available backend is READY on this host is the readiness probe's job.
    """
    out: dict[str, tuple[str, str]] = {}
    for backend in CONFINED_BACKENDS:
        if method == INSTALL_DOCKER:
            out[backend] = ("install_method_docker", _DOCKER_REASON)
        elif platform != "linux":
            out[backend] = ("platform_unsupported", _PLATFORM_REASON)
    return out


def check_backend_available(isolation: IsolationConfig, method: str, platform: str) -> None:
    """Raise when the configured backend cannot work with this install method."""
    unavailable = backend_unavailability(method, platform)
    if isolation.backend not in unavailable:
        return
    _, reason = unavailable[isolation.backend]
    alternatives = [
        b
        for b in _allowed_backends(isolation.level)
        if b not in unavailable and b != isolation.backend
    ]
    hint = (
        f'use backend {" or ".join(repr(b) for b in alternatives)} for level '
        f'"{isolation.level}", or the package install on a Linux host'
        if alternatives
        else "use the package install on a Linux host"
    )
    where = f"the {method.capitalize()} install" if method else f"this platform ({platform})"
    raise IsolationConfigError(
        f'workspace.isolation.backend "{isolation.backend}" is not available in {where}: '
        f"{reason}. {hint[0].upper()}{hint[1:]}."
    )


def validate_config_file(config_path: str, platform: str) -> IsolationConfig | None:
    """Startup check of ``workspace.isolation`` in *config_path*.

    Raises :class:`IsolationConfigError` for a removed key, an invalid block
    or a backend this install method / platform cannot provide. Returns the
    parsed block, or None when there is no config file (nothing to check).
    """
    import json

    if not os.path.exists(config_path):
        return None
    try:
        with open(config_path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise IsolationConfigError(f"cannot read {config_path}: {exc}") from None
    workspace = data.get("workspace", {}) if isinstance(data, dict) else {}
    if not isinstance(workspace, dict):
        raise IsolationConfigError("workspace must be an object")
    isolation = parse_isolation(workspace)
    if workspace.get("enabled"):
        check_backend_available(isolation, install_method(), platform)
    return isolation
