"""Regression tests for CI repair milestone round_number (Issue #1851).

Root cause: ``_create_milestone`` is idempotent via ``_find_existing_milestone``,
which matches on ``(phase, milestone_type, dev_round, round_number)``. The four
``ci_repair_*`` milestones omitted ``round_number``, so every attempt within one
dev_round produced an identical match key. When attempt 1 succeeded (milestone
became ``completed``), attempt 2's ``_create_milestone`` calls for
``ci_repair_started`` / ``ci_repair_applied`` hit the idempotency guard and
returned attempt 1's milestone instead of creating new ones. ``_run_merge_ci_repair``
reused attempt 1's ``repair_ms``; the resumed agent produced no new commit; the
SHA-unchanged branch then overwrote attempt 1's success into
``"agent produced no code changes"`` and failed the workflow — even though
``MAX_CI_REPAIR_ATTEMPTS`` is 3 and only 2 had run.

Fix: each CI repair milestone now carries ``round_number=<attempt>``, matching
the pattern already used by every other multi-round milestone (plan_created,
pr_reviewed, ...). The guard still protects against true scheduler re-entrancy
(same attempt) but no longer collapses distinct attempts together.
"""

from unittest.mock import MagicMock, patch


def _make_workflow(**overrides):
    base = {
        "workflow_id": "wf-1851",
        "user_id": 1,
        "title": "issue-1851",
        "status": "merging",
        "requirements_text": "Fix CI",
        "requirements_issue_url": "",
        "project_path": "/tmp/repo",
        "project_repo_url": "",
        "is_new_project": False,
        "cli_tool": "claude-code",
        "model": "claude-sonnet-4-6",
        "permission_mode": "auto-edit",
        "branch_name": "auto-dev/wf-1851",
        "branch_strategy": "worktree",
        "workspace_type": "local",
        "remote_machine_id": "",
        "worktree_path": "/tmp/repo",
        "preferred_worktree_path": "/tmp/repo",
        "github_issue_number": 1851,
        "github_pr_number": 1859,
        "github_pr_url": "",
        "current_phase": "merge",
        "current_round": 0,
        "dev_round": 1,
        "max_plan_rounds": 3,
        "max_pr_review_rounds": 5,
        "require_full_review_rounds": False,
        "total_tokens": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_requests": 0,
        "error_message": "",
        "last_ci_failure_head_sha": "",
        "last_ci_failure_signature": "",
        "ci_repair_attempts": 0,
    }
    base.update(overrides)
    return base


def _make_orchestrator(wf_data):
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    with (
        patch("app.modules.workspace.autonomous.orchestrator.Database"),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
        ) as mock_repo_cls,
    ):
        mock_repo = MagicMock()
        mock_repo.get_workflow.return_value = wf_data
        mock_repo.list_milestones.return_value = []
        mock_repo.create_milestone.return_value = {
            "milestone_id": "ms-1",
            "workflow_id": wf_data["workflow_id"],
        }
        mock_repo.create_event.return_value = {"id": 1}
        mock_repo.update_workflow.return_value = wf_data
        mock_repo_cls.return_value = mock_repo

        orch = AutonomousOrchestrator(wf_data["workflow_id"])
        orch.repo = mock_repo
        orch.emitter = MagicMock()
        orch._sync_failed_pr_with_main = MagicMock(return_value=False)
    return orch, mock_repo


def _capturing_repo(wf_data):
    """Repo mock whose create_milestone returns a type+round-specific id.

    Mirrors the tests/issues/1813 side_effect pattern so callers can assert
    which milestone was created/returned by inspecting update_milestone args.
    """
    from unittest.mock import MagicMock, patch

    mock_repo = MagicMock()
    mock_repo.get_workflow.return_value = wf_data
    mock_repo.list_milestones.return_value = []
    mock_repo.create_event.return_value = {"id": 1}
    mock_repo.update_workflow.return_value = wf_data

    def fake_create(kwargs):
        rn = kwargs.get("round_number")
        rn_part = f"-r{rn}" if rn is not None else ""
        return {
            "milestone_id": f"ms-{kwargs.get('milestone_type', '')}{rn_part}",
            "workflow_id": wf_data["workflow_id"],
        }

    mock_repo.create_milestone.side_effect = fake_create
    return mock_repo


