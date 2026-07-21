"""Regression tests for autonomous PR review diff selection."""

from unittest.mock import MagicMock

from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator


def test_review_diff_prefers_github_pr_diff():
    gh = MagicMock()
    gh.get_pr_diff.return_value = "pr-only diff"

    result = AutonomousOrchestrator._get_pr_review_diff(gh, 1950, "auto-dev/example")

    assert result == "pr-only diff"
    gh.get_pr_diff.assert_called_once_with(1950)
    gh._run_git.assert_not_called()
    gh.get_diff.assert_not_called()


def test_review_diff_fallback_starts_at_merge_base():
    gh = MagicMock()
    gh.get_pr_diff.return_value = ""
    gh._run_git.return_value.stdout = "abc123\n"
    gh.get_diff.return_value = "merge-base diff"

    result = AutonomousOrchestrator._get_pr_review_diff(gh, 1950, "auto-dev/example")

    assert result == "merge-base diff"
    gh._run_git.assert_called_once_with(["merge-base", "main", "auto-dev/example"])
    gh.get_diff.assert_called_once_with("abc123", "auto-dev/example")


def test_review_diff_without_pr_still_uses_merge_base():
    gh = MagicMock()
    gh._run_git.return_value.stdout = "base456\n"
    gh.get_diff.return_value = "local merge-base diff"

    result = AutonomousOrchestrator._get_pr_review_diff(gh, None, "auto-dev/example")

    assert result == "local merge-base diff"
    gh.get_pr_diff.assert_not_called()
    gh.get_diff.assert_called_once_with("base456", "auto-dev/example")
