"""Security tests for the openace-git sudo wrapper."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WRAPPER_PATH = PROJECT_ROOT / "scripts" / "openace-git.py"


def _load_wrapper():
    spec = importlib.util.spec_from_file_location("openace_git_wrapper", WRAPPER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def wrapper():
    return _load_wrapper()


def make_config(**overrides):
    config = {
        "allowed_path_roots": ["/tmp", "/private/tmp", "/home", "/workspace", "/srv"],
        "workflow_branch_patterns": [r"^(auto-dev|review-fix|ci-repair|fork)/[A-Za-z0-9._/-]+$"],
        "require_owned_paths": False,
        "safe_configs": {
            "core.hooksPath": ["/dev/null"],
            "core.fsmonitor": ["false"],
            "safe.directory": "path",
        },
    }
    config.update(overrides)
    return config


def sudo_git_prefix(path: str = "/tmp/repo") -> list[str]:
    return [
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=false",
        "-c",
        f"safe.directory={path}",
        "-C",
        path,
    ]


def assert_allowed(wrapper, argv):
    result = wrapper.validate_git_argv(argv, config=make_config())
    assert result.allowed, result.reason


def assert_denied(wrapper, argv):
    result = wrapper.validate_git_argv(argv, config=make_config())
    assert not result.allowed


def test_version_and_help_are_only_standalone_passthrough(wrapper):
    assert_allowed(wrapper, ["--version"])
    assert_allowed(wrapper, ["--help"])
    assert_denied(wrapper, ["-c", "alias.pwn=!id", "pwn", "--version"])
    assert_denied(wrapper, ["status", "--help"])


def test_main_denies_before_running_git(wrapper, monkeypatch):
    calls = []

    monkeypatch.setattr(
        wrapper,
        "load_git_config",
        lambda: wrapper.ValidationResult(True, config=make_config()),
    )
    monkeypatch.setattr(wrapper.subprocess, "run", lambda *args, **kwargs: calls.append(args))

    assert wrapper.main(["-c", "alias.pwn=!id", "pwn", "--version"]) == 126
    assert calls == []


def test_main_runs_absolute_git_binary_after_validation(wrapper, monkeypatch):
    calls = []

    monkeypatch.setattr(
        wrapper,
        "load_git_config",
        lambda: wrapper.ValidationResult(True, config=make_config()),
    )
    monkeypatch.setattr(wrapper, "_real_git_path", lambda config: "/usr/bin/git")
    monkeypatch.setattr(
        wrapper.subprocess,
        "run",
        lambda args: calls.append(args) or type("Result", (), {"returncode": 0})(),
    )

    assert wrapper.main(sudo_git_prefix() + ["status", "--porcelain"]) == 0
    assert calls == [["/usr/bin/git", *sudo_git_prefix(), "status", "--porcelain"]]


def test_forbidden_global_options_and_configs_are_denied(wrapper):
    assert_denied(wrapper, ["--exec-path=/tmp/x", "status"])
    assert_denied(wrapper, ["-c", "protocol.ext.allow=always", "fetch", "origin"])
    assert_denied(wrapper, ["-c", "core.fsmonitor=true", "status"])
    assert_denied(wrapper, ["-c", "core.sshCommand=sh", "fetch", "origin"])


def test_git_commands_require_hardening_globals(wrapper):
    assert_denied(wrapper, ["-C", "/tmp/repo", "commit", "-m", "x"])
    assert_denied(
        wrapper,
        [
            "-c",
            "core.hooksPath=/dev/null",
            "-C",
            "/tmp/repo",
            "status",
            "--porcelain",
        ],
    )
    assert_denied(
        wrapper,
        [
            "-c",
            "core.fsmonitor=false",
            "-C",
            "/tmp/repo",
            "status",
            "--porcelain",
        ],
    )


def test_git_context_paths_must_be_owned_when_config_requires_it(wrapper, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    config = make_config(require_owned_paths=True, allowed_path_roots=[str(tmp_path)])

    result = wrapper.validate_git_argv(
        sudo_git_prefix(str(repo)) + ["status", "--porcelain"], config=config
    )
    assert result.allowed, result.reason

    real_stat = wrapper.os.stat

    def fake_stat(path, *args, **kwargs):
        stat_result = real_stat(path, *args, **kwargs)
        if str(path) == str(repo):
            values = list(stat_result)
            values[4] = wrapper.os.getuid() + 1
            return type(stat_result)(values)
        return stat_result

    monkeypatch.setattr(wrapper.os, "stat", fake_stat)
    result = wrapper.validate_git_argv(
        sudo_git_prefix(str(repo)) + ["status", "--porcelain"], config=config
    )
    assert not result.allowed


def test_safe_directory_values_do_not_require_target_user_ownership(wrapper, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    config = make_config(require_owned_paths=True, allowed_path_roots=[str(tmp_path)])

    real_stat = wrapper.os.stat

    def fake_stat(path, *args, **kwargs):
        stat_result = real_stat(path, *args, **kwargs)
        if str(path) == str(repo):
            values = list(stat_result)
            values[4] = wrapper.os.getuid() + 1
            return type(stat_result)(values)
        return stat_result

    monkeypatch.setattr(wrapper.os, "stat", fake_stat)
    result = wrapper.validate_git_argv(
        [
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-c",
            f"safe.directory={repo}",
            "status",
            "--porcelain",
        ],
        config=config,
    )

    assert result.allowed, result.reason


def test_mutating_branches_are_limited_to_workflow_branches(wrapper):
    assert_denied(wrapper, ["push", "origin", "main", "--force"])
    assert_denied(wrapper, ["push", "origin", "main", "--force-with-lease"])
    assert_denied(wrapper, ["branch", "-D", "main"])
    assert_denied(wrapper, ["checkout", "-b", "main"])
    assert_allowed(
        wrapper, sudo_git_prefix() + ["push", "origin", "auto-dev/abc", "--force-with-lease"]
    )


def test_relative_path_operands_cannot_escape_worktree(wrapper):
    assert_denied(wrapper, ["reset", "-q", "HEAD", "--", "../secret"])
    assert_denied(
        wrapper,
        [
            "grep",
            "--no-index",
            "-l",
            "-I",
            "-E",
            "-e",
            r"^<{7,}( |$)",
            "-e",
            r"^={7,}$",
            "-e",
            r"^>{7,}( |$)",
            "--",
            "a/../b",
        ],
    )


def test_show_tree_paths_allow_real_filenames_but_not_escapes(wrapper):
    assert_allowed(wrapper, sudo_git_prefix() + ["show", "HEAD:docs/file name.md"])
    assert_allowed(wrapper, sudo_git_prefix() + ["show", "HEAD:src/package[data].py"])
    assert_denied(wrapper, sudo_git_prefix() + ["show", "HEAD:../secret"])
    assert_denied(wrapper, sudo_git_prefix() + ["show", "HEAD:/tmp/secret"])


@pytest.mark.parametrize(
    "argv",
    [
        ["remote", "get-url", "origin"],
        ["remote", "add", "origin", "https://github.com/open-ace/open-ace.git"],
        ["push", "-u", "origin", "auto-dev/abc"],
        ["push", "origin", "--delete", "auto-dev/abc"],
        ["push", "origin", "auto-dev/abc"],
        ["push", "origin", "auto-dev/abc", "--force-with-lease"],
        ["push", "origin", "fork/from-12345678", "--force-with-lease"],
        ["branch", "--show-current"],
        ["branch", "-D", "auto-dev/abc"],
        ["rev-parse", "HEAD"],
        ["rev-parse", "origin/main"],
        ["rev-parse", "main"],
        ["rev-parse", "auto-dev/abc"],
        ["rev-parse", "a" * 40 + "^2"],
        ["rev-parse", "--verify", "origin/main^{commit}"],
        ["rev-parse", "--show-toplevel"],
        ["rev-parse", "--absolute-git-dir"],
        ["rev-parse", "--path-format=absolute", "--git-common-dir"],
        ["checkout", "origin/main"],
        ["checkout", "--detach", "HEAD"],
        ["checkout", "-b", "auto-dev/abc"],
        ["checkout", "-b", "auto-dev/abc", "origin/main"],
        ["show-ref", "--verify", "--quiet", "refs/heads/auto-dev/abc"],
        ["show-ref", "--verify", "--quiet", "refs/remotes/origin/auto-dev/abc"],
        ["show-ref", "--verify", "--quiet", "refs/heads/fork/from-12345678"],
        ["show-ref", "--verify", "--quiet", "refs/remotes/origin/fork/from-12345678"],
        ["reset", "--hard", "HEAD"],
        ["reset", "--hard", "origin/main"],
        ["reset", "-q", "HEAD", "--", "app/file.py"],
        ["ls-remote", "origin", "main"],
        ["worktree", "add", "-b", "auto-dev/abc", "/tmp/wt", "origin/main"],
        ["worktree", "add", "/tmp/wt", "auto-dev/abc"],
        ["worktree", "add", "-b", "fork/from-12345678", "/tmp/wt", "origin/main"],
        ["worktree", "add", "/tmp/wt", "fork/from-12345678"],
        ["worktree", "add", "--detach", "/tmp/wt", "a" * 40],
        ["worktree", "remove", "/tmp/wt", "--force"],
        ["worktree", "list", "--porcelain", "-z"],
        ["worktree", "prune"],
        ["symbolic-ref", "--short", "HEAD"],
        ["cat-file", "-e", "abc123^{commit}"],
        ["cat-file", "-e", "abc123"],
        ["fetch", "--no-tags", "origin", "abc123"],
        ["fetch", "origin", "main"],
        ["diff", "HEAD~1", "HEAD"],
        ["diff", "--numstat", "HEAD~1", "HEAD"],
        ["diff", "--numstat", "HEAD"],
        ["diff", "--name-only", "HEAD~1", "HEAD"],
        ["diff", "-M", "--name-status", "HEAD~1", "HEAD"],
        ["diff", "--name-only", "--diff-filter=U"],
        ["diff", "--name-only"],
        ["diff", "--cached", "--name-only"],
        ["rev-list", "--count", "HEAD~1..HEAD"],
        ["log", "--full-history", "--format=%H", "HEAD~1..HEAD", "--", "app/file.py"],
        ["log", "--oneline", "origin/main..HEAD"],
        ["show", "--format=", "HEAD"],
        ["show", "--numstat", "--format=", "HEAD"],
        ["show", "--name-only", "--format=", "HEAD"],
        ["show", "HEAD:pyproject.toml"],
        ["show", "HEAD:docs/file name.md"],
        ["merge", "a" * 40],
        ["merge-base", "origin/main", "auto-dev/abc"],
        ["merge-base", "--is-ancestor", "origin/main", "auto-dev/abc"],
        ["clone", "https://github.com/open-ace/open-ace.git", "/tmp/project"],
        ["status", "--porcelain"],
        ["ls-files", "--others", "--exclude-standard"],
        ["ls-files", "--stage", "-z"],
        [
            "grep",
            "--no-index",
            "-l",
            "-I",
            "-E",
            "-e",
            r"^<{7,}( |$)",
            "-e",
            r"^={7,}$",
            "-e",
            r"^>{7,}( |$)",
            "--",
            "app/file.py",
        ],
        ["add", "-A"],
        ["rm", "-r", "--cached", "--ignore-unmatch", ".worktrees"],
        ["commit", "-m", "message"],
        ["commit", "-m", "message", "--no-verify"],
        ["init"],
    ],
)
def test_current_github_ops_git_shapes_are_allowed(wrapper, argv):
    assert_allowed(wrapper, sudo_git_prefix() + argv)


def test_missing_and_writable_config_files_fail_closed(wrapper, tmp_path):
    assert not wrapper.load_git_config(tmp_path / "missing.json", require_root_owner=False).allowed

    path = tmp_path / "git-wrapper.json"
    path.write_text("{}", encoding="utf-8")
    path.chmod(0o664)
    assert not wrapper.load_git_config(path, require_root_owner=False).allowed

    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    assert not wrapper.load_git_config(link, require_root_owner=False).allowed
