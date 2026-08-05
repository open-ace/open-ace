"""PR creation must attribute to the configured AI bot account, not the repo
owner (#2340). Mirrors the #2339 comment fix: when a bot token is configured and
the command would otherwise sudo (cross-user), create_pr uses the REST API as the
service user (api_only) so GH_TOKEN reaches gh. Without a token it falls back to
`gh pr create` (owner identity) — unchanged.
"""

from unittest.mock import MagicMock, patch

import app.modules.workspace.autonomous.github_ops as gh_mod
from app.modules.workspace.autonomous.github_ops import GitHubOps

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
    gh._repo_slug = "open-ace/open-ace"
    gh._repo_host = None
    return gh


def test_create_pr_uses_rest_api_as_service_user_with_bot_token():
    gh = _gh()
    post_resp = MagicMock(
        returncode=0,
        stdout='{"number": 42, "html_url": "https://github.com/open-ace/open-ace/pull/42"}',
        stderr="",
    )
    view_resp = MagicMock(returncode=0, stdout='{"number": 42}', stderr="")
    run = MagicMock(side_effect=[post_resp, view_resp])
    with (
        patch.object(gh, "_needs_sudo", return_value=True),
        patch.object(gh, "_verify_trusted_git_context"),
        patch("app.utils.config.get_ai_github_env", return_value=BOT_ENV),
        patch.object(gh_mod, "subprocess") as sub_mod,
        patch.object(gh, "get_pr", return_value={"number": 42}) as mock_get_pr,
    ):
        sub_mod.run = run
        result = gh.create_pr(title="T", body="B", head="auto-dev/x", base="main")
    cmd = run.call_args.args[0]
    assert cmd[0] == "gh", f"create_pr must not sudo (got {cmd[:3]})"
    assert "api" in cmd
    assert "POST" in cmd
    assert "repos/open-ace/open-ace/pulls" in cmd
    assert run.call_args.kwargs["env"]["GH_TOKEN"] == "ghp-bot"
    # title/head/base/body carried as -f fields
    flat = " ".join(cmd)
    assert "title=T" in flat and "head=auto-dev/x" in flat and "base=main" in flat
    mock_get_pr.assert_called_once_with(42)
    assert result["number"] == 42


def test_create_pr_falls_back_to_gh_pr_create_without_token():
    gh = _gh()
    run = MagicMock(
        return_value=MagicMock(
            returncode=0, stdout="https://github.com/open-ace/open-ace/pull/42\n", stderr=""
        )
    )
    with (
        patch.object(gh, "_needs_sudo", return_value=True),
        patch.object(gh, "_verify_trusted_git_context"),
        patch("app.utils.config.get_ai_github_env", return_value=None),
        patch.object(gh_mod, "subprocess") as sub_mod,
        patch.object(gh, "get_pr", return_value={"number": 42}),
    ):
        sub_mod.run = run
        gh.create_pr(title="T", body="B", head="auto-dev/x")
    cmd = run.call_args.args[0]
    assert "pr" in cmd and "create" in cmd  # legacy fallback (owner identity)
    assert "api" not in cmd


def test_create_pr_rest_api_uses_bot_token_same_user():
    # Same-user (no sudo) with a bot token: api_only is a no-op for sudo, but the
    # REST path is still taken because the token is configured -> bot identity.
    gh = _gh()
    post_resp = MagicMock(returncode=0, stdout='{"number": 7}', stderr="")
    run = MagicMock(side_effect=[post_resp])
    with (
        patch.object(gh, "_needs_sudo", return_value=False),
        patch.object(gh, "_verify_trusted_git_context"),
        patch("app.utils.config.get_ai_github_env", return_value=BOT_ENV),
        patch.object(gh_mod, "subprocess") as sub_mod,
        patch.object(gh, "get_pr", return_value={"number": 7}),
    ):
        sub_mod.run = run
        gh.create_pr(title="T", body="", head="br")
    cmd = run.call_args.args[0]
    assert "api" in cmd and "POST" in cmd  # REST path even same-user when token set
