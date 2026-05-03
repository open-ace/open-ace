"""Unit tests for scripts/lint/sql_compat_checker.py."""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts" / "lint"
sys.path.insert(0, str(SCRIPTS_DIR))

from sql_compat_checker import (
    _ESCAPE_LIKE_WINDOW,
    PROJECT_ROOT,
    _check_sql001,
    _check_sql003,
    _extract_base_name,
    _is_boolean_field,
    _is_var_used_in_sql,
    _load_baseline,
    _relative_path,
    _violation_signature,
    check_file,
)

# ---------------------------------------------------------------------------
# _is_boolean_field tests
# ---------------------------------------------------------------------------


class TestIsBooleanField:
    def test_is_prefix(self):
        assert _is_boolean_field("is_active") is True
        assert _is_boolean_field("is_admin") is True
        assert _is_boolean_field("is_shared") is True

    def test_enabled_suffix(self):
        assert _is_boolean_field("email_enabled") is True
        assert _is_boolean_field("push_enabled") is True

    def test_standalone_enabled(self):  # #24
        assert _is_boolean_field("enabled") is True

    def test_special_words(self):
        assert _is_boolean_field("read") is True
        assert _is_boolean_field("success") is True
        assert _is_boolean_field("acknowledged") is True

    def test_counter_fields_not_boolean(self):
        assert _is_boolean_field("message_count") is False
        assert _is_boolean_field("tokens_used") is False
        assert _is_boolean_field("total_requests") is False
        assert _is_boolean_field("active_users") is False

    def test_regular_fields(self):
        assert _is_boolean_field("name") is False
        assert _is_boolean_field("email") is False
        assert _is_boolean_field("created_at") is False


# ---------------------------------------------------------------------------
# _extract_base_name tests (#26, #31)
# ---------------------------------------------------------------------------


class TestExtractBaseName:
    def test_int_suffix(self):
        assert _extract_base_name("is_active_int") == "is_active"

    def test_val_suffix(self):
        assert _extract_base_name("must_change_val") == "must_change"

    def test_no_cascading_replace_is_valid(self):  # #26
        assert _extract_base_name("is_valid_int") == "is_valid"

    def test_no_cascading_replace_is_value(self):  # #26
        assert _extract_base_name("is_value_int") == "is_value"

    def test_no_cascading_replace_is_val(self):  # #26
        assert _extract_base_name("is_val_int") == "is_val"

    def test_is_int_edge_case(self):  # #31
        assert _extract_base_name("is_int") == "is"
        assert _is_boolean_field("is") is False  # should not cause false positive

    def test_no_suffix(self):
        assert _extract_base_name("is_active") == "is_active"


# ---------------------------------------------------------------------------
# _is_var_used_in_sql tests (#20/#27/#30)
# ---------------------------------------------------------------------------


class TestIsVarUsedInSql:
    def test_var_in_sql_below(self):
        lines = [
            "is_active_int = 1 if is_active else 0",
            'query = "WHERE is_active = ?"',
            "cursor.execute(query, (is_active_int,))",
        ]
        assert _is_var_used_in_sql("is_active_int", lines, 1) is True

    def test_var_not_in_sql(self):
        lines = [
            "is_ready_int = 1 if is_ready else 0",
            "print(is_ready_int)",
        ]
        assert _is_var_used_in_sql("is_ready_int", lines, 1) is False

    def test_var_reassigned_before_sql(self):  # #30 boundary 1
        lines = [
            "is_active_int = 1 if is_active else 0",
            "is_active_int = adapt_boolean_value(is_active)",
            'query = "WHERE is_active = ?"',
            "cursor.execute(query, (is_active_int,))",
        ]
        assert _is_var_used_in_sql("is_active_int", lines, 1) is False

    def test_word_boundary_not_substring(self):  # #30 boundary 2
        lines = [
            "is_active_int = 1 if is_active else 0",
            'query = f"WHERE is_active_internal = 1"',
        ]
        assert _is_var_used_in_sql("is_active_int", lines, 1) is False


