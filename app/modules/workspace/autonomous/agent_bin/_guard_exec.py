#!/usr/bin/python3
"""Execution guards injected into autonomous agent PATH."""

import json
import os
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


def _parse_git_command(args: list[str]) -> tuple[str, list[str], str, list[str]]:
    """Return command, command args, effective cwd, and path redirections."""
    effective_cwd = os.path.realpath(os.getcwd())
    redirected_paths: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"-c", "--config-env"}:
            index += 2
            continue
        if arg.startswith("-c") and arg != "-c":
            index += 1
            continue
        if arg == "-C":
            if index + 1 >= len(args):
                return "", [], effective_cwd, redirected_paths
            effective_cwd = _resolve_from(args[index + 1], effective_cwd)
            redirected_paths.append(effective_cwd)
            index += 2
            continue
        if arg in {"--git-dir", "--work-tree"}:
            if index + 1 >= len(args):
                return "", [], effective_cwd, redirected_paths
            redirected_paths.append(_resolve_from(args[index + 1], effective_cwd))
            index += 2
            continue
        if arg.startswith("--git-dir=") or arg.startswith("--work-tree="):
            redirected_paths.append(_resolve_from(arg.split("=", 1)[1], effective_cwd))
            index += 1
            continue
        if arg.startswith("-"):
            index += 1
            continue
        return arg.lower(), args[index + 1 :], effective_cwd, redirected_paths
    return "", [], effective_cwd, redirected_paths


def _cache_git_mutation_allowed(args: list[str]) -> bool:
    """Allow pre-commit to manage only its credentialless cache repositories."""
    configured_root = os.environ.get("OPENACE_GIT_CACHE_ROOT", "").strip()
    if not configured_root:
        return False
    cache_root = os.path.realpath(configured_root)
    command, command_args, effective_cwd, redirected_paths = _parse_git_command(args)
    if not command:
        return False

    for env_name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE"):
        env_path = os.environ.get(env_name, "").strip()
        if env_path and not _is_within(_resolve_from(env_path, effective_cwd), cache_root):
            return False
    if any(not _is_within(path, cache_root) for path in redirected_paths):
        return False

    # Once pre-commit is inside its cache clone, remote/fetch/checkout and
    # other normal repository setup operations are safe. Explicit -C,
    # --git-dir and environment redirects above cannot escape the cache root.
    if _is_within(effective_cwd, cache_root):
        return True

    # The first operation starts outside the cache and initializes a new cache
    # repository. Accept only an init target below the configured root; never
    # allow a separate git dir that could redirect metadata elsewhere.
    if command != "init" or any(
        arg == "--separate-git-dir" or arg.startswith("--separate-git-dir=") for arg in command_args
    ):
        return False
    positional: list[str] = []
    skip_next = False
    for arg in command_args:
        if skip_next:
            skip_next = False
            continue
        if arg in {"--template", "--object-format", "--ref-format"}:
            skip_next = True
            continue
        if arg.startswith("-"):
            continue
        positional.append(arg)
    target = _resolve_from(positional[-1], effective_cwd) if positional else effective_cwd
    return _is_within(target, cache_root)


def main() -> None:
    invoked = os.path.basename(sys.argv[0])
    args = sys.argv[1:]
    if invoked == "git":
        command, _, _, _ = _parse_git_command(args)
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
