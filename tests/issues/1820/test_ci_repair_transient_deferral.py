"""Tests for CI repair transient-API-error deferral (issue #1820).

When the CI repair agent produces no code changes due to a transient API error
(429/5xx/overload), the workflow must defer to the next scheduler cycle
WITHOUT consuming a real ``ci_repair_attempts`` slot. A separate counter
``ci_repair_transient_retries`` (bounded by ``MAX_CI_REPAIR_TRANSIENT_RETRIES``)
tracks these deferrals so a sustained outage cannot loop forever.

These tests verify:
1. A transient-deferred round does NOT increment ``ci_repair_attempts``.
2. The transient retry counter IS incremented.
3. After ``MAX_CI_REPAIR_TRANSIENT_RETRIES``, the workflow is marked failed.
4. Diagnostics-incomplete polling preserves the transient signal and counter.
5. A non-transient round resets the transient counter to 0.
"""

from unittest.mock import MagicMock, patch

from app.modules.workspace.autonomous.orchestrator import MAX_CI_REPAIR_TRANSIENT_RETRIES


def _make_workflow(**overrides):
    base = {
        "workflow_id": "wf-1820",
        "user_id": 1,
        "status": "merging",
        "current_phase": "merge",
        "ci_repair_attempts": 1,
        "ci_repair_transient_retries": 0,
        "ci_diagnostics_attempts": 0,
        "last_ci_failure_signature": "",
        "last_ci_failure_head_sha": "",
        "branch_name": "auto-dev/wf-1820",
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
        "name": "lint",
        "state": "failure",
        "bucket": "fail",
        "link": "https://github.com/open-ace/open-ace/runs/123",
    }
]


# ── 1. Transient-deferred round does NOT consume a real attempt ──


def test_transient_deferred_does_not_increment_attempts():
    """Re-entering after a transient deferral must NOT bump ci_repair_attempts."""
    wf = _make_workflow(
        error_message="CI repair deferred: transient API error - 503 Service Unavailable",
        ci_repair_attempts=2,
        ci_repair_transient_retries=1,
    )
    orch, mock_repo = _make_orchestrator(wf)
    gh = MagicMock()
    gh.get_pr_head_sha.return_value = "sha-new"
    gh.get_check_failure_excerpt.return_value = "pytest failed\n1 failed"
    orch._get_gh = MagicMock(return_value=gh)
    orch._run_merge_ci_repair = MagicMock()

    orch._start_ci_repair_round(wf, 1820, _FAILED_CHECKS)

    orch._run_merge_ci_repair.assert_called_once()
    updates = [c.args[1] for c in mock_repo.update_workflow.call_args_list]
    # ci_repair_attempts must NOT have been incremented (stays at 2)
    assert any(u.get("ci_repair_attempts") == 2 for u in updates)
    # ci_repair_transient_retries must have been incremented (1 → 2)
    assert any(u.get("ci_repair_transient_retries") == 2 for u in updates)


# ── 2. After MAX_CI_REPAIR_TRANSIENT_RETRIES, workflow is marked failed ──


def test_transient_retries_exhausted_marks_failed():
    """When transient retries hit the cap, the workflow fails."""
    wf = _make_workflow(
        error_message="CI repair deferred: transient API error - 503 Service Unavailable",
        ci_repair_attempts=2,
        ci_repair_transient_retries=MAX_CI_REPAIR_TRANSIENT_RETRIES,
    )
    orch, mock_repo = _make_orchestrator(wf)
    gh = MagicMock()
    gh.get_pr_head_sha.return_value = "sha-new"
    gh.get_check_failure_excerpt.return_value = "pytest failed\n1 failed"
    orch._get_gh = MagicMock(return_value=gh)
    orch._run_merge_ci_repair = MagicMock()

    orch._start_ci_repair_round(wf, 1820, _FAILED_CHECKS)

    # Agent must NOT be called — the workflow is terminal
    orch._run_merge_ci_repair.assert_not_called()
    updates = [c.args[1] for c in mock_repo.update_workflow.call_args_list]
    assert any(u.get("status") == "failed" for u in updates)
    assert any("transient" in (u.get("error_message") or "").lower() for u in updates)


# ── 3. Non-transient round resets the transient counter ──


