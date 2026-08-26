"""GitHub comments must post as the configured AI bot account, not the repo owner (#2339).

On multi-user deployments the ``open-ace`` service runs as user ``openace`` but each
workflow's repo lives under its owner's home (``system_account`` = owner). For pure
API ``gh`` subcommands (``issue``/``pr comment`` with ``-R owner/repo``) the
``sudo -u <owner>`` wrapper in ``_run_gh`` strips ``GH_TOKEN`` via sudo ``env_reset``
(sudoers ``env_keep`` keeps ``GIT_AUTHOR_*`` but not ``GH_TOKEN``), so ``gh``
authenticates as the owner and the comment is authored by the owner's personal
account — not the bot. See issue #2339.

Fix: comment posting runs as the *service* user with ``GH_TOKEN`` when a bot token is
configured (no ``sudo -u owner``), since these are pure API calls that need no local
repo access. Git ops and other ``gh`` commands keep the existing sudo path.

These tests pin the ``_run_gh`` cmd/env actually handed to ``subprocess.run``. The
behaviour change is observed as the *absence* of the ``sudo`` wrapper: when ``sudo``
is gone, ``GH_TOKEN`` reaches ``gh`` for real (today it is in the env but stripped at
the sudo boundary, invisible to the test). ``git`` ops and non-comment ``gh`` commands
must keep ``sudo`` (regression guard).
"""

from unittest.mock import MagicMock, patch

import pytest

import app.modules.workspace.autonomous.github_ops as gh_mod
from app.modules.workspace.autonomous.github_ops import GitHubOps

pytestmark = [pytest.mark.regression, pytest.mark.issue(2339)]

BOT_ENV = {
    "GH_TOKEN": "ghp-bot-token-2339",
    "GIT_AUTHOR_NAME": "Open ACE AI",
    "GIT_AUTHOR_EMAIL": "bot@open-ace.com",
    "GIT_COMMITTER_NAME": "Open ACE AI",
    "GIT_COMMITTER_EMAIL": "bot@open-ace.com",
}


def _make_gh(
    *,
    owner_repo: str = "open-ace/open-ace",
    repo_host: str | None = None,
    system_account: str = "repoowner",
) -> GitHubOps:
    """A GitHubOps wired for a cross-user workflow with owner/repo pre-resolved."""
    gh = GitHubOps("/srv/owners/repo", system_account=system_account)
    # Pre-resolve owner/repo so no _run_git remote read is needed (it is a separate
    # sudo read unaffected by this fix; we isolate the comment-posting path here).
    gh._owner_repo_resolved = True
    gh._owner_repo = owner_repo
    gh._repo_host = repo_host
    return gh


def _patch_run_capture() -> MagicMock:
    """Patch subprocess.run inside github_ops, returning a completed-process mock."""
    run = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
    return run


# ── comment posting: bot token + cross-user ⇒ runs as service user (no sudo) ──


def test_issue_comment_runs_as_service_user_with_bot_token_cross_user():
    """add_issue_comment under cross-user + bot token must NOT sudo to the owner;
    gh runs as the service user so GH_TOKEN reaches it and the post is the bot."""
    gh = _make_gh()
    run = _patch_run_capture()
    with (
        patch.object(gh, "_needs_sudo", return_value=True),
        patch.object(gh, "_verify_trusted_git_context"),
        patch("app.utils.config.get_ai_github_env", return_value=BOT_ENV),
        patch.object(gh_mod, "subprocess") as sub_mod,
    ):
        sub_mod.run = run
        gh.add_issue_comment(42, "body")
    cmd = run.call_args.args[0]
    env = run.call_args.kwargs["env"]
    assert cmd[0] == "gh", f"must not sudo (got {cmd[:3]})"
    assert "sudo" not in cmd
    assert "-R" in cmd and "open-ace/open-ace" in cmd
    assert env["GH_TOKEN"] == "ghp-bot-token-2339"
    assert env["HOME"], "gh needs HOME to manage state; must be preserved"
    assert "cwd" not in run.call_args.kwargs or not run.call_args.kwargs.get("cwd")


def test_pr_comment_runs_as_service_user_with_bot_token_cross_user():
    gh = _make_gh()
    run = _patch_run_capture()
    with (
        patch.object(gh, "_needs_sudo", return_value=True),
        patch.object(gh, "_verify_trusted_git_context"),
        patch("app.utils.config.get_ai_github_env", return_value=BOT_ENV),
        patch.object(gh_mod, "subprocess") as sub_mod,
    ):
        sub_mod.run = run
        gh.add_pr_comment(7, "body")
    cmd = run.call_args.args[0]
    assert cmd[0] == "gh"
    assert "sudo" not in cmd
    assert "-R" in cmd
    assert run.call_args.kwargs["env"]["GH_TOKEN"] == "ghp-bot-token-2339"