# ---------------------------------------------------------------------------
# _relative_path tests (#19)
# ---------------------------------------------------------------------------


class TestRelativePath:
    def test_absolute_to_relative(self):
        result = _relative_path(PROJECT_ROOT / "app" / "repositories" / "user_repo.py")
        assert result == "app/repositories/user_repo.py"

    def test_already_relative(self):
        result = _relative_path("some/path.py")
        assert "some/path.py" in result

    def test_project_root_itself(self):
        result = _relative_path(PROJECT_ROOT)
        assert result == "."


# ---------------------------------------------------------------------------
# SQL001 tests
# ---------------------------------------------------------------------------


class TestSQL001:
    """Tests for boolean = integer detection."""

    def test_detect_simple_bool_equals_int(self):
        code = textwrap.dedent("""\
            query = "SELECT * FROM users WHERE is_active = 1"
        """)
        violations = _check_sql001(code, "test.py")
        assert len(violations) == 1
        assert violations[0]["rule"] == "SQL001"
        assert "is_active" in violations[0]["message"]

    def test_detect_with_table_alias(self):
        code = textwrap.dedent("""\
            query = "SELECT * FROM users u WHERE u.is_active = 1"
        """)
        violations = _check_sql001(code, "test.py")
        assert len(violations) == 1
        assert violations[0]["rule"] == "SQL001"

    def test_detect_int_variable_pattern(self):
        code = textwrap.dedent("""\
            is_active_int = 1 if is_active else 0
            query = "INSERT INTO users (is_active) VALUES (?)"
            cursor.execute(query, (is_active_int,))
        """)
        violations = _check_sql001(code, "test.py")
        assert len(violations) == 1
        assert violations[0]["rule"] == "SQL001"
        assert "is_active_int" in violations[0]["message"]

    def test_detect_int_variable_with_method_call(self):  # #12, #21
        """_INT_VAR_RE with .+? matches method calls in condition expression."""
        # Note: the variable name must end in _int/_val for base extraction to work.
        # audit_log_val -> audit_log (not a boolean field name) would NOT be detected.
        # Use a name where base extraction yields a boolean field name.
        code = textwrap.dedent("""\
            is_enabled_int = 1 if settings_dict.get("is_enabled", True) else 0
            cursor.execute("INSERT INTO settings (is_enabled) VALUES (?)", (is_enabled_int,))
        """)
        violations = _check_sql001(code, "test.py")
        assert len(violations) == 1
        assert violations[0]["rule"] == "SQL001"
        assert "is_enabled_int" in violations[0]["message"]

    def test_int_variable_not_used_in_sql_no_violation(self):  # #20
        code = textwrap.dedent("""\
            is_ready_int = 1 if is_ready else 0
            print(is_ready_int)
        """)
        violations = _check_sql001(code, "test.py")
        assert len(violations) == 0

    def test_no_cascading_replace_bug(self):  # #26, #29
        code = textwrap.dedent("""\
            is_valid_int = 1 if is_valid else 0
            cursor.execute("INSERT INTO t (is_valid) VALUES (?)", (is_valid_int,))
        """)
        violations = _check_sql001(code, "test.py")
        assert len(violations) == 1
        assert "is_valid" in violations[0]["message"]

    def test_no_violation_with_adapt_boolean(self):
        code = textwrap.dedent("""\
            query = f"SELECT * FROM users WHERE {adapt_boolean_condition('is_active', True)}"
        """)
        violations = _check_sql001(code, "test.py")
        assert len(violations) == 0

    def test_no_violation_python_ternary_non_boolean(self):
        """Python ternary with non-boolean variable name is not flagged."""
        code = textwrap.dedent("""\
            count = 1 if flag else 0
        """)
        violations = _check_sql001(code, "test.py")
        assert len(violations) == 0

    def test_no_violation_comment_line(self):
        code = textwrap.dedent("""\
            # is_active = 1 is used in SQLite
        """)
        violations = _check_sql001(code, "test.py")
        assert len(violations) == 0

    def test_detect_multiple_in_same_file(self):
        code = textwrap.dedent("""\
            query1 = "SELECT * FROM users WHERE is_active = 1"
            query2 = "SELECT * FROM projects WHERE is_shared = 0"
        """)
        violations = _check_sql001(code, "test.py")
        assert len(violations) == 2

    def test_detect_enabled_field(self):
        code = textwrap.dedent("""\
            query = "SELECT * FROM config WHERE email_enabled = 1"
        """)
        violations = _check_sql001(code, "test.py")
        assert len(violations) == 1

    def test_line_number_correct(self):
        code = textwrap.dedent("""\
            # line 1
            # line 2
            query = "WHERE is_active = 1"
        """)
        violations = _check_sql001(code, "test.py")
        assert violations[0]["line"] == 3


