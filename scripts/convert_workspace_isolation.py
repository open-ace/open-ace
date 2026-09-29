#!/usr/bin/env python3
"""Convert a config.json to the ``workspace.isolation`` block (Issue #3446).

Before #3446 workspace isolation was spread over nine ``workspace`` keys
(``multi_user_mode``, ``required_isolation_level``, ``os_user_confinement``,
``sandbox_tier``, ``confinement_*``). The server now refuses to start while any
of them is present, so every install and upgrade path runs this script first:
the package installer (fresh install, local upgrade, remote upgrade) and the
Docker entrypoint (a config.json its older self generated).

    convert_workspace_isolation.py CONFIG_JSON [--multi-user true|false]

``--multi-user`` is the package installer wizard's answer: it sets ``plain``
or ``shared`` unless the config names another backend, which it keeps.

The script is idempotent: a config that already uses ``workspace.isolation``
and has none of the removed keys is left byte-for-byte alone. It rewrites the
file only when something changed (atomically, keeping owner and mode), prints
what it did, and exits 1 when the file cannot be read or written, 2 when the
old keys cannot be converted without guessing (an os_user_confinement value
the old server refused), leaving the file untouched.

Each old key is read the way the pre-#3446 server read it, so the converted
block keeps what was running: a tier with a ``webui_image`` in
sandbox-backends.json ran pods (whatever ``multi_user_mode`` said), a declared
floor above the old mode is kept by raising the backend.

It is stdlib-only on purpose: the installer runs it before the application's
dependencies are installed. ``tests/unit/test_convert_workspace_isolation_3446.py``
checks that every result passes ``parse_isolation``.
"""

from __future__ import annotations

import argparse
import errno
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


class ConversionError(ValueError):
    """The old keys cannot be converted safely; the admin must decide."""


