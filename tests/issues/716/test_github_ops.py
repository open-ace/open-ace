"""Unit tests for GitHubOps using mocked subprocess calls."""

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.github_ops import GitHubOps, GitHubOpsError


class TestGitHubOpsInit:
    """Tests for GitHubOps initialization."""

    def test_init(self):
        gh = GitHubOps("/tmp/test-repo")
        assert gh.repo_path == "/tmp/test-repo"


class TestGitHubOpsRepo:
    """Tests for repository operations."""

    def setup_method(self):
        self.gh = GitHubOps("/tmp/test-repo")

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_create_repo(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"name": "test-repo", "url": "https://github.com/user/test-repo", "owner": {"login": "user"}}',
        )
        result = self.gh.create_repo("test-repo", private=True, description="Test")
        assert result["name"] == "test-repo"
        cmd = mock_run.call_args[0][0]
        assert "gh" in cmd
        assert "repo" in cmd
        assert "create" in cmd
        assert "--private" in cmd

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_create_repo_public(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout='{"name": "test-repo"}')
        self.gh.create_repo("test-repo", private=False)
        cmd = mock_run.call_args[0][0]
        assert "--public" in cmd

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_get_repo_url(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="https://github.com/user/test-repo.git"
        )
        url = self.gh.get_repo_url()
        assert url == "https://github.com/user/test-repo.git"

    @patch.object(GitHubOps, "get_repo_url", return_value="https://github.com/user/test-repo.git")
    def test_get_repo_name(self, _mock_url):
        # get_repo_name now resolves from the origin remote (not gh repo view),
        # so it returns the parsed owner/repo slug without a gh subprocess call.
        name = self.gh.get_repo_name()
        assert name == "user/test-repo"

    @patch.object(GitHubOps, "get_repo_url", side_effect=GitHubOpsError("no origin"))
    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_get_repo_name_fallback_no_remote(self, mock_run, _mock_url):
        # When no origin remote is resolvable, fall back to gh repo view.
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"nameWithOwner": "user/test-repo"}'
        )
        name = self.gh.get_repo_name()
        assert name == "user/test-repo"


class TestGitHubOpsIssue:
    """Tests for issue operations."""

    def setup_method(self):
        self.gh = GitHubOps("/tmp/test-repo")

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_create_issue(self, mock_run):
        # gh issue create prints the issue URL to stdout (no --json support)
        mock_run.return_value = MagicMock(
            returncode=0, stdout="https://github.com/user/test/issues/42"
        )
        result = self.gh.create_issue("Bug fix", body="Fix the bug", labels=["bug"])
        assert result["number"] == 42
        cmd = mock_run.call_args[0][0]
        assert "--label" in cmd
        assert "bug" in cmd

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_create_issue_no_labels(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="https://github.com/user/test/issues/43"
        )
        result = self.gh.create_issue("Feature", body="New feature")
        assert result["number"] == 43

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_get_issue(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"number": 42, "title": "Bug", "body": "Fix it", "state": "open", "comments": [{"body": "More detail", "createdAt": "2026-06-05T12:00:00Z"}]}',
        )
        result = self.gh.get_issue(42)
        assert result["number"] == 42
        assert result["title"] == "Bug"
        # Comments are now included so downstream phases see the full discussion
        args = mock_run.call_args[0][0]
        json_fields = next(a for a in args if a.startswith("number,title"))
        assert "comments" in json_fields
        assert len(result["comments"]) == 1

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_add_issue_comment(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        result = self.gh.add_issue_comment(42, "This is a comment")
        assert result["number"] == 42

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_list_issue_comments(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"comments": [{"body": "Comment 1", "createdAt": "2026-06-05T12:00:00Z"}, {"body": "Comment 2", "createdAt": "2026-06-05T13:00:00Z"}]}',
        )
        comments = self.gh.list_issue_comments(42)
        assert len(comments) == 2

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_list_issue_comments_with_since(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"comments": [{"body": "Comment 1", "createdAt": "2026-06-05T12:00:00Z"}, {"body": "Comment 2", "createdAt": "2026-06-05T13:00:00Z"}]}',
        )
        comments = self.gh.list_issue_comments(42, since="2026-06-05T12:30:00Z")
        assert len(comments) == 1
        assert comments[0]["body"] == "Comment 2"

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_update_issue(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"number": 42, "url": "https://github.com/user/test/issues/42"}'
        )
        result = self.gh.update_issue(42, title="Updated title")
        assert result["number"] == 42


