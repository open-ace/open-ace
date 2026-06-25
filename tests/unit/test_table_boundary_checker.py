"""Tests for the table-boundary checker (#1125 CI guard).

Verifies TBL001 (Work routes must not read daily_messages — text + call-graph)
and TBL002 (Manage routes must not write session_messages/agent_sessions),
plus baseline suppression. Uses synthetic file content via monkeypatching the
guard's file readers so the tests are hermetic.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Load the guard module directly (it's not a package). Register it in
# sys.modules so dataclass field-type resolution works on Python 3.9.
_SPEC = importlib.util.spec_from_file_location(
    "table_boundary_checker",
    Path(__file__).resolve().parent.parent.parent
    / "scripts"
    / "lint"
    / "table_boundary_checker.py",
)
assert _SPEC and _SPEC.loader
tbc = importlib.util.module_from_spec(_SPEC)
sys.modules["table_boundary_checker"] = tbc
_SPEC.loader.exec_module(tbc)


# ---------------------------------------------------------------------------
# TBL001 — text-level
# ---------------------------------------------------------------------------


class TestTbl001Text:
    def test_flags_daily_messages_in_comment(self):
        # TBL001 bans the token even in comments.
        lines = "# Work page must not read daily_messages analysis table\n"
        hits = [i for i, ln in enumerate(lines.splitlines(), 1) if "daily_messages" in ln]
        assert hits, "a daily_messages comment must be flagged at the text level"

    def test_clean_work_route_no_violation(self):
        content = (
            "# No mention of the forbidden table\nresult = usage_repo.get_session_only_usage()\n"
        )
        violations = [ln for ln in content.splitlines() if "daily_messages" in ln]
        assert violations == []


# ---------------------------------------------------------------------------
# TBL001 — call-graph
# ---------------------------------------------------------------------------


class TestTbl001Callgraph:
    def test_clean_method_not_flagged(self):
        """get_session_only_usage (no daily_messages in body) is not flagged."""
        import ast

        src = """
class UsageRepository:
    def get_session_only_usage(self, uid, s, e):
        return self.db.fetch_one("SELECT * FROM agent_sessions WHERE user_id=?", (uid,))
"""
        tree = ast.parse(src)
        cls_node = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef))
        method = next(
            n
            for n in cls_node.body
            if isinstance(n, ast.FunctionDef) and n.name == "get_session_only_usage"
        )
        assert tbc._method_reads_daily_messages(method) is False

    def test_reading_method_flagged(self):
        """get_combined_usage (reads daily_messages) IS flagged."""
        import ast

        src = """
class UsageRepository:
    def get_combined_usage(self, uid, s, e):
        return self.db.fetch_one("SELECT * FROM daily_messages WHERE user_id=?", (uid,))
"""
        tree = ast.parse(src)
        cls_node = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef))
        method = next(
            n
            for n in cls_node.body
            if isinstance(n, ast.FunctionDef) and n.name == "get_combined_usage"
        )
        assert tbc._method_reads_daily_messages(method) is True

    def test_docstring_mention_not_flagged(self):
        """A docstring saying 'avoids daily_messages' must NOT trip the check."""
        import ast

        src = '''
class UsageRepository:
    def get_session_only_usage(self, uid, s, e):
        """Work-page usage: no daily_messages (per #1125)."""
        return self.db.fetch_one("SELECT * FROM agent_sessions WHERE user_id=?", (uid,))
'''
        tree = ast.parse(src)
        cls_node = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef))
        method = next(
            n
            for n in cls_node.body
            if isinstance(n, ast.FunctionDef) and n.name == "get_session_only_usage"
        )
        assert tbc._method_reads_daily_messages(method) is False


# ---------------------------------------------------------------------------
# TBL002 — Manage routes must not write runtime tables
# ---------------------------------------------------------------------------


class TestTbl002:
    def test_insert_session_messages_flagged(self):
        violations = tbc.check_tbl002_content(
            "    db.execute('INSERT INTO session_messages (id) VALUES (?)')\n",
            "app/routes/admin.py",
        )
        assert len(violations) == 1
        assert violations[0].rule == "TBL002"
        assert violations[0].symbol == "session_messages"

    def test_update_agent_sessions_flagged(self):
        violations = tbc.check_tbl002_content(
            "    db.execute('UPDATE agent_sessions SET status = ?')\n",
            "app/routes/governance.py",
        )
        assert len(violations) == 1
        assert violations[0].symbol == "agent_sessions"

    def test_delete_flagged(self):
        violations = tbc.check_tbl002_content(
            "    db.execute('DELETE FROM session_messages WHERE id = ?')\n",
            "app/routes/admin.py",
        )
        assert len(violations) == 1

    def test_comment_not_flagged(self):
        violations = tbc.check_tbl002_content(
            "# INSERT INTO session_messages is forbidden here\n",
            "app/routes/admin.py",
        )
        assert violations == []

    def test_read_not_flagged(self):
        violations = tbc.check_tbl002_content(
            "    rows = db.fetch_all('SELECT * FROM agent_sessions')\n",
            "app/routes/admin.py",
        )
        assert violations == []

    def test_case_insensitive(self):
        violations = tbc.check_tbl002_content(
            "    db.execute('insert into session_messages (id) values (?)')\n",
            "app/routes/admin.py",
        )
        assert len(violations) == 1


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------


class TestBaseline:
    def test_key_excludes_line_number(self):
        v = tbc.BoundaryViolation(
            "TBL001", "app/routes/quota.py", "UsageRepository.get_combined_usage", "x"
        )
        assert v.key() == "TBL001|app/routes/quota.py|UsageRepository.get_combined_usage"

    def test_load_missing_baseline_is_empty(self, tmp_path):
        with patch.object(tbc, "BASELINE_PATH", tmp_path / "nope.json"):
            assert tbc.load_baseline() == set()

    def test_suppression_filters_violation(self, tmp_path):
        v = tbc.BoundaryViolation("TBL002", "app/routes/admin.py", "session_messages", "msg")
        baseline = tmp_path / "baseline.json"
        import json

        baseline.write_text(
            json.dumps(
                [
                    {
                        "key": v.key(),
                        "rule": v.rule,
                        "file": v.file,
                        "symbol": v.symbol,
                        "message": v.message,
                    }
                ]
            )
        )
        with patch.object(tbc, "BASELINE_PATH", baseline):
            keys = tbc.load_baseline()
        assert v.key() in keys


# ---------------------------------------------------------------------------
# Current codebase is compliant (the guard's reason for existing)
# ---------------------------------------------------------------------------


class TestCurrentCodebaseCompliant:
    """The guard must report zero non-baselined violations on the real tree."""

    def test_real_codebase_clean(self):
        readers = tbc.build_repo_method_table(tbc.REPO_FILES)
        violations = tbc.check_tbl001_text(tbc.WORK_ROUTE_FILES)
        violations += tbc.check_tbl001_callgraph(tbc.WORK_ROUTE_FILES, readers)
        violations += tbc.check_tbl002(tbc.MANAGE_ROUTE_FILES)
        assert violations == [], (
            "Guard found violations on the current codebase — either a real "
            "regression or the guard needs adjusting. Violations: "
            + "; ".join(f"{v.rule} {v.file} {v.message}" for v in violations)
        )
