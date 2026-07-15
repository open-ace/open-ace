"""Tests for GitHubOps changed-file helpers (Issue #1647)."""

import subprocess
from unittest.mock import MagicMock

from app.modules.workspace.autonomous.github_ops import GitHubOps


def _completed(stdout: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["git"], returncode=0, stdout=stdout, stderr="")


def test_get_changed_files_uses_name_only_and_filters_empty_lines():
    gh = GitHubOps("/tmp/repo")
    gh._run_git = MagicMock(return_value=_completed("a.py\n\nfrontend/x.ts\n"))

    changed = gh.get_changed_files("main", "feature")

    gh._run_git.assert_called_once_with(["diff", "--name-only", "main", "feature"])
    assert changed == ["a.py", "frontend/x.ts"]


def test_get_commit_changed_files_uses_show_name_only_and_filters_empty_lines():
    gh = GitHubOps("/tmp/repo")
    gh._run_git = MagicMock(return_value=_completed("app/a.py\n\nfrontend/b.tsx\n"))

    changed = gh.get_commit_changed_files("abc1234")

    gh._run_git.assert_called_once_with(["show", "--name-only", "--format=", "abc1234"])
    assert changed == ["app/a.py", "frontend/b.tsx"]
