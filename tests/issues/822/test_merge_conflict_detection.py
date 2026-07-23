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

from unittest.mock import MagicMock, call, patch

import pytest

from app.modules.workspace.autonomous.github_ops import GitHubOps, GitHubOpsError
from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator, WorkflowPaused


def _make_workflow(**overrides):
    base = {
        "workflow_id": "wf-822",
        "title": "gh issue 807-845 (#822)",
        "cli_tool": "claude-code",
        "model": "",
        "branch_strategy": "worktree",
        "branch_name": "auto-dev/fc82f22a",
        "worktree_path": "",
        "preferred_worktree_path": "/srv/repo/.worktrees/wf-822",
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
    o._validate_pre_merge_change_scope = MagicMock(return_value="")
    o._sync_failed_pr_with_main = MagicMock(return_value=False)
    return o, mock_repo


def _set_unchanged_index(gh, path: str = "app/x.py"):
    snapshot = {
        path: (
            "100644 base 1",
            "100644 ours 2",
            "100644 theirs 3",
        )
    }
    gh.get_index_snapshot.side_effect = [snapshot, snapshot]
    gh.get_index_changed_paths.side_effect = GitHubOps.get_index_changed_paths


def _set_valid_merge_result(
    orchestrator,
    gh,
    *,
    conflict: bool = True,
    original_head: str = "head-before",
    resolved_head: str = "head-after",
):
    """Configure strict branch/index/commit-graph postconditions for a success test."""
    gh.resolve_commit.return_value = "main-head"
    gh.get_current_branch.return_value = "auto-dev/fc82f22a"
    gh.get_current_commit.side_effect = (
        [original_head, original_head, resolved_head]
        if conflict
        else [original_head, resolved_head]
    )
    if conflict:
        gh.get_unmerged_paths.side_effect = [["app/x.py"], ["app/x.py"], []]
        gh.get_conflict_marker_paths.return_value = []
        gh.get_worktree_changed_paths.return_value = ["app/x.py"]
        _set_unchanged_index(gh)
    orchestrator._ancestor_check = MagicMock(return_value=True)
    orchestrator._validate_autonomous_change_scope = MagicMock(return_value="")


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
            # Only the merge of the pinned FETCH_HEAD commit matters; merge --abort and
            # other ops are no-ops.
            if args[:2] == ["merge", "main-head"] and check:
                raise GitHubOpsError("git merge main-head failed")
            if args[:2] == ["merge", "main-head"] and not check:
                return MagicMock(
                    returncode=1,
                    stdout=(
                        "CONFLICT (content): Merge conflict in " "app/services/auth_service.py\n"
                    ),
                    stderr="",
                )
            if args == ["diff", "--name-only", "--diff-filter=U"]:
                return MagicMock(returncode=0, stdout="app/services/auth_service.py\n", stderr="")
            return MagicMock()

        mock_gh._run_git = MagicMock(side_effect=run_git)
        o._gh = mock_gh
        _set_valid_merge_result(o, mock_gh)

        # Stub the AI conflict resolver so we verify it IS reached.
        o._run_agent = MagicMock()
        from app.modules.workspace.autonomous.models import AgentTaskResult

        o._run_agent.return_value = AgentTaskResult(
            session_id="s1",
            success=True,
            response_text="All tests passed.",
        )
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
        mock_gh.resolve_commit.return_value = "main-head"

        def run_git(args, check=True):
            if args[:2] == ["merge", "main-head"] and check:
                raise GitHubOpsError("git merge failed")
            if args[:2] == ["merge", "main-head"] and not check:
                # No CONFLICT anywhere — a genuine non-conflict failure.
                return MagicMock(returncode=1, stdout="fatal: bad object", stderr="")
            if args == ["diff", "--name-only", "--diff-filter=U"]:
                return MagicMock(returncode=0, stdout="", stderr="")
            return MagicMock()

        mock_gh._run_git = MagicMock(side_effect=run_git)
        o._gh = mock_gh

        import pytest

        with pytest.raises(GitHubOpsError, match="non-conflict"):
            o._resolve_merge_conflicts(mock_gh, "auto-dev/fc82f22a", 1103)

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_localized_conflict_uses_unmerged_index(self, mock_gh_cls):
        """Translated git output still enters conflict resolution via U paths."""
        o, _ = _make_orchestrator(_make_workflow())
        mock_gh = MagicMock()
        mock_gh_cls.return_value = mock_gh

        def run_git(args, check=True):
            if args[:2] == ["merge", "main-head"] and not check:
                return MagicMock(
                    returncode=1,
                    stdout="自动合并失败；修正冲突后提交结果。\n",
                    stderr="",
                )
            if args == ["diff", "--name-only", "--diff-filter=U"]:
                return MagicMock(returncode=0, stdout="app/routes/auth.py\n", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_gh._run_git.side_effect = run_git
        _set_valid_merge_result(o, mock_gh)
        from app.modules.workspace.autonomous.models import AgentTaskResult

        o._run_agent = MagicMock(
            return_value=AgentTaskResult(
                session_id="resolver", success=True, response_text="42 passed"
            )
        )

        o._resolve_merge_conflicts(mock_gh, "auto-dev/fc82f22a", 1103)

        o._run_agent.assert_called_once()

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_empty_merge_diagnostics_report_exit_code(self, mock_gh_cls):
        """An empty localized failure still exposes an actionable exit code."""
        o, _ = _make_orchestrator(_make_workflow())
        mock_gh = MagicMock()
        mock_gh_cls.return_value = mock_gh
        mock_gh.resolve_commit.return_value = "main-head"

        def run_git(args, check=True):
            if args[:2] == ["merge", "main-head"] and not check:
                return MagicMock(returncode=128, stdout="", stderr="")
            if args == ["diff", "--name-only", "--diff-filter=U"]:
                return MagicMock(returncode=0, stdout="", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_gh._run_git.side_effect = run_git
        import pytest

        with pytest.raises(GitHubOpsError, match="exit code 128"):
            o._resolve_merge_conflicts(mock_gh, "auto-dev/fc82f22a", 1103)

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_unmerged_index_query_failure_is_reported(self, mock_gh_cls):
        """Index inspection errors must not be mistaken for real conflicts."""
        o, _ = _make_orchestrator(_make_workflow())
        mock_gh = MagicMock()
        mock_gh_cls.return_value = mock_gh
        mock_gh.resolve_commit.return_value = "main-head"

        def run_git(args, check=True):
            if args[:2] == ["merge", "main-head"] and not check:
                return MagicMock(returncode=1, stdout="", stderr="")
            if args == ["diff", "--name-only", "--diff-filter=U"]:
                raise GitHubOpsError("index unavailable")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_gh._run_git.side_effect = run_git
        import pytest

        with pytest.raises(GitHubOpsError, match="index unavailable"):
            o._resolve_merge_conflicts(mock_gh, "auto-dev/fc82f22a", 1103)

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_conflict_text_without_u_stage_fails_closed(self, mock_gh_cls):
        """English output alone cannot launch a resolver without an unmerged index."""
        o, _ = _make_orchestrator(_make_workflow())
        main_gh = MagicMock()
        wt_gh = MagicMock()
        wt_gh.resolve_commit.return_value = "main-head"
        wt_gh._run_git.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=1, stdout="CONFLICT (content): app/x.py\n", stderr=""),
        ]
        wt_gh.get_unmerged_paths.return_value = []
        mock_gh_cls.side_effect = [main_gh, wt_gh]
        o._run_agent = MagicMock()

        with pytest.raises(GitHubOpsError, match="no unmerged paths"):
            o._resolve_merge_conflicts(MagicMock(), "auto-dev/fc82f22a", 1103)

        o._run_agent.assert_not_called()


# ── Bug 2: branch-policy rejection uses --auto ───────────────────────────


class TestDoMergeDeferredRetry:
    """Merge defers to the next scheduler cycle when CI is pending, instead of
    blocking on a synchronous poll or failing."""

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_ci_pending_defers_merge(self, mock_gh_cls):
        """When CI checks are still pending, return without attempting merge."""
        o, _ = _make_orchestrator(_make_workflow())
        mock_gh = MagicMock()
        mock_gh_cls.return_value = mock_gh
        o._gh = mock_gh
        mock_gh.get_pr_checks.return_value = [
            {"name": "test", "bucket": "pending"},
        ]

        o._do_merge(_make_workflow())

        mock_gh.merge_pr.assert_not_called()

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_branch_behind_main_syncs_before_ci_and_merge(self, mock_gh_cls):
        """A required-up-to-date rule must be satisfied before merge is tried."""
        o, _ = _make_orchestrator(_make_workflow())
        mock_gh = MagicMock()
        mock_gh_cls.return_value = mock_gh
        o._gh = mock_gh
        o._sync_failed_pr_with_main.return_value = True
        mock_gh.get_pr_head_sha.return_value = "old-pr-head"

        o._do_merge(_make_workflow())

        o._sync_failed_pr_with_main.assert_called_once_with(
            mock_gh,
            "auto-dev/fc82f22a",
            1103,
            "old-pr-head",
        )
        mock_gh.get_pr_checks.assert_not_called()
        mock_gh.merge_pr.assert_not_called()

    # Pre-merge scope must exclude upstream files merged into an old PR.

    def test_uses_graph_merge_base_and_backfills_stale_scope_base(self):
        wf = _make_workflow(base_commit_sha="old-branch-base")
        o, _ = _make_orchestrator(wf)
        gh = MagicMock()
        gh.resolve_commit.return_value = "fetched-main-head"
        gh._run_git.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="fetched-main-head\n", stderr=""),
        ]
        gh.get_changed_files.return_value = [f"business/file-{index}.py" for index in range(16)]

        assert (
            AutonomousOrchestrator._validate_pre_merge_change_scope(o, gh, wf, "resolved-pr-head")
            == ""
        )

        assert gh._run_git.call_args_list == [
            call(["fetch", "origin", "main"]),
            call(["merge-base", "resolved-pr-head", "fetched-main-head"], check=False),
        ]
        gh.get_changed_files.assert_called_once_with("fetched-main-head", "resolved-pr-head")
        o._update_workflow.assert_called_once_with({"base_commit_sha": "fetched-main-head"})
        assert wf["base_commit_sha"] == "fetched-main-head"

    def test_effective_pr_delta_still_enforces_scope_cap(self):
        wf = _make_workflow(base_commit_sha="old-branch-base")
        o, _ = _make_orchestrator(wf)
        gh = MagicMock()
        gh.resolve_commit.return_value = "fetched-main-head"
        gh._run_git.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="effective-base\n", stderr=""),
        ]
        gh.get_changed_files.return_value = [f"agent/file-{index}.py" for index in range(61)]

        error = AutonomousOrchestrator._validate_pre_merge_change_scope(
            o, gh, wf, "resolved-pr-head"
        )

        assert "61 files changed" in error
        o._update_workflow.assert_not_called()

    def test_merge_base_failure_fails_closed(self):
        o, _ = _make_orchestrator(_make_workflow())
        gh = MagicMock()
        gh.resolve_commit.return_value = "fetched-main-head"
        gh._run_git.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=1, stdout="", stderr="no common ancestor"),
        ]

        error = AutonomousOrchestrator._validate_pre_merge_change_scope(
            o, gh, _make_workflow(), "pr-head"
        )

        assert "could not derive effective PR base" in error
        gh.get_changed_files.assert_not_called()
        o._update_workflow.assert_not_called()

    def test_same_cycle_ci_repair_receives_refreshed_scope_base(self):
        wf = _make_workflow(base_commit_sha="old-branch-base")
        o, _ = _make_orchestrator(wf)
        o._validate_pre_merge_change_scope = (
            AutonomousOrchestrator._validate_pre_merge_change_scope.__get__(
                o, AutonomousOrchestrator
            )
        )
        o._start_ci_repair_round = MagicMock()
        gh = MagicMock()
        o._gh = gh
        gh.get_pr_head_sha.return_value = "resolved-pr-head"
        gh.resolve_commit.return_value = "fetched-main-head"
        gh._run_git.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="fetched-main-head\n", stderr=""),
        ]
        gh.get_changed_files.return_value = [f"business/file-{index}.py" for index in range(18)]
        failed_checks = [{"name": "migration", "bucket": "fail"}]
        gh.get_pr_checks.return_value = failed_checks

        o._do_merge(wf)

        o._start_ci_repair_round.assert_called_once_with(wf, 1103, failed_checks)
        assert o._start_ci_repair_round.call_args.args[0]["base_commit_sha"] == (
            "fetched-main-head"
        )
        gh.merge_pr.assert_not_called()

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_ci_pass_merges_successfully(self, mock_gh_cls):
        """When CI passes, merge proceeds immediately."""
        o, _ = _make_orchestrator(_make_workflow())
        mock_gh = MagicMock()
        mock_gh_cls.return_value = mock_gh
        o._gh = mock_gh
        mock_gh.get_pr_checks.return_value = [
            {"name": "test", "bucket": "pass"},
        ]
        mock_gh.merge_pr.return_value = {"merged": True}

        o._do_merge(_make_workflow())

        mock_gh.merge_pr.assert_called_once_with(1103, strategy="merge")

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_policy_rejection_no_ci_fail_defers(self, mock_gh_cls):
        """A newly pending required check is a confirmed transient wait."""
        o, _ = _make_orchestrator(_make_workflow())
        mock_gh = MagicMock()
        mock_gh_cls.return_value = mock_gh
        o._gh = mock_gh
        mock_gh.get_pr_checks.side_effect = [
            [{"name": "test", "bucket": "pass"}],
            [{"name": "new head check", "bucket": "pending"}],
        ]
        mock_gh.get_pr_merge_state.return_value = {
            "mergeable": True,
            "mergeable_state": "blocked",
        }
        mock_gh.merge_pr.side_effect = GitHubOpsError("GraphQL: Repository rule violations found")
        o._resolve_merge_conflicts = MagicMock()

        o._do_merge(_make_workflow())

        # Did not fail, did not resolve — refreshed checks and deferred.
        mock_gh.merge_pr.assert_called_once()
        assert mock_gh.get_pr_checks.call_count == 2
        o._resolve_merge_conflicts.assert_not_called()
        paused_updates = [
            call_args
            for call_args in o._update_workflow.call_args_list
            if call_args.args[0].get("status") == "paused"
        ]
        assert not paused_updates

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_policy_rejection_with_ci_fail_raises(self, mock_gh_cls):
        """Failed CI at merge time should restart development instead of failing."""
        o, _ = _make_orchestrator(_make_workflow())
        mock_gh = MagicMock()
        mock_gh_cls.return_value = mock_gh
        o._gh = mock_gh
        o._start_ci_repair_round = MagicMock()
        mock_gh.get_pr_checks.return_value = [
            {"name": "test (3.9)", "bucket": "fail"},
        ]

        o._do_merge(_make_workflow())

        mock_gh.merge_pr.assert_not_called()
        o._start_ci_repair_round.assert_called_once()

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_clean_conflict_goes_straight_to_resolve(self, mock_gh_cls):
        """A conflict error (not policy) goes straight to resolution."""
        o, _ = _make_orchestrator(_make_workflow())
        mock_gh = MagicMock()
        mock_gh_cls.return_value = mock_gh
        o._gh = mock_gh
        mock_gh.get_pr_checks.return_value = [{"name": "test", "bucket": "pass"}]
        mock_gh.merge_pr.side_effect = [
            GitHubOpsError("gh pr merge 1103 failed: the merge commit cannot be cleanly created"),
        ]
        mock_gh.get_pr_merge_state.return_value = {
            "mergeable": False,
            "mergeable_state": "dirty",
        }
        o._resolve_merge_conflicts = MagicMock()

        o._do_merge(_make_workflow())

        mock_gh.merge_pr.assert_called_once()
        o._resolve_merge_conflicts.assert_called_once()

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_unknown_merge_failure_is_not_misclassified_as_conflict(self, mock_gh_cls):
        """Permission/infrastructure errors must retain their original cause."""
        o, _ = _make_orchestrator(_make_workflow())
        mock_gh = MagicMock()
        mock_gh_cls.return_value = mock_gh
        o._gh = mock_gh
        mock_gh.get_pr_checks.return_value = [{"name": "test", "bucket": "pass"}]
        mock_gh.get_pr_merge_state.return_value = {
            "mergeable": True,
            "mergeable_state": "clean",
        }
        mock_gh.merge_pr.side_effect = GitHubOpsError("GraphQL: Resource not accessible")
        o._resolve_merge_conflicts = MagicMock()

        with pytest.raises(GitHubOpsError, match="Resource not accessible"):
            o._do_merge(_make_workflow())

        o._resolve_merge_conflicts.assert_not_called()

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_blocked_merge_state_is_not_a_conflict_even_when_mergeable_false(self, mock_gh_cls):
        """A confirmed no-pending policy block becomes manually recoverable."""
        o, _ = _make_orchestrator(_make_workflow())
        mock_gh = MagicMock()
        mock_gh_cls.return_value = mock_gh
        o._gh = mock_gh
        mock_gh.get_pr_checks.return_value = [{"name": "test", "bucket": "pass"}]
        mock_gh.get_pr_merge_state.return_value = {
            "mergeable": False,
            "mergeable_state": "blocked",
        }
        mock_gh.merge_pr.side_effect = GitHubOpsError("GraphQL: review required")
        o._resolve_merge_conflicts = MagicMock()

        with pytest.raises(WorkflowPaused, match="Merge blocked by repository policy"):
            o._do_merge(_make_workflow())

        o._resolve_merge_conflicts.assert_not_called()
        pause_update = o._update_workflow.call_args.args[0]
        assert pause_update["status"] == "paused"
        assert pause_update["paused_at"]
        assert "Merge blocked by repository policy:" in pause_update["error_message"]
        assert "resume the workflow" in pause_update["error_message"]
        o.emitter.emit.assert_called()

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_policy_pause_survives_advance_success_cleanup(self, mock_gh_cls):
        """An older transient retry counter must not clear the pause reason."""
        wf = _make_workflow(transient_retry_count=2)
        o, _ = _make_orchestrator(wf)
        o._ensure_worktree = MagicMock()
        mock_gh = MagicMock()
        mock_gh_cls.return_value = mock_gh
        o._gh = mock_gh
        mock_gh.get_pr_checks.return_value = [{"name": "test", "bucket": "pass"}]
        mock_gh.get_pr_merge_state.return_value = {
            "mergeable": True,
            "mergeable_state": "blocked",
        }
        mock_gh.merge_pr.side_effect = GitHubOpsError("GraphQL: Repository rule violations found")

        o.advance()

        updates = [call_args.args[0] for call_args in o._update_workflow.call_args_list]
        pause_updates = [update for update in updates if update.get("status") == "paused"]
        assert len(pause_updates) == 1
        assert "Merge blocked by repository policy:" in pause_updates[0]["error_message"]
        assert not any(update.get("error_message") == "" for update in updates)

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_blocked_state_does_not_swallow_unknown_permission_error(self, mock_gh_cls):
        """Merge state alone cannot turn infrastructure failure into policy wait."""
        o, _ = _make_orchestrator(_make_workflow())
        mock_gh = MagicMock()
        mock_gh_cls.return_value = mock_gh
        o._gh = mock_gh
        mock_gh.get_pr_checks.return_value = [{"name": "test", "bucket": "pass"}]
        mock_gh.get_pr_merge_state.return_value = {
            "mergeable": False,
            "mergeable_state": "blocked",
        }
        mock_gh.merge_pr.side_effect = GitHubOpsError("GraphQL: Resource not accessible")
        o._resolve_merge_conflicts = MagicMock()

        with pytest.raises(GitHubOpsError, match="Resource not accessible"):
            o._do_merge(_make_workflow())

        o._resolve_merge_conflicts.assert_not_called()
        o._update_workflow.assert_not_called()

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_dirty_state_wins_over_generic_repository_rule_text(self, mock_gh_cls):
        """Authoritative dirty state must resolve even with mixed policy text."""
        o, _ = _make_orchestrator(_make_workflow())
        mock_gh = MagicMock()
        mock_gh_cls.return_value = mock_gh
        o._gh = mock_gh
        mock_gh.get_pr_checks.return_value = [{"name": "test", "bucket": "pass"}]
        mock_gh.get_pr_merge_state.return_value = {
            "mergeable": False,
            "mergeable_state": "dirty",
        }
        mock_gh.merge_pr.side_effect = GitHubOpsError("GraphQL: Repository Rule Violations Found")
        o._resolve_merge_conflicts = MagicMock()

        o._do_merge(_make_workflow())

        o._resolve_merge_conflicts.assert_called_once()
        paused_updates = [
            call_args
            for call_args in o._update_workflow.call_args_list
            if call_args.args[0].get("status") == "paused"
        ]
        assert not paused_updates

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_resolve_returns_without_cleanup(self, mock_gh_cls):
        """After _resolve_merge_conflicts pushes, _do_merge must return early
        (stay 'merging') — NOT fall through to cleanup/completed, which would
        delete the branch before the PR is actually merged (#1112 P1).
        """
        o, _ = _make_orchestrator(_make_workflow())
        mock_gh = MagicMock()
        mock_gh_cls.return_value = mock_gh
        o._gh = mock_gh
        mock_gh.get_pr_checks.return_value = [{"name": "test", "bucket": "pass"}]
        mock_gh.merge_pr.side_effect = [
            GitHubOpsError("the merge commit cannot be cleanly created"),
        ]
        mock_gh.get_pr_merge_state.return_value = {
            "mergeable": False,
            "mergeable_state": "dirty",
        }
        o._resolve_merge_conflicts = MagicMock()

        o._do_merge(_make_workflow())

        # Resolve was called...
        o._resolve_merge_conflicts.assert_called_once()
        # ...but cleanup was NOT reached: no branch deletion, no completed status.
        mock_gh.delete_branch.assert_not_called()
        completed_updates = [
            c for c in o._update_workflow.call_args_list if c[0][1].get("status") == "completed"
        ]
        assert not completed_updates, "workflow was marked completed before PR merged"

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_cleanup_after_successful_merge_deletes_branch(self, mock_gh_cls):
        """After a successful merge (CI passed, merge_pr succeeded), the
        cleanup block deletes the branch. Verifies the cleanup path works
        end-to-end and that wf is re-read so worktree_path is current.

        Previously (#1107 P2) cleanup used the stale pre-resolution wf and
        retried worktree removal; now it only runs on the success path where
        worktree_path may still be set (normal merge) or cleared (post-resolve
        merge on a later cycle).
        """
        wf_arg = _make_workflow(worktree_path="/srv/repo/../auto-dev-fc82f22a")
        o, mock_repo = _make_orchestrator(wf_arg)
        mock_gh = MagicMock()
        mock_gh_cls.return_value = mock_gh
        o._gh = mock_gh
        mock_gh.get_pr_checks.return_value = [{"name": "test", "bucket": "pass"}]
        mock_gh.merge_pr.return_value = {"merged": True}

        # get_workflow returns the same wf (with worktree_path) for all reads.
        mock_repo.get_workflow.return_value = wf_arg

        o._do_merge(wf_arg)

        # Merge succeeded → cleanup ran → branch deleted.
        mock_gh.delete_branch.assert_called_once_with("auto-dev/fc82f22a")

    def test_start_ci_repair_round_restores_preferred_worktree(self):
        """CI repair recreates and binds the PR worktree before launching its agent."""
        wf = _make_workflow(worktree_path="", preferred_worktree_path="/srv/repo/.worktrees/wf-822")
        o, _ = _make_orchestrator(wf)
        main_gh = MagicMock()
        main_gh.get_pr_head_sha.return_value = "sha-old"
        main_gh.get_check_failure_excerpt.return_value = "pytest failed"
        worktree_gh = MagicMock()
        o._get_gh = MagicMock(side_effect=[main_gh, worktree_gh])
        o._ensure_worktree = MagicMock(return_value="/srv/repo/.worktrees/wf-822")
        o._run_merge_ci_repair = MagicMock()

        o._start_ci_repair_round(
            wf,
            1103,
            [{"name": "test (3.9)", "bucket": "fail", "state": "failure"}],
        )

        update_payload = o._update_workflow.call_args.args[0]
        assert update_payload["current_phase"] == "merge"
        assert update_payload["status"] == "merging"
        assert "dev_round" not in update_payload
        assert update_payload["ci_repair_attempts"] == 1
        assert update_payload["worktree_path"] == "/srv/repo/.worktrees/wf-822"
        assert update_payload["preferred_worktree_path"] == "/srv/repo/.worktrees/wf-822"
        restore_wf = o._ensure_worktree.call_args.args[0]
        assert restore_wf["worktree_path"] == "/srv/repo/.worktrees/wf-822"
        assert restore_wf["branch_name"] == "auto-dev/fc82f22a"
        o._run_merge_ci_repair.assert_called_once_with(
            restore_wf,
            worktree_gh,
            1103,
            [
                {
                    "name": "test (3.9)",
                    "bucket": "fail",
                    "state": "failure",
                    "failure_excerpt": "pytest failed",
                }
            ],
        )

    def test_worktree_restore_failure_does_not_consume_ci_repair_attempt(self):
        """Infrastructure retries must not spend the bounded AI repair budget."""
        wf = _make_workflow(
            worktree_path="",
            preferred_worktree_path="/srv/repo/.worktrees/wf-822",
        )
        o, _ = _make_orchestrator(wf)
        main_gh = MagicMock()
        main_gh.get_pr_head_sha.return_value = "sha-old"
        main_gh.get_check_failure_excerpt.return_value = "pytest failed"
        o._get_gh = MagicMock(return_value=main_gh)
        o._ensure_worktree = MagicMock(side_effect=GitHubOpsError("network timed out"))
        o._run_merge_ci_repair = MagicMock()

        with pytest.raises(GitHubOpsError, match="network timed out"):
            o._start_ci_repair_round(
                wf,
                1103,
                [{"name": "test (3.9)", "bucket": "fail", "state": "failure"}],
            )

        o._run_merge_ci_repair.assert_not_called()
        attempt_updates = [
            call_args.args[0]
            for call_args in o._update_workflow.call_args_list
            if call_args.args and "ci_repair_attempts" in call_args.args[0]
        ]
        assert attempt_updates == []
        started_milestones = [
            call_args.kwargs
            for call_args in o._create_milestone.call_args_list
            if call_args.kwargs.get("milestone_type") == "ci_repair_started"
        ]
        assert started_milestones == []

    def test_start_ci_repair_round_fails_when_signature_repeats(self):
        """A repeated failed-check signature should stop the auto-repair loop.

        Uses a real fine-grained fingerprint (name::sha256[:12] of the normalized
        excerpt) so the give-up guard's signature comparison is meaningful. The
        excerpt is mocked to be deterministic. Previously this test used the
        pre-#1811 pipe format ("test (3.9)|failure|fail") which never matched
        the new name::hash signature, so the guard never fired and the test
        fell through to _build_ci_repair_context hitting an unmocked MagicMock.
        """
        import hashlib

        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        excerpt = "FAILED tests/test_x.py::test_y - AssertionError\n"
        expected_digest = hashlib.sha256(
            AutonomousOrchestrator._normalize_failure_excerpt(excerpt).encode()
        ).hexdigest()[:12]
        expected_fingerprint = f"test (3.9)::{expected_digest}"

        wf = _make_workflow(
            ci_repair_attempts=1,
            last_ci_failure_signature=expected_fingerprint,
            last_ci_failure_head_sha="sha-old",
        )
        o, _ = _make_orchestrator(wf)
        mock_gh = MagicMock()
        mock_gh.get_pr_head_sha.return_value = "sha-new"
        mock_gh.get_check_failure_excerpt.return_value = excerpt
        o._get_gh = MagicMock(return_value=mock_gh)

        o._start_ci_repair_round(
            wf,
            1103,
            [{"name": "test (3.9)", "bucket": "fail", "state": "failure"}],
        )

        failure_update = o._update_workflow.call_args.args[0]
        assert failure_update["status"] == "failed"
        assert "仍未变化" in failure_update["error_message"]


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


