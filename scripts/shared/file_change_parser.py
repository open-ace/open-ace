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
from dataclasses import asdict, dataclass
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
    old_path: str | None = None
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
    ) -> list[FileChangeRecord]:
        """Parse tool input and generate file change records."""
        pass

    @property
    def source(self) -> str:
        """Return the source identifier for this parser."""
        return "unknown"


class FileChangeParserRegistry:
    """Registry for file change parsers."""

    _parsers: list[FileChangeParser] = []

    @classmethod
    def register(cls, parser: FileChangeParser) -> None:
        """Register a parser."""
        cls._parsers.append(parser)
        logger.debug(f"Registered parser: {parser.__class__.__name__}")

    @classmethod
    def parse(
        cls, tool_name: str, tool_input: dict, context: ParserContext
    ) -> list[FileChangeRecord]:
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
        return bool(re.search(r"\bmkdir\b", command))

    def parse(
        self, tool_name: str, tool_input: dict, context: ParserContext
    ) -> list[FileChangeRecord]:
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

    def _extract_paths(self, command: str) -> list[str]:
        """Extract paths from mkdir command."""
        cmd = command.strip()
        mkdir_match = re.match(r"\bmkdir\b\s*", cmd)
        if not mkdir_match:
            return []
        cmd = cmd[mkdir_match.end() :]

        while cmd:
            opt_match = re.match(r"-[a-zA-Z]+\s*", cmd)
            if opt_match:
                cmd = cmd[opt_match.end() :]
                continue
            long_opt_match = re.match(r"--[a-zA-Z-]+(?:=\S+)?\s*", cmd)
            if long_opt_match:
                cmd = cmd[long_opt_match.end() :]
                continue
            if cmd.startswith("-- "):
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
                    cmd = cmd[match.end() :]
                    continue
            if cmd.startswith("'"):
                match = re.match(r"'([^']+)'\s*", cmd)
                if match:
                    paths.append(match.group(1))
                    cmd = cmd[match.end() :]
                    continue
            match = re.match(r"([^\s]+)\s*", cmd)
            if match:
                path = match.group(1)
                if not path.startswith("-"):
                    paths.append(path)
                cmd = cmd[match.end() :]
                continue
            break
        return paths

    def _resolve_path(self, path: str, project_path: str) -> str | None:
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
            dangerous_chars = ["$", "`", "|", ";", "&", "<", ">", "*"]
            return not any(char in path for char in dangerous_chars)
        except Exception:
            return False


class RmParser(FileChangeParser):
    """Parser for rm shell commands."""

    def can_parse(self, tool_name: str, tool_input: dict) -> bool:
        if tool_name != "Bash":
            return False
        command = tool_input.get("command", "")
        return bool(re.search(r"\brm\b", command))

    def parse(
        self, tool_name: str, tool_input: dict, context: ParserContext
    ) -> list[FileChangeRecord]:
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

    def _extract_paths(self, command: str) -> list[str]:
        cmd = command.strip()
        rm_match = re.match(r"\brm\b\s*", cmd)
        if not rm_match:
            return []
        cmd = cmd[rm_match.end() :]
        while cmd:
            opt_match = re.match(r"-[rfv]+\s*", cmd)
            if opt_match:
                cmd = cmd[opt_match.end() :]
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
                    cmd = cmd[match.end() :]
                    continue
            if cmd.startswith("'"):
                match = re.match(r"'([^']+)'\s*", cmd)
                if match:
                    paths.append(match.group(1))
                    cmd = cmd[match.end() :]
                    continue
            match = re.match(r"([^\s]+)\s*", cmd)
            if match:
                path = match.group(1)
                if not path.startswith("-"):
                    paths.append(path)
                cmd = cmd[match.end() :]
                continue
            break
        return paths

    def _resolve_path(self, path: str, project_path: str) -> str | None:
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
            return not (not abs_path.startswith(abs_project + os.sep) and abs_path != abs_project)
        except Exception:
            return False

    def _is_directory_flag(self, command: str) -> bool:
        return bool(re.search(r"-[rf]+\b", command) or re.search(r"-[a-zA-Z]*r", command))


class WriteFileParser(FileChangeParser):
    """Parser for write_file tool."""

    def can_parse(self, tool_name: str, tool_input: dict) -> bool:
        return tool_name in ("write_file", "Write")

    def parse(
        self, tool_name: str, tool_input: dict, context: ParserContext
    ) -> list[FileChangeRecord]:
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

    def _resolve_path(self, path: str, project_path: str) -> str | None:
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
            return not (not abs_path.startswith(abs_project + os.sep) and abs_path != abs_project)
        except Exception:
            return False


