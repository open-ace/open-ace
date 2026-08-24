"""Regression tests for Issue #2909: GH_TOKEN stripped by sudo env_reset in
multi-user ``gh repo create``.

These tests verify that ``create_repo()`` uses ``api_only=True`` so the
configured AI GitHub token (``GH_TOKEN``) is not stripped by sudo's
``env_reset`` policy when the service user differs from the system account.
They also cover the fallback (no token → sudo), same-user compatibility,
token-leak prevention, ``-R`` absence, and Git ops isolation.
"""

from unittest.mock import MagicMock, patch

from app.modules.workspace.autonomous.github_ops import GitHubOps, GitHubOpsError


def _completed(stdout: str = "", returncode: int = 0, stderr: str = "") -> MagicMock:
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


_FAKE_ENV = {
    "GH_TOKEN": "ghp_faketoken_2909",
    "GIT_AUTHOR_NAME": "Bot",
    "GIT_AUTHOR_EMAIL": "bot@example.com",
    "GIT_COMMITTER_NAME": "Bot",
    "GIT_COMMITTER_EMAIL": "bot@example.com",
}


class TestCreateRepoSudoTokenPreservation:
    """Issue #2909: create_repo must not strip GH_TOKEN under sudo."""

    @patch.object(GitHubOps, "_get_env", return_value=_FAKE_ENV)
    @patch.object(GitHubOps, "_needs_sudo", return_value=True)
    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_cross_user_with_token_skips_sudo(self, mock_run, _needs, _env):
        """Condition 1-3: _needs_sudo True, GH_TOKEN present, create_repo
        does NOT go through the sudo path that would strip the token."""
        mock_run.return_value = _completed(
            stdout="https://github.com/open-ace/new-repo\n✓ Created repository"
        )
        gh = GitHubOps("/workspace/alice/project", system_account="alice")

        gh.create_repo("new-repo", private=True)

        cmd = mock_run.call_args.args[0]
        assert "sudo" not in cmd
        assert cmd[0] == "gh"

    @patch.object(GitHubOps, "_get_env", return_value=_FAKE_ENV)
    @patch.object(GitHubOps, "_needs_sudo", return_value=True)
    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_cross_user_with_token_preserves_gh_token_in_env(self, mock_run, _needs, _env):
        """Condition 5: GH_TOKEN is correctly passed into the gh subprocess env."""
        mock_run.return_value = _completed(
            stdout="https://github.com/open-ace/new-repo\n✓ Created repository"
        )
        gh = GitHubOps("/workspace/alice/project", system_account="alice")

        gh.create_repo("new-repo", private=True)

        env = mock_run.call_args.kwargs.get("env", {})
        assert env.get("GH_TOKEN") == "ghp_faketoken_2909"

    @patch.object(GitHubOps, "_get_env", return_value=_FAKE_ENV)
    @patch.object(GitHubOps, "_needs_sudo", return_value=True)
    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_cross_user_with_token_no_dash_r(self, mock_run, _needs, _env):
        """Condition 6: gh repo create must not carry -R (it rejects it)."""
        mock_run.return_value = _completed(
            stdout="https://github.com/open-ace/new-repo\n✓ Created repository"
        )
        gh = GitHubOps("/workspace/alice/project", system_account="alice")

        gh.create_repo("new-repo", private=True)

        cmd = mock_run.call_args.args[0]
        assert "-R" not in cmd

    @patch.object(GitHubOps, "_get_env", return_value=_FAKE_ENV)
    @patch.object(GitHubOps, "_needs_sudo", return_value=True)
    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_cross_user_with_token_drops_cwd(self, mock_run, _needs, _env):
        """The api_as_service path must drop cwd (service user may lack access
        to the owner's workspace directory)."""
        mock_run.return_value = _completed(
            stdout="https://github.com/open-ace/new-repo\n✓ Created repository"
        )
        gh = GitHubOps("/workspace/alice/project", system_account="alice")

        gh.create_repo("new-repo", private=True)

        assert "cwd" not in mock_run.call_args.kwargs


class TestCreateRepoNoTokenFallback:
    """Condition 8: without a bot token, create_repo falls back to sudo."""

    @patch.object(GitHubOps, "_get_env", return_value=None)
    @patch.object(GitHubOps, "_needs_sudo", return_value=True)
    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_cross_user_no_token_uses_sudo(self, mock_run, _needs, _env):
        mock_run.return_value = _completed(
            stdout="https://github.com/open-ace/new-repo\n✓ Created repository"
        )
        gh = GitHubOps("/workspace/alice/project", system_account="alice")

        gh.create_repo("new-repo", private=True)

        cmd = mock_run.call_args.args[0]
        assert cmd[:3] == ["sudo", "-u", "alice"]
        assert cmd[3] == "/usr/local/bin/openace-gh"

    @patch.object(GitHubOps, "_get_env", return_value=None)
    @patch.object(GitHubOps, "_needs_sudo", return_value=True)
    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_cross_user_no_token_no_gh_token_in_env(self, mock_run, _needs, _env):
        """No token configured → env should not contain GH_TOKEN."""
        mock_run.return_value = _completed(
            stdout="https://github.com/open-ace/new-repo\n✓ Created repository"
        )
        gh = GitHubOps("/workspace/alice/project", system_account="alice")

        gh.create_repo("new-repo", private=True)

        env = mock_run.call_args.kwargs.get("env", {})
        assert "GH_TOKEN" not in env or env.get("GH_TOKEN") is None


