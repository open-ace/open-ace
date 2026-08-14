"""
File change block extraction for Qwen CLI sessions.

Issue #8: Qwen CLI emits tool_use blocks for file operations (mkdir/mv/cp/rm,
write_file/edit) without corresponding file_change blocks. This module extracts
file change information from tool_use blocks for the frontend file-change panel.

Issue #2589: File change panel configuration for detecting file/folder operations.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Protocol
else:
    Protocol = object  # type: ignore[misc, assignment]

logger = logging.getLogger(__name__)

__all__ = ["append_file_change_blocks", "extract_file_changes"]


@dataclass
class FileChangeRecord:
    """Represents a single file/folder change detected from a tool_use block."""

    path: str
    change_type: str  # 'add' | 'modify' | 'delete'


class FileChangeParser(Protocol):
    """Protocol for file change parsers."""

    def can_parse(self, tool_name: str, tool_input: dict) -> bool:
        """Check if this parser can handle the given tool invocation."""
        ...

    def parse(self, tool_name: str, tool_input: dict, project_path: str) -> list[FileChangeRecord]:
        """Extract file changes from the tool invocation."""
        ...


class MkdirParser:
    """Parser for mkdir commands."""

    def can_parse(self, tool_name: str, tool_input: dict) -> bool:
        if tool_name != "Bash":
            return False
        command = tool_input.get("command", "")
        return "mkdir" in command

    def parse(self, tool_name: str, tool_input: dict, project_path: str) -> list[FileChangeRecord]:
        """Extract paths from mkdir command."""
        command = tool_input.get("command", "")
        records: list[FileChangeRecord] = []

        try:
            # Parse mkdir command to extract paths
            paths = self._extract_mkdir_paths(command)
            for path in paths:
                abs_path = self._resolve_path(path, project_path)
                if abs_path and self._is_safe_path(abs_path, project_path):
                    records.append(FileChangeRecord(path=abs_path, change_type="add"))
        except Exception as e:
            logger.warning(f"Failed to parse mkdir command '{command[:50]}...': {e}")

        return records

    def _extract_mkdir_paths(self, command: str) -> list[str]:
        """Extract path arguments from mkdir command."""
        paths: list[str] = []

        # Remove mkdir and flags, extract path arguments
        # Handle: mkdir foo, mkdir -p foo/bar, mkdir "path with spaces"
        try:
            # Simple regex approach for common cases
            # Match mkdir followed by optional flags and then paths
            mkdir_pattern = r"mkdir\s+(?:-[pvPm]+\s+)*(.+?)(?:\s*;|\s*&&|\s*\|\||$)"
            match = re.search(mkdir_pattern, command, re.IGNORECASE)
            if not match:
                return paths

            args_str = match.group(1).strip()

            # Split by spaces, respecting quotes
            try:
                parts = shlex.split(args_str)
            except ValueError:
                # Fallback to simple split if shlex fails
                parts = args_str.split()

            for part in parts:
                # Skip flags
                if part.startswith("-"):
                    continue
                paths.append(part)
        except Exception as e:
            logger.debug(f"Error extracting mkdir paths: {e}")

        return paths

    def _resolve_path(self, path: str, project_path: str) -> str | None:
        """Resolve relative path to absolute path."""
        if not path:
            return None

        try:
            path = os.path.expanduser(path)
            path = os.path.expandvars(path)

            if os.path.isabs(path):
                return os.path.normpath(path)

            return os.path.normpath(os.path.join(project_path, path))
        except Exception as e:
            logger.debug(f"Error resolving path '{path}': {e}")
            return None

    def _is_safe_path(self, path: str, project_path: str) -> bool:
        """Verify path is within project directory (prevent path traversal)."""
        try:
            abs_path = os.path.realpath(path)
            abs_project = os.path.realpath(project_path)

            # Path must be within project directory
            return abs_path.startswith(abs_project + os.sep) or abs_path == abs_project
        except Exception:
            return False


class WriteFileParser:
    """Parser for write_file tool."""

    def can_parse(self, tool_name: str, tool_input: dict) -> bool:
        return tool_name in ("write_file", "WriteFile")

    def parse(self, tool_name: str, tool_input: dict, project_path: str) -> list[FileChangeRecord]:
        file_path = tool_input.get("file_path", "")
        if not file_path:
            return []

        abs_path = self._resolve_path(file_path, project_path)
        if not abs_path or not self._is_safe_path(abs_path, project_path):
            return []

        return [FileChangeRecord(path=abs_path, change_type="add")]

    def _resolve_path(self, path: str, project_path: str) -> str | None:
        if not path:
            return None
        try:
            path = os.path.expanduser(path)
            path = os.path.expandvars(path)
            if os.path.isabs(path):
                return os.path.normpath(path)
            return os.path.normpath(os.path.join(project_path, path))
        except Exception:
            return None

    def _is_safe_path(self, path: str, project_path: str) -> bool:
        try:
            abs_path = os.path.realpath(path)
            abs_project = os.path.realpath(project_path)
            return abs_path.startswith(abs_project + os.sep) or abs_path == abs_project
        except Exception:
            return False


class EditParser:
    """Parser for edit tool."""

    def can_parse(self, tool_name: str, tool_input: dict) -> bool:
        return tool_name == "edit"

    def parse(self, tool_name: str, tool_input: dict, project_path: str) -> list[FileChangeRecord]:
        file_path = tool_input.get("file_path", "")
        if not file_path:
            return []

        abs_path = self._resolve_path(file_path, project_path)
        if not abs_path or not self._is_safe_path(abs_path, project_path):
            return []

        return [FileChangeRecord(path=abs_path, change_type="modify")]

    def _resolve_path(self, path: str, project_path: str) -> str | None:
        if not path:
            return None
        try:
            path = os.path.expanduser(path)
            path = os.path.expandvars(path)
            if os.path.isabs(path):
                return os.path.normpath(path)
            return os.path.normpath(os.path.join(project_path, path))
        except Exception:
            return None

    def _is_safe_path(self, path: str, project_path: str) -> bool:
        try:
            abs_path = os.path.realpath(path)
            abs_project = os.path.realpath(project_path)
            return abs_path.startswith(abs_project + os.sep) or abs_path == abs_project
        except Exception:
            return False


# Registered parsers (P0: mkdir, write_file, edit)
_PARSERS: list[FileChangeParser] = [
    MkdirParser(),
    WriteFileParser(),
    EditParser(),
]


def extract_file_changes(tool_use_block: dict, project_path: str) -> list[dict] | None:
    """Extract file change records from a tool_use block.

    Args:
        tool_use_block: A tool_use content block with 'name' and 'input' keys.
        project_path: The project root path for resolving relative paths.

    Returns:
        List of file_change records (dict with 'path' and 'change_type'),
        or None if no file changes detected.
    """
    if not tool_use_block:
        return None

    tool_name = tool_use_block.get("name", "")
    tool_input = tool_use_block.get("input", {})

    if not isinstance(tool_input, dict):
        tool_input = {}

    all_records: list[FileChangeRecord] = []

    for parser in _PARSERS:
        try:
            if parser.can_parse(tool_name, tool_input):
                records = parser.parse(tool_name, tool_input, project_path)
                all_records.extend(records)
        except Exception as e:
            logger.warning(f"Parser {parser.__class__.__name__} failed: {e}")
            continue

    if not all_records:
        return None

    # Convert to dict format expected by frontend
    return [
        {"path": r.path, "change_type": r.change_type}
        for r in all_records
    ]


def append_file_change_blocks(blocks: list[dict]) -> None:
    """Append synthetic file_change blocks derived from tool_use blocks.

    Args:
        blocks: List of content blocks to augment (modified in place).

    Note:
        This function is kept for backward compatibility.
        Prefer using extract_file_changes() directly.
    """
    # This is now a no-op placeholder
    # The actual implementation is in agent_runner integration
    pass