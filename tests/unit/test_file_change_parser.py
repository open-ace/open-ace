"""
Unit tests for file change parser.

Issue #2589: File change panel configuration for detecting file/folder operations.
Issue #2725: Add mv and cp command parser support.
"""

import os
import tempfile
from datetime import datetime, timezone

import pytest

from shared.file_change_parser import (
    FileChangeParserRegistry,
    FileChangeRecord,
    MkdirParser,
    MvParser,
    CpParser,
    ParserContext,
    append_file_change_blocks,
    extract_file_changes,
    _contains_wildcard,
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


class TestContainsWildcard:
    """Tests for _contains_wildcard helper function."""

    def test_no_wildcard(self):
        """Test paths without wildcards return False."""
        assert _contains_wildcard("path/to/file.txt") is False
        assert _contains_wildcard("/absolute/path") is False
        assert _contains_wildcard("simple") is False

    def test_asterisk_wildcard(self):
        """Test asterisk wildcard is detected."""
        assert _contains_wildcard("*.txt") is True
        assert _contains_wildcard("path/*.txt") is True
        assert _contains_wildcard("file*.log") is True

    def test_question_mark_wildcard(self):
        """Test question mark wildcard is detected."""
        assert _contains_wildcard("file?.txt") is True
        assert _contains_wildcard("path/file?") is True

    def test_bracket_wildcard(self):
        """Test bracket wildcard is detected."""
        assert _contains_wildcard("file[0-9].txt") is True
        assert _contains_wildcard("[abc].txt") is True


class TestMvParser:
    """Tests for MvParser - Issue #2725."""

    def setup_method(self):
        """Clear registry before each test."""
        FileChangeParserRegistry.clear()
        FileChangeParserRegistry.register(MvParser())

    def test_can_parse_bash_mv(self):
        """Test that parser recognizes mv in Bash commands."""
        parser = MvParser()
        assert parser.can_parse("Bash", {"command": "mv old.txt new.txt"}) is True
        assert parser.can_parse("Bash", {"command": "mv -f old.txt new.txt"}) is True
        assert parser.can_parse("Bash", {"command": "ls -la"}) is False
        assert parser.can_parse("Write", {"file_path": "/tmp/test.txt"}) is False

    def test_extract_paths_simple_rename(self):
        """Test extracting paths from simple mv command."""
        parser = MvParser()
        sources, dest = parser._extract_paths("mv old.txt new.txt")
        assert sources == ["old.txt"]
        assert dest == "new.txt"

    def test_extract_paths_with_options(self):
        """Test extracting paths from mv with options."""
        parser = MvParser()
        sources, dest = parser._extract_paths("mv -f old.txt new.txt")
        assert sources == ["old.txt"]
        assert dest == "new.txt"

    def test_extract_paths_multi_source(self):
        """Test extracting paths from multi-source mv command."""
        parser = MvParser()
        sources, dest = parser._extract_paths("mv a.txt b.txt dest/")
        assert sources == ["a.txt", "b.txt"]
        assert dest == "dest/"

    def test_extract_paths_quoted(self):
        """Test extracting paths with quotes."""
        parser = MvParser()
        sources, dest = parser._extract_paths('mv "path with spaces" dest')
        assert sources == ["path with spaces"]
        assert dest == "dest"

    def test_parse_simple_rename(self):
        """Test that parse creates FileChangeRecord for rename."""
        parser = MvParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            context = ParserContext(
                session_id="test-session",
                tool_use_id="tool-123",
                project_path=tmpdir,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            records = parser.parse("Bash", {"command": "mv old.txt new.txt"}, context)

            assert len(records) == 1
            assert records[0].change_type == "rename"
            assert records[0].old_path is not None
            assert "old.txt" in records[0].old_path
            assert "new.txt" in records[0].path
            assert records[0].source == "bash_mv"

    def test_parse_multi_source_moves(self):
        """Test multi-source mv generates correct target paths."""
        parser = MvParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            context = ParserContext(
                session_id="test-session",
                tool_use_id="tool-123",
                project_path=tmpdir,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            records = parser.parse("Bash", {"command": "mv a.txt b.txt dest/"}, context)

            assert len(records) == 2
            # First file
            assert records[0].old_path is not None
            assert "a.txt" in records[0].old_path
            assert "dest" in records[0].path and "a.txt" in records[0].path
            # Second file
            assert records[1].old_path is not None
            assert "b.txt" in records[1].old_path
            assert "dest" in records[1].path and "b.txt" in records[1].path

    def test_parse_wildcard_rejected(self):
        """Test that wildcard paths are rejected."""
        parser = MvParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            context = ParserContext(
                session_id="test-session",
                tool_use_id="tool-123",
                project_path=tmpdir,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            # Wildcard in source
            records = parser.parse("Bash", {"command": "mv *.txt dest/"}, context)
            assert len(records) == 0

    def test_parse_source_escape_rejected(self):
        """Test that source path escaping project is rejected."""
        parser = MvParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            context = ParserContext(
                session_id="test-session",
                tool_use_id="tool-123",
                project_path=tmpdir,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            # Source path escapes project
            records = parser.parse("Bash", {"command": "mv /etc/passwd ./safe"}, context)
            assert len(records) == 0

    def test_parse_dest_escape_rejected(self):
        """Test that destination path escaping project is rejected."""
        parser = MvParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            context = ParserContext(
                session_id="test-session",
                tool_use_id="tool-123",
                project_path=tmpdir,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            # Destination path escapes project
            records = parser.parse("Bash", {"command": "mv ./safe /tmp/out"}, context)
            assert len(records) == 0

    def test_is_safe_path_valid(self):
        """Test that paths within project are safe."""
        parser = MvParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            assert parser._is_safe_path(os.path.join(tmpdir, "foo"), tmpdir) is True

    def test_is_safe_path_escape(self):
        """Test that paths escaping project are unsafe."""
        parser = MvParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            assert parser._is_safe_path("/etc/passwd", tmpdir) is False


class TestCpParser:
    """Tests for CpParser - Issue #2725."""

    def setup_method(self):
        """Clear registry before each test."""
        FileChangeParserRegistry.clear()
        FileChangeParserRegistry.register(CpParser())

    def test_can_parse_bash_cp(self):
        """Test that parser recognizes cp in Bash commands."""
        parser = CpParser()
        assert parser.can_parse("Bash", {"command": "cp src.txt dest.txt"}) is True
        assert parser.can_parse("Bash", {"command": "cp -r src_dir dest_dir"}) is True
        assert parser.can_parse("Bash", {"command": "ls -la"}) is False
        assert parser.can_parse("Write", {"file_path": "/tmp/test.txt"}) is False

    def test_extract_paths_simple_copy(self):
        """Test extracting paths from simple cp command."""
        parser = CpParser()
        sources, dest = parser._extract_paths("cp src.txt dest.txt")
        assert sources == ["src.txt"]
        assert dest == "dest.txt"

    def test_extract_paths_with_options(self):
        """Test extracting paths from cp with options."""
        parser = CpParser()
        sources, dest = parser._extract_paths("cp -r src_dir dest_dir")
        assert sources == ["src_dir"]
        assert dest == "dest_dir"

    def test_extract_paths_multi_source(self):
        """Test extracting paths from multi-source cp command."""
        parser = CpParser()
        sources, dest = parser._extract_paths("cp a.txt b.txt dest/")
        assert sources == ["a.txt", "b.txt"]
        assert dest == "dest/"

    def test_is_recursive_flag(self):
        """Test detection of recursive flags."""
        parser = CpParser()
        assert parser._is_recursive_flag("cp -r src dest") is True
        assert parser._is_recursive_flag("cp -R src dest") is True
        assert parser._is_recursive_flag("cp -a src dest") is True
        assert parser._is_recursive_flag("cp --recursive src dest") is True
        assert parser._is_recursive_flag("cp -rf src dest") is True
        assert parser._is_recursive_flag("cp src dest") is False

    def test_parse_simple_copy(self):
        """Test that parse creates FileChangeRecord for copy."""
        parser = CpParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            context = ParserContext(
                session_id="test-session",
                tool_use_id="tool-123",
                project_path=tmpdir,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            records = parser.parse("Bash", {"command": "cp src.txt dest.txt"}, context)

            assert len(records) == 1
            assert records[0].change_type == "copy"
            assert records[0].old_path is not None
            assert "src.txt" in records[0].old_path
            assert "dest.txt" in records[0].path
            assert records[0].is_directory is False
            assert records[0].source == "bash_cp"

    def test_parse_recursive_copy(self):
        """Test that recursive copy sets is_directory=True."""
        parser = CpParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            context = ParserContext(
                session_id="test-session",
                tool_use_id="tool-123",
                project_path=tmpdir,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            records = parser.parse("Bash", {"command": "cp -r src_dir dest_dir"}, context)

            assert len(records) == 1
            assert records[0].is_directory is True

    def test_parse_archive_copy(self):
        """Test that archive copy (-a) sets is_directory=True."""
        parser = CpParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            context = ParserContext(
                session_id="test-session",
                tool_use_id="tool-123",
                project_path=tmpdir,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            records = parser.parse("Bash", {"command": "cp -a src dest"}, context)

            assert len(records) == 1
            assert records[0].is_directory is True

    def test_parse_multi_source_copies(self):
        """Test multi-source cp generates correct target paths."""
        parser = CpParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            context = ParserContext(
                session_id="test-session",
                tool_use_id="tool-123",
                project_path=tmpdir,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            records = parser.parse("Bash", {"command": "cp a.txt b.txt dest/"}, context)

            assert len(records) == 2
            # First file
            assert "a.txt" in records[0].old_path
            assert "dest" in records[0].path and "a.txt" in records[0].path
            # Second file
            assert "b.txt" in records[1].old_path
            assert "dest" in records[1].path and "b.txt" in records[1].path

    def test_parse_wildcard_rejected(self):
        """Test that wildcard paths are rejected."""
        parser = CpParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            context = ParserContext(
                session_id="test-session",
                tool_use_id="tool-123",
                project_path=tmpdir,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            records = parser.parse("Bash", {"command": "cp *.txt dest/"}, context)
            assert len(records) == 0

    def test_parse_source_escape_rejected(self):
        """Test that source path escaping project is rejected."""
        parser = CpParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            context = ParserContext(
                session_id="test-session",
                tool_use_id="tool-123",
                project_path=tmpdir,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            records = parser.parse("Bash", {"command": "cp /etc/passwd ./safe"}, context)
            assert len(records) == 0

    def test_is_safe_path_valid(self):
        """Test that paths within project are safe."""
        parser = CpParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            assert parser._is_safe_path(os.path.join(tmpdir, "foo"), tmpdir) is True

    def test_is_safe_path_escape(self):
        """Test that paths escaping project are unsafe."""
        parser = CpParser()
        with tempfile.TemporaryDirectory() as tmpdir:
            assert parser._is_safe_path("/etc/passwd", tmpdir) is False


class TestMvCpParserIntegration:
    """Integration tests for MvParser and CpParser with Registry."""

    def setup_method(self):
        """Clear registry before each test."""
        FileChangeParserRegistry.clear()

    def test_registry_calls_mv_parser(self):
        """Test that Registry correctly invokes MvParser."""
        FileChangeParserRegistry.register(MvParser())

        with tempfile.TemporaryDirectory() as tmpdir:
            context = ParserContext(
                session_id="test",
                tool_use_id="tool-1",
                project_path=tmpdir,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

            records = FileChangeParserRegistry.parse(
                "Bash", {"command": "mv old.txt new.txt"}, context
            )

            assert len(records) == 1
            assert records[0].change_type == "rename"

    def test_registry_calls_cp_parser(self):
        """Test that Registry correctly invokes CpParser."""
        FileChangeParserRegistry.register(CpParser())

        with tempfile.TemporaryDirectory() as tmpdir:
            context = ParserContext(
                session_id="test",
                tool_use_id="tool-1",
                project_path=tmpdir,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

            records = FileChangeParserRegistry.parse(
                "Bash", {"command": "cp src.txt dest.txt"}, context
            )

            assert len(records) == 1
            assert records[0].change_type == "copy"

    def test_append_file_change_blocks_with_mv(self):
        """Test append_file_change_blocks works with mv command."""
        FileChangeParserRegistry.register(MvParser())

        with tempfile.TemporaryDirectory() as tmpdir:
            context = ParserContext(
                session_id="test",
                tool_use_id="tool-1",
                project_path=tmpdir,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

            blocks = [
                {"type": "tool_use", "name": "Bash", "input": {"command": "mv old.txt new.txt"}}
            ]

            append_file_change_blocks(blocks, context)

            assert len(blocks) == 2
            assert blocks[1]["type"] == "file_change"
            assert blocks[1]["status"] == "pending"
            assert len(blocks[1]["changes"]) >= 1
            assert blocks[1]["changes"][0]["change_type"] == "rename"

    def test_append_file_change_blocks_with_cp(self):
        """Test append_file_change_blocks works with cp command."""
        FileChangeParserRegistry.register(CpParser())

        with tempfile.TemporaryDirectory() as tmpdir:
            context = ParserContext(
                session_id="test",
                tool_use_id="tool-1",
                project_path=tmpdir,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

            blocks = [
                {"type": "tool_use", "name": "Bash", "input": {"command": "cp src.txt dest.txt"}}
            ]

            append_file_change_blocks(blocks, context)

            assert len(blocks) == 2
            assert blocks[1]["type"] == "file_change"
            assert blocks[1]["changes"][0]["change_type"] == "copy"

    def test_extract_file_changes_with_mv(self):
        """Test extract_file_changes works with mv command."""
        FileChangeParserRegistry.register(MvParser())

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
                "input": {"command": "mv old.txt new.txt"},
            }

            changes = extract_file_changes(tool_use, context)

            assert changes is not None
            assert len(changes) >= 1
            assert changes[0]["change_type"] == "rename"
            assert changes[0]["old_path"] is not None

    def test_extract_file_changes_with_cp(self):
        """Test extract_file_changes works with cp command."""
        FileChangeParserRegistry.register(CpParser())

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
                "input": {"command": "cp src.txt dest.txt"},
            }

            changes = extract_file_changes(tool_use, context)

            assert changes is not None
            assert len(changes) >= 1
            assert changes[0]["change_type"] == "copy"
            assert changes[0]["old_path"] is not None
