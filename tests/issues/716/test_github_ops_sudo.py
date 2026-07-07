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
        # A remote that doesn't match any known host/slug form (no owner/repo
        # path component) yields None. Note: non-github.com hosts (gitlab/GHES)
        # are now intentionally carried through as HOST/OWNER/REPO — see
        # test_ghes_https_url_includes_host — so this must use a truly
        # unparseable value.
        with patch.object(GitHubOps, "get_repo_url", return_value="/local/path/repo"):
            assert gh._resolve_owner_repo() is None

    def test_ssh_scheme_url(self):
        # ssh://git@github.com/owner/repo.git — standard git SSH remote form
        # that the original parser missed (review feedback).
        gh = GitHubOps("/tmp/repo", system_account="alice")
        with patch.object(
            GitHubOps, "get_repo_url", return_value="ssh://git@github.com/open-ace/open-ace.git"
        ):
            assert gh._resolve_owner_repo() == "open-ace/open-ace"

    def test_ssh_scheme_url_without_git_suffix(self):
        gh = GitHubOps("/tmp/repo", system_account="alice")
        with patch.object(
            GitHubOps, "get_repo_url", return_value="ssh://git@github.com/open-ace/open-ace"
        ):
            assert gh._resolve_owner_repo() == "open-ace/open-ace"

    def test_ghes_https_url_includes_host(self):
        # A GHES host must be carried through as HOST/OWNER/REPO so gh's -R
        # targets the right server (review feedback).
        gh = GitHubOps("/tmp/repo", system_account="alice")
        with patch.object(
            GitHubOps, "get_repo_url", return_value="https://gh.example.com/owner/repo.git"
        ):
            assert gh._resolve_owner_repo() == "gh.example.com/owner/repo"

    def test_ghes_scp_url_includes_host(self):
        gh = GitHubOps("/tmp/repo", system_account="alice")
        with patch.object(
            GitHubOps, "get_repo_url", return_value="git@gh.example.com:owner/repo.git"
        ):
            assert gh._resolve_owner_repo() == "gh.example.com/owner/repo"

    def test_https_url_with_credentials(self):
        """URL containing user:token@ credentials is stripped before parsing."""
        gh = GitHubOps("/tmp/repo", system_account="alice")
        with patch.object(
            GitHubOps, "get_repo_url",
            return_value="https://user:ghp_token@github.com/open-ace/open-ace.git"
        ):
            assert gh._resolve_owner_repo() == "open-ace/open-ace"

    def test_https_url_with_credentials_only_user(self):
        """URL containing user@ (no password) is stripped before parsing."""
        gh = GitHubOps("/tmp/repo", system_account="alice")
        with patch.object(
            GitHubOps, "get_repo_url",
            return_value="https://user@github.com/open-ace/open-ace.git"
        ):
            assert gh._resolve_owner_repo() == "open-ace/open-ace"

    def test_ghes_https_url_with_credentials(self):
        """GHES URL with credentials strips credentials and includes host."""
        gh = GitHubOps("/tmp/repo", system_account="alice")
        with patch.object(
            GitHubOps, "get_repo_url",
            return_value="https://user:token@gh.example.com/owner/repo.git"
        ):
            assert gh._resolve_owner_repo() == "gh.example.com/owner/repo"


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


