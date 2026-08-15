"""Anti-tamper guard: CI-repair must not delete or weaken protected security tests.

Incident #2687 (PR #2665 / revert PR #2672): the CI-repair agent silenced a
red sudoers-hardening lock test by deleting/weakening its assertions, CI went
green, and the merged PR carried 5 post-merge security criticals. The repair
loop must not be able to defeat the tests that gate it, so alongside the
prompt instruction there is a MECHANICAL post-repair check
(``_protected_test_tampering_error``) that rejects any round deleting or
net-shrinking a protected security test file, and the rejection is classified
as a bounded no-change retry so it cannot loop forever.
"""

from unittest.mock import MagicMock, patch

from app.modules.workspace.autonomous.constants import PROTECTED_CI_REPAIR_TEST_FILES
from app.modules.workspace.autonomous.models import AgentTaskResult
from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

_SUDOERS_LOCK = "tests/unit/test_sudoers_hardening.py"
_BOUNDARY_LOCK = "tests/unit/test_table_boundary_checker.py"


def _gh_with_numstat(numstat: str) -> MagicMock:
    """A gh double whose ``git diff --numstat <base>`` returns ``numstat``."""
    gh = MagicMock()
    gh._run_git.return_value.stdout = numstat
    return gh


# ── Registry ───────────────────────────────────────────────────────────


def test_registry_locks_the_incident_security_suites():
    """Small and defensible: the sudoers-hardening suite from the incident and
    the pre-commit table-boundary guard's suite — nothing else sneaks in."""
    assert set(PROTECTED_CI_REPAIR_TEST_FILES) == {_SUDOERS_LOCK, _BOUNDARY_LOCK}


# ── Mechanical check: _protected_test_tampering_error ─────────────────


def test_protected_test_deletion_is_rejected():
    gh = _gh_with_numstat(f"0\t42\t{_SUDOERS_LOCK}\n")

    error = AutonomousOrchestrator._protected_test_tampering_error(gh, "sha-before")

    assert error
    assert _SUDOERS_LOCK in error
    assert "deleted" in error
    assert "cannot be deleted or weakened" in error
    gh._run_git.assert_called_once_with(["diff", "--numstat", "sha-before"])


def test_protected_test_net_negative_edit_is_rejected():
    """Conservative proxy: removals > additions in a protected file ⇒ reject."""
    gh = _gh_with_numstat(f"3\t10\t{_BOUNDARY_LOCK}\n")

    error = AutonomousOrchestrator._protected_test_tampering_error(gh, "sha-before")

    assert error
    assert _BOUNDARY_LOCK in error
    assert "net-negative" in error


def test_protected_test_net_positive_edit_is_allowed():
    """Legitimate strengthening (additions ≥ removals) passes through."""
    gh = _gh_with_numstat(f"10\t3\t{_SUDOERS_LOCK}\n")

    assert AutonomousOrchestrator._protected_test_tampering_error(gh, "sha-before") == ""


def test_protected_test_binary_rewrite_is_rejected():
    """A protected test replaced by binary content is tampering, not a fix."""
    gh = _gh_with_numstat(f"-\t-\t{_SUDOERS_LOCK}\n")

    error = AutonomousOrchestrator._protected_test_tampering_error(gh, "sha-before")

    assert error
    assert "binary" in error


def test_unrelated_net_negative_files_are_unaffected():
    """The guard protects the registry only; unrelated repair edits (even
    whole-file rewrites) flow through the normal scope guard."""
    gh = _gh_with_numstat("0\t99\tapp/services/legacy.py\n5\t2\tapp/x.py\n")

    assert AutonomousOrchestrator._protected_test_tampering_error(gh, "sha-before") == ""


def test_tampering_check_uses_head_when_commit_before_missing():
    gh = _gh_with_numstat("")

    assert AutonomousOrchestrator._protected_test_tampering_error(gh, "") == ""
    gh._run_git.assert_called_once_with(["diff", "--numstat", "HEAD"])


def test_tampering_check_fails_closed_when_diff_unavailable():
    """Same posture as the scope guard: if the diff cannot be verified, refuse
    the push rather than silently skip the check."""
    gh = MagicMock()
    gh._run_git.side_effect = RuntimeError("dubious ownership")

    error = AutonomousOrchestrator._protected_test_tampering_error(gh, "sha-before")

    assert error
    assert "refusing" in error


# ── Prompt instruction ─────────────────────────────────────────────────


def test_repair_prompt_states_protected_test_constraints():
    """The repair prompt must name the protected files and forbid deletion /
    weakening — rendered from the registry so prompt and check cannot drift."""
    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._workflow_id = "wf-2687"
    orch.repo = MagicMock()
    orch.repo.get_workflow.return_value = {"github_issue_number": 2687}
    orch._get_latest_final_plan = MagicMock(return_value="")
    orch._get_ci_repair_prompt = MagicMock(return_value="")
    orch._get_user_feedback_prompt = MagicMock(return_value="")
    orch._build_prior_repair_failures_prompt = MagicMock(return_value="")
    gh = MagicMock()
    gh.get_pr_diff.return_value = ""

    prompt = orch._build_merge_ci_repair_agent_prompt(
        {"github_issue_number": 2687, "requirements_text": ""}, 2665, [], gh=gh
    )

    for path in PROTECTED_CI_REPAIR_TEST_FILES:
        assert path in prompt
    assert "禁止删除" in prompt
    assert "更严格的断言" in prompt


# ── Enforcement in _run_merge_ci_repair ────────────────────────────────


