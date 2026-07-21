"""Regression tests for autonomous PR review diff selection."""

import subprocess
from unittest.mock import MagicMock

from app.modules.workspace.autonomous.github_ops import GitHubOps
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
    gh._run_git.assert_called_once_with(["merge-base", "origin/main", "auto-dev/example"])
    gh.get_diff.assert_called_once_with("abc123", "auto-dev/example")


def test_review_diff_without_pr_still_uses_merge_base():
    gh = MagicMock()
    gh._run_git.return_value.stdout = "base456\n"
    gh.get_diff.return_value = "local merge-base diff"

    result = AutonomousOrchestrator._get_pr_review_diff(gh, None, "auto-dev/example")

    assert result == "local merge-base diff"
    gh.get_pr_diff.assert_not_called()
    gh.get_diff.assert_called_once_with("base456", "auto-dev/example")


def test_review_diff_fallback_ignores_stale_local_main(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "-b", "main")
    git("config", "user.name", "Test User")
    git("config", "user.email", "test@example.com")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    git("add", "base.txt")
    git("commit", "-m", "base")
    stale_main = git("rev-parse", "HEAD")

    (repo / "main-before-branch.txt").write_text("already in PR base\n", encoding="utf-8")
    git("add", "main-before-branch.txt")
    git("commit", "-m", "advance main before branch")
    branch_base = git("rev-parse", "HEAD")
    git("update-ref", "refs/remotes/origin/main", branch_base)

    git("switch", "-c", "auto-dev/example")
    (repo / "feature.txt").write_text("feature\n", encoding="utf-8")
    git("add", "feature.txt")
    git("commit", "-m", "feature")
    git("branch", "-f", "main", stale_main)

    gh = GitHubOps(str(repo))
    result = AutonomousOrchestrator._get_pr_review_diff(gh, None, "auto-dev/example")

    assert "feature.txt" in result
    assert "main-before-branch.txt" not in result
