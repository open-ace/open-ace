"""Tests for mtime-fallback session resolution hardening (issue #723, group B).

Two fixes:
  * `_find_latest_claude_session_id` now excludes JSONL files whose session id
    is already bound to another workflow line (main/review/test). Without this,
    a shared "main" session — continuously appended and thus always newest by
    mtime — was wrongly picked for a fresh review/test line, collapsing the
    3-session design (proven by web_server.log: review/test all resolved to
    main's 44484763 via the fallback).
  * `_peek_jsonl_session_id` reads a candidate file's first record to identify
    its owning session without parsing the whole transcript.
"""

import json
import os
import pwd
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner


def _write_jsonl(path: Path, session_id: str, *, mtime_offset: float = 0.0) -> Path:
    """Write a minimal claude JSONL whose first record carries sessionId."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"type": "summary", "sessionId": session_id, "timestamp": "2026-06-24T12:00:00Z"}
        )
        + "\n",
        encoding="utf-8",
    )
    if mtime_offset:
        ts = time.time() + mtime_offset
        os.utime(path, (ts, ts))
    return path


class TestPeekJsonlSessionId:
    def setup_method(self):
        self.runner = AutonomousAgentRunner()

    def test_reads_session_id_from_first_record(self, tmp_path):
        f = _write_jsonl(tmp_path / "abc123.jsonl", "abc123")
        assert self.runner._peek_jsonl_session_id(f) == "abc123"

    def test_falls_back_to_uuid_field(self, tmp_path):
        f = tmp_path / "def456.jsonl"
        f.write_text(json.dumps({"uuid": "def456"}) + "\n", encoding="utf-8")
        assert self.runner._peek_jsonl_session_id(f) == "def456"

    def test_empty_or_unparseable_returns_empty(self, tmp_path):
        f = tmp_path / "x.jsonl"
        f.write_text("\n\nnot json\n", encoding="utf-8")
        assert self.runner._peek_jsonl_session_id(f) == ""

    def test_missing_file_returns_empty(self, tmp_path):
        assert self.runner._peek_jsonl_session_id(tmp_path / "nope.jsonl") == ""


class TestFindLatestClaudeSessionIdExcludesBound:
    """The regression guard: a fresh review/test line must NOT pick the
    already-bound main session even though its mtime is newest."""

    def setup_method(self):
        self.runner = AutonomousAgentRunner()

    def test_bound_main_session_is_skipped(self, tmp_path):
        encoded = "encoded-worktree"
        with patch("pathlib.Path.home", return_value=tmp_path):
            project_dir = tmp_path / ".claude" / "projects" / encoded
            # main session file — bound, and with the NEWEST mtime (the bug).
            _write_jsonl(project_dir / "44484763.jsonl", "44484763", mtime_offset=+5.0)
            # new review session file — not bound, slightly older mtime.
            _write_jsonl(project_dir / "36c2fe72.jsonl", "36c2fe72", mtime_offset=+1.0)

            now = time.time()
            # Without exclusion, main (newest mtime) would win.
            result = self.runner._find_latest_claude_session_id(encoded, now)
            assert result == "44484763", "sanity: without exclusion, newest mtime wins"

            # With main excluded as bound, the review file is picked instead.
            result = self.runner._find_latest_claude_session_id(
                encoded, now, bound_cli_session_ids={"44484763"}
            )
            assert result == "36c2fe72", "bound main must be excluded; review should win"

    def test_all_candidates_bound_returns_empty(self, tmp_path):
        """If every candidate is already bound, return '' so the caller falls
        back to resume=False (start fresh) rather than misbinding."""
        encoded = "encoded-worktree"
        with patch("pathlib.Path.home", return_value=tmp_path):
            project_dir = tmp_path / ".claude" / "projects" / encoded
            _write_jsonl(project_dir / "aaa.jsonl", "aaa", mtime_offset=+1.0)
            _write_jsonl(project_dir / "bbb.jsonl", "bbb", mtime_offset=+2.0)
            result = self.runner._find_latest_claude_session_id(
                encoded, time.time(), bound_cli_session_ids={"aaa", "bbb"}
            )
            assert result == ""

    def test_no_bound_set_behaves_like_before(self, tmp_path):
        """Backward compat: bound_cli_session_ids=None keeps old behavior."""
        encoded = "encoded-worktree"
        with patch("pathlib.Path.home", return_value=tmp_path):
            project_dir = tmp_path / ".claude" / "projects" / encoded
            _write_jsonl(project_dir / "older.jsonl", "older", mtime_offset=+1.0)
            _write_jsonl(project_dir / "newer.jsonl", "newer", mtime_offset=+3.0)
            result = self.runner._find_latest_claude_session_id(encoded, time.time())
            assert result == "newer"

    def test_uses_system_account_home_not_service_home(self, tmp_path):
        encoded = "encoded-worktree"
        current_user = pwd.getpwuid(os.getuid()).pw_name
        user_home = tmp_path / "user-home"
        service_home = tmp_path / "service-home"
        _write_jsonl(user_home / ".claude" / "projects" / encoded / "newer.jsonl", "newer")
        (service_home / ".claude" / "projects").mkdir(parents=True, exist_ok=True)

        fake_pw = type("Pw", (), {"pw_dir": str(user_home)})()
        with (
            patch("pwd.getpwnam", return_value=fake_pw),
            patch("pathlib.Path.home", return_value=service_home),
        ):
            result = self.runner._find_latest_claude_session_id(
                encoded,
                time.time(),
                system_account=current_user,
            )

        assert result == "newer"


def _init_session_manager(tmp_path):
    """Build a SessionManager over a temp SQLite DB with a usable schema.

    SessionManager.__init__ does NOT auto-create tables, so _ensure_tables()
    is required before create_session(). project_id/project_path are added by
    _ensure_tables()'s alter_columns (fixed for parity with the authoritative
    schema files, #723).
    """
    from app.modules.workspace.session_manager import SessionManager

    sm = SessionManager(db_path=str(tmp_path / "test_sessions.db"))
    sm._ensure_tables()
    return sm


class TestListCliSessionIdsForProject:
    def test_returns_distinct_nonempty_ids(self, tmp_path):
        sm = _init_session_manager(tmp_path)
        # Insert sessions: two with cli_session_id, one without, different project.
        for sid, cli, proj in [
            ("w1", "main123", "/proj"),
            ("w2", "review456", "/proj"),
            ("w3", "", "/proj"),
            ("w4", "other789", "/other"),
        ]:
            sm.create_session(
                session_id=sid,
                session_type="workflow",
                title="t",
                tool_name="claude-code",
                user_id=1,
                project_path=proj,
                workspace_type="local",
            )
            if cli:
                sm.update_session_fields(sid, {"cli_session_id": cli})

        ids = sm.list_cli_session_ids_for_project("/proj")
        assert ids == {"main123", "review456"}

    def test_empty_project_path_returns_empty_set(self, tmp_path):
        sm = _init_session_manager(tmp_path)
        assert sm.list_cli_session_ids_for_project("") == set()