class EditParser(FileChangeParser):
    """Parser for edit tool."""

    def can_parse(self, tool_name: str, tool_input: dict) -> bool:
        return tool_name == "edit"

    def parse(
        self, tool_name: str, tool_input: dict, context: ParserContext
    ) -> list[FileChangeRecord]:
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

    def _resolve_path(self, path: str, project_path: str) -> str | None:
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
            return not (not abs_path.startswith(abs_project + os.sep) and abs_path != abs_project)
        except Exception:
            return False


def _contains_wildcard(path: str) -> bool:
    """Check if a path contains shell wildcard characters.

    Wildcards: *, ?, [...]
    The parser cannot resolve shell-expanded paths, so such paths should be rejected.

    Args:
        path: The path to check.

    Returns:
        True if the path contains wildcards, False otherwise.
    """
    return bool(re.search(r"[\*\?\[\]]", path))


class MvParser(FileChangeParser):
    """Parser for mv shell commands.

    Issue #2725: Add support for mv (rename/move) command parsing.

    Handles:
        - Simple rename: mv old.txt new.txt
        - Directory move: mv src_dir dest_dir
        - Multi-source move: mv a.txt b.txt dest/

    Output: FileChangeRecord with change_type='rename' and old_path set.
    """

    def can_parse(self, tool_name: str, tool_input: dict) -> bool:
        if tool_name != "Bash":
            return False
        command = tool_input.get("command", "")
        return bool(re.search(r"\bmv\b", command))

    def parse(
        self, tool_name: str, tool_input: dict, context: ParserContext
    ) -> list[FileChangeRecord]:
        command = tool_input.get("command", "")
        sources, destination = self._extract_paths(command)

        if not sources or not destination:
            return []

        records = []
        for idx, src_path in enumerate(sources):
            # Skip paths with wildcards - cannot resolve shell-expanded paths
            if _contains_wildcard(src_path):
                logger.warning(f"MvParser: Skipping path with wildcards: {src_path}")
                continue
            if _contains_wildcard(destination):
                logger.warning(f"MvParser: Skipping due to wildcard in destination: {destination}")
                continue

            abs_src = self._resolve_path(src_path, context.project_path)
            if not abs_src:
                continue

            # Security check on source path (old_path)
            if not self._is_safe_path(abs_src, context.project_path):
                continue

            # Determine target path
            # For multi-source moves, destination is a directory
            # Target path = destination + source filename
            if len(sources) > 1:
                src_basename = os.path.basename(src_path)
                target_path = os.path.join(destination, src_basename)
            else:
                target_path = destination

            abs_dest = self._resolve_path(target_path, context.project_path)
            if not abs_dest:
                continue

            # Security check on destination path
            if not self._is_safe_path(abs_dest, context.project_path):
                continue

            record = FileChangeRecord(
                id=f"{context.session_id}:{context.tool_use_id}:{idx}",
                path=abs_dest,
                old_path=abs_src,
                change_type="rename",
                timestamp=context.timestamp,
                # is_directory is set to False because:
                # - mv command doesn't require flags (like -r) to move directories
                # - We cannot reliably determine if the source is a directory from
                #   the command string alone without filesystem access
                # - The frontend can still display the change appropriately regardless
                is_directory=False,
                session_id=context.session_id,
                tool_use_id=context.tool_use_id,
                source=self.source,
                tool_name=tool_name,
            )
            records.append(record)
        return records

    @property
    def source(self) -> str:
        return "bash_mv"

    def _extract_paths(self, command: str) -> tuple[list[str], str | None]:
        """Extract source paths and destination from mv command.

        Returns:
            Tuple of (list of source paths, destination path or None).
        """
        cmd = command.strip()
        mv_match = re.match(r"\bmv\b\s*", cmd)
        if not mv_match:
            return [], None
        cmd = cmd[mv_match.end() :]

        # Skip common options: -f, -i, -n, -v, --backup, -b, -S, -t, -T, -u, -Z
        while cmd:
            # Skip short options like -f, -iv, etc.
            opt_match = re.match(r"-[finvubTS]+\s*", cmd)
            if opt_match:
                cmd = cmd[opt_match.end() :]
                continue
            # Skip long options like --backup, --target-directory, etc.
            long_opt_match = re.match(r"--[a-zA-Z-]+(?:=\S+)?\s*", cmd)
            if long_opt_match:
                cmd = cmd[long_opt_match.end() :]
                continue
            # Stop at -- (end of options)
            if cmd.startswith("-- "):
                cmd = cmd[3:]
                break
            break

        paths = []
        while cmd:
            cmd = cmd.strip()
            if not cmd:
                break
            # Handle double-quoted paths
            if cmd.startswith('"'):
                match = re.match(r'"([^"]+)"\s*', cmd)
                if match:
                    paths.append(match.group(1))
                    cmd = cmd[match.end() :]
                    continue
            # Handle single-quoted paths
            if cmd.startswith("'"):
                match = re.match(r"'([^']+)'\s*", cmd)
                if match:
                    paths.append(match.group(1))
                    cmd = cmd[match.end() :]
                    continue
            # Handle unquoted paths
            match = re.match(r"([^\s]+)\s*", cmd)
            if match:
                path = match.group(1)
                if not path.startswith("-"):
                    paths.append(path)
                cmd = cmd[match.end() :]
                continue
            break

        if len(paths) < 2:
            return paths, None

        # Last path is destination, rest are sources
        destination = paths[-1]
        sources = paths[:-1]
        return sources, destination

    def _resolve_path(self, path: str, project_path: str) -> str | None:
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
            dangerous_chars = ["$", "`", "|", ";", "&", "<", ">"]
            return not any(char in path for char in dangerous_chars)
        except Exception:
            return False