def _sandbox_backends_file(config_path: str) -> str | None:
    """The OpenSandbox backend config the server would load (same search order).

    The user fallback is the ``sandbox-backends.json`` beside config.json: that
    is the service account's ``~/.open-ace`` even when root runs the installer.
    """
    explicit = os.environ.get(SANDBOX_BACKENDS_ENV, "").strip()
    if explicit:
        # The server raises on a missing explicit path; no fallback here either.
        return explicit if os.path.isfile(explicit) else None
    candidates: list[str] = []
    candidates += [
        SYSTEM_SANDBOX_BACKENDS,
        os.path.join(os.path.dirname(os.path.abspath(config_path)), "sandbox-backends.json"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


_DIGEST_PINNED = re.compile(r"@sha256:[0-9a-f]{64}$")


def _has_webui_image(config_path: str, tier: str) -> bool:
    """Whether the OpenSandbox config gives the (default) tier a usable webui_image.

    Usable as the old capability probe judged it statically: digest-pinned and,
    when the file has an image_allowlist, on it. Anything else fell back to the
    local form. (Its runtime checks, such as the API key variable, cannot be
    judged here.)
    """
    path = _sandbox_backends_file(config_path)
    if path is None:
        return False
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        endpoints = raw.get("endpoints") or {}
        tier = tier or str(raw.get("default_tier") or "").strip()
        endpoint = endpoints.get(tier) or {}
        image = str(endpoint.get("webui_image") or "").strip()
        allowlist = raw.get("image_allowlist") or []
        if not image or not _DIGEST_PINNED.search(image):
            return False
        return not allowlist or image in allowlist
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
    ws: dict[str, Any], config_path: str, multi_user_answer: bool | None = None
) -> tuple[bool, list[str]]:
    """Convert ``ws`` in place. Returns (changed, human-readable notes).

    ``multi_user_answer`` is the package installer wizard's "multi-user mode?"
    answer on a fresh install: it picks ``plain`` or ``shared`` unless the
    config already names another backend (a deliberate choice it keeps).
    """
    floor = str(ws.get("required_isolation_level") or "").strip().lower()
    changed, notes = _convert_legacy(ws, config_path, multi_user_answer)
    if multi_user_answer is None:
        return changed, notes
    block = ws.get("isolation")
    wanted = "plain" if multi_user_answer else "shared"
    if _LEVEL_RANK.get(floor, 0) > _LEVEL_RANK[_BACKEND_LEVEL[wanted]]:
        # The answer never lowers a floor the old config declared.
        notes.append(f'kept the declared floor "{floor}" over the installer answer "{wanted}"')
        return changed, notes
    if block is None or (
        isinstance(block, dict)
        and block.get("backend") in ("shared", "plain")
        and block.get("backend") != wanted
    ):
        ws["isolation"] = {"level": _BACKEND_LEVEL[wanted], "backend": wanted}
        notes.append(f'installer choice: workspace.isolation.backend = "{wanted}"')
        return True, notes
    if isinstance(block, dict) and block.get("backend") not in ("shared", "plain", wanted):
        notes.append(f'kept workspace.isolation.backend "{block.get("backend")}"')
    return changed, notes


def _convert_legacy(
    ws: dict[str, Any], config_path: str, multi_user_answer: bool | None
) -> tuple[bool, list[str]]:
    notes: list[str] = []
    present = [key for key in REMOVED_KEYS if key in ws]

    if isinstance(ws.get("isolation"), dict):
        # Already converted; stale keys beside the block lose to it (the
        # server refused such a config, so they never took effect).
        for key in present:
            notes.append(
                f"workspace.isolation already set; removed {key}={json.dumps(ws.pop(key))}"
            )
        return bool(present), notes

    if not present:
        # Nothing to convert. A config without the block means "shared" in the
        # new format; never infer pods from sandbox-backends.json alone.
        return False, notes

    # Each key is read exactly as the pre-#3446 server read it.
    required = str(ws.get("required_isolation_level") or "").strip().lower()
    tier = str(ws.get("sandbox_tier") or "").strip()
    confinement = str(ws.get("os_user_confinement") or "").strip().lower()
    if "multi_user_mode" in ws:
        multi_user = bool(ws["multi_user_mode"])  # truthiness, as before ("false" is true)
    else:
        multi_user = bool(multi_user_answer)

    # The old server refused every launch for an unknown confinement value
    # (confinement_mode_invalid) and the installer left the unconfined launch
    # rule out of sudoers. Guessing a backend would fail open: stop instead.
    if confinement not in ("", "off") and confinement not in _CONFINEMENT_BACKEND:
        raise ConversionError(
            f"os_user_confinement {ws.get('os_user_confinement')!r} is not bwrap, runsc, kata "
            'or off; set workspace.isolation yourself, e.g. {"level": "os_user", '
            '"backend": "bwrap"} (see docs/guide/WORKSPACE_ISOLATION.md)'
        )
    # The old capability snapshot checked OpenSandbox first, whatever
    # multi_user_mode or os_user_confinement said: a (default) tier with a
    # webui_image ran the WebUI in pods. A tier without one fell back to local.
    if _has_webui_image(config_path, tier):
        backend = "opensandbox"
        if confinement not in ("", "off"):
            notes.append(
                f'os_user_confinement "{confinement}" dropped: OpenSandbox pods took precedence'
            )
    else:
        backend = _CONFINEMENT_BACKEND.get(confinement) or ("plain" if multi_user else "shared")
        if tier and required != "sandboxed":
            notes.append(f'sandbox_tier "{tier}" dropped: it has no webui_image')
            tier = ""
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
    """Replace ``path`` (resolved through symlinks) keeping its owner and mode.

    The installer runs this as root in a directory the service account can
    write, so mode and owner are set on the open descriptor, never by path (a
    swapped-in symlink must not receive them). A single-file bind mount cannot
    be replaced (EBUSY); it is rewritten in place instead.
    """
    target = os.path.realpath(path)
    if os.path.islink(path) and hasattr(os, "geteuid") and os.geteuid() == 0:
        # Root following a link the service account planted would rewrite
        # any root-owned JSON file: only follow links whose owner owns the target.
        if os.lstat(path).st_uid != os.stat(target).st_uid:
            raise OSError(errno.EPERM, f"refusing to follow {path} -> {target}: different owners")
    path = target
    text = json.dumps(config, indent=2, ensure_ascii=False) + "\n"
    st = os.stat(path)
    fd, tmp = tempfile.mkstemp(prefix=".config.json.", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            os.fchmod(fh.fileno(), st.st_mode & 0o7777)
            try:
                os.fchown(fh.fileno(), st.st_uid, st.st_gid)
            except PermissionError:
                pass  # not root: the file stays ours, as it already was
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.replace(tmp, path)
        except OSError as exc:
            if exc.errno != errno.EBUSY:
                raise
            os.unlink(tmp)
            with open(path, "r+", encoding="utf-8") as fh:
                fh.write(text)
                fh.truncate()
    except BaseException:
        if os.path.lexists(tmp):
            os.unlink(tmp)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("config", help="path to config.json")
    parser.add_argument(
        "--multi-user",
        choices=("true", "false"),
        help="the installer's answer: plain or shared, unless another backend is configured",
    )
    args = parser.parse_args(argv)
    default = None if args.multi_user is None else args.multi_user == "true"

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

    try:
        changed, notes = convert_workspace(ws, args.config, default)
    except ConversionError as exc:
        print(f"convert_workspace_isolation: {args.config}: {exc}", file=sys.stderr)
        return 2
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
