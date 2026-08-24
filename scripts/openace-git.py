#!/usr/bin/env python3
"""Validating sudo wrapper for git commands used by Open ACE."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

CONFIG_PATH = Path("/etc/openace/git-wrapper.json")

DEFAULT_CONFIG: dict[str, Any] = {
    "allowed_path_roots": ["/home", "/workspace", "/srv", "/tmp", "/private/tmp"],
    "workflow_branch_patterns": [r"^(auto-dev|review-fix|ci-repair|fork)/[A-Za-z0-9._/-]+$"],
    "require_owned_paths": True,
    "real_git_paths": ["/usr/bin/git", "/usr/local/bin/git"],
}

REF_RE = re.compile(r"^[A-Za-z0-9._/@{}^:+~,-]+$")
REMOTE_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
REMOTE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
URL_RE = re.compile(r"^(?:https://|ssh://|git@)[^\s]+$")
SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
NUMBER_RE = re.compile(r"^\d+$")
CONFLICT_MARKERS = [r"^<{7,}( |$)", r"^={7,}$", r"^>{7,}( |$)"]


class ValidationResult:
    def __init__(self, allowed: bool, reason: str = "", config: dict[str, Any] | None = None):
        self.allowed = allowed
        self.reason = reason
        self.config = config


def _allow() -> ValidationResult:
    return ValidationResult(True)


def _deny(reason: str) -> ValidationResult:
    return ValidationResult(False, reason)


def _config(config: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(DEFAULT_CONFIG)
    if config:
        merged.update(config)
    return merged


def _allowed_roots(config: dict[str, Any]) -> list[str]:
    roots = config.get("allowed_path_roots") or DEFAULT_CONFIG["allowed_path_roots"]
    return [os.path.realpath(str(root)) for root in roots if str(root).startswith("/")]


def _under_allowed_root(path: str, config: dict[str, Any]) -> bool:
    if not path or "\0" in path or not os.path.isabs(path):
        return False
    real = os.path.realpath(path)
    for root in _allowed_roots(config):
        try:
            if os.path.commonpath((real, root)) == root:
                return True
        except ValueError:
            continue
    return False


def _owned_by_current_user(path: str, config: dict[str, Any]) -> bool:
    if not config.get("require_owned_paths", True):
        return True
    try:
        return os.stat(path).st_uid == os.getuid()
    except OSError:
        return False


def _context_path(path: str, config: dict[str, Any]) -> bool:
    return _under_allowed_root(path, config) and _owned_by_current_user(path, config)


def _plain_relative_path(path: str) -> bool:
    if not path or "\0" in path or os.path.isabs(path):
        return False
    parts = path.split("/")
    return all(part and part not in {".", ".."} for part in parts)


def _path_operand(path: str, config: dict[str, Any]) -> bool:
    if os.path.isabs(path):
        return _under_allowed_root(path, config)
    return _plain_relative_path(path)


def _show_target(value: str) -> bool:
    if ":" not in value:
        return _ref(value)
    ref, path = value.split(":", 1)
    return _ref(ref) and _plain_relative_path(path)


def _ref(value: str) -> bool:
    return bool(value) and not value.startswith("-") and bool(REF_RE.fullmatch(value))


def _branch(value: str, config: dict[str, Any]) -> bool:
    patterns = config.get("workflow_branch_patterns") or DEFAULT_CONFIG["workflow_branch_patterns"]
    return (
        bool(value)
        and not value.startswith("-")
        and any(re.fullmatch(pattern, value) for pattern in patterns)
    )


def _remote(value: str) -> bool:
    return bool(REMOTE_RE.fullmatch(value or ""))


def _remote_name(value: str) -> bool:
    return bool(REMOTE_NAME_RE.fullmatch(value or ""))


def _real_git_path(config: dict[str, Any]) -> str:
    configured = config.get("real_git_paths") or DEFAULT_CONFIG["real_git_paths"]
    candidates = configured if isinstance(configured, list) else DEFAULT_CONFIG["real_git_paths"]
    for candidate in candidates:
        path = str(candidate)
        if path.startswith("/") and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return str(DEFAULT_CONFIG["real_git_paths"][0])


def _safe_config_arg(value: str, config: dict[str, Any], state: dict[str, Any]) -> bool:
    if "=" not in value:
        return False
    key, val = value.split("=", 1)
    if key in {"alias", "protocol", "filter"} or key.startswith(("alias.", "protocol.", "filter.")):
        return False
    if key in {"core.sshCommand", "core.editor", "core.pager"}:
        return False
    if key == "core.hooksPath":
        if val == "/dev/null":
            state["hooks_path"] = True
            return True
        return False
    if key == "core.fsmonitor":
        if val == "false":
            state["fsmonitor"] = True
            return True
        return False
    if key == "safe.directory":
        if _under_allowed_root(val, config):
            state["safe_directory"] = True
            return True
        return False
    return False


def _parse_global_options(
    argv: list[str], config: dict[str, Any]
) -> tuple[ValidationResult, list[str], dict[str, Any]]:
    i = 0
    saw_git_dir = False
    saw_work_tree = False
    state: dict[str, Any] = {"hooks_path": False, "fsmonitor": False, "safe_directory": False}
    while i < len(argv):
        arg = argv[i]
        if arg == "-c":
            if i + 1 >= len(argv) or not _safe_config_arg(argv[i + 1], config, state):
                return _deny("unsafe -c config"), [], state
            i += 2
            continue
        if arg.startswith("-c") and arg != "-c":
            if not _safe_config_arg(arg[2:], config, state):
                return _deny("unsafe -c config"), [], state
            i += 1
            continue
        if arg == "-C":
            if i + 1 >= len(argv) or not _context_path(argv[i + 1], config):
                return _deny("unsafe -C path"), [], state
            i += 2
            continue
        if arg.startswith("--git-dir="):
            if not _context_path(arg.split("=", 1)[1], config):
                return _deny("unsafe git-dir"), [], state
            saw_git_dir = True
            i += 1
            continue
        if arg.startswith("--work-tree="):
            if not _context_path(arg.split("=", 1)[1], config):
                return _deny("unsafe work-tree"), [], state
            saw_work_tree = True
            i += 1
            continue
        if arg == "--exec-path" or arg.startswith("--exec-path="):
            return _deny("exec-path is denied"), [], state
        if arg.startswith("-"):
            return _deny(f"unknown global option: {arg}"), [], state
        break
    if saw_git_dir != saw_work_tree:
        return _deny("git-dir and work-tree must be paired"), [], state
    return _allow(), argv[i:], state


def _validate_command(args: list[str], config: dict[str, Any]) -> ValidationResult:
    if not args:
        return _deny("missing git command")
    verb = args[0]

    if verb == "remote":
        if args == ["remote", "get-url", "origin"]:
            return _allow()
        if (
            len(args) == 4
            and args[:2] == ["remote", "add"]
            and _remote_name(args[2])
            and URL_RE.match(args[3])
        ):
            return _allow()
    elif verb == "push":
        if len(args) == 4 and args[:3] == ["push", "-u", "origin"] and _branch(args[3], config):
            return _allow()
        if (
            len(args) == 4
            and args[:3] == ["push", "origin", "--delete"]
            and _branch(args[3], config)
        ):
            return _allow()
        if len(args) == 3 and _remote(args[1]) and _branch(args[2], config):
            return _allow()
        if (
            len(args) == 4
            and _remote(args[1])
            and _branch(args[2], config)
            and args[3] == "--force-with-lease"
        ):
            return _allow()
    elif verb == "branch":
        if args == ["branch", "--show-current"]:
            return _allow()
        if len(args) == 3 and args[1] == "-D" and _branch(args[2], config):
            return _allow()
    elif verb == "rev-parse":
        if args == ["rev-parse", "HEAD"]:
            return _allow()
        if len(args) == 2 and args[1] in {
            "--show-toplevel",
            "--absolute-git-dir",
        }:
            return _allow()
        if args == ["rev-parse", "--path-format=absolute", "--git-common-dir"]:
            return _allow()
        if len(args) == 2 and _ref(args[1]):
            return _allow()
        if len(args) == 3 and args[1] == "--verify" and _ref(args[2]):
            return _allow()
    elif verb == "checkout":
        if args == ["checkout", "--detach", "HEAD"]:
            return _allow()
        if len(args) == 2 and _ref(args[1]):
            return _allow()
        if len(args) == 3 and args[1] == "-b" and _branch(args[2], config):
            return _allow()
        if len(args) == 4 and args[1] == "-b" and _branch(args[2], config) and _ref(args[3]):
            return _allow()
    elif verb == "show-ref":
        prefixes = ("refs/heads/", "refs/remotes/origin/")
        if len(args) == 4 and args[1:3] == ["--verify", "--quiet"]:
            for prefix in prefixes:
                if args[3].startswith(prefix):
                    return (
                        _allow()
                        if _branch(args[3][len(prefix) :], config)
                        else _deny("unsafe branch")
                    )
    elif verb == "reset":
        if args == ["reset", "--hard", "HEAD"]:
            return _allow()
        if len(args) == 3 and args[1] == "--hard" and _ref(args[2]):
            return _allow()
        if (
            len(args) == 5
            and args[:4] == ["reset", "-q", "HEAD", "--"]
            and _path_operand(args[4], config)
        ):
            return _allow()
    elif verb == "ls-remote":
        if len(args) == 3 and args[1] == "origin" and _ref(args[2]):
            return _allow()
    elif verb == "worktree":
        if (
            len(args) == 6
            and args[:3] == ["worktree", "add", "-b"]
            and _branch(args[3], config)
            and _path_operand(args[4], config)
            and _ref(args[5])
        ):
            return _allow()
        if (
            len(args) == 4
            and args[:2] == ["worktree", "add"]
            and _path_operand(args[2], config)
            and _ref(args[3])
        ):
            return _allow()
        if (
            len(args) == 5
            and args[:3] == ["worktree", "add", "--detach"]
            and _path_operand(args[3], config)
            and _ref(args[4])
        ):
            return _allow()
        if (
            len(args) == 4
            and args[:2] == ["worktree", "remove"]
            and _path_operand(args[2], config)
            and args[3] == "--force"
        ):
            return _allow()
        if args == ["worktree", "list", "--porcelain", "-z"]:
            return _allow()
        if args == ["worktree", "prune"]:
            return _allow()
    elif verb == "symbolic-ref":
        if args == ["symbolic-ref", "--short", "HEAD"]:
            return _allow()
    elif verb == "cat-file":
        if len(args) == 3 and args[1] == "-e" and _ref(args[2]):
            return _allow()
    elif verb == "fetch":
        if len(args) == 4 and args[:3] == ["fetch", "--no-tags", "origin"] and _ref(args[3]):
            return _allow()
        if len(args) == 3 and _remote(args[1]) and _ref(args[2]):
            return _allow()
    elif verb == "diff":
        if len(args) == 3 and args[1] == "--numstat" and _ref(args[2]):
            return _allow()
        if len(args) == 3 and _ref(args[1]) and _ref(args[2]):
            return _allow()
        if (
            len(args) == 4
            and args[1] in {"--numstat", "--name-only"}
            and _ref(args[2])
            and _ref(args[3])
        ):
            return _allow()
        if (
            len(args) == 5
            and args[1:3] == ["-M", "--name-status"]
            and _ref(args[3])
            and _ref(args[4])
        ):
            return _allow()
        if args in (
            ["diff", "--name-only", "--diff-filter=U"],
            ["diff", "--name-only"],
            ["diff", "--cached", "--name-only"],
        ):
            return _allow()
    elif verb == "rev-list":
        if len(args) == 3 and args[1] == "--count" and ".." in args[2] and _ref(args[2]):
            return _allow()
    elif verb == "log":
        if (
            len(args) == 6
            and args[1:3] == ["--full-history", "--format=%H"]
            and ".." in args[3]
            and args[4] == "--"
            and _ref(args[3])
            and _path_operand(args[5], config)
        ):
            return _allow()
        if len(args) == 3 and args[1] == "--oneline" and ".." in args[2] and _ref(args[2]):
            return _allow()
    elif verb == "show":
        if len(args) == 2 and _show_target(args[1]):
            return _allow()
        if len(args) == 3 and args[1] == "--format=" and _ref(args[2]):
            return _allow()
        if (
            len(args) == 4
            and args[1] in {"--numstat", "--name-only"}
            and args[2] == "--format="
            and _ref(args[3])
        ):
            return _allow()
    elif verb == "status":
        if args == ["status", "--porcelain"]:
            return _allow()
    elif verb == "ls-files":
        if args == ["ls-files", "--others", "--exclude-standard"]:
            return _allow()
        if args == ["ls-files", "--stage", "-z"]:
            return _allow()
    elif verb == "grep":
        if (
            len(args) >= 13
            and args[:5] == ["grep", "--no-index", "-l", "-I", "-E"]
            and args[5:11]
            == ["-e", CONFLICT_MARKERS[0], "-e", CONFLICT_MARKERS[1], "-e", CONFLICT_MARKERS[2]]
            and args[11] == "--"
            and all(_path_operand(path, config) for path in args[12:])
        ):
            return _allow()
    elif verb == "add":
        if args == ["add", "-A"]:
            return _allow()
    elif verb == "rm":
        if args == ["rm", "-r", "--cached", "--ignore-unmatch", ".worktrees"]:
            return _allow()
    elif verb == "commit":
        if len(args) == 3 and args[:2] == ["commit", "-m"] and args[2]:
            return _allow()
        if len(args) == 4 and args[:2] == ["commit", "-m"] and args[2] and args[3] == "--no-verify":
            return _allow()
    elif verb == "init":
        if args == ["init"]:
            return _allow()
    elif verb == "merge":
        if len(args) == 2 and _ref(args[1]):
            return _allow()
    elif verb == "merge-base":
        if len(args) == 3 and _ref(args[1]) and _ref(args[2]):
            return _allow()
        if len(args) == 4 and args[1] == "--is-ancestor" and _ref(args[2]) and _ref(args[3]):
            return _allow()
    elif verb == "clone":
        if len(args) == 3 and URL_RE.match(args[1]) and _path_operand(args[2], config):
            return _allow()

    return _deny(f"git command is not allowed: {' '.join(args)}")


def validate_git_argv(argv: list[str], config: dict[str, Any] | None = None) -> ValidationResult:
    cfg = _config(config)
    if argv in (["--version"], ["--help"]):
        return _allow()
    parsed, command, state = _parse_global_options(list(argv), cfg)
    if not parsed.allowed:
        return parsed
    if not state["hooks_path"] or not state["fsmonitor"] or not state["safe_directory"]:
        return _deny("missing required git hardening globals")
    return _validate_command(command, cfg)


def load_git_config(
    path: str | Path = CONFIG_PATH, *, require_root_owner: bool = True
) -> ValidationResult:
    try:
        lst = os.lstat(path)
        if stat.S_ISLNK(lst.st_mode):
            return ValidationResult(False, "config must not be a symlink")
        st = os.stat(path)
    except OSError as exc:
        return ValidationResult(False, f"config not readable: {exc}")
    if not stat.S_ISREG(st.st_mode):
        return ValidationResult(False, "config is not a regular file")
    if require_root_owner and st.st_uid != 0:
        return ValidationResult(False, "config is not owned by root")
    if st.st_mode & 0o022:
        return ValidationResult(False, "config is group/world writable")
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        return ValidationResult(False, f"config malformed: {exc}")
    if not isinstance(data, dict):
        return ValidationResult(False, "config must be a JSON object")
    return ValidationResult(True, config=_config(data))


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    loaded = load_git_config()
    if not loaded.allowed:
        print(f"openace-git: {loaded.reason}", file=sys.stderr)
        return 126
    result = validate_git_argv(args, config=loaded.config)
    if not result.allowed:
        print(f"openace-git: denied: {result.reason}", file=sys.stderr)
        return 126
    try:
        completed = subprocess.run([_real_git_path(loaded.config or {}), *args])
        return completed.returncode
    except FileNotFoundError as exc:
        print(f"openace-git: real git binary not found: {exc}", file=sys.stderr)
        return 126


if __name__ == "__main__":
    raise SystemExit(main())