class CpParser(FileChangeParser):
    """Parser for cp shell commands.

    Issue #2725: Add support for cp (copy) command parsing.

    Handles:
        - Simple copy: cp src.txt dest.txt
        - Recursive copy: cp -r src_dir dest_dir
        - Archive copy: cp -a src dest (includes -r)
        - Multi-source copy: cp a.txt b.txt dest/

    Output: FileChangeRecord with change_type='copy', old_path set, and
            is_directory determined by -r/-R/-a flags.
    """

    def can_parse(self, tool_name: str, tool_input: dict) -> bool:
        if tool_name != "Bash":
            return False
        command = tool_input.get("command", "")
        return bool(re.search(r"\bcp\b", command))

    def parse(
        self, tool_name: str, tool_input: dict, context: ParserContext
    ) -> list[FileChangeRecord]:
        command = tool_input.get("command", "")
        sources, destination = self._extract_paths(command)

        if not sources or not destination:
            return []

        is_recursive = self._is_recursive_flag(command)

        records = []
        for idx, src_path in enumerate(sources):
            # Skip paths with wildcards - cannot resolve shell-expanded paths
            if _contains_wildcard(src_path):
                logger.warning(f"CpParser: Skipping path with wildcards: {src_path}")
                continue
            if _contains_wildcard(destination):
                logger.warning(f"CpParser: Skipping due to wildcard in destination: {destination}")
                continue

            abs_src = self._resolve_path(src_path, context.project_path)
            if not abs_src:
                continue

            # Security check on source path (old_path)
            if not self._is_safe_path(abs_src, context.project_path):
                continue

            # Determine target path
            # For multi-source copies, destination is a directory
            # Target path = destination + source filename
            if len(sources) > 1:
                src_basename = os.path.basename(src_path)
                target_path = os.path.join(destination, src_basename)
            else:
                target_path = destination

            abs_dest = self._resolve_path(target_path, context.project_path)
            if not abs_dest:
                continue

            # Security check on destination path
            if not self._is_safe_path(abs_dest, context.project_path):
                continue

            record = FileChangeRecord(
                id=f"{context.session_id}:{context.tool_use_id}:{idx}",
                path=abs_dest,
                old_path=abs_src,
                change_type="copy",
                timestamp=context.timestamp,
                is_directory=is_recursive,
                session_id=context.session_id,
                tool_use_id=context.tool_use_id,
                source=self.source,
                tool_name=tool_name,
            )
            records.append(record)
        return records

    @property
    def source(self) -> str:
        return "bash_cp"

    def _extract_paths(self, command: str) -> tuple[list[str], str | None]:
        """Extract source paths and destination from cp command.

        Returns:
            Tuple of (list of source paths, destination path or None).
        """
        cmd = command.strip()
        cp_match = re.match(r"\bcp\b\s*", cmd)
        if not cp_match:
            return [], None
        cmd = cmd[cp_match.end() :]

        # Skip common options: -r, -R, -a, -f, -i, -p, -v, -l, -s, -u, -x, -Z
        while cmd:
            # Skip short options like -r, -rf, -av, etc.
            opt_match = re.match(r"-[rRaflipsuvxZ]+\s*", cmd)
            if opt_match:
                cmd = cmd[opt_match.end() :]
                continue
            # Skip long options like --recursive, --archive, etc.
            long_opt_match = re.match(r"--[a-zA-Z-]+(?:=\S+)?\s*", cmd)
            if long_opt_match:
                cmd = cmd[long_opt_match.end() :]
                continue
            # Stop at -- (end of options)
            if cmd.startswith("-- "):
                cmd = cmd[3:]
                break
            break

        paths = []
        while cmd:
            cmd = cmd.strip()
            if not cmd:
                break
            # Handle double-quoted paths
            if cmd.startswith('"'):
                match = re.match(r'"([^"]+)"\s*', cmd)
                if match:
                    paths.append(match.group(1))
                    cmd = cmd[match.end() :]
                    continue
            # Handle single-quoted paths
            if cmd.startswith("'"):
                match = re.match(r"'([^']+)'\s*", cmd)
                if match:
                    paths.append(match.group(1))
                    cmd = cmd[match.end() :]
                    continue
            # Handle unquoted paths
            match = re.match(r"([^\s]+)\s*", cmd)
            if match:
                path = match.group(1)
                if not path.startswith("-"):
                    paths.append(path)
                cmd = cmd[match.end() :]
                continue
            break

        if len(paths) < 2:
            return paths, None

        # Last path is destination, rest are sources
        destination = paths[-1]
        sources = paths[:-1]
        return sources, destination

    def _is_recursive_flag(self, command: str) -> bool:
        """Check if command has recursive/archive flags.

        Returns True if -r, -R, -a, or --recursive is present.
        -a (archive) implies -r.
        """
        # Check for -r or -R (possibly combined with other flags like -rf)
        if re.search(r"-[a-zA-Z]*r[a-zA-Z]*\b", command):
            return True
        if re.search(r"-[a-zA-Z]*R[a-zA-Z]*\b", command):
            return True
        # Check for -a (archive, includes -r)
        if re.search(r"-[a-zA-Z]*a[a-zA-Z]*\b", command):
            return True
        # Check for --recursive long option
        if re.search(r"--recursive\b", command):
            return True
        return False

    def _resolve_path(self, path: str, project_path: str) -> str | None:
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
            dangerous_chars = ["$", "`", "|", ";", "&", "<", ">"]
            return not any(char in path for char in dangerous_chars)
        except Exception:
            return False


