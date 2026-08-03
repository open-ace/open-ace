"""Unit coverage for the Issue #8 file-change parser.

Covers ``scripts/shared/file_change_parser.py``: converting shell command
(mkdir/mv/cp/rm) and native file tool (write_file/edit) ``tool_use`` blocks
into frontend-friendly ``file_change`` ``changes`` entries so the Qwen Code
CLI file-change panel shows every AI-driven file/folder change.
"""

import importlib.util
from pathlib import Path

# Load the parser directly by file path so the ``shared`` package __init__
# chain (which pulls in requests/dingtalk/etc.) is not required.
_PARSER_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "shared" / "file_change_parser.py"
)
_spec = importlib.util.spec_from_file_location("file_change_parser", _PARSER_PATH)
_parser = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_parser)
extract_file_changes = _parser.extract_file_changes
append_file_change_blocks = _parser.append_file_change_blocks


def _tool_use(name: str, input_: dict | str) -> dict:
    return {"type": "tool_use", "name": name, "input": input_}


# ── Shell commands ───────────────────────────────────────────────────────


def test_mkdir_parses_as_add():
    assert extract_file_changes("run_shell_command", {"command": "mkdir -p src/app"}) == [
        {"path": "src/app", "change_type": "add"}
    ]


def test_touch_parses_as_add():
    assert extract_file_changes("shell_command", {"command": "touch README.md"}) == [
        {"path": "README.md", "change_type": "add"}
    ]


def test_mv_parses_as_move_with_old_path():
    assert extract_file_changes("bash", {"command": "mv old.txt new.txt"}) == [
        {"path": "new.txt", "change_type": "move", "old_path": "old.txt"}
    ]


def test_cp_parses_as_add_of_destination():
    assert extract_file_changes("sh", {"command": "cp a.txt b.txt"}) == [
        {"path": "b.txt", "change_type": "add"}
    ]


def test_rm_parses_as_delete():
    assert extract_file_changes("cmd", {"command": "rm -rf build"}) == [
        {"path": "build", "change_type": "delete"}
    ]


def test_rmdir_and_del_parse_as_delete():
    assert extract_file_changes("powershell", {"command": "rmdir old_dir"}) == [
        {"path": "old_dir", "change_type": "delete"}
    ]
    assert extract_file_changes("pwsh", {"command": "del temp.txt"}) == [
        {"path": "temp.txt", "change_type": "delete"}
    ]


def test_sudo_wrapper_is_stripped():
    assert extract_file_changes("bash", {"command": "sudo mkdir -p /opt/app"}) == [
        {"path": "/opt/app", "change_type": "add"}
    ]


def test_quoted_paths_with_spaces():
    assert extract_file_changes(
        "bash", {"command": "mkdir 'my project'"}
    ) == [{"path": "my project", "change_type": "add"}]


def test_compound_commands_split_on_separators():
    changes = extract_file_changes(
        "bash", {"command": "mkdir src && mv a.txt src/a.txt\ncp b.txt src/b.txt"}
    )
    assert changes == [
        {"path": "src", "change_type": "add"},
        {"path": "src/a.txt", "change_type": "move", "old_path": "a.txt"},
        {"path": "src/b.txt", "change_type": "add"},
    ]


def test_non_file_commands_ignored():
    assert extract_file_changes("bash", {"command": "ls -la"}) == []
    assert extract_file_changes("bash", {"command": "npm install"}) == []


def test_unsafe_constructs_skipped():
    # globs / pipes / redirection / variables / substitution are too
    # ambiguous to interpret safely -> skipped to avoid false positives.
    assert extract_file_changes("bash", {"command": "rm *.log"}) == []
    assert extract_file_changes("bash", {"command": "cat a | tee b"}) == []
    assert extract_file_changes("bash", {"command": "mkdir $DIR"}) == []
    assert extract_file_changes("bash", {"command": "mkdir -p > out.txt"}) == []


def test_windows_paths_survive_tokenization():
    changes = extract_file_changes(
        "powershell", {"command": r"mkdir C:\workspace\demo"}
    )
    assert changes == [{"path": r"C:\workspace\demo", "change_type": "add"}]


# ── Native file tools ────────────────────────────────────────────────────


def test_write_file_parses_as_modify():
    assert extract_file_changes(
        "write_file", {"file_path": "app/main.py"}
    ) == [{"path": "app/main.py", "change_type": "modify"}]


def test_create_file_accepts_path_key():
    assert extract_file_changes(
        "create_file", {"path": "app/__init__.py"}
    ) == [{"path": "app/__init__.py", "change_type": "modify"}]


def test_edit_parses_as_modify():
    assert extract_file_changes(
        "edit", {"file_path": "app/main.py"}
    ) == [{"path": "app/main.py", "change_type": "modify"}]


def test_native_tool_without_path_ignored():
    assert extract_file_changes("write_file", {"content": "no path"}) == []
    assert extract_file_changes("edit", {}) == []


# ── Other tool shapes ────────────────────────────────────────────────────


def test_non_file_tools_ignored():
    assert extract_file_changes("Read", {"file_path": "a.txt"}) == []
    assert extract_file_changes("Glob", {"pattern": "**/*.py"}) == []
    assert extract_file_changes("grep", {"pattern": "foo"}) == []


def test_invalid_tool_name_or_input_ignored():
    assert extract_file_changes(None, None) == []
    assert extract_file_changes("", {}) == []
    assert extract_file_changes("bash", None) == []
    assert extract_file_changes("bash", 42) == []


def test_string_tool_input_used_as_command():
    assert extract_file_changes("bash", "mkdir src") == [
        {"path": "src", "change_type": "add"}
    ]


def test_nested_args_dict_supported():
    assert extract_file_changes(
        "run_shell_command", {"args": {"command": "mkdir -p nested/dir"}}
    ) == [{"path": "nested/dir", "change_type": "add"}]


# ── append_file_change_blocks (shared helper) ────────────────────────────


def test_append_file_change_blocks_appends_derived_blocks_in_place():
    blocks = [
        {"type": "text", "text": "creating"},
        {"type": "tool_use", "name": "run_shell_command", "input": {"command": "mkdir src"}},
        {"type": "tool_use", "name": "write_file", "input": {"file_path": "src/main.py"}},
        {"type": "tool_use", "name": "Read", "input": {"file_path": "src/main.py"}},
    ]
    append_file_change_blocks(blocks)
    file_changes = [b for b in blocks if b.get("type") == "file_change"]
    assert file_changes == [
        {
            "type": "file_change",
            "changes": [{"path": "src", "change_type": "add"}],
            "status": "accepted",
            "success": True,
        },
        {
            "type": "file_change",
            "changes": [{"path": "src/main.py", "change_type": "modify"}],
            "status": "accepted",
            "success": True,
        },
    ]
    # Non-file tools (Read) produce no file_change; original blocks preserved.
    assert blocks[0] == {"type": "text", "text": "creating"}


def test_append_file_change_blocks_noop_without_tool_use():
    blocks = [{"type": "text", "text": "hi"}, {"type": "thinking", "text": "t"}]
    append_file_change_blocks(blocks)
    assert all(b.get("type") != "file_change" for b in blocks)
    assert len(blocks) == 2
