"""Security tests for the openace-gh sudo wrapper."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WRAPPER_PATH = PROJECT_ROOT / "scripts" / "openace-gh.py"

FIXED_ISSUE_COMMENT_FILTER = (
    ".[] | {id, body, createdAt: .created_at, author: {login: .user.login}}"
)
FIXED_PAGINATED_ISSUE_COMMENT_FILTER = ".[] | {id, body, created_at, user: .user.login}"
FIXED_CLOSURE_FILTER = (
    '.[] | select(.event == "closed") | {closed_at: .created_at, closer_login: .actor.login}'
)
FIXED_REVIEW_COMMENT_FILTER = ".[] | {id, path, body, line, created_at, user: .user.login}"


def _load_wrapper():
    spec = importlib.util.spec_from_file_location("openace_gh_wrapper", WRAPPER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def wrapper():
    return _load_wrapper()


def make_config(**overrides):
    config = {
        "allow_admin_merge": False,
        "workflow_branch_patterns": [r"^(auto-dev|review-fix|ci-repair|fork)/[A-Za-z0-9._/-]+$"],
        "fixed_jq_filters": {
            "issue_comment": FIXED_ISSUE_COMMENT_FILTER,
            "paginated_issue_comment": FIXED_PAGINATED_ISSUE_COMMENT_FILTER,
            "closure": FIXED_CLOSURE_FILTER,
            "review_comment": FIXED_REVIEW_COMMENT_FILTER,
        },
    }
    config.update(overrides)
    return config


def assert_allowed(wrapper, argv, **config_overrides):
    result = wrapper.validate_gh_argv(argv, config=make_config(**config_overrides))
    assert result.allowed, result.reason


def assert_denied(wrapper, argv, **config_overrides):
    result = wrapper.validate_gh_argv(argv, config=make_config(**config_overrides))
    assert not result.allowed


def test_version_and_help_are_only_standalone_passthrough(wrapper):
    assert_allowed(wrapper, ["--version"])
    assert_allowed(wrapper, ["--help"])
    assert_denied(wrapper, ["api", "user", "--version"])
    assert_denied(wrapper, ["-R", "owner/repo", "pr", "view", "1", "--help"])


def test_main_denies_before_running_gh(wrapper, monkeypatch):
    calls = []

    monkeypatch.setattr(
        wrapper,
        "load_gh_config",
        lambda: wrapper.ValidationResult(True, config=make_config()),
    )
    monkeypatch.setattr(wrapper.subprocess, "run", lambda *args, **kwargs: calls.append(args))

    assert wrapper.main(["api", "--method", "DELETE", "repos/owner/repo"]) == 126
    assert calls == []


def test_main_runs_absolute_gh_binary_after_validation(wrapper, monkeypatch):
    calls = []

    monkeypatch.setattr(
        wrapper,
        "load_gh_config",
        lambda: wrapper.ValidationResult(True, config=make_config()),
    )
    monkeypatch.setattr(wrapper, "_real_gh_path", lambda config: "/usr/bin/gh")
    monkeypatch.setattr(
        wrapper.subprocess,
        "run",
        lambda args: calls.append(args) or type("Result", (), {"returncode": 0})(),
    )

    assert wrapper.main(["api", "user", "--jq", ".login"]) == 0
    assert calls == [["/usr/bin/gh", "api", "user", "--jq", ".login"]]


def test_dangerous_gh_shapes_are_denied(wrapper):
    assert_denied(wrapper, ["repo", "delete", "owner/repo"])
    assert_denied(wrapper, ["api", "-X", "DELETE", "repos/owner/repo"])
    assert_denied(wrapper, ["api", "--method", "DELETE", "repos/owner/repo"])
    assert_denied(wrapper, ["pr", "view", "1", "--web"])
    assert_denied(
        wrapper,
        [
            "pr",
            "list",
            "--head",
            "main",
            "--base",
            "main",
            "--state",
            "open",
            "--json",
            "number,url,title",
            "--limit",
            "1",
        ],
    )
    assert_denied(wrapper, ["api", "repos/owner/repo/issues/1/comments", "--jq", ".[]"])


def test_admin_merge_is_config_gated(wrapper):
    assert_denied(wrapper, ["pr", "merge", "1", "--admin"])
    assert_denied(wrapper, ["pr", "merge", "1", "--auto"])
    assert_denied(wrapper, ["pr", "merge", "1", "--admin"], allow_admin_merge=True)
    assert_allowed(wrapper, ["pr", "merge", "1", "--merge", "--admin"], allow_admin_merge=True)


@pytest.mark.parametrize(
    "argv",
    [
        ["repo", "view", "--json", "nameWithOwner"],
        ["repo", "create", "new-repo", "--private", "--description", "desc"],
        [
            "-R",
            "owner/repo",
            "issue",
            "create",
            "--title",
            "title",
            "--body",
            "body",
            "--label",
            "bug",
            "--repo",
            "owner/repo",
        ],
        ["-R", "owner/repo", "issue", "comment", "1", "--body", "body"],
        ["-R", "owner/repo", "issue", "close", "1"],
        ["-R", "owner/repo", "issue", "reopen", "1"],
        ["-R", "owner/repo", "issue", "edit", "1", "--title", "title"],
        ["-R", "owner/repo", "issue", "edit", "1", "--body", "- bullet"],
        [
            "-R",
            "owner/repo",
            "issue",
            "view",
            "1",
            "--json",
            "number,title,body,url,state,labels,comments",
        ],
        ["-R", "owner/repo", "issue", "view", "1", "--comments", "--json", "comments"],
        ["-R", "owner/repo", "issue", "view", "1", "--json", "state,closedAt"],
        ["-R", "owner/repo", "pr", "close", "1"],
        ["-R", "owner/repo", "pr", "reopen", "1"],
        ["-R", "owner/repo", "pr", "comment", "1", "--body", "body"],
        [
            "-R",
            "owner/repo",
            "pr",
            "create",
            "--title",
            "title",
            "--body",
            "body",
            "--base",
            "main",
            "--head",
            "auto-dev/abc",
            "--draft",
        ],
        [
            "-R",
            "owner/repo",
            "pr",
            "create",
            "--title",
            "title",
            "--body",
            "body",
            "--base",
            "main",
            "--head",
            "fork/from-12345678",
            "--draft",
        ],
        [
            "-R",
            "owner/repo",
            "pr",
            "list",
            "--head",
            "auto-dev/abc",
            "--base",
            "main",
            "--state",
            "open",
            "--json",
            "number,url,title",
            "--limit",
            "1",
        ],
        [
            "-R",
            "owner/repo",
            "pr",
            "list",
            "--head",
            "fork/from-12345678",
            "--base",
            "main",
            "--state",
            "open",
            "--json",
            "number,url,title",
            "--limit",
            "1",
        ],
        [
            "-R",
            "owner/repo",
            "pr",
            "view",
            "1",
            "--json",
            "number,title,body,url,state,headRefName,baseRefName,additions,deletions,changedFiles,commits",
        ],
        ["-R", "owner/repo", "pr", "view", "1", "--json", "commits"],
        [
            "-R",
            "owner/repo",
            "pr",
            "view",
            "1",
            "--json",
            "mergeCommit",
            "--jq",
            ".mergeCommit.oid",
        ],
        ["-R", "owner/repo", "pr", "checks", "1", "--json", "name,state,bucket,link"],
        ["-R", "owner/repo", "pr", "diff", "1"],
        ["-R", "owner/repo", "pr", "merge", "1", "--merge", "--auto"],
        [
            "-R",
            "owner/repo",
            "run",
            "list",
            "--commit",
            "a" * 40,
            "--json",
            "databaseId,name",
            "--limit",
            "30",
        ],
        ["-R", "owner/repo", "run", "view", "123", "--log-failed", "--allow-escape-sequences"],
        [
            "-R",
            "owner/repo",
            "run",
            "view",
            "123",
            "--job",
            "456",
            "--log-failed",
            "--allow-escape-sequences",
        ],
        ["api", "user", "--jq", ".login"],
        [
            "api",
            "--method",
            "POST",
            "repos/owner/repo/pulls",
            "-f",
            "title=title",
            "-f",
            "base=main",
            "-f",
            "body=body",
            "-f",
            "head=auto-dev/abc",
        ],
        [
            "api",
            "--method",
            "POST",
            "repos/owner/repo/pulls",
            "-f",
            "title=title",
            "-f",
            "base=main",
            "-f",
            "body=body",
            "-f",
            "head=fork/from-12345678",
        ],
        ["api", "repos/owner/repo/pulls/1"],
        ["api", "repos/owner/repo/commits/" + "a" * 40],
        ["api", "repos/owner/repo/branches/main/protection"],
        ["api", "--paginate", "repos/owner/repo/rules/branches/main"],
        ["api", "repos/owner/repo/issues/1/comments", "--jq", FIXED_ISSUE_COMMENT_FILTER],
        [
            "api",
            "--paginate",
            "repos/owner/repo/issues/1/comments",
            "--jq",
            FIXED_ISSUE_COMMENT_FILTER,
        ],
        [
            "api",
            "repos/owner/repo/issues/1/comments",
            "--jq",
            FIXED_PAGINATED_ISSUE_COMMENT_FILTER,
        ],
        ["api", "--paginate", "repos/owner/repo/issues/1/timeline", "--jq", FIXED_CLOSURE_FILTER],
        ["api", "repos/owner/repo/pulls/1/comments", "--jq", FIXED_REVIEW_COMMENT_FILTER],
        ["api", "repos/owner/repo/commits/" + "a" * 40 + "/status"],
        ["api", "repos/owner/repo/commits/" + "a" * 40 + "/check-runs"],
        ["api", "repos/owner/repo/actions/jobs/456/logs", "--allow-escape-sequences"],
        ["api", "--hostname", "gh.example.com", "repos/owner/repo/actions/jobs/456/logs"],
    ],
)
def test_current_github_ops_gh_shapes_are_allowed(wrapper, argv):
    assert_allowed(wrapper, argv)


def test_missing_and_writable_config_files_fail_closed(wrapper, tmp_path):
    assert not wrapper.load_gh_config(tmp_path / "missing.json", require_root_owner=False).allowed

    path = tmp_path / "gh-wrapper.json"
    path.write_text("{}", encoding="utf-8")
    path.chmod(0o664)
    assert not wrapper.load_gh_config(path, require_root_owner=False).allowed

    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    assert not wrapper.load_gh_config(link, require_root_owner=False).allowed
