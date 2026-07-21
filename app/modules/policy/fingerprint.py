"""
Open ACE - Policy request fingerprinting & per-tool normalization.

The fingerprint is the typed identity a decision (and its approval) binds to.
It is built from canonicalized fields and is re-computable, so a later verifier
can detect drift, a changed normalization profile, or a changed policy version
(see plan §2.3, review #2/#3).

Normalization profiles are EXPLICIT and VERSIONED per tool/action. MVP profiles:
- ``generic:v1``      — deterministic key ordering only (no semantic folding).
- ``bash-command:v1`` — command tokenization + leading-flag reordering.
- ``file-path:v1``    — ``~`` / home-dir expansion + lexical path normalization.

Invariant: normalizers only fold SURFACE forms; they never make a path or
command *more permissive* (no symlink resolution, no case folding without an
explicit hint), so two genuinely-different requests never collapse to one
digest.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from collections.abc import Callable
from typing import Any

from app.modules.policy.models import RequestFingerprint

# ---------------------------------------------------------------------------
# Profile registry
# ---------------------------------------------------------------------------

PROFILE_GENERIC = "generic"
PROFILE_BASH_COMMAND = "bash-command"
PROFILE_FILE_PATH = "file-path"
PROFILE_VERSION = 1


def _canonical_mapping(value: Any) -> Any:
    """Recursively produce a deterministically-ordered, JSON-serialisable value."""
    if isinstance(value, dict):
        return {k: _canonical_mapping(value[k]) for k in sorted(value.keys())}
    if isinstance(value, (list, tuple)):
        return [_canonical_mapping(v) for v in value]
    return value


def normalize_generic(args: dict[str, Any], home_dir: str | None = None) -> dict[str, Any]:
    """Stable key ordering only. The identity profile — never folds semantics."""
    out: dict[str, Any] = _canonical_mapping(args) if isinstance(args, dict) else args
    return out


def _normalize_command(command: str) -> str:
    """Tokenize a shell command and reorder its flags.

    The program name (first token) stays first; positional operands keep their
    relative order; all flag tokens (``-x`` / ``--xxx``) are sorted. This folds
    ``git -a -b commit`` == ``git -b -a commit`` and ``rm -rf /tmp`` ==
    ``rm /tmp -rf`` without changing which program runs, which flags are present,
    or the order of positional operands — so genuinely-different commands never
    collapse.
    """
    if not isinstance(command, str) or not command.strip():
        return command if isinstance(command, str) else ""
    try:
        tokens = shlex.split(command)
    except ValueError:
        # Unbalanced quotes etc. — fall back to whitespace split (best effort).
        tokens = command.split()
    if not tokens:
        return ""
    flags = sorted(t for t in tokens if t.startswith("-") and t != "-")
    non_flags = [t for t in tokens if not (t.startswith("-") and t != "-")]
    if non_flags:
        normalized = [non_flags[0]] + flags + non_flags[1:]
    else:
        normalized = flags
    return " ".join(normalized)


def normalize_bash_command(args: dict[str, Any], home_dir: str | None = None) -> dict[str, Any]:
    """Apply ``_normalize_command`` to any ``command``/``cmd`` field."""
    out = normalize_generic(args, home_dir)
    if not isinstance(out, dict):
        return out
    for key in ("command", "cmd"):
        if key in out and isinstance(out[key], str):
            out[key] = _normalize_command(out[key])
    return out


def _normalize_path(path: str, home_dir: str | None = None) -> str:
    """Lexical path normalization + ``~`` / home-dir expansion.

    If ``home_dir`` is known, an absolute home prefix is folded to ``~`` so
    ``~/foo`` and ``/home/user/foo`` collapse to the same token. A bare leading
    ``~`` is expanded to a stable ``<HOME>`` token. No symlink resolution and no
    case folding — both could fold genuinely-different paths.
    """
    if not isinstance(path, str) or not path:
        return path if isinstance(path, str) else ""
    p = path
    if home_dir:
        if p == home_dir:
            p = "~"
        elif p.startswith(home_dir.rstrip("/") + "/"):
            p = "~" + p[len(home_dir.rstrip("/")) :]
    if p.startswith("~"):
        p = "<HOME>" + p[1:]
    # Collapse repeated separators and strip a trailing separator (not root).
    p = re.sub(r"/{2,}", "/", p)
    if len(p) > 1 and p.endswith("/"):
        p = p.rstrip("/")
    return p


def normalize_file_path(args: dict[str, Any], home_dir: str | None = None) -> dict[str, Any]:
    """Apply ``_normalize_path`` to recognized path-bearing fields."""
    out = normalize_generic(args, home_dir)
    if not isinstance(out, dict):
        return out
    for key in ("file_path", "path", "filename", "file", "notebook_path"):
        if key in out and isinstance(out[key], str):
            out[key] = _normalize_path(out[key], home_dir)
    return out


# (profile_id, version) -> normalizer
ProfilesRegistry: dict[tuple[str, int], Callable[[dict[str, Any], str | None], dict[str, Any]]] = {
    (PROFILE_GENERIC, PROFILE_VERSION): normalize_generic,
    (PROFILE_BASH_COMMAND, PROFILE_VERSION): normalize_bash_command,
    (PROFILE_FILE_PATH, PROFILE_VERSION): normalize_file_path,
}


def get_profile(profile_id: str | None, version: int | None) -> tuple[str, int]:
    """Resolve a profile id/version, falling back to generic:v1."""
    pid = profile_id or PROFILE_GENERIC
    ver = version or PROFILE_VERSION
    if (pid, ver) not in ProfilesRegistry:
        return (PROFILE_GENERIC, PROFILE_VERSION)
    return (pid, ver)


# ---------------------------------------------------------------------------
# Digests
# ---------------------------------------------------------------------------


def compute_args_digest(
    profile_id: str,
    profile_version: int,
    args: Any,
    home_dir: str | None = None,
) -> str:
    """SHA-256 over the canonical serialisation of normalized args."""
    normalizer = ProfilesRegistry.get((profile_id, profile_version), normalize_generic)
    normalized = normalizer(args if isinstance(args, dict) else {"value": args}, home_dir)
    payload = json.dumps(normalized, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_fingerprint_hash(fingerprint: RequestFingerprint) -> str:
    """SHA-256 over the fingerprint's canonical payload (re-computable)."""
    payload = json.dumps(fingerprint.to_canonical_payload(), sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# control_request extraction
# ---------------------------------------------------------------------------

_PATH_KEYS = ("file_path", "path", "filename", "file", "notebook_path")
_COMMAND_KEYS = ("command", "cmd")


def extract_request_fields(control_request: dict[str, Any]) -> dict[str, Any]:
    """Pull the CLI-agnostic identity fields out of a permission control_request.

    Defensive against schema variation across CLIs: request_id / subtype /
    tool_name live under ``request`` (Claude-style) or at the top level; args
    live under ``request.input`` / ``request.args`` / ``request``.
    """
    if not isinstance(control_request, dict):
        return {}
    request = control_request.get("request")
    if not isinstance(request, dict):
        request = {}
    request_id = (
        control_request.get("request_id") or request.get("request_id") or control_request.get("id")
    )
    action = request.get("subtype") or request.get("action") or control_request.get("subtype")
    tool = request.get("tool_name") or control_request.get("tool_name")
    args = request.get("input")
    if not isinstance(args, dict):
        args = request.get("args")
    if not isinstance(args, dict):
        args = {k: v for k, v in request.items() if k not in ("subtype", "tool_name", "request_id")}
    command = next((args.get(k) for k in _COMMAND_KEYS if args.get(k)), None)
    file_path = next((args.get(k) for k in _PATH_KEYS if args.get(k)), None)
    resource_target = command or file_path
    return {
        "request_id": request_id,
        "action": action,
        "tool": tool,
        "args": args,
        "command": command,
        "file_path": file_path,
        "resource_target": resource_target,
    }


def select_profile(command: str | None, file_path: str | None, tool: str | None) -> tuple[str, int]:
    """Pick the normalization profile from the request's dominant resource."""
    if command:
        return (PROFILE_BASH_COMMAND, PROFILE_VERSION)
    if file_path:
        return (PROFILE_FILE_PATH, PROFILE_VERSION)
    return (PROFILE_GENERIC, PROFILE_VERSION)


def build_fingerprint(
    control_request: dict[str, Any],
    *,
    machine_id: str | None = None,
    workspace_scope: str | None = None,
    home_dir: str | None = None,
    policy_rule_id: int | None = None,
    policy_rule_version: int | None = None,
    issued_ts: str | None = None,
    profile_override: tuple[str, int] | None = None,
) -> tuple[RequestFingerprint, str, str | None]:
    """Build a typed fingerprint + args digest + resource target.

    Returns ``(fingerprint, args_digest, resource_target)``. ``fingerprint_hash``
    is NOT set here when ``policy_rule_id`` is unknown yet; callers that need the
    hash recompute it via :func:`compute_fingerprint_hash` once the matched rule
    is known (the fingerprint is mutable until hashed).
    """
    fields = extract_request_fields(control_request)
    tool = fields.get("tool")
    command = fields.get("command")
    file_path = fields.get("file_path")
    resource_target = fields.get("resource_target")
    args = fields.get("args") or {}

    pid, pver = profile_override or select_profile(command, file_path, tool)
    args_digest = compute_args_digest(pid, pver, args, home_dir=home_dir)

    fingerprint = RequestFingerprint(
        tool=tool,
        action=fields.get("action"),
        args_digest=args_digest,
        normalization_profile_id=pid,
        normalization_profile_version=pver,
        machine_id=machine_id,
        workspace_scope=workspace_scope,
        resource_target=resource_target,
        policy_rule_id=policy_rule_id,
        policy_rule_version=policy_rule_version,
        request_id=fields.get("request_id"),
        issued_ts=issued_ts,
    )
    return fingerprint, args_digest, resource_target


__all__ = [
    "PROFILE_GENERIC",
    "PROFILE_BASH_COMMAND",
    "PROFILE_FILE_PATH",
    "PROFILE_VERSION",
    "ProfilesRegistry",
    "normalize_generic",
    "normalize_bash_command",
    "normalize_file_path",
    "get_profile",
    "compute_args_digest",
    "compute_fingerprint_hash",
    "extract_request_fields",
    "select_profile",
    "build_fingerprint",
]