def test_non_transient_round_resets_transient_counter():
    """A fresh (non-transient) CI repair round resets transient_retries to 0."""
    wf = _make_workflow(
        error_message="",  # no transient signal
        ci_repair_attempts=1,
        ci_repair_transient_retries=3,  # stale from a previous transient cycle
    )
    orch, mock_repo = _make_orchestrator(wf)
    gh = MagicMock()
    gh.get_pr_head_sha.return_value = "sha-new"
    gh.get_check_failure_excerpt.return_value = "pytest failed\n1 failed"
    orch._get_gh = MagicMock(return_value=gh)
    orch._run_merge_ci_repair = MagicMock()

    orch._start_ci_repair_round(wf, 1820, _FAILED_CHECKS)

    orch._run_merge_ci_repair.assert_called_once()
    updates = [c.args[1] for c in mock_repo.update_workflow.call_args_list]
    # ci_repair_attempts incremented (1 → 2)
    assert any(u.get("ci_repair_attempts") == 2 for u in updates)
    # transient counter reset to 0
    assert any(u.get("ci_repair_transient_retries") == 0 for u in updates)


# ── 4. Diagnostics-incomplete preserves transient signal and counter ──


def test_diagnostics_incomplete_preserves_transient_signal():
    """When diagnostics are incomplete during a transient-deferred round,
    the error_message must retain the "CI repair deferred: transient API error"
    prefix and ci_repair_transient_retries must be persisted so the next
    scheduler cycle still detects the transient state."""
    wf = _make_workflow(
        error_message="CI repair deferred: transient API error - 503 Service Unavailable",
        ci_repair_attempts=2,
        ci_repair_transient_retries=1,
        ci_diagnostics_attempts=0,
    )
    orch, mock_repo = _make_orchestrator(wf)
    gh = MagicMock()
    gh.get_pr_head_sha.return_value = "sha-new"
    gh.get_check_failure_excerpt.return_value = ""  # no logs → diagnostics incomplete
    orch._get_gh = MagicMock(return_value=gh)
    orch._run_merge_ci_repair = MagicMock()

    orch._start_ci_repair_round(wf, 1820, _FAILED_CHECKS)

    # Agent must NOT be called — diagnostics incomplete
    orch._run_merge_ci_repair.assert_not_called()
    updates = [c.args[1] for c in mock_repo.update_workflow.call_args_list]
    # The error_message must retain the transient-deferred prefix
    assert any(
        u.get("error_message", "").startswith("CI repair deferred: transient API error")
        for u in updates
    ), [u.get("error_message") for u in updates]
    # ci_repair_transient_retries must be persisted (1 → 2)
    assert any(u.get("ci_repair_transient_retries") == 2 for u in updates)
    # ci_repair_attempts must NOT be incremented
    assert not any(u.get("ci_repair_attempts", 0) > 2 for u in updates)


# ── 5. Diagnostics complete after transient deferral proceeds normally ──


def test_diagnostics_complete_after_transient_deferral_proceeds():
    """When diagnostics complete during a transient-deferred round, the agent
    runs and ci_repair_attempts is NOT incremented (transient retry)."""
    wf = _make_workflow(
        error_message="CI repair deferred: transient API error - 503 Service Unavailable",
        ci_repair_attempts=2,
        ci_repair_transient_retries=1,
    )
    orch, mock_repo = _make_orchestrator(wf)
    gh = MagicMock()
    gh.get_pr_head_sha.return_value = "sha-new"
    gh.get_check_failure_excerpt.return_value = "pytest failed\n1 failed"
    orch._get_gh = MagicMock(return_value=gh)
    orch._run_merge_ci_repair = MagicMock()

    orch._start_ci_repair_round(wf, 1820, _FAILED_CHECKS)

    orch._run_merge_ci_repair.assert_called_once()
    updates = [c.args[1] for c in mock_repo.update_workflow.call_args_list]
    # ci_repair_attempts stays at 2 (transient retry, not a new attempt)
    assert any(u.get("ci_repair_attempts") == 2 for u in updates)
    # transient counter incremented
    assert any(u.get("ci_repair_transient_retries") == 2 for u in updates)
    # diagnostics counter reset to 0 (fresh budget for the round)
    assert any(u.get("ci_diagnostics_attempts") == 0 for u in updates)
