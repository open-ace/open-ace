"""
File change block extraction for Qwen CLI sessions.

Issue #2589: File change panel for detecting file/folder operations.

This module implements a plugin architecture for parsing tool_use blocks
and generating file_change blocks for the frontend file-change panel.
"""

from __future__ import annotations

import logging
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class FileChangeRecord:
    """Represents a single file change record."""

    id: str
    path: str
    change_type: str  # create|modify|delete|rename|copy
    timestamp: str
    is_directory: bool
    session_id: str
    tool_use_id: str
    status: str = "pending"
    old_path: Optional[str] = None
    source: str = "unknown"
    tool_name: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


@dataclass
class ParserContext:
    """Context for file change parsing."""

    session_id: str
    tool_use_id: str
    project_path: str
    timestamp: str


class FileChangeParser(ABC):
    """Base class for file change parsers."""

    @abstractmethod
    def can_parse(self, tool_name: str, tool_input: dict) -> bool:
        """Check if this parser can handle the given tool."""
        pass

    @abstractmethod
    def parse(
        self, tool_name: str, tool_input: dict, context: ParserContext
    ) -> List[FileChangeRecord]:
        """Parse tool input and generate file change records."""
        pass

    @property
    def source(self) -> str:
        """Return the source identifier for this parser."""
        return "unknown"


class FileChangeParserRegistry:
    """Registry for file change parsers."""

    _parsers: List[FileChangeParser] = []

    @classmethod
    def register(cls, parser: FileChangeParser) -> None:
        """Register a parser."""
        cls._parsers.append(parser)
        logger.debug(f"Registered parser: {parser.__class__.__name__}")

    @classmethod
    def parse(
        cls, tool_name: str, tool_input: dict, context: ParserContext
    ) -> List[FileChangeRecord]:
        """Parse tool input using all registered parsers."""
        records = []
        for parser in cls._parsers:
            if parser.can_parse(tool_name, tool_input):
                try:
                    new_records = parser.parse(tool_name, tool_input, context)
                    records.extend(new_records)
                except Exception as e:
                    logger.warning(f"Parser {parser.__class__.__name__} failed: {e}")
        return records

    @classmethod
    def clear(cls) -> None:
        """Clear all registered parsers (for testing)."""
        cls._parsers = []


class MkdirParser(FileChangeParser):
    """Parser for mkdir shell commands."""

    def can_parse(self, tool_name: str, tool_input: dict) -> bool:
        if tool_name != "Bash":
            return False
        command = tool_input.get("command", "")
        return bool(re.search(r'\bmkdir\b', command))

    def parse(
        self, tool_name: str, tool_input: dict, context: ParserContext
    ) -> List[FileChangeRecord]:
        command = tool_input.get("command", "")
        paths = self._extract_paths(command)

        records = []
        for idx, path in enumerate(paths):
            abs_path = self._resolve_path(path, context.project_path)
            if not abs_path:
                continue
            if not self._is_safe_path(abs_path, context.project_path):
                continue

            record = FileChangeRecord(
                id=f"{context.session_id}:{context.tool_use_id}:{idx}",
                path=abs_path,
                change_type="create",
                timestamp=context.timestamp,
                is_directory=True,
                session_id=context.session_id,
                tool_use_id=context.tool_use_id,
                source=self.source,
                tool_name=tool_name,
            )
            records.append(record)
        return records

    @property
    def source(self) -> str:
        return "bash_mkdir"

    def _extract_paths(self, command: str) -> List[str]:
        """Extract paths from mkdir command."""
        cmd = command.strip()
        mkdir_match = re.match(r'\bmkdir\b\s*', cmd)
        if not mkdir_match:
            return []
        cmd = cmd[mkdir_match.end():]

        while cmd:
            opt_match = re.match(r'-[a-zA-Z]+\s*', cmd)
            if opt_match:
                cmd = cmd[opt_match.end():]
                continue
            long_opt_match = re.match(r'--[a-zA-Z-]+(?:=\S+)?\s*', cmd)
            if long_opt_match:
                cmd = cmd[long_opt_match.end():]
                continue
            if cmd.startswith('-- '):
                cmd = cmd[3:]
                break
            break

        paths = []
        while cmd:
            cmd = cmd.strip()
            if not cmd:
                break
            if cmd.startswith('"'):
                match = re.match(r'"([^"]+)"\s*', cmd)
                if match:
                    paths.append(match.group(1))
                    cmd = cmd[match.end():]
                    continue
            if cmd.startswith("'"):
                match = re.match(r"'([^']+)'\s*", cmd)
                if match:
                    paths.append(match.group(1))
                    cmd = cmd[match.end():]
                    continue
            match = re.match(r'([^\s]+)\s*', cmd)
            if match:
                path = match.group(1)
                if not path.startswith('-'):
                    paths.append(path)
                cmd = cmd[match.end():]
                continue
            break
        return paths

    def _resolve_path(self, path: str, project_path: str) -> Optional[str]:
        if not path:
            return None
        path = os.path.expandvars(path)
        path = os.path.expanduser(path)
        if os.path.isabs(path):
            return os.path.normpath(path)
        return os.path.normpath(os.path.join(project_path, path))

    def _is_safe_path(self, path: str, project_path: str) -> bool:
        try:
            abs_path = os.path.realpath(path)
            abs_project = os.path.realpath(project_path)
            if not abs_path.startswith(abs_project + os.sep) and abs_path != abs_project:
                return False
            dangerous_chars = ['$', '`', '|', ';', '&', '<', '>', '*']
            if any(char in path for char in dangerous_chars):
                return False
            return True
        except Exception:
            return False


