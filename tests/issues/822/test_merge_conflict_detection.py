"""Tests for merge conflict detection + branch-policy auto-merge (#822).

Two bugs caused worktrees in the 807-845 batch to fail at the merge phase:

1. ``_resolve_merge_conflicts`` checked only ``stderr`` for the ``CONFLICT``
   marker, but ``git merge`` writes conflict summaries to **stdout** (stderr is
   empty). A real conflict was misclassified as a "non-conflict" failure and
   the AI conflict resolver was never invoked.

2. ``_do_merge`` treated every ``GitHubOpsError`` from ``merge_pr`` as a
   conflict and dived into local resolution. But "base branch policy
   prohibits the merge" (CI pending / review required) is not a conflict — it
   needs ``--auto`` so GitHub merges asynchronously once requirements pass.
"""

from unittest.mock import MagicMock, patch

from app.modules.workspace.autonomous.github_ops import GitHubOps, GitHubOpsError
from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator


def _make_workflow(**overrides):
    base = {
        "workflow_id": "wf-822",
        "title": "gh issue 807-845 (#822)",
        "cli_tool": "claude-code",
        "model": "",
        "branch_strategy": "worktree",
        "branch_name": "auto-dev/fc82f22a",
        "worktree_path": "",
        "project_path": "/srv/repo",
        "workspace_type": "local",
        "current_phase": "merge",
        "status": "merging",
        "github_pr_number": 1103,
        "github_issue_number": 822,
        "dev_round": 1,
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
        mock_repo.create_milestone.return_value = {
            "milestone_id": "ms-1",
            "workflow_id": wf["workflow_id"],
        }
        mock_repo.create_event.return_value = {"id": 1}
        mock_repo.update_workflow.return_value = wf
        mock_repo_cls.return_value = mock_repo
        o = AutonomousOrchestrator(wf["workflow_id"])
        o.repo = mock_repo
    o.emitter = MagicMock()
    o._update_workflow = MagicMock()
    o._create_milestone = MagicMock(return_value={"milestone_id": "ms-1"})
    o._accumulate_tokens = MagicMock()
    o._write_phase_usage = MagicMock()
    return o, mock_repo


# ── Bug 1: conflict detection must check stdout ──────────────────────────


class TestResolveMergeConflictsStdoutConflict:
    """``git merge`` writes CONFLICT to stdout, not stderr (#822)."""

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_conflict_in_stdout_invokes_resolver(self, mock_gh_cls):
        """A conflict reported on stdout must NOT be treated as a hard failure."""
        o, _ = _make_orchestrator(_make_workflow())
        mock_gh = MagicMock()
        mock_gh_cls.return_value = mock_gh

        # Model _run_git so the *second* merge (check=False) returns CONFLICT
        # on stdout with empty stderr — the exact shape that broke #822.
        def run_git(args, check=True):
            # Only the "merge origin/main" call matters; merge --abort and
            # other ops are no-ops.
            if args[:2] == ["merge", "origin/main"] and check:
                raise GitHubOpsError("git merge origin/main failed")
            if args[:2] == ["merge", "origin/main"] and not check:
                return MagicMock(
                    returncode=1,
                    stdout=(
                        "CONFLICT (content): Merge conflict in " "app/services/auth_service.py\n"
                    ),
                    stderr="",
                )
            return MagicMock()

        mock_gh._run_git = MagicMock(side_effect=run_git)
        o._gh = mock_gh

        # Stub the AI conflict resolver so we verify it IS reached.
        o._run_agent = MagicMock()
        o._resolve_session_line = MagicMock(return_value=("sess", None, False))
        o._link_session_to_current_milestone = MagicMock()

        o._resolve_merge_conflicts(mock_gh, "auto-dev/fc82f22a", 1103)

        # The conflict was detected on stdout → resolver was invoked, not a
        # hard failure. _run_agent was called with the conflict prompt.
        assert o._run_agent.call_count >= 1
        prompt = o._run_agent.call_args.kwargs.get("prompt", "")
        assert "冲突" in prompt or "conflict" in prompt.lower()

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_non_conflict_failure_still_raises(self, mock_gh_cls):
        """A real non-conflict error (no CONFLICT anywhere) must still raise."""
        o, _ = _make_orchestrator(_make_workflow())
        mock_gh = MagicMock()
        mock_gh_cls.return_value = mock_gh

        def run_git(args, check=True):
            if args[:2] == ["merge", "origin/main"] and check:
                raise GitHubOpsError("git merge failed")
            if args[:2] == ["merge", "origin/main"] and not check:
                # No CONFLICT anywhere — a genuine non-conflict failure.
                return MagicMock(returncode=1, stdout="fatal: bad object", stderr="")
            return MagicMock()

        mock_gh._run_git = MagicMock(side_effect=run_git)
        o._gh = mock_gh

        import pytest

        with pytest.raises(GitHubOpsError, match="non-conflict"):
            o._resolve_merge_conflicts(mock_gh, "auto-dev/fc82f22a", 1103)


# ── Bug 2: branch-policy rejection uses --auto ───────────────────────────


class TestDoMergeBranchPolicyAuto:
    """ "base branch policy prohibits" should retry with --auto (#820)."""

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_policy_prohibition_retries_with_auto(self, mock_gh_cls):
        o, _ = _make_orchestrator(_make_workflow())
        mock_gh = MagicMock()
        mock_gh_cls.return_value = mock_gh
        # First merge_pr (no auto) → policy rejection.
        # Second merge_pr (auto=True) → success.
        mock_gh.merge_pr.side_effect = [
            GitHubOpsError(
                "gh pr merge 1103 --merge failed (exit 1): "
                "Pull request #1103 is not mergeable: "
                "the base branch policy prohibits the merge."
            ),
            {"merged": True, "number": 1103},
        ]
        o._gh = mock_gh

        o._do_merge(_make_workflow())

        assert mock_gh.merge_pr.call_count == 2
        # Second call must include auto=True.
        second_call = mock_gh.merge_pr.call_args_list[1]
        assert second_call.kwargs.get("auto") is True

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_policy_then_auto_fail_falls_to_resolve(self, mock_gh_cls):
        """If --auto is also rejected, fall through to conflict resolution."""
        o, _ = _make_orchestrator(_make_workflow())
        mock_gh = MagicMock()
        mock_gh_cls.return_value = mock_gh
        mock_gh.merge_pr.side_effect = [
            GitHubOpsError("base branch policy prohibits the merge"),
            GitHubOpsError("--auto also rejected"),
        ]
        o._gh = mock_gh
        o._resolve_merge_conflicts = MagicMock()

        o._do_merge(_make_workflow())

        o._resolve_merge_conflicts.assert_called_once()

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_clean_conflict_goes_straight_to_resolve(self, mock_gh_cls):
        """A conflict error (not policy) skips --auto and resolves directly."""
        o, _ = _make_orchestrator(_make_workflow())
        mock_gh = MagicMock()
        mock_gh_cls.return_value = mock_gh
        mock_gh.merge_pr.side_effect = [
            GitHubOpsError("gh pr merge 1103 failed: the merge commit cannot be cleanly created"),
        ]
        o._gh = mock_gh
        o._resolve_merge_conflicts = MagicMock()

        o._do_merge(_make_workflow())

        # Only one merge_pr call (no --auto attempt); went straight to resolve.
        mock_gh.merge_pr.assert_called_once()
        assert mock_gh.merge_pr.call_args.kwargs.get("auto") is not True
        o._resolve_merge_conflicts.assert_called_once()


# ── github_ops.merge_pr --auto flag ──────────────────────────────────────


class TestMergePrAutoFlag:
    def test_auto_flag_adds_auto_arg(self):
        gh = GitHubOps("/tmp/repo")
        with patch.object(gh, "_run_gh") as mock_run:
            gh.merge_pr(10, strategy="merge", auto=True)
            cmd = mock_run.call_args.args[0]
            assert "--auto" in cmd
            assert "--merge" in cmd

    def test_no_auto_by_default(self):
        gh = GitHubOps("/tmp/repo")
        with patch.object(gh, "_run_gh") as mock_run:
            gh.merge_pr(10, strategy="merge")
            cmd = mock_run.call_args.args[0]
            assert "--auto" not in cmd
