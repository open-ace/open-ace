"""Tests for ``git_push(force_with_lease=True)`` on workflow branches (Issue #1854).

Root cause: review-fix / CI-repair / dev-round-2+ agents re-commit already-pushed
work in a resumed main session, producing a new commit SHA that diverges from the
remote tip. The plain ``git push`` then hits a non-fast-forward rejection, which
is (correctly) classified non-transient and fails the workflow permanently.

Fix: ``git_push`` accepts ``force_with_lease``; when set it appends
``--force-with-lease`` and refuses to run unless the resolved branch is a
managed workflow branch. All four orchestrator push call sites pass
``force_with_lease=True``.

Migrated from tests/issues/1854/test_force_with_lease_push.py.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.github_ops import GitHubOps, GitHubOpsError

pytestmark = [pytest.mark.regression, pytest.mark.issue(1854)]


class TestGitPushForceWithLease:
    """``git_push`` force-with-lease flag and workflow branch guard."""

    def setup_method(self):
        self.gh = GitHubOps("/tmp/test-repo")

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_force_with_lease_appended_on_auto_dev_branch(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        self.gh.git_push(branch="auto-dev/abc12345", force_with_lease=True)
        cmd = mock_run.call_args[0][0]
        assert "--force-with-lease" in cmd
        # Order: push <remote> <branch> --force-with-lease
        assert cmd[-1] == "--force-with-lease"
        assert "auto-dev/abc12345" in cmd

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_force_with_lease_appended_on_fork_branch(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        self.gh.git_push(branch="fork/from-12345678", force_with_lease=True)
        cmd = mock_run.call_args[0][0]
        assert "--force-with-lease" in cmd
        assert "fork/from-12345678" in cmd

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_no_force_with_lease_by_default(self, mock_run):
        """Backward compatibility: plain git_push must not force-push."""
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        self.gh.git_push(branch="auto-dev/abc12345")
        cmd = mock_run.call_args[0][0]
        assert "--force-with-lease" not in cmd
        assert "--force" not in cmd

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_force_with_lease_refused_on_non_workflow_branch(self, mock_run):
        """Force-push to main/release/user branches must be refused."""
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        with pytest.raises(GitHubOpsError, match="non-workflow branch 'main'"):
            self.gh.git_push(branch="main", force_with_lease=True)
        # The guard runs before _run_git, so no git command should execute.
        mock_run.assert_not_called()

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_force_with_lease_refused_on_release_branch(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        with pytest.raises(GitHubOpsError, match="non-workflow branch 'release-1.0'"):
            self.gh.git_push(branch="release-1.0", force_with_lease=True)
        mock_run.assert_not_called()

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    @patch.object(GitHubOps, "get_current_branch", return_value="auto-dev/def67890")
    def test_force_with_lease_resolves_current_branch_when_not_passed(self, mock_branch, mock_run):
        """No branch arg resolves via get_current_branch; workflow branch is allowed."""
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        self.gh.git_push(force_with_lease=True)
        mock_branch.assert_called_once()
        # The validated current branch must be explicit. Otherwise an
        # inherited upstream (often main) makes push.default=simple reject it.
        push_cmd = mock_run.call_args[0][0]
        assert "--force-with-lease" in push_cmd
        assert "auto-dev/def67890" in push_cmd

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    @patch.object(GitHubOps, "get_current_branch", return_value="main")
    def test_force_with_lease_refused_when_current_branch_not_workflow(self, mock_branch, mock_run):
        """No branch arg + current branch is main → refused."""
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        with pytest.raises(GitHubOpsError, match="non-workflow branch 'main'"):
            self.gh.git_push(force_with_lease=True)
        # The guard runs before _run_git, so no git push command should execute.
        mock_run.assert_not_called()

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    @patch.object(
        GitHubOps,
        "get_current_branch",
        side_effect=GitHubOpsError("branch --show-current failed"),
    )
    def test_force_with_lease_refused_when_branch_unresolvable(self, mock_branch, mock_run):
        """No branch arg + get_current_branch fails → GitHubOpsError."""
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        with pytest.raises(GitHubOpsError, match="current branch could not be resolved"):
            self.gh.git_push(force_with_lease=True)
        mock_run.assert_not_called()
