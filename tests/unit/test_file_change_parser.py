"""
Unit tests for file change parser.

Issue #2589: File change panel configuration for detecting file/folder operations.
"""


from scripts.shared.file_change_parser import (
    extract_file_changes,
    MkdirParser,
    WriteFileParser,
    EditParser,
    FileChangeRecord,
)


class TestMkdirParser:
    """Tests for MkdirParser."""

    def test_can_parse_bash_mkdir(self):
        """Test can_parse returns True for Bash tool with mkdir command."""
        parser = MkdirParser()
        assert parser.can_parse("Bash", {"command": "mkdir foo"}) is True
        assert parser.can_parse("Bash", {"command": "mkdir -p foo/bar"}) is True

    def test_cannot_parse_other_tools(self):
        """Test can_parse returns False for non-Bash tools."""
        parser = MkdirParser()
        assert parser.can_parse("WriteFile", {"file_path": "foo.txt"}) is False
        assert parser.can_parse("Bash", {"command": "ls -la"}) is False

    def test_parse_simple_mkdir(self):
        """Test parsing simple mkdir command."""
        parser = MkdirParser()
        records = parser.parse("Bash", {"command": "mkdir foo"}, "/project")
        assert len(records) == 1
        assert records[0].path.endswith("foo")
        assert records[0].change_type == "add"

    def test_parse_mkdir_with_flag(self):
        """Test parsing mkdir with -p flag."""
        parser = MkdirParser()
        records = parser.parse("Bash", {"command": "mkdir -p foo/bar"}, "/project")
        assert len(records) == 1
        assert "foo/bar" in records[0].path

    def test_parse_mkdir_with_quotes(self):
        """Test parsing mkdir with quoted path."""
        parser = MkdirParser()
        records = parser.parse("Bash", {"command": "mkdir 'path with spaces'"}, "/project")
        assert len(records) == 1
        assert "path with spaces" in records[0].path

    def test_path_traversal_blocked(self):
        """Test path traversal is blocked."""
        parser = MkdirParser()
        records = parser.parse("Bash", {"command": "mkdir ../../../etc/passwd"}, "/project")
        # Path should be rejected due to safety check
        assert len(records) == 0


class TestWriteFileParser:
    """Tests for WriteFileParser."""

    def test_can_parse_write_file(self):
        """Test can_parse returns True for write_file tool."""
        parser = WriteFileParser()
        assert parser.can_parse("write_file", {}) is True
        assert parser.can_parse("WriteFile", {}) is True

    def test_cannot_parse_other_tools(self):
        """Test can_parse returns False for other tools."""
        parser = WriteFileParser()
        assert parser.can_parse("Bash", {}) is False
        assert parser.can_parse("edit", {}) is False

    def test_parse_write_file(self):
        """Test parsing write_file tool."""
        parser = WriteFileParser()
        records = parser.parse(
            "write_file",
            {"file_path": "/project/foo.txt"},
            "/project"
        )
        assert len(records) == 1
        assert records[0].change_type == "add"

    def test_parse_relative_path(self):
        """Test parsing relative path."""
        parser = WriteFileParser()
        records = parser.parse(
            "write_file",
            {"file_path": "src/main.py"},
            "/project"
        )
        assert len(records) == 1
        assert records[0].path.endswith("src/main.py")


class TestEditParser:
    """Tests for EditParser."""

    def test_can_parse_edit(self):
        """Test can_parse returns True for edit tool."""
        parser = EditParser()
        assert parser.can_parse("edit", {}) is True

    def test_cannot_parse_other_tools(self):
        """Test can_parse returns False for other tools."""
        parser = EditParser()
        assert parser.can_parse("write_file", {}) is False

    def test_parse_edit(self):
        """Test parsing edit tool."""
        parser = EditParser()
        records = parser.parse(
            "edit",
            {"file_path": "/project/foo.txt"},
            "/project"
        )
        assert len(records) == 1
        assert records[0].change_type == "modify"


class TestExtractFileChanges:
    """Tests for extract_file_changes function."""

    def test_extract_from_mkdir(self):
        """Test extracting file changes from mkdir tool_use."""
        result = extract_file_changes(
            {"name": "Bash", "input": {"command": "mkdir foo"}},
            "/project"
        )
        assert result is not None
        assert len(result) == 1
        assert result[0]["change_type"] == "add"

    def test_extract_from_write_file(self):
        """Test extracting file changes from write_file tool_use."""
        result = extract_file_changes(
            {"name": "write_file", "input": {"file_path": "foo.txt"}},
            "/project"
        )
        assert result is not None
        assert result[0]["change_type"] == "add"

    def test_extract_from_edit(self):
        """Test extracting file changes from edit tool_use."""
        result = extract_file_changes(
            {"name": "edit", "input": {"file_path": "foo.txt"}},
            "/project"
        )
        assert result is not None
        assert result[0]["change_type"] == "modify"

    def test_no_changes_for_unsupported_tool(self):
        """Test returns None for unsupported tools."""
        result = extract_file_changes(
            {"name": "Bash", "input": {"command": "ls -la"}},
            "/project"
        )
        assert result is None

    def test_empty_input(self):
        """Test handles empty input gracefully."""
        result = extract_file_changes({}, "/project")
        assert result is None