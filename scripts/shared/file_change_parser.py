"""Extract file/folder changes from AI tool_use blocks (Issue #8).

The frontend "file change" panel renders content_blocks of type
``file_change`` (see frontend MessageContent.tsx). Codex CLI emits these
natively via ``patch_apply_end`` events; Qwen Code CLI only emits
``tool_use``/``tool_result`` blocks, so the file operations hidden inside
shell commands or native file tools are extracted here and converted into
``file_change`` blocks so every AI-driven file/folder change is shown.

This module is intentionally dependency-free (stdlib only) so it can be
imported both by the app package (``scripts.shared.file_change_parser``) and
by standalone fetch scripts (``shared.file_change_parser``).

Heuristic by design: shell command parsing cannot verify whether an operation
succeeded, so commands that are too complex to interpret safely (shell
variables, globs, redirection, pipes) are skipped rather than guessed.
"""

from __future__ import annotations

import re
from typing import Any

# Native Qwen CLI file tools.
_WRITE_TOOLS = {"write_file", "create_file"}
_EDIT_TOOLS = {"edit"}

# Tools whose ``input.command`` holds a shell command string.
_SHELL_TOOLS = {
    "run_shell_command",
    "shell_command",
    "execute_command",
    "bash",
    "zsh",
    "sh",
    "cmd",
    "powershell",
    "pwsh",
}

# Leading shell command word -> change_type.
#   add    -> new file/folder created
#   move   -> file/folder renamed/moved (frontend renders as warning)
#   copy   -> new file created by copying (frontend renders as warning)
#   delete -> file/folder removed
_SHELL_OP_TYPES = {
    "mkdir": "add",
    "touch": "add",
    "mv": "move",
    "rename": "move",
    "cp": "copy",
    "rm": "delete",
    "rmdir": "delete",
    "del": "delete",
    "rd": "delete",
}

# Constructs we refuse to interpret (false-positive avoidance).
_UNSAFE_RE = re.compile(r"[`$<>|*{}\[\]&]")


def _tokenize(command: str) -> list[str]:
    """Split a shell command into tokens, respecting single/double quotes.

    Backslashes are kept literal so Windows paths (``C:\\workspace``) survive;
    surrounding quotes are stripped from quoted tokens.
    """
    tokens: list[str] = []
    current: list[str] = []
    quote: str | None = None
    for ch in command:
        if quote:
            if ch == quote:
                quote = None
            else:
                current.append(ch)
        elif ch in ("'", '"'):
            quote = ch
        elif ch.isspace():
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(ch)
    if current:
        tokens.append("".join(current))
    return tokens


def _parse_shell_command(command: str) -> list[dict[str, Any]]:
    """Parse one shell command line into a list of file_change dicts."""
    if _UNSAFE_RE.search(command):
        return []
    tokens = _tokenize(command)
    if not tokens:
        return []

    # Strip leading privilege escalation wrapper (e.g. `sudo mkdir ...`).
    if tokens[0] == "sudo":
        tokens = tokens[1:]
        if not tokens:
            return []

    op = tokens[0].rstrip("-")
    change_type = _SHELL_OP_TYPES.get(op)
    if change_type is None:
        return []

    args = [a for a in tokens[1:] if a != "--" and not a.startswith("-")]

    if op in ("mv", "rename", "cp"):
        if len(args) < 2:
            return []
        src, dst = args[0], args[-1]
        if op == "cp":
            return [{"path": dst, "change_type": "add"}]
        return [{"path": dst, "change_type": "move", "old_path": src}]

    # mkdir / touch / rm / rmdir / del / rd: every non-flag arg is a path.
    return [{"path": p, "change_type": change_type} for p in args if p]


def extract_file_changes(tool_name: Any, tool_input: Any) -> list[dict[str, Any]]:
    """Return file_change ``changes`` entries derived from a tool_use block.

    Args:
        tool_name: The tool name (e.g. ``run_shell_command``, ``write_file``).
        tool_input: The tool input/args dict (e.g. ``{"command": "..."}``).

    Returns:
        A list of ``{"path": ..., "change_type": ...}`` dicts (may be empty).
    """
    if not isinstance(tool_name, str) or not tool_name:
        return []

    if tool_name in _WRITE_TOOLS or tool_name in _EDIT_TOOLS:
        path = None
        if isinstance(tool_input, dict):
            path = tool_input.get("file_path") or tool_input.get("path")
        if isinstance(path, str) and path.strip():
            return [{"path": path.strip(), "change_type": "modify"}]
        return []

    if tool_name not in _SHELL_TOOLS:
        return []

    command = None
    if isinstance(tool_input, dict):
        command = tool_input.get("command")
        if not command:
            # Some producers nest args one level deeper.
            args = tool_input.get("args")
            if isinstance(args, dict):
                command = args.get("command")
    elif isinstance(tool_input, str):
        command = tool_input

    if not isinstance(command, str) or not command.strip():
        return []

    changes: list[dict[str, Any]] = []
    # Handle compound commands: `a && b`, `a ; b`, `a || b`, newlines.
    for segment in re.split(r"\s*(?:&&|\|\||;)\s*|\n", command):
        segment = segment.strip()
        if not segment or segment.startswith("#"):
            continue
        changes.extend(_parse_shell_command(segment))
    return changes


def append_file_change_blocks(blocks: list[dict]) -> None:
    """Append file_change blocks derived from file-operating tool_use blocks.

    Issue #8: Qwen Code CLI emits only ``tool_use`` blocks for shell commands
    (mkdir/mv/cp/rm) and native file tools (write_file/edit); it never emits
    ``file_change`` blocks like Codex does via ``patch_apply_end``. The
    frontend file-change panel requires ``file_change`` content blocks, so the
    file operations hidden inside tool_use blocks are parsed into synthetic
    ``file_change`` blocks that ride along with the turn.

    Mutates ``blocks`` in place by appending the derived ``file_change``
    blocks. Shared by every write path that produces content_blocks:
    - live path: ``RemoteSessionManager._append_file_change_blocks``
    - historical replay: ``fetch_qwen``
    - web-terminal session sync: ``app/routes/remote.py`` (msg_type=session_sync)
    """
    file_change_blocks: list[dict[str, Any]] = []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            changes = extract_file_changes(block.get("name"), block.get("input"))
            if changes:
                file_change_blocks.append(
                    {
                        "type": "file_change",
                        "changes": changes,
                        "status": "accepted",
                        "success": True,
                    }
                )
    blocks.extend(file_change_blocks)