class TestGetUnmergedPaths:
    def test_returns_authoritative_u_stage_paths(self):
        gh = GitHubOps("/tmp/repo")
        with patch.object(
            gh,
            "_run_git",
            return_value=MagicMock(
                returncode=0,
                stdout="app/a.py\nfrontend/b.tsx\n",
                stderr="",
            ),
        ):
            assert gh.get_unmerged_paths() == ["app/a.py", "frontend/b.tsx"]

    def test_query_failure_raises(self):
        gh = GitHubOps("/tmp/repo")
        with patch.object(
            gh,
            "_run_git",
            return_value=MagicMock(returncode=128, stdout="", stderr="bad index"),
        ):
            import pytest

            with pytest.raises(GitHubOpsError, match="exit code 128"):
                gh.get_unmerged_paths()


class TestResolveCommit:
    def test_resolves_ref_to_immutable_commit(self):
        gh = GitHubOps("/tmp/repo")
        with patch.object(
            gh,
            "_run_git",
            return_value=MagicMock(returncode=0, stdout="abc123\n", stderr=""),
        ) as mock_run:
            assert gh.resolve_commit("origin/main") == "abc123"
        mock_run.assert_called_once_with(["rev-parse", "--verify", "origin/main^{commit}"])

    def test_empty_resolution_raises(self):
        gh = GitHubOps("/tmp/repo")
        with patch.object(
            gh,
            "_run_git",
            return_value=MagicMock(returncode=0, stdout="", stderr=""),
        ):
            with pytest.raises(GitHubOpsError, match="Unable to resolve"):
                gh.resolve_commit("origin/main")


