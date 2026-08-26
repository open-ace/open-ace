from unittest.mock import MagicMock, patch

import pytest

import app.modules.workspace.autonomous.github_ops as gh_mod
from app.modules.workspace.autonomous.github_ops import GitHubOps

pytestmark = [
    pytest.mark.regression,
    pytest.mark.issue(2335),
    pytest.mark.usefixtures("_enable_acceptance_verification"),
]

BOT_ENV = {
    "GH_TOKEN": "ghp-bot",
    "GIT_AUTHOR_NAME": "Open ACE AI",
    "GIT_AUTHOR_EMAIL": "bot@open-ace.com",
    "GIT_COMMITTER_NAME": "Open ACE AI",
    "GIT_COMMITTER_EMAIL": "bot@open-ace.com",
}


def _gh():
    gh = GitHubOps("/srv/owners/repo", system_account="repoowner")
    gh._owner_repo_resolved = True
    gh._owner_repo = "open-ace/open-ace"
    gh._repo_host = None
    return gh


def test_close_issue_runs_as_service_user_with_bot_token():
    gh = _gh()
    run = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
    with (
        patch.object(gh, "_needs_sudo", return_value=True),
        patch.object(gh, "_verify_trusted_git_context"),
        patch("app.utils.config.get_ai_github_env", return_value=BOT_ENV),
        patch.object(gh_mod, "subprocess") as sub_mod,
    ):
        sub_mod.run = run
        gh.close_issue(42)
    cmd = run.call_args.args[0]
    assert cmd[0] == "gh", f"close_issue must not sudo (got {cmd[:3]})"
    assert "issue" in cmd and "close" in cmd and "42" in cmd
    assert run.call_args.kwargs["env"]["GH_TOKEN"] == "ghp-bot"


def test_reopen_issue_runs_as_service_user_with_bot_token():
    gh = _gh()
    run = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
    with (
        patch.object(gh, "_needs_sudo", return_value=True),
        patch.object(gh, "_verify_trusted_git_context"),
        patch("app.utils.config.get_ai_github_env", return_value=BOT_ENV),
        patch.object(gh_mod, "subprocess") as sub_mod,
    ):
        sub_mod.run = run
        gh.reopen_issue(42)
    cmd = run.call_args.args[0]
    assert cmd[0] == "gh"
    assert "issue" in cmd and "reopen" in cmd and "42" in cmd


def test_get_merge_commit_sha_returns_oid():
    gh = _gh()
    fake = MagicMock(returncode=0, stdout="abc123\n", stderr="")
    with patch.object(gh, "_run_gh", return_value=fake):
        assert gh.get_merge_commit_sha(99) == "abc123"


def test_get_merge_commit_sha_none_when_empty():
    gh = _gh()
    fake = MagicMock(returncode=1, stdout="", stderr="not merged")
    with patch.object(gh, "_run_gh", return_value=fake):
        assert gh.get_merge_commit_sha(99) is None