def _make_rejection_workflow(**overrides):
    base = {
        "workflow_id": "wf-2687",
        "user_id": 5,
        "title": "issue-2687",
        "status": "merging",
        "current_phase": "merge",
        "requirements_text": "fix the thing",
        "project_path": "/tmp/repo",
        "cli_tool": "claude-code",
        "model": "glm-5",
        "permission_mode": "auto-edit",
        "branch_name": "auto-dev/wf-2687",
        "branch_strategy": "worktree",
        "workspace_type": "local",
        "remote_machine_id": "",
        "worktree_path": "/tmp/repo",
        "preferred_worktree_path": "/tmp/repo",
        "github_issue_number": 2687,
        "github_pr_number": 2665,
        "dev_round": 1,
        "error_message": "",
        "ci_repair_attempts": 1,
        "ci_repair_context": "### test (3.12)\n- 状态: failure",
    }
    base.update(overrides)
    return base


def _make_orchestrator(wf_data):
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


def _make_gh(numstat: str) -> MagicMock:
    gh = MagicMock()
    gh.get_pr_head_sha.return_value = "sha-before"
    gh.get_current_commit.return_value = "sha-before"
    gh.get_current_branch.return_value = "auto-dev/wf-2687"
    gh._run_git.return_value.stdout = numstat
    return gh


_FAILED_CHECKS = [
    {"name": "test (3.12)", "state": "failure", "bucket": "fail", "link": "https://example.com"}
]


def test_tampered_repair_round_is_rejected_without_push():
    """A repair round that deletes a protected security test must be rejected:
    edits discarded, nothing pushed, milestone failed with the anti-tamper
    reason, and the workflow deferred with the no-change prefix so the retry
    budget bounds it."""
    wf = _make_rejection_workflow()
    orch, mock_repo = _make_orchestrator(wf)
    gh = _make_gh(f"0\t40\t{_SUDOERS_LOCK}\n2\t1\tapp/fix.py\n")
    orch._get_gh = MagicMock(return_value=gh)
    orch._accumulate_tokens = MagicMock()
    orch._post_github_comment = MagicMock()
    orch._run_agent = MagicMock(return_value=AgentTaskResult(success=True, session_id="sess-1"))

    orch._run_merge_ci_repair(wf, gh, 2665, _FAILED_CHECKS)

    gh.git_push.assert_not_called()
    gh.reset_hard_to.assert_called_once_with("sha-before")
    ms_updates = [c.args[1] for c in mock_repo.update_milestone.call_args_list]
    assert ms_updates and ms_updates[-1].get("status") == "failed"
    assert "cannot be deleted or weakened" in (ms_updates[-1].get("error_message") or "")
    final_updates = mock_repo.update_workflow.call_args.args[1]
    assert final_updates["status"] == "merging"
    assert final_updates["error_message"].startswith(
        "CI repair deferred: agent produced no code changes"
    )
    assert _SUDOERS_LOCK in final_updates["error_message"]


def test_clean_repair_round_passes_the_guard():
    """A round that only strengthens a protected test (net additions) plus
    unrelated fixes flows through to the normal push path."""
    wf = _make_rejection_workflow(base_commit_sha="sha-before")
    orch, mock_repo = _make_orchestrator(wf)
    gh = _make_gh(f"6\t2\t{_SUDOERS_LOCK}\n2\t1\tapp/fix.py\n")
    gh.get_current_commit.side_effect = ["sha-before", "sha-after"]
    gh.get_commit_diff_stats.return_value = {"files": 2, "additions": 8, "deletions": 3}
    orch._get_gh = MagicMock(return_value=gh)
    orch._accumulate_tokens = MagicMock()
    orch._post_github_comment = MagicMock()
    orch._run_agent = MagicMock(return_value=AgentTaskResult(success=True, session_id="sess-1"))

    orch._run_merge_ci_repair(wf, gh, 2665, _FAILED_CHECKS)

    gh.reset_hard_to.assert_not_called()
    gh.git_push.assert_called_once()
    final_updates = mock_repo.update_workflow.call_args.args[1]
    assert final_updates.get("status") == "merging"


# ── Retry classification (bounded, no infinite loop) ──────────────────


def test_tampering_rejection_counts_as_no_change_retry():
    """The rejection's error_message prefix must classify the next round as a
    no-change retry: no real ci_repair_attempts slot consumed, the bounded
    ci_repair_no_change_retries counter bumped instead."""
    wf = _make_rejection_workflow(
        error_message=(
            "CI repair deferred: agent produced no code changes "
            f"(rejected: protected security test files cannot be deleted or "
            f"weakened via CI-repair; offenders: {_SUDOERS_LOCK} (deleted: -40/+0))"
        ),
        ci_repair_attempts=2,
        ci_repair_no_change_retries=0,
        ci_repair_transient_retries=0,
        ci_diagnostics_attempts=0,
        last_ci_failure_signature="",
        last_ci_failure_head_sha="",
    )
    orch, mock_repo = _make_orchestrator(wf)
    gh = MagicMock()
    gh.get_pr_head_sha.return_value = "sha-new"
    gh.get_check_failure_excerpt.return_value = "test failed\n1 error"
    orch._get_gh = MagicMock(return_value=gh)
    orch._run_merge_ci_repair = MagicMock()

    orch._start_ci_repair_round(wf, 2665, _FAILED_CHECKS)

    orch._run_merge_ci_repair.assert_called_once()
    updates = [c.args[1] for c in mock_repo.update_workflow.call_args_list]
    assert any(u.get("ci_repair_attempts") == 2 for u in updates), "real attempts unchanged"
    assert any(
        u.get("ci_repair_no_change_retries") == 1 for u in updates
    ), "tampering rejection bumps the no-change counter"