class TestCiRepairMilestoneCarriesRoundNumber:
    """Each CI repair milestone must encode the attempt in round_number."""

    def test_start_ci_repair_round_started_carries_next_attempt(self):
        """ci_repair_started created with round_number == next_attempt."""
        wf = _make_workflow(ci_repair_attempts=0)  # next_attempt = 1
        orch, mock_repo = _make_orchestrator(wf)
        gh = MagicMock()
        gh.get_pr_head_sha.return_value = "sha-1"
        gh.get_check_failure_excerpt.return_value = "black failed"
        orch._get_gh = MagicMock(return_value=gh)
        orch._run_merge_ci_repair = MagicMock()

        orch._start_ci_repair_round(
            wf,
            1859,
            [{"name": "lint", "state": "failure", "bucket": "fail"}],
        )

        started_calls = [
            c
            for c in mock_repo.create_milestone.call_args_list
            if c.args and c.args[0].get("milestone_type") == "ci_repair_started"
        ]
        assert started_calls, "ci_repair_started milestone was not created"
        assert started_calls[0].args[0]["round_number"] == 1

    def test_start_ci_repair_round_started_round_number_advances_on_attempt_2(self):
        """attempt 2 (ci_repair_attempts=1) → round_number=2."""
        wf = _make_workflow(ci_repair_attempts=1, last_ci_failure_signature="sig-a")
        orch, mock_repo = _make_orchestrator(wf)
        gh = MagicMock()
        gh.get_pr_head_sha.return_value = "sha-2"
        gh.get_check_failure_excerpt.return_value = "black failed"
        orch._get_gh = MagicMock(return_value=gh)
        orch._run_merge_ci_repair = MagicMock()

        orch._start_ci_repair_round(
            wf,
            1859,
            [{"name": "lint", "state": "failure", "bucket": "fail"}],
        )

        started_calls = [
            c
            for c in mock_repo.create_milestone.call_args_list
            if c.args and c.args[0].get("milestone_type") == "ci_repair_started"
        ]
        assert started_calls
        assert started_calls[0].args[0]["round_number"] == 2

    def test_run_merge_ci_repair_applied_carries_attempt(self):
        """ci_repair_applied created with round_number == attempt."""
        from app.modules.workspace.autonomous.orchestrator import AgentTaskResult

        wf = _make_workflow(ci_repair_attempts=2)  # attempt = 2
        orch, mock_repo = _make_orchestrator(wf)
        gh = MagicMock()
        gh.get_pr_head_sha.return_value = "sha-2"
        gh.get_current_commit.return_value = "sha-3"
        orch._get_gh = MagicMock(return_value=gh)
        orch._run_agent = MagicMock(
            return_value=AgentTaskResult(success=True, session_id="s1", error="")
        )
        orch._build_merge_ci_repair_agent_prompt = MagicMock(return_value="prompt")
        orch._accumulate_tokens = MagicMock()
        orch._artifact_text = MagicMock(return_value="")
        orch._artifact_tldr = MagicMock(return_value="")
        orch._build_dev_result_summary = MagicMock(return_value="summary")
        orch._post_github_comment = MagicMock()

        orch._run_merge_ci_repair(
            wf,
            gh,
            1859,
            [{"name": "lint", "state": "failure", "bucket": "fail"}],
        )

        applied_calls = [
            c
            for c in mock_repo.create_milestone.call_args_list
            if c.args and c.args[0].get("milestone_type") == "ci_repair_applied"
        ]
        assert applied_calls, "ci_repair_applied milestone was not created"
        assert applied_calls[0].args[0]["round_number"] == 2


