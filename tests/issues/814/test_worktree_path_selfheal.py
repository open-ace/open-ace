"""Tests for worktree path normalization + self-heal (#814).

Two bugs caused a worktree-strategy workflow to fail with
``Failed to detect Claude sidebar session JSONL``:

  1. ``_encode_project_path`` encoded the raw ``f"{repo}/../branch"`` path
     verbatim (``..`` intact), which never matched the dir Claude CLI
     actually wrote under ``~/.claude/projects`` (realpath-resolved).
  2. Retrying/resuming after the worktree dir was cleaned up launched the
     agent against an empty path because no phase checked whether the
     worktree/branch still existed.

Covers:
  - ``_encode_project_path`` resolves ``..`` and symlinks before encoding.
  - ``_ensure_worktree`` normalizes a stale (``..``) stored path when the
    worktree is still valid.
  - ``_ensure_worktree`` recreates a missing worktree, reusing the branch
    when it survives or creating fresh from origin/main.
  - ``_ensure_worktree`` is a no-op for non-worktree strategies.
"""

import os
import re
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner
from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

# ── _encode_project_path ─────────────────────────────────────────────────


class TestEncodeProjectPathNormalizes:
    def test_collapses_dotdot(self):
        # The exact shape that broke #814: a worktree path built as
        # f"{repo}/../branch-name" still carries ".." when stored.
        raw = "/Users/rhuang/workspace/open-ace/../auto-dev-1390d553"
        encoded = AutonomousAgentRunner._encode_project_path(raw)
        # realpath collapses the ".." — encoding must match what Claude CLI
        # writes, not the raw input.
        expected = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(raw))
        assert encoded == expected
        assert ".." not in encoded

    def test_resolves_symlinks(self, tmp_path):
        # /tmp on macOS is a symlink to /private/tmp — encoding must follow it
        # so it agrees with Claude's getcwd-based encoding.
        link_target = tmp_path / "real"
        link_target.mkdir()
        encoded = AutonomousAgentRunner._encode_project_path(str(link_target))
        assert encoded == re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(link_target)))

    def test_already_canonical_unchanged(self):
        path = "/Users/rhuang/workspace/open-ace"
        assert AutonomousAgentRunner._encode_project_path(path) == re.sub(
            r"[^A-Za-z0-9]", "-", os.path.realpath(path)
        )

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            (
                "/Users/open_ace/repo name/.worktrees/workflow.v1",
                "-Users-open-ace-repo-name--worktrees-workflow-v1",
            ),
            ("/Users/rhuang/open-ace", "-Users-rhuang-open-ace"),
        ],
    )
    def test_matches_claude_code_non_alphanumeric_contract(self, path, expected):
        # Fixed expected values intentionally do not reuse the implementation's
        # regex. Claude Code 2.1.201 embeds the equivalent shell rule:
        #   pwd | sed 's|[^a-zA-Z0-9]|-|g'
        assert AutonomousAgentRunner._encode_project_path(path) == expected

    def test_empty_string_returns_empty(self):
        assert AutonomousAgentRunner._encode_project_path("") == ""


# ── _ensure_worktree ─────────────────────────────────────────────────────


def _make_workflow(**overrides):
    base = {
        "workflow_id": "wf-814",
        "title": "gh issue 814",
        "cli_tool": "claude-code",
        "model": "",
        "branch_strategy": "worktree",
        "branch_name": "auto-dev/1390d553",
        "worktree_path": "/Users/rhuang/workspace/open-ace/../auto-dev-1390d553",
        "project_path": "/Users/rhuang/workspace/open-ace",
        "workspace_type": "local",
        "current_phase": "planning",
        "status": "planning",
    }
    base.update(overrides)
    return base


def _make_orchestrator(wf):
    with (
        patch("app.modules.workspace.autonomous.orchestrator.Database"),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
        ) as mock_repo_cls,
    ):
        mock_repo = MagicMock()
        mock_repo.get_workflow.return_value = wf
        mock_repo_cls.return_value = mock_repo
        o = AutonomousOrchestrator(wf["workflow_id"])
        o.repo = mock_repo
    o.emitter = MagicMock()
    o._update_workflow = MagicMock()
    o._create_milestone = MagicMock()
    return o


