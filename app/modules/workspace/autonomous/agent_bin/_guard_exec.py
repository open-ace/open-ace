#!/usr/bin/python3
"""Execution guards injected into autonomous agent PATH."""

from __future__ import annotations

import json
import os
import shlex
import sys

_READ_ONLY_GIT_COMMANDS = {
    "check-attr",
    "diff",
    "grep",
    "log",
    "ls-files",
    "rev-parse",
    "show",
    "status",
}
_SAFE_CACHE_GIT_CONFIGS = {
    ("core.autocrlf", "false"),
    ("core.usebuiltinfsmonitor", "false"),
    ("protocol.version", "2"),
}


def _deny(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(126)


def _is_within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath((os.path.realpath(path), root)) == root
    except (OSError, ValueError):
        return False


def _resolve_from(path: str, base: str) -> str:
    return os.path.realpath(path if os.path.isabs(path) else os.path.join(base, path))


def _safe_cache_git_config(value: str) -> bool:
    key, separator, setting = value.partition("=")
    return bool(separator) and (key.lower(), setting.lower()) in _SAFE_CACHE_GIT_CONFIGS


def _parse_git_command(args: list[str]) -> tuple[str, list[str], str, list[str], bool]:
    """Return command details and whether its global options are cache-safe."""
    effective_cwd = os.path.realpath(os.getcwd())
    redirected_paths: list[str] = []
    safe_global_options = True
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "-c":
            if index + 1 >= len(args) or not _safe_cache_git_config(args[index + 1]):
                safe_global_options = False
            index += 2
            continue
        if arg.startswith("-c") and arg != "-c":
            if not _safe_cache_git_config(arg[2:]):
                safe_global_options = False
            index += 1
            continue
        if arg == "--config-env" or arg.startswith("--config-env="):
            safe_global_options = False
            index += 2 if arg == "--config-env" else 1
            continue
        if arg == "-C":
            if index + 1 >= len(args):
                return "", [], effective_cwd, redirected_paths, False
            effective_cwd = _resolve_from(args[index + 1], effective_cwd)
            redirected_paths.append(effective_cwd)
            index += 2
            continue
        if arg in {"--git-dir", "--work-tree"}:
            if index + 1 >= len(args):
                return "", [], effective_cwd, redirected_paths, False
            redirected_paths.append(_resolve_from(args[index + 1], effective_cwd))
            index += 2
            continue
        if arg.startswith("--git-dir=") or arg.startswith("--work-tree="):
            redirected_paths.append(_resolve_from(arg.split("=", 1)[1], effective_cwd))
            index += 1
            continue
        if arg.startswith("-"):
            safe_global_options = False
            index += 1
            continue
        return (
            arg.lower(),
            args[index + 1 :],
            effective_cwd,
            redirected_paths,
            safe_global_options,
        )
    return "", [], effective_cwd, redirected_paths, False


def _safe_environment_git_configs() -> bool:
    parameters = os.environ.get("GIT_CONFIG_PARAMETERS", "").strip()
    if parameters:
        try:
            if not all(_safe_cache_git_config(item) for item in shlex.split(parameters)):
                return False
        except ValueError:
            return False

    raw_count = os.environ.get("GIT_CONFIG_COUNT", "").strip()
    if not raw_count:
        return True
    try:
        count = int(raw_count)
    except ValueError:
        return False
    return count >= 0 and all(
        _safe_cache_git_config(
            f"{os.environ.get(f'GIT_CONFIG_KEY_{index}', '')}="
            f"{os.environ.get(f'GIT_CONFIG_VALUE_{index}', '')}"
        )
        for index in range(count)
    )


def _cache_init_target(command_args: list[str], effective_cwd: str) -> str | None:
    """Accept only pre-commit's `init --template[=]... <cache-path>` form."""
    target: str | None = None
    index = 0
    while index < len(command_args):
        arg = command_args[index]
        if arg == "--template":
            if index + 1 >= len(command_args):
                return None
            index += 2
            continue
        if arg.startswith("--template="):
            index += 1
            continue
        if arg.startswith("-") or target is not None:
            return None
        target = _resolve_from(arg, effective_cwd)
        index += 1
    return target


def _cache_setup_command_allowed(command: str, command_args: list[str]) -> bool:
    """Allow only the Git mutations used while cloning pre-commit hook repos."""
    if command == "remote":
        return len(command_args) == 3 and command_args[:2] == ["add", "origin"]
    if command == "fetch":
        return bool(command_args) and command_args[0] == "origin"
    if command == "checkout":
        return bool(command_args)
    if command == "submodule":
        return bool(command_args) and command_args[0] == "update"
    return False


def _cache_git_mutation_allowed(args: list[str]) -> bool:
    """Allow pre-commit to manage only its credentialless cache repositories."""
    configured_root = os.environ.get("OPENACE_GIT_CACHE_ROOT", "").strip()
    if not configured_root:
        return False
    cache_root = os.path.realpath(configured_root)
    command, command_args, effective_cwd, redirected_paths, safe_global_options = (
        _parse_git_command(args)
    )
    if not command or not safe_global_options or not _safe_environment_git_configs():
        return False

    for env_name in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_QUARANTINE_PATH",
    ):
        env_path = os.environ.get(env_name, "").strip()
        if env_path and not _is_within(_resolve_from(env_path, effective_cwd), cache_root):
            return False
    if any(
        os.environ.get(name, "").strip()
        for name in (
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            "GIT_CONFIG_GLOBAL",
            "GIT_CONFIG_SYSTEM",
        )
    ):
        return False
    if any(not _is_within(path, cache_root) for path in redirected_paths):
        return False

    if command == "init":
        target = _cache_init_target(command_args, effective_cwd)
        return target is not None and _is_within(target, cache_root)
    return _is_within(effective_cwd, cache_root) and _cache_setup_command_allowed(
        command, command_args
    )


def main() -> None:
    invoked = os.path.basename(sys.argv[0])
    args = sys.argv[1:]
    if invoked == "git":
        command, _, _, _, _ = _parse_git_command(args)
        if command not in _READ_ONLY_GIT_COMMANDS and not _cache_git_mutation_allowed(args):
            _deny("mutating git commands are reserved for the Open ACE orchestrator")
        target = os.environ.get("OPENACE_REAL_GIT", "")
        if not target:
            _deny("OPENACE_REAL_GIT is not configured")
        os.execv(target, [target, *args])  # nosec B606
    if invoked == "gh":
        normalized = [arg.lower() for arg in args]
        allowed = len(normalized) >= 2 and (normalized[0], normalized[1]) in {
            ("issue", "view"),
            ("pr", "view"),
            ("pr", "diff"),
            ("pr", "checks"),
            ("repo", "view"),
        }
        if not allowed:
            _deny("this gh command is reserved for the Open ACE orchestrator")
        target = os.environ.get("OPENACE_REAL_GH", "")
        if not target:
            _deny("OPENACE_REAL_GH is not configured")
        os.execv(target, [target, *args])  # nosec B606

    raw_command = os.environ.get("OPENACE_PYTHON_COMMAND", "")
    try:
        command = json.loads(raw_command)
    except (TypeError, json.JSONDecodeError):
        command = []
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(item, str) and item for item in command)
    ):
        _deny("OPENACE_PYTHON_COMMAND is not configured")
    if invoked == "pytest":
        command = [*command, "-m", "pytest"]
    os.execvpe(command[0], [*command, *args], os.environ)  # nosec B606


if __name__ == "__main__":
    main()
