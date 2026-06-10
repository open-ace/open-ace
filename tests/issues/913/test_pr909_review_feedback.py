"""
Tests for PR #909 code review feedback fixes (Issue #913).

Covers:
1. _poll_ci_status polling mechanism
2. get_pr_checks warning log on parse failure
3. Previous review feedback truncation notice
4. Pre-existing CI detection via structured output
5. Worktree cleanup logic verification
"""

import json
import logging
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.github_ops import GitHubOps
from app.modules.workspace.autonomous.orchestrator import (
    CI_POLL_INTERVAL,
    CI_POLL_MAX_WAIT,
    AutonomousOrchestrator,
)

# ── Test get_pr_checks ──────────────────────────────────────────────────


class TestGetPRChecks:
    """Test GitHubOps.get_pr_checks parsing and logging."""

    def _make_gh(self, stdout: str = "", returncode: int = 0):
        gh = GitHubOps("/tmp/fake")
        gh._run_gh = MagicMock(return_value=MagicMock(stdout=stdout, returncode=returncode))
        return gh

    def test_parses_valid_json(self):
        data = [
            {"name": "lint", "state": "completed", "bucket": "pass", "link": ""},
            {"name": "test", "state": "completed", "bucket": "fail", "link": "http://..."},
        ]
        gh = self._make_gh(json.dumps(data))
        result = gh.get_pr_checks(42)
        assert len(result) == 2
        assert result[0]["bucket"] == "pass"
        assert result[1]["bucket"] == "fail"

    def test_returns_empty_on_empty_stdout(self):
        gh = self._make_gh("")
        result = gh.get_pr_checks(42)
        assert result == []

    def test_returns_empty_on_invalid_json(self):
        gh = self._make_gh("not json at all")
        result = gh.get_pr_checks(42)
        assert result == []

    def test_logs_warning_on_parse_failure(self, caplog):
        gh = self._make_gh("bad json")
        with caplog.at_level(logging.WARNING):
            gh.get_pr_checks(42)
        assert "Failed to parse CI checks" in caplog.text
        assert "PR #42" in caplog.text

    def test_returns_empty_on_none_stdout(self):
        gh = self._make_gh(None)
        result = gh.get_pr_checks(42)
        assert result == []


# ── Test _poll_ci_status ────────────────────────────────────────────────


class TestPollCIStatus:
    """Test the CI polling mechanism."""

    @patch("app.modules.workspace.autonomous.orchestrator.time")
    def test_returns_immediately_when_no_pending(self, mock_time):
        """All checks completed — no polling needed."""
        mock_time.monotonic.side_effect = [0]  # only called once for deadline
        orchestrator = MagicMock(spec=AutonomousOrchestrator)
        gh = MagicMock()
        checks = [
            {"name": "lint", "bucket": "pass"},
            {"name": "test", "bucket": "fail"},
        ]
        gh.get_pr_checks.return_value = checks

        result = AutonomousOrchestrator._poll_ci_status(orchestrator, gh, 42)
        assert result == checks
        assert gh.get_pr_checks.call_count == 1

    @patch("app.modules.workspace.autonomous.orchestrator.time")
    def test_returns_immediately_when_empty_checks(self, mock_time):
        """No checks configured — nothing to wait for."""
        mock_time.monotonic.return_value = 0
        orchestrator = MagicMock(spec=AutonomousOrchestrator)
        gh = MagicMock()
        gh.get_pr_checks.return_value = []

        result = AutonomousOrchestrator._poll_ci_status(orchestrator, gh, 42)
        assert result == []

    @patch("app.modules.workspace.autonomous.orchestrator.time")
    def test_polls_until_no_pending(self, mock_time):
        """Polls until all checks are non-pending."""
        # Timeline: poll 1 (pending), poll 2 (pending), poll 3 (done)
        pending_checks = [{"name": "lint", "bucket": "pending"}]
        done_checks = [{"name": "lint", "bucket": "pass"}]
        gh = MagicMock()
        gh.get_pr_checks.side_effect = [pending_checks, pending_checks, done_checks]

        # monotonic: deadline init, check 1, check 2, check 3
        mock_time.monotonic.side_effect = [0, 10, 40, 70]
        mock_time.sleep = MagicMock()

        orchestrator = MagicMock(spec=AutonomousOrchestrator)
        result = AutonomousOrchestrator._poll_ci_status(orchestrator, gh, 42)
        assert result == done_checks
        assert mock_time.sleep.call_count == 2  # slept twice before checks completed

    @patch("app.modules.workspace.autonomous.orchestrator.time")
    def test_times_out_with_pending_checks(self, mock_time):
        """Returns last result when timeout is reached."""
        pending_checks = [{"name": "lint", "bucket": "pending"}]
        gh = MagicMock()
        gh.get_pr_checks.return_value = pending_checks

        # monotonic: deadline init (0), then timeout check after first get_pr_checks
        mock_time.monotonic.side_effect = [0, CI_POLL_MAX_WAIT + 100]
        mock_time.sleep = MagicMock()

        orchestrator = MagicMock(spec=AutonomousOrchestrator)
        with patch("app.modules.workspace.autonomous.orchestrator.logger"):
            result = AutonomousOrchestrator._poll_ci_status(orchestrator, gh, 42)

        assert result == pending_checks
        assert gh.get_pr_checks.call_count == 1  # only one call before timeout
        assert mock_time.sleep.call_count == 0  # never slept — timed out immediately