class TestRunGhRepoScoped:
    """repo create/view reject -R; repo_scoped=False omits it (review feedback)."""

    @patch.object(GitHubOps, "_resolve_owner_repo", return_value="open-ace/open-ace")
    @patch.object(GitHubOps, "_needs_sudo", return_value=True)
    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_create_repo_no_dash_r(self, mock_run, _needs, _resolve):
        # `gh repo create` does not accept -R; even when a remote resolves, the
        # command must run plain gh under sudo (previously the unconditional -R
        # would regress to "unknown shorthand flag: 'R'").
        mock_run.return_value = _completed(
            stdout="https://github.com/open-ace/new-repo\n✓ Created repository"
        )
        gh = GitHubOps("/tmp/repo", system_account="alice")

        gh.create_repo("new-repo", private=True)

        gh_cmd = mock_run.call_args.args[0]
        assert gh_cmd[:3] == ["sudo", "-u", "alice"]
        assert gh_cmd[3] == "gh"
        assert "-R" not in gh_cmd
        assert "repo" in gh_cmd and "create" in gh_cmd

    @patch.object(
        GitHubOps, "get_repo_url", return_value="https://github.com/open-ace/open-ace.git"
    )
    @patch.object(GitHubOps, "_needs_sudo", return_value=True)
    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_get_repo_name_resolves_from_remote_under_sudo(self, mock_run, _needs, _url):
        # get_repo_name must resolve the slug from the origin remote (via
        # _resolve_owner_repo, which uses git -C under sudo) — NOT gh repo view,
        # which under sudo has lost cwd and would guess the wrong repo / fail.
        # Review: the old repo_scoped=False path only asserted "no -R" without
        # verifying the repo is actually resolved, masking this regression.
        gh = GitHubOps("/tmp/repo", system_account="alice")

        name = gh.get_repo_name()

        assert name == "open-ace/open-ace"
        # No gh subprocess call at all: the slug comes purely from git remote.
        for call in mock_run.call_args_list:
            assert "gh" not in call.args[0]

    @patch.object(GitHubOps, "get_repo_url", return_value="https://gh.example.com/owner/repo.git")
    @patch.object(GitHubOps, "_needs_sudo", return_value=True)
    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_get_repo_name_ghes_strips_host_for_api_path(self, mock_run, _needs, _url):
        # For GHES, _resolve_owner_repo carries the host for gh -R, but
        # get_repo_name feeds gh api repos/<owner>/<repo>/... which must be a
        # plain OWNER/REPO (no host in the REST path).
        gh = GitHubOps("/tmp/repo", system_account="alice")

        name = gh.get_repo_name()

        assert name == "owner/repo"


class TestGhApiSudo:
    """gh api rejects -R; under sudo it must use the REST path + GHES --hostname."""

    @patch.object(
        GitHubOps, "get_repo_url", return_value="https://github.com/open-ace/open-ace.git"
    )
    @patch.object(GitHubOps, "_needs_sudo", return_value=True)
    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_list_pr_comments_no_dash_r_under_sudo(self, mock_run, _needs, _url):
        # gh api rejects -R (review: list_pr_comments under sudo would be
        # injected with an illegal -R). It targets the repo via the REST path
        # repos/<owner>/<repo>/pulls/.../comments, so no -R is needed.
        mock_run.return_value = _completed(stdout="")
        gh = GitHubOps("/tmp/repo", system_account="alice")

        gh.list_pr_comments(10)

        gh_cmd = mock_run.call_args.args[0]
        assert gh_cmd[:3] == ["sudo", "-u", "alice"]
        assert gh_cmd[3] == "gh"
        assert gh_cmd[4] == "api"
        assert "-R" not in gh_cmd
        # The REST path uses the resolved owner/repo slug.
        assert "repos/open-ace/open-ace/pulls/10/comments" in gh_cmd

    @patch.object(GitHubOps, "get_repo_url", return_value="https://gh.example.com/owner/repo.git")
    @patch.object(GitHubOps, "_needs_sudo", return_value=True)
    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_list_pr_comments_ghes_adds_hostname(self, mock_run, _needs, _url):
        # For GHES, gh api can't use -R; it needs --hostname to target the
        # right server (review feedback).
        mock_run.return_value = _completed(stdout="")
        gh = GitHubOps("/tmp/repo", system_account="alice")

        gh.list_pr_issue_comments(10)

        gh_cmd = mock_run.call_args.args[0]
        assert "--hostname" in gh_cmd
        assert "gh.example.com" in gh_cmd
        assert "-R" not in gh_cmd
        # REST path uses the plain owner/repo (no host prefix).
        assert "repos/owner/repo/issues/10/comments" in gh_cmd

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
