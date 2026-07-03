"""Tests for GitHubOps sudo wrapper and owner/repo resolution.

These cover the system_account / cross-user code paths that the original
test_github_ops.py never exercised — the gap that let PR #1422's invalid
`gh -C <path>` flag ship (gh has no `-C`; only git does), breaking every
gh operation under a sudo wrapper and causing empty-requirements plans.
"""

from unittest.mock import MagicMock, patch

from app.modules.workspace.autonomous.github_ops import GitHubOps, GitHubOpsError


def _completed(stdout: str = "", returncode: int = 0, stderr: str = "") -> MagicMock:
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


class TestNeedsSudo:
    """_needs_sudo short-circuits when the service already runs as system_account."""

    @patch("app.modules.workspace.autonomous.github_ops.pwd.getpwuid")
    def test_same_user_skips_sudo(self, mock_getpwuid):
        mock_getpwuid.return_value.pw_name = "alice"
        gh = GitHubOps("/tmp/repo", system_account="alice")
        assert gh._needs_sudo() is False

    @patch("app.modules.workspace.autonomous.github_ops.pwd.getpwuid")
    def test_cross_user_needs_sudo(self, mock_getpwuid):
        mock_getpwuid.return_value.pw_name = "openace"
        gh = GitHubOps("/tmp/repo", system_account="alice")
        assert gh._needs_sudo() is True

    def test_no_system_account_needs_no_sudo(self):
        assert GitHubOps("/tmp/repo")._needs_sudo() is False

    @patch(
        "app.modules.workspace.autonomous.github_ops.pwd.getpwuid",
        side_effect=KeyError("no such uid"),
    )
    def test_pwd_lookup_failure_defaults_to_sudo(self, _mock):
        gh = GitHubOps("/tmp/repo", system_account="alice")
        # Cannot determine current user → stay safe and assume cross-user.
        assert gh._needs_sudo() is True


class TestResolveOwnerRepo:
    """_resolve_owner_repo derives owner/repo from the origin remote."""

    def test_https_url(self):
        gh = GitHubOps("/tmp/repo", system_account="alice")
        with patch.object(
            GitHubOps, "get_repo_url", return_value="https://github.com/open-ace/open-ace.git"
        ):
            assert gh._resolve_owner_repo() == "open-ace/open-ace"

    def test_ssh_url(self):
        gh = GitHubOps("/tmp/repo", system_account="alice")
        with patch.object(
            GitHubOps, "get_repo_url", return_value="git@github.com:open-ace/open-ace.git"
        ):
            assert gh._resolve_owner_repo() == "open-ace/open-ace"

    def test_https_url_without_git_suffix(self):
        gh = GitHubOps("/tmp/repo", system_account="alice")
        with patch.object(
            GitHubOps, "get_repo_url", return_value="https://github.com/open-ace/open-ace"
        ):
            assert gh._resolve_owner_repo() == "open-ace/open-ace"

    def test_result_is_cached(self):
        gh = GitHubOps("/tmp/repo", system_account="alice")
        with patch.object(
            GitHubOps, "get_repo_url", return_value="https://github.com/open-ace/open-ace.git"
        ) as m:
            assert gh._resolve_owner_repo() == "open-ace/open-ace"
            assert gh._resolve_owner_repo() == "open-ace/open-ace"
            # get_repo_url (and thus the underlying git call) runs only once.
            assert m.call_count == 1

    def test_no_remote_returns_none(self):
        gh = GitHubOps("/tmp/repo", system_account="alice")
        with patch.object(GitHubOps, "get_repo_url", side_effect=GitHubOpsError("no origin")):
            # A GitHubOpsError (e.g. missing origin remote on a new project)
            # must not propagate; resolution yields None.
            assert gh._resolve_owner_repo() is None

    def test_unparseable_url_returns_none(self):
        gh = GitHubOps("/tmp/repo", system_account="alice")
        with patch.object(GitHubOps, "get_repo_url", return_value="https://gitlab.com/foo/bar"):
            assert gh._resolve_owner_repo() is None


class TestRunGhSudo:
    """_run_gh under a sudo wrapper uses -R owner/repo, never -C."""

    @patch.object(GitHubOps, "_resolve_owner_repo", return_value="open-ace/open-ace")
    @patch.object(GitHubOps, "_needs_sudo", return_value=True)
    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_sudo_uses_repo_flag_not_dash_c(self, mock_run, _needs, _resolve):
        mock_run.return_value = _completed(stdout='{"number": 240}')
        gh = GitHubOps("/tmp/repo", system_account="alice")

        gh.get_issue(240)

        gh_cmd = mock_run.call_args.args[0]
        assert gh_cmd[:4] == ["sudo", "-u", "alice", "gh"]
        assert "-R" in gh_cmd
        assert "open-ace/open-ace" in gh_cmd
        # gh has no -C flag — it must never appear.
        assert "-C" not in gh_cmd

    @patch.object(GitHubOps, "_resolve_owner_repo", return_value="open-ace/open-ace")
    @patch.object(GitHubOps, "_needs_sudo", return_value=True)
    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_sudo_drops_cwd_kwarg(self, mock_run, _needs, _resolve):
        mock_run.return_value = _completed(stdout='{"number": 240}')
        gh = GitHubOps("/tmp/repo", system_account="alice")

        gh.get_issue(240)

        # The gh subprocess call must not pass cwd (Issue #1421: cwd under
        # sudo triggers a Python permission check as the service user).
        assert "cwd" not in mock_run.call_args.kwargs

    @patch.object(GitHubOps, "_needs_sudo", return_value=False)
    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_same_user_no_sudo_no_dash_c(self, mock_run, _needs):
        # Service runs as the target user → sudo skipped, gh infers the repo
        # from cwd as it always did.
        mock_run.return_value = _completed(stdout='{"number": 240}')
        gh = GitHubOps("/tmp/repo", system_account="alice")

        gh.get_issue(240)

        gh_cmd = mock_run.call_args.args[0]
        assert gh_cmd[0] == "gh"  # no sudo prefix
        assert "sudo" not in gh_cmd
        assert "-C" not in gh_cmd
        assert "-R" not in gh_cmd  # no explicit -R needed; cwd is kept
        # cwd is preserved so gh can resolve the repo from the working dir.
        assert mock_run.call_args.kwargs.get("cwd") == "/tmp/repo"


class TestEnsureSafeDirectory:
    """_ensure_safe_directory must not crash on duplicate timeout kwarg."""

    def setup_method(self):
        # The class-level cache persists across tests; reset it so each test
        # actually exercises the subprocess path.
        GitHubOps._safe_directory_configured.clear()

    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_no_duplicate_timeout(self, mock_run):
        # Regression guard: previously this raised
        # "got multiple values for keyword argument 'timeout'" (a hard
        # TypeError swallowed as a warning), so safe.directory was never set.
        mock_run.return_value = _completed(stdout="")
        gh = GitHubOps("/tmp/repo")

        gh._ensure_safe_directory()

        # subprocess.run ran and completed without raising; the call was made
        # with exactly one timeout value.
        assert mock_run.call_count == 1
        assert mock_run.call_args.kwargs.get("timeout") == 5