# Register default parsers
def _register_default_parsers() -> None:
    """Register default file change parsers.

    Parsers are registered based on configuration. Each parser can be
    enabled/disabled via config/file_change_parser.yaml.

    Registered parsers:
        - MkdirParser: mkdir commands (create directories)
        - RmParser: rm commands (delete files/directories)
        - WriteFileParser: write_file/Write tool calls
        - EditParser: edit tool calls
        - MvParser: mv commands (rename/move files) - Issue #2725
        - CpParser: cp commands (copy files) - Issue #2725
    """
    registry = FileChangeParserRegistry

    # Try to load configuration, fall back to enabling all parsers on error
    try:
        from app.utils.file_change_config import get_file_change_parser_config

        config = get_file_change_parser_config(use_cache=False)

        if config.parsers.mkdir.enabled:
            registry.register(MkdirParser())
        if config.parsers.rm.enabled:
            registry.register(RmParser())
        if config.parsers.write_file.enabled:
            registry.register(WriteFileParser())
        if config.parsers.edit.enabled:
            registry.register(EditParser())
        if config.parsers.mv.enabled:
            registry.register(MvParser())
        if config.parsers.cp.enabled:
            registry.register(CpParser())
    except Exception as e:
        # Fall back to registering all parsers if config loading fails
        logger.warning(f"Failed to load parser config, registering all parsers: {e}")
        registry.register(MkdirParser())
        registry.register(RmParser())
        registry.register(WriteFileParser())
        registry.register(EditParser())
        registry.register(MvParser())
        registry.register(CpParser())


_register_default_parsers()


# Legacy API compatibility
__all__ = [
    "append_file_change_blocks",
    "extract_file_changes",
    "FileChangeRecord",
    "ParserContext",
    "FileChangeParser",
    "FileChangeParserRegistry",
    "MkdirParser",
    "RmParser",
    "WriteFileParser",
    "EditParser",
    "MvParser",
    "CpParser",
    "_contains_wildcard",
]


def append_file_change_blocks(blocks: list[dict], context: ParserContext | None = None) -> None:
    """Append synthetic file_change blocks derived from tool_use blocks.

    Args:
        blocks: List of content blocks to augment (modified in place).
        context: Optional parser context. If None, no file_change blocks are appended.

    Note:
        Context is optional for backward compatibility with callers that don't have
        session/project information available.
    """
    if context is None:
        return

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
    if not tool_use_block:
        return None

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
