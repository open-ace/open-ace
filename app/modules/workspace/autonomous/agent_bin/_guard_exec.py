#!/usr/bin/python3
"""Execution guards injected into autonomous agent PATH."""

import json
import os
import sys


def _deny(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(126)


def main() -> None:
    invoked = os.path.basename(sys.argv[0])
    args = sys.argv[1:]
    if invoked == "git":
        command = next((arg.lower() for arg in args if not arg.startswith("-")), "")
        if command not in {
            "check-attr",
            "diff",
            "grep",
            "log",
            "ls-files",
            "rev-parse",
            "show",
            "status",
        }:
            _deny("mutating git commands are reserved for the Open ACE orchestrator")
        target = os.environ.get("OPENACE_REAL_GIT", "")
        if not target:
            _deny("OPENACE_REAL_GIT is not configured")
        os.execv(target, [target, *args])
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
        os.execv(target, [target, *args])

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
    os.execvpe(command[0], [*command, *args], os.environ)


if __name__ == "__main__":
    main()