class TestGitHubOpsBranch:
    """Tests for branch operations."""

    def setup_method(self):
        self.gh = GitHubOps("/tmp/test-repo")

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_create_branch_from_head(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        result = self.gh.create_branch("feature/test")
        assert result["branch"] == "feature/test"
        # Should have two calls: checkout -b and push
        assert mock_run.call_count == 2

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_create_branch_from_base(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        result = self.gh.create_branch("feature/test", base="main")
        assert result["branch"] == "feature/test"

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_get_current_branch(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="feature/test\n")
        branch = self.gh.get_current_branch()
        assert branch == "feature/test"

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_get_current_commit(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="abc123def456\n")
        sha = self.gh.get_current_commit()
        assert sha == "abc123def456"

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_delete_branch(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        self.gh.delete_branch("feature/test")
        assert mock_run.call_count == 2  # local + remote


class TestGitHubOpsPR:
    """Tests for PR operations."""

    def setup_method(self):
        self.gh = GitHubOps("/tmp/test-repo")

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_create_pr(self, mock_run):
        # create_pr makes two gh calls: `pr create` (prints URL) then
        # `pr view --json` (structured data).
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="https://github.com/user/test/pull/10"),
            MagicMock(
                returncode=0,
                stdout='{"number": 10, "url": "https://github.com/user/test/pull/10", "headRefName": "feature/test"}',
            ),
        ]
        result = self.gh.create_pr(
            title="New feature", body="Description", head="feature/test", base="main"
        )
        assert result["number"] == 10

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_create_pr_draft(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="https://github.com/user/test/pull/11"),
            MagicMock(
                returncode=0,
                stdout='{"number": 11, "url": "https://github.com/user/test/pull/11", "headRefName": "draft"}',
            ),
        ]
        self.gh.create_pr("Draft PR", draft=True)
        # --draft is on the `pr create` call (the first one), not the follow-up view
        cmd = mock_run.call_args_list[0][0][0]
        assert "--draft" in cmd

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_get_pr(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"number": 10, "title": "Feature", "state": "open", "additions": 100, "deletions": 20}',
        )
        result = self.gh.get_pr(10)
        assert result["number"] == 10
        assert result["additions"] == 100

    @patch.object(GitHubOps, "get_repo_url", return_value="https://github.com/user/test.git")
    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_get_pr_merge_state(self, mock_run, _mock_url):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"mergeable": true, "mergeable_state": "blocked"}',
        )

        result = self.gh.get_pr_merge_state(10)

        assert result == {"mergeable": True, "mergeable_state": "blocked"}
        cmd = mock_run.call_args[0][0]
        assert "repos/user/test/pulls/10" in cmd

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_merge_pr(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout='{"merged": true}')
        result = self.gh.merge_pr(10, strategy="squash")
        assert result["merged"] is True
        cmd = mock_run.call_args[0][0]
        assert "--squash" in cmd

    @patch.object(GitHubOps, "get_repo_url", return_value="https://github.com/user/test.git")
    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_list_pr_comments(self, mock_run, _mock_url):
        # get_repo_name now resolves from the remote (no gh subprocess call),
        # so only the gh api subprocess.run is mocked here.
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"id": 1, "path": "a.py", "body": "LGTM", "line": 10}\n{"id": 2, "path": "b.py", "body": "Fix", "line": 20}',
        )
        comments = self.gh.list_pr_comments(10)
        assert len(comments) == 2

    @patch.object(GitHubOps, "get_repo_url", return_value="https://github.com/user/test.git")
    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_list_pr_comments_empty(self, mock_run, _mock_url):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        comments = self.gh.list_pr_comments(10)
        assert comments == []


class TestGitHubOpsDiff:
    """Tests for diff operations."""

    def setup_method(self):
        self.gh = GitHubOps("/tmp/test-repo")

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_get_diff(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="diff --git a/file.py b/file.py\n+new line"
        )
        diff = self.gh.get_diff("HEAD~1", "HEAD")
        assert "new line" in diff

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_get_diff_stats(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="10\t5\tfile1.py\n3\t1\tfile2.py\n"),
            MagicMock(returncode=0, stdout="2\n"),
        ]
        stats = self.gh.get_diff_stats("HEAD~1", "HEAD")
        assert stats["additions"] == 13
        assert stats["deletions"] == 6
        assert stats["files"] == 2
        assert stats["commits"] == 2

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_get_commit_diff(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="diff --git a/file.py b/file.py\n+change"
        )
        diff = self.gh.get_commit_diff("abc123")
        assert "+change" in diff

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_get_commit_diff_stats(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="10\t5\tfile1.py\n3\t1\tfile2.py\n",
        )
        stats = self.gh.get_commit_diff_stats("abc123")
        assert stats["additions"] == 13
        assert stats["deletions"] == 6
        assert stats["files"] == 2
        assert stats["commits"] == 1


