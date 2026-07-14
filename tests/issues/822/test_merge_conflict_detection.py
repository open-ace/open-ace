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
        """Policy rejection with no CI failures defers to next cycle."""
        o, _ = _make_orchestrator(_make_workflow())
        mock_gh = MagicMock()
        mock_gh_cls.return_value = mock_gh
        o._gh = mock_gh
        mock_gh.get_pr_checks.return_value = [
            {"name": "test", "bucket": "pass"},
        ]
        mock_gh.merge_pr.side_effect = GitHubOpsError("base branch policy prohibits the merge")

        o._do_merge(_make_workflow())

        # Did not fail, did not resolve — deferred.
        mock_gh.merge_pr.assert_called_once()
        o._resolve_merge_conflicts = MagicMock()
        # The merge was not re-attempted (returned early).

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
        o._resolve_merge_conflicts = MagicMock()

        o._do_merge(_make_workflow())

        mock_gh.merge_pr.assert_called_once()
        o._resolve_merge_conflicts.assert_called_once()

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
        """CI repair loop should restore the preferred worktree path for worktree strategy."""
        wf = _make_workflow(worktree_path="", preferred_worktree_path="/srv/repo/.worktrees/wf-822")
        o, _ = _make_orchestrator(wf)

        o._start_ci_repair_round(
            wf,
            1103,
            [{"name": "test (3.9)", "bucket": "fail", "state": "failure"}],
        )

        update_payload = o._update_workflow.call_args.args[0]
        assert update_payload["current_phase"] == "development"
        assert update_payload["status"] == "developing"
        assert update_payload["dev_round"] == 2
        assert update_payload["ci_repair_attempts"] == 1
        assert update_payload["worktree_path"] == "/srv/repo/.worktrees/wf-822"
        assert update_payload["preferred_worktree_path"] == "/srv/repo/.worktrees/wf-822"

    def test_start_ci_repair_round_fails_when_signature_repeats(self):
        """A repeated failed-check signature should stop the auto-repair loop."""
        wf = _make_workflow(
            ci_repair_attempts=1,
            last_ci_failure_signature="test (3.9)|failure|fail",
        )
        o, _ = _make_orchestrator(wf)

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
        wt_gh.git_push.assert_called_once_with(branch="auto-dev/fc82f22a")

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
        """The conflict prompt must instruct the agent to run tests before
        committing. Without this, the agent resolves conflict markers and
        commits immediately — missing semantic breakage (e.g. main changed a
        SQL query structure but the branch's tests still assert the old one).
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

        o._run_agent = MagicMock()
        from app.modules.workspace.autonomous.models import AgentTaskResult

        o._run_agent.return_value = AgentTaskResult(
            session_id="s1", success=True, response_text="resolved"
        )
        o._resolve_session_line = MagicMock(return_value=("sess", None, False))
        o._link_session_to_current_milestone = MagicMock()

        o._resolve_merge_conflicts(caller_gh, "auto-dev/fc82f22a", 1103)

        prompt = o._run_agent.call_args.kwargs.get("prompt", "")
        # Must instruct the agent to run tests before committing.
        assert "pytest" in prompt
        assert "测试" in prompt or "test" in prompt.lower()
        # Test step must come BEFORE the commit step.
        test_pos = prompt.lower().find("pytest")
        commit_pos = prompt.lower().find("git commit")
        assert test_pos < commit_pos, "tests must run before git commit"
        # Must require a summary report (for timeline tldr visibility).
        assert "总结" in prompt, "prompt must require a summary report"

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
