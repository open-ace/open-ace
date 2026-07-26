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

import re
from dataclasses import dataclass
from pathlib import Path

# Ephemeral, root-owned parent for every per-task runtime tree. Lives under
# /run so it is wiped on reboot and never lands inside a user HOME.
DEFAULT_TASK_ROOT = "/run/openace-agent-tasks"

# Filesystem-safe charset for a task_id used as a single path component.
# session ids (uuids or CLI-derived) occasionally carry '/', ':', or spaces;
# those are replaced rather than rejected so a real session id always maps to
# a usable directory name.
_SAFE_TASK_ID = re.compile(r"[A-Za-z0-9._-]")
_MAX_TASK_ID_LEN = 128

_RUNTIME_VARS = ("home", "tmp", "cache", "config", "data")


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


def _normalize_cgroup_enabled(raw: str) -> str:
    val = raw.strip().lower()
    if val in ("on", "true", "yes", "1", "enabled"):
        return "on"
    if val in ("off", "false", "no", "0", "disabled"):
        return "off"
    return "auto"


def read_agent_task_policy(conf_path: str) -> AgentTaskPolicy:
    """Parse the agent-launcher.conf keys into an :class:`AgentTaskPolicy`.

    Missing or unreadable file → all defaults. Malformed integer values fall
    back to the default for that field. The format is simple ``key=value``
    lines (one per line); comments start with ``#`` and blank lines are
    ignored. Values are whitespace-trimmed and surrounding quotes removed.
    """
    fields: dict[str, object] = {}
    try:
        text = Path(conf_path).read_text(encoding="utf-8")
    except OSError:
        return AgentTaskPolicy()

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
        elif key == "agent_max_concurrent_workflows":
            fields["max_concurrent_workflows"] = max(1, _parse_int_or_default(value, 3))

    return AgentTaskPolicy(**fields)  # type: ignore[arg-type]


def _parse_int_or_default(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