class TestGitHubOpsGit:
    """Tests for raw git operations."""

    def setup_method(self):
        self.gh = GitHubOps("/tmp/test-repo")

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_git_add_all(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        self.gh.git_add_all()
        cmd = mock_run.call_args[0][0]
        assert "git" in cmd
        assert "add" in cmd
        assert "-A" in cmd

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_git_commit(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout="abc123\n"),
        ]
        result = self.gh.git_commit("feat: add feature")
        assert result["sha"] == "abc123"
        assert result["message"] == "feat: add feature"

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_git_commit_no_verify(self, mock_run):
        """git_commit(no_verify=True) should pass --no-verify to git."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout="def456\n"),
        ]
        result = self.gh.git_commit("auto: changes", no_verify=True)
        assert result["sha"] == "def456"
        # First call is the commit, second is rev-parse
        commit_cmd = mock_run.call_args_list[0][0][0]
        assert "commit" in commit_cmd
        assert "--no-verify" in commit_cmd

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_git_commit_default_no_verify_flag(self, mock_run):
        """git_commit() without no_verify should NOT include --no-verify."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=""),
            MagicMock(returncode=0, stdout="abc123\n"),
        ]
        self.gh.git_commit("feat: normal commit")
        commit_cmd = mock_run.call_args_list[0][0][0]
        assert "--no-verify" not in commit_cmd

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_git_push(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        self.gh.git_push("origin", "main")
        cmd = mock_run.call_args[0][0]
        assert "push" in cmd
        assert "origin" in cmd
        assert "main" in cmd

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_git_init(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        self.gh.git_init()
        cmd = mock_run.call_args[0][0]
        assert "git" in cmd
        assert "init" in cmd


class TestGitHubOpsWorktree:
    """Tests for worktree operations."""

    def setup_method(self):
        self.gh = GitHubOps("/tmp/test-repo")

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_create_worktree(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        result = self.gh.create_worktree("/tmp/test-repo-wt", "feature/wt")
        assert result["worktree_path"] == "/tmp/test-repo-wt"
        assert result["branch"] == "feature/wt"
        # Default base is HEAD
        cmd = mock_run.call_args[0][0]
        assert cmd[-1] == "HEAD"

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_create_worktree_with_base(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        result = self.gh.create_worktree("/tmp/test-repo-wt", "feature/wt", base="origin/main")
        assert result["worktree_path"] == "/tmp/test-repo-wt"
        assert result["branch"] == "feature/wt"
        # Should use the provided base ref instead of HEAD
        cmd = mock_run.call_args[0][0]
        assert cmd[-1] == "origin/main"

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_remove_worktree(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        result = self.gh.remove_worktree("/tmp/test-repo-wt")
        assert result["removed"] == "/tmp/test-repo-wt"

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_list_worktrees(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="worktree /tmp/main\nbranch refs/heads/main\nworktree /tmp/feature\nbranch refs/heads/feature\n",
        )
        worktrees = self.gh.list_worktrees()
        assert len(worktrees) == 2
        assert worktrees[0]["path"] == "/tmp/main"
        assert worktrees[0]["branch"] == "refs/heads/main"


class TestGitHubOpsError:
    """Tests for error handling."""

    def setup_method(self):
        self.gh = GitHubOps("/tmp/test-repo")

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_gh_command_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr="error: not found")
        with pytest.raises(GitHubOpsError, match="failed"):
            self.gh.get_repo_url()

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_gh_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=120)
        with pytest.raises(GitHubOpsError, match="timed out"):
            self.gh.get_repo_url()

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_gh_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        with pytest.raises(GitHubOpsError, match="gh CLI not found"):
            self.gh.get_repo_name()  # Uses _run_gh, not _run_git

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_check_false_no_exception(self, mock_run):
        """With check=False, should not raise even on failure."""
        mock_run.return_value = MagicMock(returncode=1, stderr="error")
        result = self.gh._run_gh(["status"], check=False)
        assert result.returncode == 1
