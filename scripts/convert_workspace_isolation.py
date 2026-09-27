#!/usr/bin/env python3
"""Convert a config.json to the ``workspace.isolation`` block (Issue #3446).

Before #3446 workspace isolation was spread over nine ``workspace`` keys
(``multi_user_mode``, ``required_isolation_level``, ``os_user_confinement``,
``sandbox_tier``, ``confinement_*``). The server now refuses to start while any
of them is present, so every install and upgrade path runs this script first:
the package installer (fresh install, local upgrade, remote upgrade) and the
Docker entrypoint (a config.json its older self generated).

    convert_workspace_isolation.py CONFIG_JSON [--default-multi-user true|false]

The script is idempotent: a config that already uses ``workspace.isolation``
and has none of the removed keys is left byte-for-byte alone. It rewrites the
file only when something changed (atomically, keeping owner and mode), prints
what it did, and exits non-zero only when the file cannot be read or written.

It is stdlib-only on purpose: the installer runs it before the application's
dependencies are installed. ``tests/unit/test_convert_workspace_isolation_3446.py``
checks that every result passes ``parse_isolation``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from typing import Any

REMOVED_KEYS = (
    "multi_user_mode",
    "required_isolation_level",
    "os_user_confinement",
    "sandbox_tier",
    "confinement_memory_max",
    "confinement_cpu_quota",
    "confinement_tasks_max",
    "confinement_egress_allow",
    "confinement_container_webui",
)
# os_user_confinement values -> backend. "off" / "" mean no confinement.
_CONFINEMENT_BACKEND = {"bwrap": "bwrap", "runsc": "local-gvisor", "kata": "local-kata"}
_BACKEND_LEVEL = {
    "shared": "none",
    "plain": "os_user",
    "bwrap": "os_user",
    "local-gvisor": "sandboxed",
    "local-kata": "sandboxed",
    "opensandbox": "sandboxed",
}
_LEVEL_RANK = {"none": 0, "os_user": 1, "sandboxed": 2}
_CONFINED = ("bwrap", "local-gvisor", "local-kata")
_CONTAINERS = ("local-gvisor", "local-kata")
_MEMORY_RE = re.compile(r"^[1-9][0-9]{0,12}[KMGT]?$")

SANDBOX_BACKENDS_ENV = "OPENACE_SANDBOX_BACKENDS"
SYSTEM_SANDBOX_BACKENDS = "/etc/openace/sandbox-backends.json"


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _sandbox_backends_file(config_path: str) -> str | None:
    """The OpenSandbox backend config the server would load (same search order).

    The user fallback is the ``sandbox-backends.json`` beside config.json: that
    is the service account's ``~/.open-ace`` even when root runs the installer.
    """
    explicit = os.environ.get(SANDBOX_BACKENDS_ENV, "").strip()
    candidates = [explicit] if explicit else []
    candidates += [
        SYSTEM_SANDBOX_BACKENDS,
        os.path.join(os.path.dirname(os.path.abspath(config_path)), "sandbox-backends.json"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def _has_webui_image(config_path: str, tier: str) -> bool:
    """Whether the OpenSandbox config gives the (default) tier a webui_image."""
    path = _sandbox_backends_file(config_path)
    if path is None:
        return False
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        endpoints = raw.get("endpoints") or {}
        tier = tier or str(raw.get("default_tier") or "").strip()
        endpoint = endpoints.get(tier) or {}
        return bool(str(endpoint.get("webui_image") or "").strip())
    except (OSError, ValueError, AttributeError):
        return False


def _limits(ws: dict[str, Any], notes: list[str]) -> dict[str, Any]:
    """The old confinement_* limits, coerced to the types parse_isolation accepts."""
    out: dict[str, Any] = {}
    if "confinement_memory_max" in ws:
        memory = str(ws["confinement_memory_max"] or "").strip()
        if _MEMORY_RE.fullmatch(memory):
            out["memory"] = memory
        else:
            notes.append(f"dropped invalid confinement_memory_max {ws['confinement_memory_max']!r}")
    for old, new, low, high in (
        ("confinement_cpu_quota", "cpu_percent", 1, 6400),
        ("confinement_tasks_max", "tasks", 16, 65535),
    ):
        if old not in ws:
            continue
        value = ws[old]
        try:
            number = int(str(value).strip()) if not isinstance(value, bool) else None
        except ValueError:
            number = None
        if number is not None and low <= number <= high:
            out[new] = number
        else:
            notes.append(f"dropped invalid {old} {value!r} (must be {low}..{high})")
    return out


def convert_workspace(
    ws: dict[str, Any], config_path: str, default_multi_user: bool | None
) -> tuple[bool, list[str]]:
    """Convert ``ws`` in place. Returns (changed, human-readable notes)."""
    notes: list[str] = []
    present = [key for key in REMOVED_KEYS if key in ws]

    if isinstance(ws.get("isolation"), dict):
        # Already converted; stale keys beside the block lose to it.
        for key in present:
            del ws[key]
        if present:
            notes.append(f"workspace.isolation already set; removed {', '.join(present)}")
        return bool(present), notes

    if not present and default_multi_user is None:
        return False, notes  # nothing to convert; the server default (shared) applies

    required = str(ws.get("required_isolation_level") or "").strip().lower()
    tier = str(ws.get("sandbox_tier") or "").strip()
    confinement = str(ws.get("os_user_confinement") or "").strip().lower()
    if "multi_user_mode" in ws:
        multi_user = _truthy(ws["multi_user_mode"])
    else:
        multi_user = bool(default_multi_user)

    backend = _CONFINEMENT_BACKEND.get(confinement)
    if confinement not in ("", "off") and backend is None:
        notes.append(f"unknown os_user_confinement {confinement!r} ignored")
    if backend is None:
        if required == "sandboxed" or tier or (multi_user and _has_webui_image(config_path, tier)):
            backend = "opensandbox"
        else:
            backend = "plain" if multi_user else "shared"
    # The old floor could exceed what the old mode provided (every launch was
    # then refused). Keep the floor: raise the backend to meet it.
    if required in _LEVEL_RANK and _LEVEL_RANK[required] > _LEVEL_RANK[_BACKEND_LEVEL[backend]]:
        raised = "opensandbox" if required == "sandboxed" else "plain"
        notes.append(
            f'required_isolation_level "{required}" is above backend "{backend}"; '
            f'using backend "{raised}"'
        )
        backend = raised

    isolation: dict[str, Any] = {"level": _BACKEND_LEVEL[backend], "backend": backend}
    if backend == "opensandbox" and tier:
        isolation["tier"] = tier
    if backend in _CONFINED:
        limits = _limits(ws, notes)
        if limits:
            isolation["limits"] = limits
        egress = ws.get("confinement_egress_allow")
        if isinstance(egress, str):
            egress = egress.split(",")
        if isinstance(egress, list):
            hosts = [item.strip() for item in egress if isinstance(item, str) and item.strip()]
            if hosts:
                isolation["egress_allow"] = hosts
    if backend in _CONTAINERS:
        webui = ws.get("confinement_container_webui")
        if isinstance(webui, str) and os.path.isabs(webui.strip()):
            isolation["container_webui"] = webui.strip()

    for key in present:
        del ws[key]
    ws["isolation"] = isolation
    notes.insert(0, f"workspace.isolation = {json.dumps(isolation, sort_keys=True)}")
    if present:
        notes.append(f"removed {', '.join(present)}")
    return True, notes


def _write_atomic(path: str, config: dict[str, Any]) -> None:
    st = os.stat(path)
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".config.json.", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(config, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.chmod(tmp, st.st_mode & 0o7777)
        if hasattr(os, "chown"):
            try:
                os.chown(tmp, st.st_uid, st.st_gid)
            except PermissionError:
                pass  # not root: the file stays ours, as it already was
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("config", help="path to config.json")
    parser.add_argument(
        "--default-multi-user",
        choices=("true", "false"),
        help="per-user (plain) or shared WebUI when the config states neither",
    )
    args = parser.parse_args(argv)
    default = None if args.default_multi_user is None else args.default_multi_user == "true"

    try:
        with open(args.config, encoding="utf-8") as fh:
            config = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"convert_workspace_isolation: cannot read {args.config}: {exc}", file=sys.stderr)
        return 1
    if not isinstance(config, dict):
        print(f"convert_workspace_isolation: {args.config} is not a JSON object", file=sys.stderr)
        return 1
    ws = config.get("workspace")
    if not isinstance(ws, dict):
        if default is None:
            return 0
        ws = config["workspace"] = {}

    changed, notes = convert_workspace(ws, args.config, default)
    for note in notes:
        print(f"convert_workspace_isolation: {note}")
    if changed:
        try:
            _write_atomic(args.config, config)
        except OSError as exc:
            print(
                f"convert_workspace_isolation: cannot write {args.config}: {exc}", file=sys.stderr
            )
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