class TestGetWorktreeChangedPaths:
    def test_combines_tracked_unmerged_and_untracked_paths(self):
        gh = GitHubOps("/tmp/repo")
        with patch.object(
            gh,
            "_run_git",
            side_effect=[
                MagicMock(
                    returncode=0,
                    stdout="app/conflict.py\napp/edited.py\n",
                    stderr="",
                ),
                MagicMock(returncode=0, stdout="tests/new_test.py\n", stderr=""),
            ],
        ) as mock_run:
            assert gh.get_worktree_changed_paths() == [
                "app/conflict.py",
                "app/edited.py",
                "tests/new_test.py",
            ]
        assert mock_run.call_args_list[0].args[0] == ["diff", "--name-only"]
        assert mock_run.call_args_list[1].args[0] == [
            "ls-files",
            "--others",
            "--exclude-standard",
        ]

    @pytest.mark.parametrize("failed_probe", [0, 1])
    def test_probe_failure_raises(self, failed_probe):
        results = [
            MagicMock(returncode=0, stdout="app/x.py\n", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        results[failed_probe] = MagicMock(returncode=128, stdout="", stderr="probe failed")
        gh = GitHubOps("/tmp/repo")
        with patch.object(gh, "_run_git", side_effect=results):
            with pytest.raises(GitHubOpsError, match="exit code 128"):
                gh.get_worktree_changed_paths()


class TestIndexSnapshot:
    def test_parses_stage_zero_and_unmerged_entries(self):
        output = (
            "100644 blob-a 0\tapp/clean.py\0"
            "100644 blob-base 1\tapp/conflict.py\0"
            "100644 blob-ours 2\tapp/conflict.py\0"
            "100644 blob-theirs 3\tapp/conflict.py\0"
        )
        gh = GitHubOps("/tmp/repo")
        with patch.object(
            gh,
            "_run_git",
            return_value=MagicMock(returncode=0, stdout=output, stderr=""),
        ) as mock_run:
            assert gh.get_index_snapshot() == {
                "app/clean.py": ("100644 blob-a 0",),
                "app/conflict.py": (
                    "100644 blob-base 1",
                    "100644 blob-ours 2",
                    "100644 blob-theirs 3",
                ),
            }
        mock_run.assert_called_once_with(["ls-files", "--stage", "-z"], check=False)

    def test_snapshot_probe_and_parse_fail_closed(self):
        gh = GitHubOps("/tmp/repo")
        with patch.object(
            gh,
            "_run_git",
            return_value=MagicMock(returncode=128, stdout="", stderr="bad index"),
        ):
            with pytest.raises(GitHubOpsError, match="exit code 128"):
                gh.get_index_snapshot()
        with patch.object(
            gh,
            "_run_git",
            return_value=MagicMock(returncode=0, stdout="malformed\0", stderr=""),
        ):
            with pytest.raises(GitHubOpsError, match="parse git index"):
                gh.get_index_snapshot()

    def test_diff_detects_added_removed_blob_and_stage_changes(self):
        before = {
            "deleted.py": ("100644 old 0",),
            "modified.py": ("100644 old 0",),
            "resolved.py": ("100644 base 1", "100644 ours 2", "100644 theirs 3"),
            "unchanged.py": ("100644 same 0",),
        }
        after = {
            "added.py": ("100644 new 0",),
            "modified.py": ("100644 new 0",),
            "resolved.py": ("100644 resolved 0",),
            "unchanged.py": ("100644 same 0",),
        }
        assert GitHubOps.get_index_changed_paths(before, after) == [
            "added.py",
            "deleted.py",
            "modified.py",
            "resolved.py",
        ]


class TestGetConflictMarkerPaths:
    def test_returns_only_matching_conflict_files(self):
        gh = GitHubOps("/tmp/repo")
        with (
            patch.object(gh, "path_exists_as_user", return_value=True),
            patch.object(
                gh,
                "_run_git",
                return_value=MagicMock(returncode=0, stdout="app/a.py\napp/b.py\n", stderr=""),
            ) as mock_run,
        ):
            assert gh.get_conflict_marker_paths(["app/a.py", "app/b.py"]) == [
                "app/a.py",
                "app/b.py",
            ]
        command = mock_run.call_args.args[0]
        assert command[:4] == ["grep", "--no-index", "-l", "-I"]
        assert command[-3:] == ["--", "app/a.py", "app/b.py"]
        assert r"^<{7,}( |$)" in command
        assert r"^={7,}$" in command
        assert r"^>{7,}( |$)" in command

    def test_no_matches_returns_empty_list(self):
        gh = GitHubOps("/tmp/repo")
        with (
            patch.object(gh, "path_exists_as_user", return_value=True),
            patch.object(
                gh,
                "_run_git",
                return_value=MagicMock(returncode=1, stdout="", stderr=""),
            ),
        ):
            assert gh.get_conflict_marker_paths(["app/a.py"]) == []

    def test_probe_failure_raises(self):
        gh = GitHubOps("/tmp/repo")
        with (
            patch.object(gh, "path_exists_as_user", return_value=True),
            patch.object(
                gh,
                "_run_git",
                return_value=MagicMock(returncode=128, stdout="", stderr="bad path"),
            ),
        ):
            with pytest.raises(GitHubOpsError, match="exit code 128"):
                gh.get_conflict_marker_paths(["app/a.py"])

    def test_deleted_conflict_path_is_a_valid_marker_free_resolution(self):
        gh = GitHubOps("/tmp/repo")
        with (
            patch.object(gh, "path_exists_as_user", return_value=False),
            patch.object(gh, "_run_git") as mock_run,
        ):
            assert gh.get_conflict_marker_paths(["app/deleted.py"]) == []
        mock_run.assert_not_called()

    def test_detects_custom_marker_size_larger_than_seven(self, tmp_path):
        conflict_file = tmp_path / "conflict.py"
        conflict_file.write_text(
            "<<<<<<<<<< HEAD\nours\n==========\ntheirs\n>>>>>>>>>> main\n",
            encoding="utf-8",
        )
        gh = GitHubOps(str(tmp_path))
        assert gh.get_conflict_marker_paths(["conflict.py"]) == ["conflict.py"]


# ── Bug 3: isolated temp worktree for conflict resolution ────────────────


class TestResolveMergeConflictsWorktreeIsolation:
    """Conflict resolution must run in a throwaway worktree, not the main repo.

    Previously ``_resolve_merge_conflicts`` checked out the PR branch in the
    shared main repo, causing index.lock races and reset clobbering. Now it
    creates a temp worktree (``add_worktree``), does all git ops there, and
    removes it in a ``finally``.
    """

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_creates_temp_worktree_and_cleans_up_on_success(self, mock_gh_cls):
        o, _ = _make_orchestrator(_make_workflow())

        # GitHubOps is constructed 3 times: main_gh (add_worktree),
        # wt_gh (merge ops), and the gh passed in (merge_pr). We route them
        # through distinct MagicMock instances via side_effect.
        main_gh = MagicMock()
        wt_gh = MagicMock()
        caller_gh = MagicMock()
        # wt_gh._run_git: fetch ok, merge succeeds (no conflict).
        wt_gh._run_git.side_effect = [
            MagicMock(),  # fetch origin main
            MagicMock(returncode=0, stdout="", stderr=""),  # merge (clean)
        ]
        mock_gh_cls.side_effect = [main_gh, wt_gh, caller_gh]
        _set_valid_merge_result(o, wt_gh, conflict=False)

        o._resolve_merge_conflicts(caller_gh, "auto-dev/fc82f22a", 1103)

        # Temp worktree created on main repo gh.
        main_gh.add_worktree.assert_called_once()
        wt_path = main_gh.add_worktree.call_args.args[0]
        assert wt_path.endswith("merge-wf-822")  # merge-<workflow_id[:8]>
        # Cleaned up in finally.
        main_gh.remove_worktree.assert_called_once_with(wt_path)
        # _resolve_merge_conflicts only pushes now — merge is deferred to
        # _do_merge's next cycle (after CI passes).
        caller_gh.merge_pr.assert_not_called()
        # git_push runs inside the temp worktree (wt_gh), not the caller gh.
        wt_gh.git_push.assert_called_once_with(branch="auto-dev/fc82f22a", force_with_lease=True)

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_cleans_up_temp_worktree_on_failure(self, mock_gh_cls):
        """Temp worktree must be removed even when resolution fails."""
        o, _ = _make_orchestrator(_make_workflow())
        main_gh = MagicMock()
        wt_gh = MagicMock()
        caller_gh = MagicMock()
        # merge returns CONFLICT on stdout → agent invoked → agent fails.
        wt_gh._run_git.side_effect = [
            MagicMock(),  # fetch origin main
            MagicMock(
                returncode=1,
                stdout="CONFLICT (content): Merge conflict in app/x.py\n",
                stderr="",
            ),  # merge (conflict)
        ]
        mock_gh_cls.side_effect = [main_gh, wt_gh, caller_gh]
        _set_valid_merge_result(o, wt_gh)

        import pytest

        o._run_agent = MagicMock()
        from app.modules.workspace.autonomous.models import AgentTaskResult

        o._run_agent.return_value = AgentTaskResult(
            session_id="s1", success=False, error="agent failed"
        )
        o._resolve_session_line = MagicMock(return_value=("sess", None, False))
        o._link_session_to_current_milestone = MagicMock()

        with pytest.raises(RuntimeError, match="Conflict resolution failed"):
            o._resolve_merge_conflicts(caller_gh, "auto-dev/fc82f22a", 1103)

        # Still cleaned up despite the failure.
        main_gh.remove_worktree.assert_called_once()

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_agent_runs_in_temp_worktree_not_main_repo(self, mock_gh_cls):
        """The AI agent's project_path must be the temp worktree path."""
        o, _ = _make_orchestrator(_make_workflow())
        main_gh = MagicMock()
        wt_gh = MagicMock()
        caller_gh = MagicMock()
        wt_gh._run_git.side_effect = [
            MagicMock(),  # fetch origin main
            MagicMock(
                returncode=1,
                stdout="CONFLICT (content): Merge conflict in app/x.py\n",
                stderr="",
            ),  # merge (conflict)
        ]
        mock_gh_cls.side_effect = [main_gh, wt_gh, caller_gh]
        _set_valid_merge_result(o, wt_gh)

        o._run_agent = MagicMock()
        from app.modules.workspace.autonomous.models import AgentTaskResult

        o._run_agent.return_value = AgentTaskResult(
            session_id="s1", success=True, response_text="resolved"
        )
        o._resolve_session_line = MagicMock(return_value=("sess", None, False))
        o._link_session_to_current_milestone = MagicMock()

        o._resolve_merge_conflicts(caller_gh, "auto-dev/fc82f22a", 1103)

        agent_project_path = o._run_agent.call_args.kwargs.get("project_path", "")
        assert agent_project_path.endswith("merge-wf-822")  # temp worktree, not main repo
        # Must NOT be the main repo project_path.
        assert agent_project_path != _make_workflow()["project_path"]
        agent_wf = o._run_agent.call_args.kwargs["wf"]
        assert agent_wf["worktree_path"] == agent_project_path
        assert agent_wf["branch_strategy"] == "worktree"
        assert agent_wf["branch_name"] == "auto-dev/fc82f22a"
        effective = o._resolve_effective_repo_context(agent_wf)
        assert effective["repo_path"] == agent_project_path
        contract = o._build_repo_execution_contract(agent_wf)
        assert agent_project_path in contract
        assert "`/srv/repo`" not in contract
        wt_gh.git_add_all.assert_called_once()
        wt_gh.git_commit.assert_called_once_with(
            "merge: resolve conflicts for PR #1103", no_verify=True
        )

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_agent_uses_fresh_session_not_main(self, mock_gh_cls):
        """Conflict resolution must use session_line='fresh', not 'main'.

        Resuming the dev-phase session ('main') loads conversation history
        that tells the agent the work is done, so it returns in seconds
        without executing any git commands. A fresh session starts clean
        in the temp worktree. Regression guard (#1112).
        """
        o, _ = _make_orchestrator(_make_workflow())
        main_gh = MagicMock()
        wt_gh = MagicMock()
        caller_gh = MagicMock()
        wt_gh._run_git.side_effect = [
            MagicMock(),  # fetch
            MagicMock(
                returncode=1,
                stdout="CONFLICT (content): Merge conflict in app/x.py\n",
                stderr="",
            ),  # merge (conflict)
        ]
        mock_gh_cls.side_effect = [main_gh, wt_gh, caller_gh]
        _set_valid_merge_result(o, wt_gh)

        o._run_agent = MagicMock()
        from app.modules.workspace.autonomous.models import AgentTaskResult

        o._run_agent.return_value = AgentTaskResult(
            session_id="s1", success=True, response_text="resolved"
        )
        o._resolve_session_line = MagicMock(return_value=("sess", None, False))
        o._link_session_to_current_milestone = MagicMock()

        o._resolve_merge_conflicts(caller_gh, "auto-dev/fc82f22a", 1103)

        session_line = o._run_agent.call_args.kwargs.get("session_line", "")
        assert session_line == "fresh"

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_conflict_prompt_requires_test_verification(self, mock_gh_cls):
        """The edit-only agent must test before orchestration commits."""
        o, _ = _make_orchestrator(_make_workflow())
        main_gh = MagicMock()
        wt_gh = MagicMock()
        caller_gh = MagicMock()
        wt_gh._run_git.side_effect = [
            MagicMock(),  # fetch
            MagicMock(
                returncode=1,
                stdout="CONFLICT (content): Merge conflict in app/x.py\n",
                stderr="",
            ),  # merge (conflict)
        ]
        mock_gh_cls.side_effect = [main_gh, wt_gh, caller_gh]
        _set_valid_merge_result(o, wt_gh)

        o._run_agent = MagicMock()
        from app.modules.workspace.autonomous.models import AgentTaskResult

        o._run_agent.return_value = AgentTaskResult(
            session_id="s1", success=True, response_text="resolved"
        )
        o._resolve_session_line = MagicMock(return_value=("sess", None, False))
        o._link_session_to_current_milestone = MagicMock()

        o._resolve_merge_conflicts(caller_gh, "auto-dev/fc82f22a", 1103)

        prompt = o._run_agent.call_args.kwargs.get("prompt", "")
        # The agent only edits and tests. Trusted orchestration owns all
        # mutating git operations because the agent command guard denies them.
        assert "pytest" in prompt
        assert "测试" in prompt or "test" in prompt.lower()
        assert "不要执行 git add、git commit 或 git push" in prompt
        assert "暂存、提交与推送由编排器" in prompt
        # Must require a summary report (for timeline tldr visibility).
        assert "总结" in prompt, "prompt must require a summary report"
        assert "merge-wf-822" in prompt
        assert "禁止调用 EnterWorktree" in prompt

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_success_with_conflict_markers_fails_before_push(self, mock_gh_cls):
        """Model success cannot bypass conflict-marker verification."""
        o, _ = _make_orchestrator(_make_workflow())
        main_gh = MagicMock()
        wt_gh = MagicMock()
        caller_gh = MagicMock()
        wt_gh.get_current_commit.return_value = "head-before"
        wt_gh.get_current_branch.return_value = "auto-dev/fc82f22a"
        wt_gh._run_git.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),  # fetch
            MagicMock(
                returncode=1,
                stdout="CONFLICT (content): app/x.py\n",
                stderr="",
            ),
        ]
        wt_gh.get_unmerged_paths.return_value = ["app/x.py"]
        wt_gh.get_conflict_marker_paths.return_value = ["app/x.py"]
        mock_gh_cls.side_effect = [main_gh, wt_gh]
        from app.modules.workspace.autonomous.models import AgentTaskResult

        o._run_agent = MagicMock(
            return_value=AgentTaskResult(
                session_id="resolver", success=True, response_text="resolved"
            )
        )

        import pytest

        with pytest.raises(RuntimeError, match="conflict markers"):
            o._resolve_merge_conflicts(caller_gh, "auto-dev/fc82f22a", 1103)

        wt_gh.git_push.assert_not_called()
        main_gh.remove_worktree.assert_called_once()

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_staged_file_still_scans_initial_conflict_path(self, mock_gh_cls):
        """Clearing U-stage cannot hide unresolved markers from the owner gate."""
        o, _ = _make_orchestrator(_make_workflow())
        main_gh = MagicMock()
        wt_gh = MagicMock()
        caller_gh = MagicMock()
        wt_gh.resolve_commit.return_value = "main-head"
        wt_gh.get_current_commit.return_value = "head-before"
        wt_gh.get_current_branch.return_value = "auto-dev/fc82f22a"
        wt_gh._run_git.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=1, stdout="CONFLICT (content): app/x.py\n", stderr=""),
        ]
        # Agent bypasses the PATH guard and stages the path, clearing U-stage.
        wt_gh.get_unmerged_paths.side_effect = [["app/x.py"], []]
        wt_gh.get_conflict_marker_paths.return_value = ["app/x.py"]
        wt_gh.get_index_snapshot.return_value = {"app/x.py": ("100644 ours 2",)}
        mock_gh_cls.side_effect = [main_gh, wt_gh]
        from app.modules.workspace.autonomous.models import AgentTaskResult

        o._run_agent = MagicMock(
            return_value=AgentTaskResult(
                session_id="resolver", success=True, response_text="staged"
            )
        )

        with pytest.raises(RuntimeError, match="left conflict markers"):
            o._resolve_merge_conflicts(caller_gh, "auto-dev/fc82f22a", 1103)

        wt_gh.get_conflict_marker_paths.assert_called_once_with(["app/x.py"])
        wt_gh.git_add_all.assert_not_called()
        wt_gh.git_push.assert_not_called()

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_agent_head_change_fails_before_owner_staging(self, mock_gh_cls):
        """An absolute git commit/abort cannot be adopted by orchestration."""
        o, _ = _make_orchestrator(_make_workflow())
        main_gh = MagicMock()
        wt_gh = MagicMock()
        caller_gh = MagicMock()
        wt_gh.resolve_commit.return_value = "main-head"
        wt_gh.get_current_commit.side_effect = ["head-before", "agent-head"]
        wt_gh.get_current_branch.return_value = "auto-dev/fc82f22a"
        wt_gh._run_git.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=1, stdout="CONFLICT (content): app/x.py\n", stderr=""),
        ]
        wt_gh.get_unmerged_paths.side_effect = [["app/x.py"], []]
        wt_gh.get_conflict_marker_paths.return_value = []
        wt_gh.get_index_snapshot.return_value = {"app/x.py": ("100644 resolved 0",)}
        mock_gh_cls.side_effect = [main_gh, wt_gh]
        from app.modules.workspace.autonomous.models import AgentTaskResult

        o._run_agent = MagicMock(
            return_value=AgentTaskResult(
                session_id="resolver", success=True, response_text="committed"
            )
        )

        with pytest.raises(RuntimeError, match="changed HEAD"):
            o._resolve_merge_conflicts(caller_gh, "auto-dev/fc82f22a", 1103)

        wt_gh.git_add_all.assert_not_called()
        wt_gh.git_push.assert_not_called()

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_deleted_conflict_file_is_staged_and_committed_by_owner(self, mock_gh_cls):
        """Deleting a conflicted path is a valid edit-only resolver outcome."""
        o, _ = _make_orchestrator(_make_workflow())
        main_gh = MagicMock()
        wt_gh = MagicMock()
        caller_gh = MagicMock()
        wt_gh.resolve_commit.return_value = "main-head"
        wt_gh.get_current_commit.side_effect = [
            "head-before",
            "head-before",
            "head-after",
        ]
        wt_gh.get_current_branch.return_value = "auto-dev/fc82f22a"
        wt_gh._run_git.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(
                returncode=1,
                stdout="CONFLICT (modify/delete): app/deleted.py\n",
                stderr="",
            ),
        ]
        wt_gh.get_unmerged_paths.side_effect = [
            ["app/deleted.py"],
            ["app/deleted.py"],
            [],
        ]
        wt_gh.get_conflict_marker_paths.return_value = []
        wt_gh.get_worktree_changed_paths.return_value = ["app/deleted.py"]
        _set_unchanged_index(wt_gh, "app/deleted.py")
        o._ancestor_check = MagicMock(return_value=True)
        o._validate_autonomous_change_scope = MagicMock(return_value="")
        mock_gh_cls.side_effect = [main_gh, wt_gh]
        from app.modules.workspace.autonomous.models import AgentTaskResult

        o._run_agent = MagicMock(
            return_value=AgentTaskResult(
                session_id="resolver", success=True, response_text="deleted intentionally"
            )
        )

        o._resolve_merge_conflicts(caller_gh, "auto-dev/fc82f22a", 1103)

        wt_gh.get_conflict_marker_paths.assert_called_once_with(["app/deleted.py"])
        wt_gh.git_add_all.assert_called_once()
        wt_gh.git_commit.assert_called_once()
        wt_gh.git_push.assert_called_once()

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_success_without_merge_commit_fails_before_push(self, mock_gh_cls):
        """A no-op resolver response must terminate instead of looping forever."""
        o, _ = _make_orchestrator(_make_workflow())
        main_gh = MagicMock()
        wt_gh = MagicMock()
        caller_gh = MagicMock()
        wt_gh.get_current_commit.side_effect = [
            "head-before",
            "head-before",
            "head-before",
        ]
        wt_gh.resolve_commit.return_value = "main-head"
        wt_gh.get_current_branch.return_value = "auto-dev/fc82f22a"
        wt_gh._run_git.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),  # fetch
            MagicMock(
                returncode=1,
                stdout="CONFLICT (content): app/x.py\n",
                stderr="",
            ),
        ]
        wt_gh.get_unmerged_paths.side_effect = [["app/x.py"], ["app/x.py"], []]
        wt_gh.get_conflict_marker_paths.return_value = []
        wt_gh.get_worktree_changed_paths.return_value = ["app/x.py"]
        _set_unchanged_index(wt_gh)
        mock_gh_cls.side_effect = [main_gh, wt_gh]
        from app.modules.workspace.autonomous.models import AgentTaskResult

        o._run_agent = MagicMock(
            return_value=AgentTaskResult(
                session_id="resolver", success=True, response_text="resolved"
            )
        )

        import pytest

        with pytest.raises(RuntimeError, match="made no commit"):
            o._resolve_merge_conflicts(caller_gh, "auto-dev/fc82f22a", 1103)

        wt_gh.git_push.assert_not_called()
        main_gh.remove_worktree.assert_called_once()

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_post_stage_unmerged_paths_fail_before_commit(self, mock_gh_cls):
        """Trusted staging must actually clear every U-stage entry."""
        o, _ = _make_orchestrator(_make_workflow())
        main_gh = MagicMock()
        wt_gh = MagicMock()
        caller_gh = MagicMock()
        wt_gh.get_current_commit.return_value = "head-before"
        wt_gh.resolve_commit.return_value = "main-head"
        wt_gh.get_current_branch.return_value = "auto-dev/fc82f22a"
        wt_gh._run_git.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=1, stdout="CONFLICT (content): app/x.py\n", stderr=""),
        ]
        wt_gh.get_unmerged_paths.side_effect = [
            ["app/x.py"],
            ["app/x.py"],
            ["app/x.py"],
        ]
        wt_gh.get_conflict_marker_paths.return_value = []
        wt_gh.get_worktree_changed_paths.return_value = ["app/x.py"]
        _set_unchanged_index(wt_gh)
        mock_gh_cls.side_effect = [main_gh, wt_gh]
        from app.modules.workspace.autonomous.models import AgentTaskResult

        o._run_agent = MagicMock(
            return_value=AgentTaskResult(
                session_id="resolver", success=True, response_text="resolved"
            )
        )

        with pytest.raises(RuntimeError, match="unmerged paths after staging"):
            o._resolve_merge_conflicts(caller_gh, "auto-dev/fc82f22a", 1103)

        wt_gh.git_add_all.assert_called_once()
        wt_gh.git_commit.assert_not_called()
        wt_gh.git_push.assert_not_called()
        failed_updates = [
            call.args[1]
            for call in o.repo.update_milestone.call_args_list
            if call.args[1].get("status") == "failed"
        ]
        assert failed_updates

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_clean_merge_no_op_refuses_unchanged_push(self, mock_gh_cls):
        """A clean but unchanged merge must not be pushed and retried forever."""
        o, _ = _make_orchestrator(_make_workflow())
        main_gh = MagicMock()
        wt_gh = MagicMock()
        wt_gh._run_git.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="Already up to date.\n", stderr=""),
        ]
        wt_gh.get_current_branch.return_value = "auto-dev/fc82f22a"
        wt_gh.get_current_commit.side_effect = ["same-head", "same-head"]
        mock_gh_cls.side_effect = [main_gh, wt_gh]

        with pytest.raises(RuntimeError, match="made no commit"):
            o._resolve_merge_conflicts(MagicMock(), "auto-dev/fc82f22a", 1103)

        wt_gh.git_push.assert_not_called()

    @pytest.mark.parametrize("actual_branch", ["", "auto-dev/unrelated"])
    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_branch_mismatch_fails_closed(self, mock_gh_cls, actual_branch):
        """Empty or different branches are never rewritten into the push target."""
        o, _ = _make_orchestrator(_make_workflow())
        main_gh = MagicMock()
        wt_gh = MagicMock()
        wt_gh._run_git.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="merged\n", stderr=""),
        ]
        wt_gh.get_current_branch.return_value = actual_branch
        wt_gh.get_current_commit.side_effect = ["head-before", "head-after"]
        mock_gh_cls.side_effect = [main_gh, wt_gh]

        with pytest.raises(RuntimeError, match="branch mismatch"):
            o._resolve_merge_conflicts(MagicMock(), "auto-dev/fc82f22a", 1103)

        wt_gh.git_push.assert_not_called()

    @pytest.mark.parametrize("ancestry", [(False, True), (True, False), (None, True)])
    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_both_merge_parents_must_be_ancestors(self, mock_gh_cls, ancestry):
        """The resolved head must contain both the PR head and fetched main."""
        o, _ = _make_orchestrator(_make_workflow())
        main_gh = MagicMock()
        wt_gh = MagicMock()
        wt_gh._run_git.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="merged\n", stderr=""),
        ]
        wt_gh.get_current_branch.return_value = "auto-dev/fc82f22a"
        wt_gh.get_current_commit.side_effect = ["head-before", "head-after"]
        wt_gh.resolve_commit.return_value = "main-head"
        o._ancestor_check = MagicMock(side_effect=list(ancestry))
        mock_gh_cls.side_effect = [main_gh, wt_gh]

        with pytest.raises(RuntimeError, match="ancestry verification failed"):
            o._resolve_merge_conflicts(MagicMock(), "auto-dev/fc82f22a", 1103)

        assert o._ancestor_check.call_args_list[0].args[1:] == ("head-before", "head-after")
        assert o._ancestor_check.call_args_list[1].args[1:] == ("main-head", "head-after")
        wt_gh.git_push.assert_not_called()

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_scope_excludes_files_brought_in_from_main(self, mock_gh_cls):
        """An old PR may merge many upstream files while the resolver edits two."""
        o, _ = _make_orchestrator(_make_workflow())
        main_gh = MagicMock()
        wt_gh = MagicMock()
        caller_gh = MagicMock()
        wt_gh._run_git.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=1, stdout="CONFLICT (content): app/x.py\n", stderr=""),
        ]
        wt_gh.get_current_branch.return_value = "auto-dev/fc82f22a"
        wt_gh.get_current_commit.side_effect = [
            "old-pr-head",
            "old-pr-head",
            "resolved-head",
        ]
        wt_gh.resolve_commit.return_value = "fetched-main-head"
        wt_gh.get_unmerged_paths.side_effect = [["app/x.py"], ["app/x.py"], []]
        wt_gh.get_conflict_marker_paths.return_value = []
        wt_gh.get_worktree_changed_paths.return_value = ["app/x.py", "tests/test_x.py"]
        _set_unchanged_index(wt_gh)

        def changed_files(base, _head):
            if base == "old-pr-head":
                return [f"upstream/file-{index}.py" for index in range(97)]
            assert base == "fetched-main-head"
            return ["app/x.py", "tests/test_x.py"]

        wt_gh.get_changed_files.side_effect = changed_files
        o._ancestor_check = MagicMock(return_value=True)
        mock_gh_cls.side_effect = [main_gh, wt_gh]
        from app.modules.workspace.autonomous.models import AgentTaskResult

        o._run_agent = MagicMock(
            return_value=AgentTaskResult(
                session_id="resolver", success=True, response_text="77 passed"
            )
        )

        o._resolve_merge_conflicts(caller_gh, "auto-dev/fc82f22a", 1103)

        wt_gh.resolve_commit.assert_called_once_with("FETCH_HEAD")
        assert wt_gh._run_git.call_args_list[1].args[0] == [
            "merge",
            "fetched-main-head",
        ]
        wt_gh.get_changed_files.assert_called_once_with("fetched-main-head", "resolved-head")
        wt_gh.git_push.assert_called_once_with(branch="auto-dev/fc82f22a", force_with_lease=True)

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_resolver_actual_edit_set_still_enforces_scope_cap(self, mock_gh_cls):
        """Excluding upstream files must not weaken the resolver's own cap."""
        o, _ = _make_orchestrator(_make_workflow())
        main_gh = MagicMock()
        wt_gh = MagicMock()
        caller_gh = MagicMock()
        wt_gh._run_git.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=1, stdout="CONFLICT (content): app/x.py\n", stderr=""),
        ]
        wt_gh.get_current_commit.return_value = "old-pr-head"
        wt_gh.resolve_commit.return_value = "fetched-main-head"
        wt_gh.get_current_branch.return_value = "auto-dev/fc82f22a"
        wt_gh.get_unmerged_paths.return_value = ["app/x.py"]
        wt_gh.get_conflict_marker_paths.return_value = []
        wt_gh.get_worktree_changed_paths.return_value = [
            f"agent/file-{index}.py" for index in range(61)
        ]
        _set_unchanged_index(wt_gh)
        mock_gh_cls.side_effect = [main_gh, wt_gh]
        from app.modules.workspace.autonomous.models import AgentTaskResult

        o._run_agent = MagicMock(
            return_value=AgentTaskResult(
                session_id="resolver", success=True, response_text="resolved"
            )
        )

        with pytest.raises(RuntimeError, match="resolver scope rejected.*61 files"):
            o._resolve_merge_conflicts(caller_gh, "auto-dev/fc82f22a", 1103)

        wt_gh.git_add_all.assert_not_called()
        wt_gh.git_commit.assert_not_called()
        wt_gh.git_push.assert_not_called()

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_agent_index_mutations_cannot_bypass_scope_cap(self, mock_gh_cls):
        """Absolute git or scripts that stage files are included in resolver scope."""
        o, _ = _make_orchestrator(_make_workflow())
        main_gh = MagicMock()
        wt_gh = MagicMock()
        caller_gh = MagicMock()
        wt_gh._run_git.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=1, stdout="CONFLICT (content): agent/file-0.py\n", stderr=""),
        ]
        wt_gh.get_current_commit.return_value = "old-pr-head"
        wt_gh.resolve_commit.return_value = "fetched-main-head"
        wt_gh.get_current_branch.return_value = "auto-dev/fc82f22a"
        wt_gh.get_unmerged_paths.return_value = ["agent/file-0.py"]
        wt_gh.get_conflict_marker_paths.return_value = []
        # A staged file disappears from normal `git diff`; only the remaining
        # conflict is visible in the working tree.
        wt_gh.get_worktree_changed_paths.return_value = ["agent/file-0.py"]
        before = {f"agent/file-{index}.py": (f"100644 old-{index} 0",) for index in range(61)}
        after = {f"agent/file-{index}.py": (f"100644 new-{index} 0",) for index in range(61)}
        wt_gh.get_index_snapshot.side_effect = [before, after]
        wt_gh.get_index_changed_paths.side_effect = GitHubOps.get_index_changed_paths
        mock_gh_cls.side_effect = [main_gh, wt_gh]
        from app.modules.workspace.autonomous.models import AgentTaskResult

        o._run_agent = MagicMock(
            return_value=AgentTaskResult(
                session_id="resolver", success=True, response_text="resolved and staged"
            )
        )

        with pytest.raises(RuntimeError, match="changed the Git index"):
            o._resolve_merge_conflicts(caller_gh, "auto-dev/fc82f22a", 1103)

        wt_gh.git_add_all.assert_not_called()
        wt_gh.git_commit.assert_not_called()
        wt_gh.git_push.assert_not_called()

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_conflict_milestone_records_tldr_and_summary(self, mock_gh_cls):
        """The conflicts_resolved milestone must store result_summary and tldr
        so users can see what the agent did (which files, test results) in the
        timeline — without digging through CI logs."""
        o, _ = _make_orchestrator(_make_workflow())
        main_gh = MagicMock()
        wt_gh = MagicMock()
        caller_gh = MagicMock()
        wt_gh._run_git.side_effect = [
            MagicMock(),  # fetch
            MagicMock(
                returncode=1,
                stdout="CONFLICT (content): Merge conflict in app/x.py\n",
                stderr="",
            ),  # merge (conflict)
        ]
        mock_gh_cls.side_effect = [main_gh, wt_gh, caller_gh]
        _set_valid_merge_result(o, wt_gh)

        o._run_agent = MagicMock()
        from app.modules.workspace.autonomous.models import AgentTaskResult

        agent_summary = (
            "解决了 auth_service.py 和 message_repo.py 的冲突。\n"
            "执行了 pytest：42 passed, 0 failed。"
        )
        o._run_agent.return_value = AgentTaskResult(
            session_id="s1", success=True, response_text=agent_summary
        )
        o._resolve_session_line = MagicMock(return_value=("sess", None, False))
        o._link_session_to_current_milestone = MagicMock()

        o._resolve_merge_conflicts(caller_gh, "auto-dev/fc82f22a", 1103)

        # milestone update must include result_summary + tldr.
        update_call = o.repo.update_milestone.call_args
        milestone_data = update_call[0][1]
        assert milestone_data["result_summary"] == agent_summary
        assert milestone_data["tldr"] is not None

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_main_repo_never_checked_out(self, mock_gh_cls):
        """The main repo gh must NOT receive checkout/reset/merge calls."""
        o, _ = _make_orchestrator(_make_workflow())
        main_gh = MagicMock()
        wt_gh = MagicMock()
        caller_gh = MagicMock()
        wt_gh._run_git.side_effect = [
            MagicMock(),  # fetch
            MagicMock(returncode=0, stdout="", stderr=""),  # merge clean
        ]
        mock_gh_cls.side_effect = [main_gh, wt_gh, caller_gh]
        _set_valid_merge_result(o, wt_gh, conflict=False)

        o._resolve_merge_conflicts(caller_gh, "auto-dev/fc82f22a", 1103)

        # main_gh should only see add_worktree + remove_worktree, never
        # checkout/reset/merge/clean (those polluted the shared repo).
        # main_gh._run_git should never be called (all ops are on wt_gh).
        assert (
            not main_gh._run_git.called
        ), f"main repo received git calls: {main_gh._run_git.call_args_list}"

    @patch("app.modules.workspace.autonomous.orchestrator.GitHubOps")
    def test_removes_existing_worktree_before_adding_temp(self, mock_gh_cls):
        """Git forbids the same branch in two worktrees, so the workflow's own
        worktree must be removed first to free the branch for the temp worktree.
        """
        # worktree_path is non-empty — simulates a worktree-strategy workflow
        # entering merge resolution with its dev worktree still attached.
        wf = _make_workflow(worktree_path="/srv/repo/../auto-dev-fc82f22a")
        o, _ = _make_orchestrator(wf)
        main_gh = MagicMock()
        rebound_gh = MagicMock()  # gh rebound to project_path after worktree removal
        wt_gh = MagicMock()
        wt_gh._run_git.side_effect = [
            MagicMock(),  # fetch
            MagicMock(returncode=0, stdout="", stderr=""),  # merge clean
        ]
        # GitHubOps construction order: main_gh, rebound_gh (after removal), wt_gh
        mock_gh_cls.side_effect = [main_gh, rebound_gh, wt_gh]
        _set_valid_merge_result(o, wt_gh, conflict=False)

        # caller_gh simulates the stale handle from _do_merge; it should be
        # replaced by rebound_gh after worktree removal.
        stale_gh = MagicMock()
        o._resolve_merge_conflicts(stale_gh, "auto-dev/fc82f22a", 1103)

        # The existing worktree was removed BEFORE the temp one was added.
        remove_calls = main_gh.remove_worktree.call_args_list
        assert len(remove_calls) == 2  # original + temp
        # First removal is the original worktree_path.
        assert remove_calls[0].args[0] == wf["worktree_path"]
        # worktree_path was cleared in DB.
        o._update_workflow.assert_any_call({"worktree_path": ""})
        # Then the temp worktree was added (branch now free).
        main_gh.add_worktree.assert_called_once()
        # Second removal is the temp worktree (finally cleanup).
        temp_path = main_gh.add_worktree.call_args.args[0]
        assert remove_calls[1].args[0] == temp_path
        # _resolve_merge_conflicts only pushes now — merge is deferred to
        # _do_merge's next cycle. No merge_pr call on either gh.
        rebound_gh.merge_pr.assert_not_called()
        stale_gh.merge_pr.assert_not_called()


# ── github_ops.add_worktree (no -b) ──────────────────────────────────────


class TestAddWorktreeExistingBranch:
    def test_add_worktree_no_b_flag(self):
        """add_worktree checks out an existing branch (no -b)."""
        gh = GitHubOps("/tmp/repo")
        with patch.object(gh, "_run_git") as mock_run:
            gh.add_worktree("/tmp/wt", "feature/existing")
            cmd = mock_run.call_args.args[0]
            assert cmd == ["worktree", "add", "/tmp/wt", "feature/existing"]
            # No -b flag — the branch already exists.
            assert "-b" not in cmd

    def test_add_worktree_returns_path_and_branch(self):
        gh = GitHubOps("/tmp/repo")
        with patch.object(gh, "_run_git"):
            result = gh.add_worktree("/tmp/wt", "feature/x")
            assert result == {"worktree_path": "/tmp/wt", "branch": "feature/x"}