# ---------------------------------------------------------------------------
# SQL003 tests
# ---------------------------------------------------------------------------


class TestSQL003:
    """Tests for LIKE without escape_like detection."""

    def test_detect_like_question_mark(self):
        code = textwrap.dedent("""\
            conditions.append("name LIKE ?")
        """)
        violations = _check_sql003(code, "test.py")
        assert len(violations) == 1
        assert violations[0]["rule"] == "SQL003"

    def test_detect_like_percent_s(self):
        code = textwrap.dedent("""\
            query = "SELECT * FROM users WHERE name LIKE %s"
        """)
        violations = _check_sql003(code, "test.py")
        assert len(violations) == 1

    def test_detect_like_param_function(self):
        code = textwrap.dedent("""\
            conditions.append(f"title LIKE {_param()}")
        """)
        violations = _check_sql003(code, "test.py")
        assert len(violations) == 1

    def test_no_violation_with_escape_like(self):
        code = textwrap.dedent("""\
            value = escape_like(user_input)
            conditions.append("name LIKE ?")
            params.append(value)
        """)
        violations = _check_sql003(code, "test.py")
        assert len(violations) == 0

    def test_no_violation_comment_line(self):
        code = textwrap.dedent("""\
            # Use LIKE ? for pattern matching
        """)
        violations = _check_sql003(code, "test.py")
        assert len(violations) == 0

    def test_line_number_correct(self):
        code = textwrap.dedent("""\
            # line 1
            conditions.append("name LIKE ?")
        """)
        violations = _check_sql003(code, "test.py")
        assert violations[0]["line"] == 2

    def test_escape_like_window_boundary(self):  # #22
        """escape_like within ±5 lines should suppress the violation."""
        # escape_like on line 1, LIKE on line 6: distance = 5 lines (within ±5 window)
        padding_lines = ["x = 1"] * 4
        code = (
            "safe = escape_like(q)\n"
            + "\n".join(padding_lines)
            + '\nconditions.append("name LIKE ?")\n'
        )
        violations = _check_sql003(code, "test.py")
        assert len(violations) == 0

    def test_escape_like_beyond_window(self):  # #22
        """escape_like beyond ±5 lines should NOT suppress the violation."""
        # escape_like on line 1, LIKE on line 7: distance = 6 lines (beyond ±5 window)
        padding_lines = ["x = 1"] * 5
        code = (
            "safe = escape_like(q)\n"
            + "\n".join(padding_lines)
            + '\nconditions.append("name LIKE ?")\n'
        )
        violations = _check_sql003(code, "test.py")
        assert len(violations) == 1


# ---------------------------------------------------------------------------
# Baseline tests
# ---------------------------------------------------------------------------


