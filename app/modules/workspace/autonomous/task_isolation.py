"""Per-task runtime isolation helpers (Issue #2020 Phase A).

Autonomous agent attempts must not share HOME, TMP, or XDG cache/config/data
state. This module owns the single deterministic layout both the Python runner
and the ``openace-run-as`` launcher derive per-task paths from, so they always
agree given the same ``task_id``.

Scope (issue #2020 comment, 2026-07-26): these helpers are used ONLY on the
autonomous execution path (``agent_runner.py`` launch chain + the isolated
launcher). The local/remote workspace file path (``app/routes/fs.py``) and the
ordinary remote-session lifecycle (``remote_session_manager.py``) are
intentionally untouched.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

# Ephemeral, root-owned parent for every per-task runtime tree. Lives under
# /run so it is wiped on reboot and never lands inside a user HOME.
DEFAULT_TASK_ROOT = "/run/openace-agent-tasks"
DEFAULT_AGENT_LAUNCHER_CONF = "/etc/openace/agent-launcher.conf"
USER_AGENT_LAUNCHER_CONF = os.path.expanduser("~/.open-ace/agent-launcher.conf")

# Filesystem-safe charset for a task_id used as a single path component.
# session ids (uuids or CLI-derived) occasionally carry '/', ':', or spaces;
# those are replaced rather than rejected so a real session id always maps to
# a usable directory name.
_SAFE_TASK_ID = re.compile(r"[A-Za-z0-9._-]")
_MAX_TASK_ID_LEN = 128

_RUNTIME_VARS = ("home", "tmp", "cache", "config", "data")


def candidate_agent_task_policy_paths(explicit_path: str | None = None) -> tuple[str, ...]:
    """Return launcher-conf candidates in precedence order.

    Priority is:

    1. ``OPENACE_LAUNCHER_CONF`` / explicit caller path
    2. system config ``/etc/openace/agent-launcher.conf``
    3. user fallback ``~/.open-ace/agent-launcher.conf``
    """

    candidates = [explicit_path, DEFAULT_AGENT_LAUNCHER_CONF, USER_AGENT_LAUNCHER_CONF]
    ordered: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in ordered:
            ordered.append(candidate)
    return tuple(ordered)


def resolve_agent_task_policy_path(explicit_path: str | None = None) -> str | None:
    """Return the first existing launcher-conf path, else ``None``."""

    for candidate in candidate_agent_task_policy_paths(explicit_path):
        if Path(candidate).is_file():
            return candidate
    return None


def sanitize_task_id(task_id: str) -> str:
    """Return a filesystem-safe single path component for *task_id*.

    Raises ``ValueError`` if the result is empty (empty input, or an input
    made entirely of unsafe characters).
    """
    if not isinstance(task_id, str):
        raise ValueError("task_id must be a string")
    sanitized = "".join(ch if _SAFE_TASK_ID.match(ch) else "_" for ch in task_id)
    sanitized = sanitized.strip("._-")
    if not sanitized:
        raise ValueError("task_id sanitizes to an empty path component")
    return sanitized[:_MAX_TASK_ID_LEN]


def task_runtime_dirs(task_id: str, root: str = DEFAULT_TASK_ROOT) -> dict[str, str]:
    """Deterministic per-task HOME/TMP/XDG directory layout.

    Returns a dict with keys ``root`` and one entry per runtime var
    (``home``, ``tmp``, ``cache``, ``config``, ``data``). The launcher mirrors
    this exact layout, so the same ``task_id`` resolves to the same paths on
    both sides.
    """
    safe = sanitize_task_id(task_id)
    base = f"{root.rstrip('/')}/{safe}"
    dirs: dict[str, str] = {"root": base}
    for var in _RUNTIME_VARS:
        dirs[var] = f"{base}/{var}"
    return dirs


def ensure_task_runtime_dirs(task_id: str, root: str = DEFAULT_TASK_ROOT) -> dict[str, str] | None:
    """Create the per-task runtime tree, returning the dirs or None.

    Used on the same-user launch path (dev/local) where the root launcher is
    not invoked and the Python runner is authoritative for the agent's
    HOME/TMP. On the cross-user path the root-owned launcher creates the tree
    under /run, which the service user cannot write to; this helper returns
    None there so the env builder falls back to the legacy shared HOME rather
    than pointing the agent at a non-existent directory.
    """
    dirs = task_runtime_dirs(task_id, root)
    try:
        for var in ("root", *_RUNTIME_VARS):
            Path(dirs[var]).mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return dirs


@dataclass(frozen=True)
class AgentTaskPolicy:
    """Per-task isolation + resource policy parsed from agent-launcher.conf.

    ``memory_max_bytes`` / ``pids_max`` of ``0`` mean "no limit configured".
    ``cgroup_enabled`` is normalized to ``auto`` / ``on`` / ``off``. The
    launcher consumes the resource keys; the scheduler consumes
    ``max_concurrent_workflows``.
    """

    task_root: str = DEFAULT_TASK_ROOT
    cgroup_root: str = "/sys/fs/cgroup/openace-agent"
    cgroup_enabled: str = "auto"
    memory_max_bytes: int = 0
    pids_max: int = 0
    cpu_max: str = ""
    max_concurrent_workflows: int = 3
    # #2020 Phase B — complete the resource-contract dimension set. memory/pids/cpu
    # are enforced by the Legacy launcher (cgroup); wall_clock is enforced by the
    # runner (covered by the CPU_MEM_PIDS_TIME_QUOTA capability). ephemeral_storage
    # / inode are NOT enforceable on Legacy (no io.max / disk quota) — declaring
    # them (>0) implies STORAGE_INODE_QUOTA, which Legacy does not declare, so a
    # spec carrying them fail-closes at create() rather than silently degrading.
    # A #2023 gVisor/container backend declares STORAGE_INODE_QUOTA and enforces.
    wall_clock_limit: int = 0
    ephemeral_storage_limit: int = 0
    inode_limit: int = 0


def _normalize_cgroup_enabled(raw: str) -> str:
    val = raw.strip().lower()
    if val in ("on", "true", "yes", "1", "enabled"):
        return "on"
    if val in ("off", "false", "no", "0", "disabled"):
        return "off"
    return "auto"


def read_agent_task_policy(conf_path: str, *, concurrency_default: int = 3) -> AgentTaskPolicy:
    """Parse the agent-launcher.conf keys into an :class:`AgentTaskPolicy`.

    Missing or unreadable file → all defaults. Malformed integer values fall
    back to the default for that field. The format is simple ``key=value``
    lines (one per line); comments start with ``#`` and blank lines are
    ignored. Values are whitespace-trimmed and surrounding quotes removed.

    ``concurrency_default`` is the value used when the conf does not set
    ``agent_max_concurrent_workflows``; the scheduler passes its own
    (monkeypatchable) module constant so tests that tweak the cap still work.
    """
    fields: dict[str, object] = {"max_concurrent_workflows": concurrency_default}
    try:
        text = Path(conf_path).read_text(encoding="utf-8")
    except OSError:
        return AgentTaskPolicy(**fields)  # type: ignore[arg-type]

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw_value = line.partition("=")
        key = key.strip()
        value = raw_value.strip().strip('"').strip("'")
        if key == "agent_task_root":
            fields["task_root"] = value
        elif key == "agent_task_cgroup_root":
            fields["cgroup_root"] = value
        elif key == "agent_task_cgroup_enabled":
            fields["cgroup_enabled"] = _normalize_cgroup_enabled(value)
        elif key == "agent_task_memory_max_bytes":
            fields["memory_max_bytes"] = _parse_int_or_default(value, 0)
        elif key == "agent_task_pids_max":
            fields["pids_max"] = _parse_int_or_default(value, 0)
        elif key == "agent_task_cpu_max":
            fields["cpu_max"] = value
        elif key == "agent_task_wall_clock_limit":
            fields["wall_clock_limit"] = _parse_int_or_default(value, 0)
        elif key == "agent_task_ephemeral_storage_limit":
            fields["ephemeral_storage_limit"] = _parse_int_or_default(value, 0)
        elif key == "agent_task_inode_limit":
            fields["inode_limit"] = _parse_int_or_default(value, 0)
        elif key == "agent_max_concurrent_workflows":
            fields["max_concurrent_workflows"] = max(
                1, _parse_int_or_default(value, concurrency_default)
            )

    return AgentTaskPolicy(**fields)  # type: ignore[arg-type]


def _parse_int_or_default(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