class TestCreateRepoSameUserCompatibility:
    """Condition 7: same-user deployment must remain unchanged."""

    @patch.object(GitHubOps, "_get_env", return_value=_FAKE_ENV)
    @patch.object(GitHubOps, "_needs_sudo", return_value=False)
    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_same_user_with_token_no_sudo_keeps_cwd(self, mock_run, _needs, _env):
        """Same-user + token: gh runs directly, cwd preserved (gh infers repo
        from working directory)."""
        mock_run.return_value = _completed(
            stdout="https://github.com/open-ace/new-repo\n✓ Created repository"
        )
        gh = GitHubOps("/workspace/alice/project", system_account="alice")

        gh.create_repo("new-repo", private=True)

        cmd = mock_run.call_args.args[0]
        assert "sudo" not in cmd
        assert cmd[0] == "gh"
        assert mock_run.call_args.kwargs.get("cwd") == "/workspace/alice/project"

    @patch.object(GitHubOps, "_get_env", return_value=None)
    @patch.object(GitHubOps, "_needs_sudo", return_value=False)
    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_same_user_no_token_no_sudo(self, mock_run, _needs, _env):
        """Same-user + no token: gh runs directly, no sudo."""
        mock_run.return_value = _completed(
            stdout="https://github.com/open-ace/new-repo\n✓ Created repository"
        )
        gh = GitHubOps("/workspace/alice/project", system_account="alice")

        gh.create_repo("new-repo", private=True)

        cmd = mock_run.call_args.args[0]
        assert "sudo" not in cmd
        assert cmd[0] == "gh"


class TestCreateRepoTokenNoLeak:
    """Condition 4 (log hygiene): error messages must not contain the real token."""

    @patch.object(GitHubOps, "_get_env", return_value=_FAKE_ENV)
    @patch.object(GitHubOps, "_needs_sudo", return_value=True)
    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_error_message_excludes_token(self, mock_run, _needs, _env):
        """GitHubOpsError from a failed gh repo create must not embed GH_TOKEN."""
        mock_run.return_value = _completed(stdout="", returncode=4, stderr="gh auth login required")
        gh = GitHubOps("/workspace/alice/project", system_account="alice")

        try:
            gh.create_repo("new-repo", private=True)
            raise AssertionError("Should have raised GitHubOpsError")
        except GitHubOpsError as exc:
            assert "ghp_faketoken_2909" not in str(exc)


class TestGitOpsStillSudoUnderCrossUser:
    """Condition 9: Git operations that need local repo access must still use
    sudo (api_only on create_repo must not bleed into _run_git)."""

    @patch.object(GitHubOps, "_get_env", return_value=_FAKE_ENV)
    @patch.object(GitHubOps, "_needs_sudo", return_value=True)
    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_run_git_still_uses_sudo(self, mock_run, _needs, _env):
        """_run_git must still use sudo -u under cross-user, unlike the
        api_only gh path."""
        mock_run.return_value = _completed(stdout="")
        gh = GitHubOps("/workspace/alice/project", system_account="alice")

        gh._run_git(["status", "--porcelain"])

        cmd = mock_run.call_args.args[0]
        assert cmd[:3] == ["sudo", "-u", "alice"]

    @patch.object(GitHubOps, "_get_env", return_value=_FAKE_ENV)
    @patch.object(GitHubOps, "_needs_sudo", return_value=True)
    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_cross_user_clone_runs_from_existing_parent(self, mock_run, _needs, _env):
        """Clone targets do not exist yet, so sudo git must run from the parent."""
        mock_run.return_value = _completed(stdout="")
        project_path = "/workspace/alice/new-repo"
        gh = GitHubOps(project_path, system_account="alice")

        gh._run_git(["clone", "https://github.com/open-ace/new-repo.git", project_path])

        cmd = mock_run.call_args.args[0]
        assert "-C" in cmd
        assert cmd[cmd.index("-C") + 1] == "/workspace/alice"
        assert project_path in cmd

    @patch.object(GitHubOps, "_get_env", return_value=_FAKE_ENV)
    @patch.object(GitHubOps, "_needs_sudo", return_value=False)
    @patch("app.modules.workspace.autonomous.github_ops.subprocess.run")
    def test_same_user_clone_runs_from_existing_parent(self, mock_run, _needs, _env):
        """Same-user clone must not set subprocess cwd to the missing target."""
        mock_run.return_value = _completed(stdout="")
        project_path = "/workspace/alice/new-repo"
        gh = GitHubOps(project_path, system_account="alice")

        gh._run_git(["clone", "https://github.com/open-ace/new-repo.git", project_path])

        assert mock_run.call_args.kwargs.get("cwd") == "/workspace/alice"
        cmd = mock_run.call_args.args[0]
        assert "-C" not in cmd
        assert project_path in cmd