class TestBaseline:
    def test_violation_signature_uses_relative_path(self):  # #19
        abs_path = str(PROJECT_ROOT / "app" / "repos" / "test.py")
        v = {"rule": "SQL001", "file": abs_path, "line": 42}
        sig = _violation_signature(v)
        assert sig == "SQL001:app/repos/test.py:42"

    def test_signature_portable_across_machines(self):  # #25
        """Signatures generated from different absolute paths resolve to same relative path."""
        v1 = {"rule": "SQL001", "file": str(PROJECT_ROOT / "app" / "foo.py"), "line": 10}
        v2 = {"rule": "SQL001", "file": "app/foo.py", "line": 10}
        # _violation_signature normalizes to relative path
        assert _violation_signature(v1) == _violation_signature(v2)

    def test_load_baseline_nonexistent(self, tmp_path):
        with patch("sql_compat_checker.BASELINE_PATH", tmp_path / "nonexistent"):
            assert _load_baseline() == set()

    def test_load_baseline_jsonl(self, tmp_path):
        baseline_file = tmp_path / ".sql_baseline"
        baseline_file.write_text(
            '{"rule": "SQL001", "file": "app/test.py", "line": 1}\n'
            '{"rule": "SQL003", "file": "app/test.py", "line": 5}\n'
        )
        with patch("sql_compat_checker.BASELINE_PATH", baseline_file):
            sigs = _load_baseline()
        assert "SQL001:app/test.py:1" in sigs
        assert "SQL003:app/test.py:5" in sigs

    def test_baseline_uses_relative_paths(self, tmp_path):  # #25
        """Generated baseline entries should use relative paths."""
        baseline_file = tmp_path / ".sql_baseline"
        abs_path = str(PROJECT_ROOT / "app" / "test.py")
        baseline_file.write_text(
            json.dumps({"rule": "SQL001", "file": "app/test.py", "line": 1}) + "\n"
        )
        with patch("sql_compat_checker.BASELINE_PATH", baseline_file):
            sigs = _load_baseline()
        v = {"rule": "SQL001", "file": abs_path, "line": 1}
        assert _violation_signature(v) in sigs


# ---------------------------------------------------------------------------
# Integration: check_file
# ---------------------------------------------------------------------------


class TestCheckFile:
    def test_mixed_violations(self, tmp_path):
        f = tmp_path / "example.py"
        f.write_text(textwrap.dedent("""\
            query = "SELECT * FROM users WHERE is_active = 1"
            conditions.append("name LIKE ?")
        """))
        violations = check_file(f)
        rules = {v["rule"] for v in violations}
        assert "SQL001" in rules
        assert "SQL003" in rules

    def test_clean_file(self, tmp_path):
        f = tmp_path / "clean.py"
        f.write_text(textwrap.dedent("""\
            query = f"SELECT * FROM users WHERE {adapt_boolean_condition('is_active', True)}"
            value = escape_like(name)
            conditions.append("name LIKE ?")
            params.append(value)
        """))
        violations = check_file(f)
        assert len(violations) == 0


# ---------------------------------------------------------------------------
# escape_like function test (in app/repositories/database.py)
# ---------------------------------------------------------------------------


class TestEscapeLike:
    def test_escape_percent(self):
        from app.repositories.database import escape_like

        assert escape_like("100%") == "100\\%"

    def test_escape_underscore(self):
        from app.repositories.database import escape_like

        assert escape_like("user_name") == "user\\_name"

    def test_escape_backslash(self):
        from app.repositories.database import escape_like

        assert escape_like("path\\file") == "path\\\\file"

    def test_no_special_chars(self):
        from app.repositories.database import escape_like

        assert escape_like("normal") == "normal"

    def test_mixed_special_chars(self):
        from app.repositories.database import escape_like

        result = escape_like("100%_test\\end")
        assert result == "100\\%\\_test\\\\end"

    def test_custom_escape_char(self):
        from app.repositories.database import escape_like

        result = escape_like("test%val", escape_char="!")
        assert result == "test!%val"