class TestFindExistingMilestoneRespectsRoundNumber:
    """The idempotency guard must NOT merge milestones across attempts."""

    def test_find_existing_milestone_returns_none_when_round_number_differs(self):
        """Core regression: attempt 1's completed ci_repair_started (round_number=1)
        must not be returned for attempt 2 (round_number=2)."""
        wf = _make_workflow()
        orch, mock_repo = _make_orchestrator(wf)

        attempt1_started = {
            "milestone_id": "ms-attempt1",
            "milestone_type": "ci_repair_started",
            "phase": "merge",
            "dev_round": 1,
            "round_number": 1,
            "status": "completed",
        }
        # _find_existing_milestone queries in_progress then completed
        mock_repo.list_milestones.side_effect = [[], [attempt1_started]]

        result = orch._find_existing_milestone(
            phase="merge",
            milestone_type="ci_repair_started",
            dev_round=1,
            round_number=2,  # attempt 2
        )
        assert result is None, (
            "Idempotency guard must not return attempt 1's milestone for attempt 2 "
            "(round_number differs) — this was the #1851 root cause"
        )

    def test_find_existing_milestone_returns_match_when_round_number_matches(self):
        """Scheduler re-entrancy (same attempt) must still hit idempotency."""
        wf = _make_workflow()
        orch, mock_repo = _make_orchestrator(wf)

        same_attempt = {
            "milestone_id": "ms-attempt1",
            "milestone_type": "ci_repair_started",
            "phase": "merge",
            "dev_round": 1,
            "round_number": 1,
            "status": "completed",
        }
        mock_repo.list_milestones.side_effect = [[], [same_attempt]]

        result = orch._find_existing_milestone(
            phase="merge",
            milestone_type="ci_repair_started",
            dev_round=1,
            round_number=1,  # same attempt → re-entrancy
        )
        assert result is not None
        assert result["milestone_id"] == "ms-attempt1"


class TestSecondAttemptCreatesDistinctMilestone:
    """End-to-end regression: attempt 2 must create its own ci_repair_started."""

    def test_attempt2_does_not_reuse_attempt1_started_milestone(self):
        """Simulate attempt 1 having produced a completed ci_repair_started
        milestone, then run _start_ci_repair_round for attempt 2. The guard
        (list_milestones returns attempt 1's completed milestone) must NOT
        suppress creation of attempt 2's ci_repair_started."""
        wf = _make_workflow(
            ci_repair_attempts=1,
            last_ci_failure_signature="sig-a",
            last_ci_failure_head_sha="sha-1",
        )
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        attempt1_started = {
            "milestone_id": "ms-attempt1-started",
            "milestone_type": "ci_repair_started",
            "phase": "merge",
            "dev_round": 1,
            "round_number": 1,
            "status": "completed",
        }

        with (
            patch("app.modules.workspace.autonomous.orchestrator.Database"),
            patch(
                "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
            ) as mock_repo_cls,
        ):
            mock_repo = MagicMock()
            mock_repo.get_workflow.return_value = wf
            mock_repo.create_event.return_value = {"id": 1}
            mock_repo.update_workflow.return_value = wf
            # First pair checks for a stale diagnostics milestone; second pair
            # checks the attempt-specific ci_repair_started milestone.
            mock_repo.list_milestones.side_effect = [[], [], [], [attempt1_started]]
            mock_repo.create_milestone.return_value = {
                "milestone_id": "ms-attempt2-started",
                "workflow_id": wf["workflow_id"],
            }
            mock_repo_cls.return_value = mock_repo

            orch = AutonomousOrchestrator(wf["workflow_id"])
            orch.repo = mock_repo
            orch.emitter = MagicMock()
            orch._sync_failed_pr_with_main = MagicMock(return_value=False)
            gh = MagicMock()
            gh.get_pr_head_sha.return_value = "sha-2"
            gh.get_check_failure_excerpt.return_value = "black failed"
            orch._get_gh = MagicMock(return_value=gh)
            orch._run_merge_ci_repair = MagicMock()

            orch._start_ci_repair_round(
                wf,
                1859,
                [{"name": "lint", "state": "failure", "bucket": "fail"}],
            )

        # The fix: ci_repair_started for attempt 2 has round_number=2, so the
        # guard (which only matches attempt 1's round_number=1) does NOT dedupe
        # it. create_milestone must have been called.
        created_types = [
            c.args[0].get("milestone_type")
            for c in mock_repo.create_milestone.call_args_list
            if c.args
        ]
        assert "ci_repair_started" in created_types, (
            "Attempt 2 must create its own ci_repair_started milestone instead of "
            "reusing attempt 1's (#1851 regression)"
        )