class RmParser(FileChangeParser):
    """Parser for rm shell commands."""

    def can_parse(self, tool_name: str, tool_input: dict) -> bool:
        if tool_name != "Bash":
            return False
        command = tool_input.get("command", "")
        return bool(re.search(r'\brm\b', command))

    def parse(
        self, tool_name: str, tool_input: dict, context: ParserContext
    ) -> List[FileChangeRecord]:
        command = tool_input.get("command", "")
        paths = self._extract_paths(command)

        records = []
        for idx, path in enumerate(paths):
            abs_path = self._resolve_path(path, context.project_path)
            if not abs_path or not self._is_safe_path(abs_path, context.project_path):
                continue

            record = FileChangeRecord(
                id=f"{context.session_id}:{context.tool_use_id}:{idx}",
                path=abs_path,
                change_type="delete",
                timestamp=context.timestamp,
                is_directory=self._is_directory_flag(command),
                session_id=context.session_id,
                tool_use_id=context.tool_use_id,
                source=self.source,
                tool_name=tool_name,
            )
            records.append(record)
        return records

    @property
    def source(self) -> str:
        return "bash_rm"

    def _extract_paths(self, command: str) -> List[str]:
        cmd = command.strip()
        rm_match = re.match(r'\brm\b\s*', cmd)
        if not rm_match:
            return []
        cmd = cmd[rm_match.end():]
        while cmd:
            opt_match = re.match(r'-[rfv]+\s*', cmd)
            if opt_match:
                cmd = cmd[opt_match.end():]
                continue
            break

        paths = []
        while cmd:
            cmd = cmd.strip()
            if not cmd:
                break
            if cmd.startswith('"'):
                match = re.match(r'"([^"]+)"\s*', cmd)
                if match:
                    paths.append(match.group(1))
                    cmd = cmd[match.end():]
                    continue
            if cmd.startswith("'"):
                match = re.match(r"'([^']+)'\s*", cmd)
                if match:
                    paths.append(match.group(1))
                    cmd = cmd[match.end():]
                    continue
            match = re.match(r'([^\s]+)\s*', cmd)
            if match:
                path = match.group(1)
                if not path.startswith('-'):
                    paths.append(path)
                cmd = cmd[match.end():]
                continue
            break
        return paths

    def _resolve_path(self, path: str, project_path: str) -> Optional[str]:
        if not path:
            return None
        path = os.path.expandvars(path)
        path = os.path.expanduser(path)
        if os.path.isabs(path):
            return os.path.normpath(path)
        return os.path.normpath(os.path.join(project_path, path))

    def _is_safe_path(self, path: str, project_path: str) -> bool:
        try:
            abs_path = os.path.realpath(path)
            abs_project = os.path.realpath(project_path)
            if not abs_path.startswith(abs_project + os.sep) and abs_path != abs_project:
                return False
            return True
        except Exception:
            return False

    def _is_directory_flag(self, command: str) -> bool:
        return bool(re.search(r'-[rf]+\b', command) or re.search(r'-[a-zA-Z]*r', command))