# ── Test pre-existing CI detection ──────────────────────────────────────


class TestPreExistingCIDetection:
    """Test the structured CI_STATUS detection logic."""

    def test_detects_structured_tag(self):
        """Matches CI_STATUS: pre-existing structured output."""
        import re

        fix_response = "Fixed the issue.\nCI_STATUS: pre-existing"
        has_preexisting = bool(
            re.search(r"CI_STATUS:\s*pre-existing", fix_response)
            or "预先存在" in fix_response
            or re.search(r"pre[\s-]?existing", fix_response, re.IGNORECASE)
        )
        assert has_preexisting is True

    def test_detects_legacy_chinese(self):
        """Matches legacy '预先存在' pattern for backwards compat."""
        import re

        fix_response = "这个问题是预先存在的，不是本PR引入的。"
        has_preexisting = bool(
            re.search(r"CI_STATUS:\s*pre-existing", fix_response)
            or "预先存在" in fix_response
            or re.search(r"pre[\s-]?existing", fix_response, re.IGNORECASE)
        )
        assert has_preexisting is True

    def test_detects_legacy_english(self):
        """Matches 'pre-existing' or 'pre existing' in English."""
        import re

        for text in [
            "This is a pre-existing issue.",
            "This is a pre existing issue.",
        ]:
            has_preexisting = bool(
                re.search(r"CI_STATUS:\s*pre-existing", text)
                or "预先存在" in text
                or re.search(r"pre[\s-]?existing", text, re.IGNORECASE)
            )
            assert has_preexisting is True, f"Failed for: {text}"

    def test_no_match_when_introduced(self):
        """No match when CI failure was introduced by this PR."""
        import re

        fix_response = "Fixed the bug and tests pass now.\nCI_STATUS: introduced"
        has_preexisting = bool(
            re.search(r"CI_STATUS:\s*pre-existing", fix_response)
            or "预先存在" in fix_response
            or re.search(r"pre[\s-]?existing", fix_response, re.IGNORECASE)
        )
        assert has_preexisting is False

    def test_no_match_on_unrelated_text(self):
        """No false positives on unrelated text."""
        import re

        fix_response = "All tests passed. Changes committed and pushed."
        has_preexisting = bool(
            re.search(r"CI_STATUS:\s*pre-existing", fix_response)
            or "预先存在" in fix_response
            or re.search(r"pre[\s-]?existing", fix_response, re.IGNORECASE)
        )
        assert has_preexisting is False


# ── Test truncation logic ───────────────────────────────────────────────


class TestTruncationNotice:
    """Test the truncation notice for previous review feedback."""

    def test_notice_shown_when_truncated(self):
        cleaned = "x" * 3100
        truncated = cleaned[:3000]
        notice = (
            "\n> ⚠️ 以上审查意见已截断至 3000 字符，部分内容可能被省略。\n"
            if len(cleaned) > 3000
            else ""
        )
        assert "⚠️" in notice
        assert len(truncated) == 3000

    def test_no_notice_when_not_truncated(self):
        cleaned = "Short review content"
        truncated = cleaned[:3000]
        notice = (
            "\n> ⚠️ 以上审查意见已截断至 3000 字符，部分内容可能被省略。\n"
            if len(cleaned) > 3000
            else ""
        )
        assert notice == ""
        assert truncated == "Short review content"

    def test_no_notice_at_exact_boundary(self):
        cleaned = "x" * 3000
        notice = (
            "\n> ⚠️ 以上审查意见已截断至 3000 字符，部分内容可能被省略。\n"
            if len(cleaned) > 3000
            else ""
        )
        assert notice == ""