class TestEnsureWorktreeSelfHeal:
    def test_normalizes_stale_dotdot_path_when_valid(self, monkeypatch):
        # Worktree dir IS valid on disk — only the stored path is dirty.
        wf = _make_workflow()
        canonical = os.path.realpath(wf["worktree_path"])

        # _ensure_worktree now checks validity via main_gh.path_exists_as_user
        # (cross-user safe; replaced the old os.path.isfile probe, Issue #1395).
        fake_gh = MagicMock()
        fake_gh.path_exists_as_user.return_value = True
        monkeypatch.setattr(
            "app.modules.workspace.autonomous.orchestrator.GitHubOps",
            lambda _path, **_kw: fake_gh,
        )
        o = _make_orchestrator(wf)

        result = o._ensure_worktree(wf)

        assert result == canonical
        # Stale path was corrected in the DB.
        o._update_workflow.assert_called_once_with({"worktree_path": canonical})
        # No recreation milestone (worktree was already valid).
        o._create_milestone.assert_not_called()

    def test_no_update_when_path_already_canonical(self, monkeypatch):
        wf = _make_workflow(worktree_path=os.path.realpath("/srv/repo/../wt"))
        canonical = wf["worktree_path"]

        fake_gh = MagicMock()
        fake_gh.path_exists_as_user.return_value = True
        monkeypatch.setattr(
            "app.modules.workspace.autonomous.orchestrator.GitHubOps",
            lambda _path, **_kw: fake_gh,
        )
        o = _make_orchestrator(wf)

        result = o._ensure_worktree(wf)

        assert result == canonical
        o._update_workflow.assert_not_called()

    def test_recreates_missing_worktree_reusing_surviving_branch(self, monkeypatch):
        # Worktree dir is gone, but the branch still exists locally.
        wf = _make_workflow()
        canonical = os.path.realpath(wf["worktree_path"])

        o = _make_orchestrator(wf)
        fake_gh = MagicMock()
        # No .git marker → worktree is considered missing.
        fake_gh.path_exists_as_user.return_value = False
        # Local branch exists.
        local_check = MagicMock(returncode=0)
        remote_check = MagicMock(returncode=1)
        fake_gh._run_git.side_effect = [
            MagicMock(),  # fetch origin main
            local_check,  # show-ref refs/heads/<branch>
            remote_check,  # show-ref refs/remotes/origin/<branch>
            # worktree add <path> <existing-branch>  (NO -b)
            MagicMock(),
        ]
        monkeypatch.setattr(
            "app.modules.workspace.autonomous.orchestrator.GitHubOps",
            lambda _path, **_kw: fake_gh,
        )

        result = o._ensure_worktree(wf)

        assert result == canonical
        # Last git call reattaches the existing branch (no -b flag).
        last_call = fake_gh._run_git.call_args_list[-1].args[0]
        assert last_call == ["worktree", "add", canonical, wf["branch_name"]]
        o._create_milestone.assert_called_once()

    def test_recreates_missing_worktree_and_branch_from_origin(self, monkeypatch):
        # Both worktree and branch are gone — must create fresh.
        wf = _make_workflow()
        canonical = os.path.realpath(wf["worktree_path"])

        o = _make_orchestrator(wf)
        fake_gh = MagicMock()
        fake_gh.path_exists_as_user.return_value = False
        not_found = MagicMock(returncode=1)
        fake_gh._run_git.side_effect = [
            MagicMock(),  # fetch origin main
            not_found,  # local branch missing
            not_found,  # remote branch missing
            # worktree add -b <branch> <path> origin/main
            MagicMock(),
        ]
        monkeypatch.setattr(
            "app.modules.workspace.autonomous.orchestrator.GitHubOps",
            lambda _path, **_kw: fake_gh,
        )

        result = o._ensure_worktree(wf)

        assert result == canonical
        last_call = fake_gh._run_git.call_args_list[-1].args[0]
        assert last_call == [
            "worktree",
            "add",
            "-b",
            wf["branch_name"],
            canonical,
            "origin/main",
        ]

    def test_noop_for_new_branch_strategy(self, monkeypatch):
        wf = _make_workflow(branch_strategy="new-branch", worktree_path="")
        o = _make_orchestrator(wf)

        # For new-branch the worktree_path is empty; returns project_path.
        result = o._ensure_worktree(wf)

        assert result == wf["project_path"]
        o._update_workflow.assert_not_called()

    def test_empty_worktree_path_is_noop_not_recreate(self, monkeypatch):
        # Regression: a merge-phase retry has worktree_path="" (cleared
        # deliberately by merge cleanup). Self-heal must NOT treat that as
        # "dir gone, recreate" — that would try `git worktree add <main_repo>`
        # and turn a retried merge into a hard failure (#1088 review).
        wf = _make_workflow(
            worktree_path="",
            branch_name="auto-dev/1390d553",
            current_phase="merge",
            status="merging",
        )
        o = _make_orchestrator(wf)

        # GitHubOps must never be constructed (no recreation attempt).
        with patch("app.modules.workspace.autonomous.orchestrator.GitHubOps") as mock_gh_cls:
            result = o._ensure_worktree(wf)
            mock_gh_cls.assert_not_called()

        # Returns project_path (main repo), no DB write, no milestone.
        assert result == wf["project_path"]
        o._update_workflow.assert_not_called()
        o._create_milestone.assert_not_called()


class TestAdvanceCallsSelfHeal:
    """``advance()`` must self-heal the worktree before downstream phases."""

    def test_advance_calls_ensure_worktree_before_planning(self):
        wf = _make_workflow(current_phase="planning", status="planning")
        o = _make_orchestrator(wf)
        o._ensure_worktree = MagicMock(return_value=os.path.realpath(wf["worktree_path"]))
        o._do_planning = MagicMock()

        o.advance()

        o._ensure_worktree.assert_called_once()
        o._do_planning.assert_called_once()

    def test_advance_skips_selfheal_during_preparation(self):
        wf = _make_workflow(current_phase="preparation", status="preparing")
        o = _make_orchestrator(wf)
        o._ensure_worktree = MagicMock()
        o._do_preparation = MagicMock()

        o.advance()

        o._ensure_worktree.assert_not_called()
        o._do_preparation.assert_called_once()
