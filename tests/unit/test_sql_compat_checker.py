"""Unit tests for scripts/lint/sql_compat_checker.py."""

from __future__ import annotations

import json

# Import the checker module
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts" / "lint"
sys.path.insert(0, str(SCRIPTS_DIR))

from sql_compat_checker import (
    _DYNAMIC_BOOL_FIELDS,
    _check_sql001,
    _check_sql003,
    _is_boolean_field,
    _load_baseline,
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
        """)
        violations = _check_sql001(code, "test.py")
        assert len(violations) == 1
        assert violations[0]["rule"] == "SQL001"
        assert "is_active_int" in violations[0]["message"]

    def test_no_violation_with_adapt_boolean(self):
        code = textwrap.dedent("""\
            query = f"SELECT * FROM users WHERE {adapt_boolean_condition('is_active', True)}"
        """)
        violations = _check_sql001(code, "test.py")
        assert len(violations) == 0

    def test_no_violation_python_ternary(self):
        """Python ternary `x = 1 if cond else 0` outside SQL is NOT a SQL violation."""
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


# ---------------------------------------------------------------------------
# Baseline tests
# ---------------------------------------------------------------------------


class TestBaseline:
    def test_violation_signature_format(self):
        v = {"rule": "SQL001", "file": "/path/to/test.py", "line": 42}
        sig = _violation_signature(v)
        assert sig == "SQL001:/path/to/test.py:42"

    def test_load_baseline_nonexistent(self, tmp_path):
        with patch("sql_compat_checker.BASELINE_PATH", tmp_path / "nonexistent"):
            assert _load_baseline() == set()

    def test_load_baseline_jsonl(self, tmp_path):
        baseline_file = tmp_path / ".sql_baseline"
        baseline_file.write_text(
            '{"rule": "SQL001", "file": "test.py", "line": 1}\n'
            '{"rule": "SQL003", "file": "test.py", "line": 5}\n'
        )
        with patch("sql_compat_checker.BASELINE_PATH", baseline_file):
            sigs = _load_baseline()
        assert "SQL001:test.py:1" in sigs
        assert "SQL003:test.py:5" in sigs


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
