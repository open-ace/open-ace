"""
Unit tests for file change parser.

Issue #2589: File change panel configuration for detecting file/folder operations.
"""

import os
import tempfile
from datetime import datetime, timezone

import pytest

from shared.file_change_parser import (
    FileChangeParserRegistry,
    FileChangeRecord,
    MkdirParser,
    ParserContext,
    append_file_change_blocks,
    extract_file_changes,
)


class TestMkdirParser:
    """Tests for MkdirParser."""

    def setup_method(self):
        """Clear registry before each test."""
        FileChangeParserRegistry.clear()
        FileChangeParserRegistry.register(MkdirParser())

    def test_can_parse_bash_mkdir(self):
        """Test that parser recognizes mkdir in Bash commands."""
        parser = MkdirParser()
        assert parser.can_parse("Bash", {"command": "mkdir foo"}) is True
        assert parser.can_parse("Bash", {"command": "mkdir -p foo/bar"}) is True
        assert parser.can_parse("Bash", {"command": "ls -la"}) is False
        assert parser.can_parse("Write", {"file_path": "/tmp/test.txt"}) is False

    def test_extract_paths_simple(self):
        """Test extracting paths from simple mkdir command."""
        parser = MkdirParser()
        paths = parser._extract_paths("mkdir foo")
        assert "foo" in paths

    def test_extract_paths_with_p_flag(self):
        """Test extracting paths from mkdir -p command."""
        parser = MkdirParser()
        paths = parser._extract_paths("mkdir -p foo/bar/baz")
        assert len(paths) >= 1

    def test_extract_paths_quoted(self):
        """Test extracting paths with quotes."""
        parser = MkdirParser()
        paths = parser._extract_paths('mkdir "path with spaces"')
        assert any("path with spaces" in p for p in paths)

    def test_resolve_path_relative(self):
        """Test resolving relative paths."""
        parser = MkdirParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            abs_path = parser._resolve_path("foo/bar", tmpdir)
            expected = os.path.normpath(os.path.join(tmpdir, "foo/bar"))
            assert abs_path == expected

    def test_resolve_path_absolute(self):
        """Test that absolute paths are preserved."""
        parser = MkdirParser()
        abs_path = parser._resolve_path("/tmp/test", "/project")
        assert abs_path == "/tmp/test"

    def test_resolve_path_home(self):
        """Test resolving home directory."""
        parser = MkdirParser()
        home = os.path.expanduser("~")
        abs_path = parser._resolve_path("~/test", "/project")
        expected = os.path.normpath(os.path.join(home, "test"))
        assert abs_path == expected

    def test_is_safe_path_valid(self):
        """Test that paths within project are safe."""
        parser = MkdirParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            assert parser._is_safe_path(os.path.join(tmpdir, "foo"), tmpdir) is True

    def test_is_safe_path_escape(self):
        """Test that paths escaping project are unsafe."""
        parser = MkdirParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            assert parser._is_safe_path("/etc/passwd", tmpdir) is False

    def test_is_safe_path_dangerous_chars(self):
        """Test that paths with dangerous characters are unsafe."""
        parser = MkdirParser()
        assert parser._is_safe_path("foo; rm -rf /", "/tmp") is False
        assert parser._is_safe_path("$(whoami)", "/tmp") is False

    def test_parse_creates_record(self):
        """Test that parse creates FileChangeRecord."""
        parser = MkdirParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            context = ParserContext(
                session_id="test-session",
                tool_use_id="tool-123",
                project_path=tmpdir,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            records = parser.parse("Bash", {"command": "mkdir test-folder"}, context)

            assert len(records) >= 1
            assert records[0].change_type == "create"
            assert records[0].is_directory is True
            assert records[0].session_id == "test-session"
            assert records[0].source == "bash_mkdir"


class TestFileChangeParserRegistry:
    """Tests for FileChangeParserRegistry."""

    def setup_method(self):
        """Clear registry before each test."""
        FileChangeParserRegistry.clear()

    def test_register_and_parse(self):
        """Test registering a parser and parsing."""
        FileChangeParserRegistry.register(MkdirParser())

        with tempfile.TemporaryDirectory() as tmpdir:
            context = ParserContext(
                session_id="test",
                tool_use_id="tool-1",
                project_path=tmpdir,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

            records = FileChangeParserRegistry.parse(
                "Bash", {"command": "mkdir test-folder"}, context
            )

            assert len(records) >= 1

    def test_parse_non_matching_tool(self):
        """Test that non-matching tools return empty list."""
        FileChangeParserRegistry.register(MkdirParser())

        context = ParserContext(
            session_id="test",
            tool_use_id="tool-1",
            project_path="/tmp",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        records = FileChangeParserRegistry.parse("Write", {"file_path": "/tmp/test.txt"}, context)

        assert records == []

    def test_parse_exception_handling(self):
        """Test that parser exceptions are caught."""

        class FailingParser(MkdirParser):
            def parse(self, tool_name, tool_input, context):
                raise RuntimeError("Intentional failure")

        FileChangeParserRegistry.register(FailingParser())

        context = ParserContext(
            session_id="test",
            tool_use_id="tool-1",
            project_path="/tmp",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        records = FileChangeParserRegistry.parse("Bash", {"command": "mkdir test"}, context)

        assert records == []


class TestFileChangeRecord:
    """Tests for FileChangeRecord."""

    def test_to_dict(self):
        """Test serialization to dictionary."""
        record = FileChangeRecord(
            id="session:tool:0",
            path="/tmp/test",
            change_type="create",
            timestamp="2026-08-14T12:00:00Z",
            is_directory=True,
            session_id="session",
            tool_use_id="tool",
            source="bash_mkdir",
        )

        d = record.to_dict()

        assert d["id"] == "session:tool:0"
        assert d["path"] == "/tmp/test"
        assert d["change_type"] == "create"
        assert d["is_directory"] is True


class TestAppendFileChangeBlocks:
    """Tests for append_file_change_blocks."""

    def setup_method(self):
        """Clear and setup registry before each test."""
        FileChangeParserRegistry.clear()
        FileChangeParserRegistry.register(MkdirParser())

    def test_appends_file_change_block(self):
        """Test that file_change blocks are appended."""
        with tempfile.TemporaryDirectory() as tmpdir:
            context = ParserContext(
                session_id="test",
                tool_use_id="tool-1",
                project_path=tmpdir,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

            blocks = [
                {"type": "tool_use", "name": "Bash", "input": {"command": "mkdir test-folder"}}
            ]

            append_file_change_blocks(blocks, context)

            assert len(blocks) == 2
            assert blocks[1]["type"] == "file_change"
            assert blocks[1]["status"] == "pending"
            assert len(blocks[1]["changes"]) >= 1

    def test_no_append_for_non_file_operations(self):
        """Test that no blocks are appended for non-file operations."""
        context = ParserContext(
            session_id="test",
            tool_use_id="tool-1",
            project_path="/tmp",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        blocks = [{"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}}]

        append_file_change_blocks(blocks, context)

        assert len(blocks) == 1


class TestExtractFileChanges:
    """Tests for extract_file_changes."""

    def setup_method(self):
        """Clear and setup registry before each test."""
        FileChangeParserRegistry.clear()
        FileChangeParserRegistry.register(MkdirParser())

    def test_extracts_changes(self):
        """Test extracting file changes from tool_use block."""
        with tempfile.TemporaryDirectory() as tmpdir:
            context = ParserContext(
                session_id="test",
                tool_use_id="tool-1",
                project_path=tmpdir,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

            tool_use = {
                "type": "tool_use",
                "name": "Bash",
                "input": {"command": "mkdir test-folder"},
            }

            changes = extract_file_changes(tool_use, context)

            assert changes is not None
            assert len(changes) >= 1
            assert changes[0]["change_type"] == "create"

    def test_returns_none_for_non_file_operations(self):
        """Test that None is returned for non-file operations."""
        context = ParserContext(
            session_id="test",
            tool_use_id="tool-1",
            project_path="/tmp",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        tool_use = {
            "name": "Bash",
            "input": {"command": "ls -la"},
        }

        changes = extract_file_changes(tool_use, context)

        assert changes is None

    def test_returns_none_for_empty_input(self):
        """Test that None is returned for empty input."""
        context = ParserContext(
            session_id="test",
            tool_use_id="tool-1",
            project_path="/tmp",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        changes = extract_file_changes(None, context)
        assert changes is None

        changes = extract_file_changes({}, context)
        assert changes is None