# ── regression guards: non-comment gh + git ops keep the sudo cross-user path ──


def test_non_comment_gh_command_still_sudos_cross_user():
    """A non-comment gh command (default api_only=False) under cross-user keeps the
    sudo -u owner wrapper — the fix must not widen beyond comment posting."""
    gh = _make_gh()
    run = _patch_run_capture()
    with (
        patch.object(gh, "_needs_sudo", return_value=True),
        patch.object(gh, "_verify_trusted_git_context"),
        patch("app.utils.config.get_ai_github_env", return_value=BOT_ENV),
        patch.object(gh_mod, "subprocess") as sub_mod,
    ):
        sub_mod.run = run
        gh._run_gh(["pr", "view", "1", "--json", "number"])
    cmd = run.call_args.args[0]
    assert cmd[:3] == ["sudo", "-u", "repoowner"], f"non-comment must keep sudo (got {cmd[:4]})"
    # Cross-user gh now runs through the root-owned validating wrapper (#2650),
    # never the bare ``gh`` binary.
    assert cmd[3] == "/usr/local/bin/openace-gh"


def test_comment_degrades_to_sudo_when_no_bot_token_cross_user():
    """No bot token configured ⇒ comment posting degrades to today's behaviour
    (sudo as owner, post as owner). The api-only path requires a token to run as
    the service user; without one, openace has no gh auth and must sudo."""
    gh = _make_gh()
    run = _patch_run_capture()
    with (
        patch.object(gh, "_needs_sudo", return_value=True),
        patch.object(gh, "_verify_trusted_git_context"),
        patch("app.utils.config.get_ai_github_env", return_value=None),
        patch.object(gh_mod, "subprocess") as sub_mod,
    ):
        sub_mod.run = run
        gh.add_issue_comment(42, "body")
    cmd = run.call_args.args[0]
    assert cmd[:3] == ["sudo", "-u", "repoowner"]


# ── same-user path: unchanged (service already runs as repo owner) ────────────


def test_comment_same_user_runs_directly_with_bot_token():
    """Same-user (_needs_sudo False): gh runs directly, GH_TOKEN honoured — both
    before and after the fix (the bug only manifests in the cross-user sudo path)."""
    gh = _make_gh()
    run = _patch_run_capture()
    with (
        patch.object(gh, "_needs_sudo", return_value=False),
        patch.object(gh, "_verify_trusted_git_context"),
        patch("app.utils.config.get_ai_github_env", return_value=BOT_ENV),
        patch.object(gh_mod, "subprocess") as sub_mod,
    ):
        sub_mod.run = run
        gh.add_issue_comment(42, "body")
    cmd = run.call_args.args[0]
    assert cmd[0] == "gh"
    assert "sudo" not in cmd
    assert run.call_args.kwargs["env"]["GH_TOKEN"] == "ghp-bot-token-2339"


# ── no local git access on the comment path once owner/repo is resolved ───────


def test_api_only_path_does_not_run_git_when_owner_repo_cached():
    """With owner/repo already resolved, a comment post must not touch local git
    (no _run_git) — the API call carries -R and needs no repo file access."""
    gh = _make_gh()
    run = _patch_run_capture()
    with (
        patch.object(gh, "_needs_sudo", return_value=True),
        patch.object(gh, "_verify_trusted_git_context"),
        patch("app.utils.config.get_ai_github_env", return_value=BOT_ENV),
        patch.object(gh, "_run_git") as run_git,
        patch.object(gh_mod, "subprocess") as sub_mod,
    ):
        sub_mod.run = run
        gh.add_pr_comment(7, "body")
    run_git.assert_not_called()
    assert run.call_args.args[0][0] == "gh"  # no sudo


# ── GHES: host-prefixed -R is preserved on the service-user path ──────────────


def test_api_only_ghes_host_passes_host_prefixed_repo():
    """A GHES host (non-github.com) must still target the right server: -R HOST/owner/repo."""
    gh = _make_gh(owner_repo="gh.example.com/open-ace/open-ace", repo_host="gh.example.com")
    run = _patch_run_capture()
    with (
        patch.object(gh, "_needs_sudo", return_value=True),
        patch.object(gh, "_verify_trusted_git_context"),
        patch("app.utils.config.get_ai_github_env", return_value=BOT_ENV),
        patch.object(gh_mod, "subprocess") as sub_mod,
    ):
        sub_mod.run = run
        gh.add_issue_comment(42, "body")
    cmd = run.call_args.args[0]
    assert cmd[0] == "gh"
    assert "-R" in cmd and "gh.example.com/open-ace/open-ace" in cmd