class WriteFileParser(FileChangeParser):
    """Parser for write_file tool."""

    def can_parse(self, tool_name: str, tool_input: dict) -> bool:
        return tool_name in ("write_file", "Write")

    def parse(
        self, tool_name: str, tool_input: dict, context: ParserContext
    ) -> List[FileChangeRecord]:
        file_path = tool_input.get("file_path") or tool_input.get("path")
        if not file_path:
            return []

        abs_path = self._resolve_path(file_path, context.project_path)
        if not abs_path or not self._is_safe_path(abs_path, context.project_path):
            return []

        record = FileChangeRecord(
            id=f"{context.session_id}:{context.tool_use_id}:0",
            path=abs_path,
            change_type="modify",
            timestamp=context.timestamp,
            is_directory=False,
            session_id=context.session_id,
            tool_use_id=context.tool_use_id,
            source=self.source,
            tool_name=tool_name,
        )
        return [record]

    @property
    def source(self) -> str:
        return "write_file"

    def _resolve_path(self, path: str, project_path: str) -> Optional[str]:
        if not path:
            return None
        path = os.path.expandvars(path)
        path = os.path.expanduser(path)
        if os.path.isabs(path):
            return os.path.normpath(path)
        return os.path.normpath(os.path.join(project_path, path))

    def _is_safe_path(self, path: str, project_path: str) -> bool:
        try:
            abs_path = os.path.realpath(path)
            abs_project = os.path.realpath(project_path)
            if not abs_path.startswith(abs_project + os.sep) and abs_path != abs_project:
                return False
            return True
        except Exception:
            return False


class EditParser(FileChangeParser):
    """Parser for edit tool."""

    def can_parse(self, tool_name: str, tool_input: dict) -> bool:
        return tool_name == "edit"

    def parse(
        self, tool_name: str, tool_input: dict, context: ParserContext
    ) -> List[FileChangeRecord]:
        file_path = tool_input.get("file_path") or tool_input.get("path")
        if not file_path:
            return []

        abs_path = self._resolve_path(file_path, context.project_path)
        if not abs_path or not self._is_safe_path(abs_path, context.project_path):
            return []

        record = FileChangeRecord(
            id=f"{context.session_id}:{context.tool_use_id}:0",
            path=abs_path,
            change_type="modify",
            timestamp=context.timestamp,
            is_directory=False,
            session_id=context.session_id,
            tool_use_id=context.tool_use_id,
            source=self.source,
            tool_name=tool_name,
        )
        return [record]

    @property
    def source(self) -> str:
        return "edit"

    def _resolve_path(self, path: str, project_path: str) -> Optional[str]:
        if not path:
            return None
        path = os.path.expandvars(path)
        path = os.path.expanduser(path)
        if os.path.isabs(path):
            return os.path.normpath(path)
        return os.path.normpath(os.path.join(project_path, path))

    def _is_safe_path(self, path: str, project_path: str) -> bool:
        try:
            abs_path = os.path.realpath(path)
            abs_project = os.path.realpath(project_path)
            if not abs_path.startswith(abs_project + os.sep) and abs_path != abs_project:
                return False
            return True
        except Exception:
            return False


# Register default parsers
def _register_default_parsers() -> None:
    registry = FileChangeParserRegistry
    registry.register(MkdirParser())
    registry.register(RmParser())
    registry.register(WriteFileParser())
    registry.register(EditParser())


_register_default_parsers()


# Legacy API compatibility
__all__ = [
    "append_file_change_blocks",
    "extract_file_changes",
    "FileChangeRecord",
    "ParserContext",
    "FileChangeParser",
    "FileChangeParserRegistry",
]


def append_file_change_blocks(blocks: list[dict], context: ParserContext) -> None:
    """Append synthetic file_change blocks derived from tool_use blocks."""
    for block in blocks:
        if block.get("type") != "tool_use":
            continue

        tool_name = block.get("name", "")
        tool_input = block.get("input", {})
        tool_use_id = block.get("id", "")

        ctx = ParserContext(
            session_id=context.session_id,
            tool_use_id=tool_use_id,
            project_path=context.project_path,
            timestamp=context.timestamp,
        )

        records = FileChangeParserRegistry.parse(tool_name, tool_input, ctx)

        if records:
            file_change_block = {
                "type": "file_change",
                "status": "pending",
                "changes": [r.to_dict() for r in records],
            }
            blocks.append(file_change_block)


def extract_file_changes(tool_use_block: dict, context: ParserContext) -> list[dict] | None:
    """Extract file change records from a tool_use block."""
    if tool_use_block.get("type") != "tool_use":
        return None

    tool_name = tool_use_block.get("name", "")
    tool_input = tool_use_block.get("input", {})
    tool_use_id = tool_use_block.get("id", "")

    ctx = ParserContext(
        session_id=context.session_id,
        tool_use_id=tool_use_id,
        project_path=context.project_path,
        timestamp=context.timestamp,
    )

    records = FileChangeParserRegistry.parse(tool_name, tool_input, ctx)

    if not records:
        return None

    return [r.to_dict() for r in records]