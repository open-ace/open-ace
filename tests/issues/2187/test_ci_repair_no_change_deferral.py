"""Tests for CI repair genuine no-code-change deferral (issue #2187).

When the CI repair agent runs cleanly but produces no code changes, the round
must defer to the next scheduler cycle WITHOUT consuming a real
``ci_repair_attempts`` slot. A separate counter ``ci_repair_no_change_retries``
(bounded by ``MAX_CI_REPAIR_NO_CHANGE_RETRIES``) tracks these deferrals so a
perpetually-empty agent cannot loop forever, but a single empty round no longer
terminal-fails the workflow (the pre-fix bug that wasted 4 of 5 attempts on
issue 2187).

Mirrors tests/issues/1820 (transient deferral) in structure.
"""

from unittest.mock import MagicMock, patch

from app.modules.workspace.autonomous.orchestrator import (
    MAX_CI_REPAIR_NO_CHANGE_RETRIES,
    MAX_MERGE_FAIL_DEV_ROUNDS,
)


def _make_workflow(**overrides):
    base = {
        "workflow_id": "wf-2187",
        "user_id": 1,
        "status": "merging",
        "current_phase": "merge",
        "ci_repair_attempts": 1,
        "ci_repair_transient_retries": 0,
        "ci_repair_no_change_retries": 0,
        "ci_diagnostics_attempts": 0,
        "last_ci_failure_signature": "",
        "last_ci_failure_head_sha": "",
        "branch_name": "auto-dev/wf-2187",
        "branch_strategy": "worktree",
        "worktree_path": "/tmp/repo",
        "preferred_worktree_path": "/tmp/repo",
        "dev_round": 1,
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


_FAILED_CHECKS = [
    {
        "name": "schema-sync",
        "state": "failure",
        "bucket": "fail",
        "link": "https://github.com/open-ace/open-ace/runs/123",
    }
]


# ── 1. No-change-deferred round does NOT consume a real attempt ──


def test_no_change_deferred_does_not_increment_attempts():
    """Re-entering after a no-change deferral must NOT bump ci_repair_attempts,
    and must increment ci_repair_no_change_retries."""
    wf = _make_workflow(
        error_message="CI repair deferred: agent produced no code changes",
        ci_repair_attempts=2,
        ci_repair_no_change_retries=0,
    )
    orch, mock_repo = _make_orchestrator(wf)
    gh = MagicMock()
    gh.get_pr_head_sha.return_value = "sha-new"
    gh.get_check_failure_excerpt.return_value = "schema-sync failed\n1 error"
    orch._get_gh = MagicMock(return_value=gh)
    orch._run_merge_ci_repair = MagicMock()

    orch._start_ci_repair_round(wf, 2187, _FAILED_CHECKS)

    orch._run_merge_ci_repair.assert_called_once()
    updates = [c.args[1] for c in mock_repo.update_workflow.call_args_list]
    assert any(u.get("ci_repair_attempts") == 2 for u in updates), "real attempts unchanged"
    assert any(u.get("ci_repair_no_change_retries") == 1 for u in updates), "no-change counter 0→1"


# ── 2. After MAX_CI_REPAIR_NO_CHANGE_RETRIES, the workflow leaves the loop ──


def test_no_change_retries_exhausted_escalates_under_cap():
    """When no-change retries hit the cap, the workflow leaves the repair loop.

    #2443 PR-C: ci_repair_no_change_exhausted is a Tier1 category, so under the
    dev-round cap (with a recoverable PR branch) it escalates to a fresh
    development round instead of terminal-failing; only at the cap does it fall
    through to failed. Either way the agent is NOT run again and the dedicated
    exhausted milestone is recorded.
    """
    # Under cap → Tier1 escalation to development.
    wf = _make_workflow(
        error_message="CI repair deferred: agent produced no code changes",
        ci_repair_attempts=2,
        ci_repair_no_change_retries=MAX_CI_REPAIR_NO_CHANGE_RETRIES,
        merge_fail_dev_rounds=0,
    )
    orch, mock_repo = _make_orchestrator(wf)
    gh = MagicMock()
    gh.get_pr_head_sha.return_value = "sha-new"
    gh.get_check_failure_excerpt.return_value = "schema-sync failed\n1 error"
    orch._get_gh = MagicMock(return_value=gh)
    orch._run_merge_ci_repair = MagicMock()

    orch._start_ci_repair_round(wf, 2187, _FAILED_CHECKS)

    orch._run_merge_ci_repair.assert_not_called()
    updates = [c.args[1] for c in mock_repo.update_workflow.call_args_list]
    assert any(u.get("status") == "developing" for u in updates)
    assert not any(u.get("status") == "failed" for u in updates)
    assert any(
        c.args[0].get("milestone_type") == "ci_repair_no_change_exhausted"
        for c in mock_repo.create_milestone.call_args_list
    )

    # At the dev-round cap → Tier2 fall-through to terminal failed.
    wf_cap = _make_workflow(
        error_message="CI repair deferred: agent produced no code changes",
        ci_repair_attempts=2,
        ci_repair_no_change_retries=MAX_CI_REPAIR_NO_CHANGE_RETRIES,
        merge_fail_dev_rounds=MAX_MERGE_FAIL_DEV_ROUNDS,
    )
    orch2, mock_repo2 = _make_orchestrator(wf_cap)
    gh2 = MagicMock()
    gh2.get_pr_head_sha.return_value = "sha-new"
    gh2.get_check_failure_excerpt.return_value = "schema-sync failed\n1 error"
    orch2._get_gh = MagicMock(return_value=gh2)
    orch2._run_merge_ci_repair = MagicMock()
    orch2._start_ci_repair_round(wf_cap, 2187, _FAILED_CHECKS)
    cap_updates = [c.args[1] for c in mock_repo2.update_workflow.call_args_list]
    assert any(u.get("status") == "failed" for u in cap_updates)
    assert any("no code changes" in (u.get("error_message") or "").lower() for u in cap_updates)


# ── 3. Change-producing (fresh) round resets the no-change counter ──


def test_change_producing_round_resets_no_change_counter():
    """A fresh round (no deferral prefix) resets no_change_retries to 0 and
    consumes a real attempt — same as the transient reset."""
    wf = _make_workflow(
        error_message="",  # no deferral signal
        ci_repair_attempts=1,
        ci_repair_no_change_retries=2,  # stale from prior no-change cycles
    )
    orch, mock_repo = _make_orchestrator(wf)
    gh = MagicMock()
    gh.get_pr_head_sha.return_value = "sha-new"
    gh.get_check_failure_excerpt.return_value = "schema-sync failed\n1 error"
    orch._get_gh = MagicMock(return_value=gh)
    orch._run_merge_ci_repair = MagicMock()

    orch._start_ci_repair_round(wf, 2187, _FAILED_CHECKS)

    orch._run_merge_ci_repair.assert_called_once()
    updates = [c.args[1] for c in mock_repo.update_workflow.call_args_list]
    assert any(u.get("ci_repair_attempts") == 2 for u in updates), "fresh round consumes an attempt"
    assert any(u.get("ci_repair_no_change_retries") == 0 for u in updates), "counter reset to 0"


# ── 4. Diagnostics-incomplete preserves the no-change signal ──


def test_diagnostics_incomplete_preserves_no_change_signal():
    """When diagnostics are incomplete during a no-change-deferred round, the
    error_message must retain the no-change prefix and the counter must persist,
    so the next cycle still classifies it as a no-change retry (mirrors #1820)."""
    wf = _make_workflow(
        error_message="CI repair deferred: agent produced no code changes",
        ci_repair_attempts=2,
        ci_repair_no_change_retries=1,
        ci_diagnostics_attempts=0,
    )
    orch, mock_repo = _make_orchestrator(wf)
    gh = MagicMock()
    gh.get_pr_head_sha.return_value = "sha-new"
    gh.get_check_failure_excerpt.return_value = ""  # no logs → diagnostics incomplete
    orch._get_gh = MagicMock(return_value=gh)
    orch._run_merge_ci_repair = MagicMock()

    orch._start_ci_repair_round(wf, 2187, _FAILED_CHECKS)

    orch._run_merge_ci_repair.assert_not_called()
    updates = [c.args[1] for c in mock_repo.update_workflow.call_args_list]
    assert any(
        u.get("error_message", "").startswith("CI repair deferred: agent produced no code changes")
        for u in updates
    ), [u.get("error_message") for u in updates]
    assert any(u.get("ci_repair_no_change_retries") == 2 for u in updates)
    assert not any(u.get("ci_repair_attempts", 0) > 2 for u in updates)
