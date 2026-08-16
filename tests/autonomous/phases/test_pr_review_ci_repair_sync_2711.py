"""Regression (#2711): pr_review CI-repair sync loop and stale CI snapshot.

Two structural bugs that both caused unbounded pr_review rounds:

1. _start_ci_repair_round synced the branch with main then returned bare,
   leaving no state change. The next scheduler cycle re-entered pr_review as a
   full new review round, bypassing all #2443 convergence guards. Fix: write a
   sentinel to error_message so the handler recognises the "just synced" state.

2. ci_failures captured at handler entry was never refreshed before the final
   CI go/no-go decision. A fix push made during the same cycle started a new CI
   run, but the entry snapshot still showed failures → spurious
   ci_failed_before_report milestone. Fix: re-poll immediately before the check.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, call, patch

import pytest

from app.modules.workspace.autonomous.models import AgentTaskResult
from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator
from app.modules.workspace.autonomous.phase_contract import PhaseResult, WorkflowContext
from app.modules.workspace.autonomous.phases import pr_review as pr_review_phase

pytestmark = [pytest.mark.regression, pytest.mark.issue(2711)]

_SYNC_SENTINEL = "CI repair deferred: waiting for CI after main sync"
_FAIL_CHECK = {"name": "ci", "bucket": "fail", "state": "failure"}
_PASS_CHECKS: list = []


# ── helpers (mirrors test_pr_review.py helpers) ───────────────────────────


def _ctx(workflow: dict) -> WorkflowContext:
    return WorkflowContext(
        workflow=workflow,
        definition_snapshot=None,
        repository_context=None,
        session_bindings={},
        cancellation=threading.Event(),
    )


def _run_git(args, check=True):
    if args[:1] == ["rev-parse"]:
        return MagicMock(stdout=f"{args[1]}-sha\n", returncode=0)
    if args[:2] == ["merge-base", "--is-ancestor"]:
        return MagicMock(stdout="", returncode=1)
    return MagicMock(stdout="", returncode=0)


def _gh(branch: str = "feature-x") -> MagicMock:
    gh = MagicMock(name="gh")
    gh._run_git.side_effect = _run_git
    gh.get_current_branch.return_value = branch
    gh.get_diff_stats.return_value = {"commits": 1, "additions": 2, "deletions": 0, "files": 1}
    gh.git_push.return_value = None
    return gh


def _agent(text: str = "approved") -> AgentTaskResult:
    return AgentTaskResult(
        session_id="sess-1",
        response_text=text,
        total_tokens=10,
        total_input_tokens=5,
        total_output_tokens=5,
        success=True,
        error=None,
    )


def _host(**overrides) -> MagicMock:
    host = MagicMock(name="host")
    host.workflow_id = "wf-test"
    host.must_run_full_review_rounds.return_value = False
    host.validate_autonomous_change_scope.return_value = ""
    host.get_workflow_field.return_value = None
    host.refresh_workflow_snapshot.return_value = {}
    host.create_milestone_idempotent.return_value = {"milestone_id": "ms-x"}
    host.get_pr_review_diff.return_value = "DIFF"
    host.smart_truncate_diff.side_effect = lambda t: t
    host.clean_agent_text.side_effect = lambda t: t or ""
    host.poll_ci_status.return_value = _PASS_CHECKS
    host.run_agent_with_context_recovery.return_value = _agent()
    host.accumulate_tokens.return_value = None
    host.abort_on_repo_integrity_violation.return_value = False
    host.is_context_overflow.return_value = False
    host.artifact_text.side_effect = lambda r: getattr(r, "response_text", "") or ""
    host.artifact_tldr.return_value = "tldr"
    host.review_is_approved.return_value = True
    host.post_github_comment.return_value = None
    host.apply_pr_review_fix.return_value = True
    host.cancel_milestone_for_shutdown.return_value = None
    host.start_ci_repair_round.return_value = None
    for k, v in overrides.items():
        if callable(v) or isinstance(v, MagicMock):
            setattr(host, k, v)
        else:
            setattr(host, k, MagicMock(return_value=v))
    return host


def _deps(host: MagicMock, gh: MagicMock) -> MagicMock:
    deps = MagicMock(name="deps")
    deps.host = host
    deps.gh = gh
    deps.repo = MagicMock(name="repo")
    deps.repo.list_milestones.return_value = []
    deps.repo.get_milestone.return_value = {}
    return deps


def _workflow(**overrides) -> dict:
    wf = {
        "workflow_id": "wf-test",
        "current_phase": "pr_review",
        "status": "pr_review",
        "current_round": 0,
        "max_pr_review_rounds": 5,
        "dev_round": 1,
        "branch_name": "feature-x",
        "github_pr_number": 1234,
        "github_issue_number": 42,
        "cli_tool": "claude-code",
        "content_language": "en",
    }
    wf.update(overrides)
    return wf


# ── Bug 1a: _start_ci_repair_round sets sentinel on sync ─────────────────


def test_start_ci_repair_round_sets_sync_sentinel_on_branch_behind_main():
    """After a non-AI main sync push, _start_ci_repair_round must write the sync
    sentinel to error_message so the next pr_review cycle can detect it and skip
    a full review round (#2711).  Without the fix, _update_workflow was never
    called and ci_repair_attempts stayed 0 forever."""
    orc = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orc._update_workflow = MagicMock()
    gh = MagicMock()
    gh.get_pr_head_sha.return_value = "sha-abc"
    orc._get_gh = MagicMock(return_value=gh)
    orc._get_preferred_worktree_path = MagicMock(return_value="/wt/x")
    orc._sync_failed_pr_with_main = MagicMock(return_value=True)

    wf = {"workflow_id": "wf-1", "branch_name": "feat/x", "ci_repair_attempts": 0}
    failed = [{"name": "ci", "bucket": "fail"}]
    orc._start_ci_repair_round(wf, 99, failed)

    orc._update_workflow.assert_called_once()
    patch_arg = orc._update_workflow.call_args[0][0]
    assert patch_arg.get("error_message", "").startswith(
        "CI repair deferred: waiting for CI after main sync"
    ), f"Expected sync sentinel, got: {patch_arg}"


# ── Bug 1b: pr_review early exit on sync sentinel ────────────────────────


def test_sync_sentinel_skips_full_review_advances_to_report_when_ci_green():
    """When the sync sentinel is present in error_message and CI is now green,
    the handler must advance to report WITHOUT running any review/fix/summary
    agent call (#2711 bug 1b).  Before the fix, a full review round ran instead,
    incrementing current_round past max_pr_review_rounds."""
    gh = _gh()
    host = _host(poll_ci_status=MagicMock(return_value=_PASS_CHECKS))
    deps = _deps(host, gh)

    result = pr_review_phase.handle(
        _ctx(_workflow(error_message=_SYNC_SENTINEL)), deps
    )

    assert result.outcome == "completed"
    assert result.next_phase == "report"
    assert result.next_status == "reporting"
    assert result.workflow_patch.get("error_message") == ""
    # No review, fix, or summary agent ran.
    host.run_agent_with_context_recovery.assert_not_called()
    host.apply_pr_review_fix.assert_not_called()
    # phase_change{report} emitted.
    host.emit_phase_change.assert_called_with({"phase": "report"})


def test_sync_sentinel_skips_full_review_triggers_ai_repair_when_ci_still_failing():
    """When the sync sentinel is present and CI is still red, the handler must
    delegate to start_ci_repair_round and return retry WITHOUT running any review
    round (#2711 bug 1b).  current_round must not be incremented."""
    gh = _gh()
    host = _host(poll_ci_status=MagicMock(return_value=[_FAIL_CHECK]))
    deps = _deps(host, gh)

    wf = _workflow(error_message=_SYNC_SENTINEL, current_round=3)
    result = pr_review_phase.handle(_ctx(wf), deps)

    assert result.outcome == "retry"
    # AI repair triggered.
    host.start_ci_repair_round.assert_called_once()
    # No review/fix/summary agent ran.
    host.run_agent_with_context_recovery.assert_not_called()
    # current_round must not be set in workflow_patch (no new review round).
    assert "current_round" not in result.workflow_patch


# ── Bug 2: CI re-polled before final decision ─────────────────────────────


def test_ci_repoll_before_decision_clears_stale_failures_on_green():
    """The entry CI snapshot is taken before any fix push; if a fix this cycle
    cleared the failures the old snapshot still shows red.  The handler must
    re-poll immediately before the go/no-go decision so a fix-resolved CI leads
    to report rather than a spurious ci_failed_before_report (#2711 bug 2)."""
    gh = _gh()
    # poll_ci_status: first call (entry) returns failures, second call
    # (re-poll before decision) returns green.
    host = _host(
        review_is_approved=True,
        poll_ci_status=MagicMock(
            side_effect=[
                [_FAIL_CHECK],  # entry snapshot
                _PASS_CHECKS,   # re-poll before decision
            ]
        ),
    )
    deps = _deps(host, gh)

    result = pr_review_phase.handle(
        _ctx(_workflow(current_round=0, max_pr_review_rounds=1)), deps
    )

    assert result.outcome == "completed"
    assert result.next_phase == "report"
    # CI repair must NOT have been triggered.
    host.start_ci_repair_round.assert_not_called()


def test_ci_repoll_before_decision_still_triggers_repair_on_persistent_failure():
    """When the re-poll also returns CI failures, the handler still enters the
    repair loop — the re-poll must not suppress a genuine CI failure."""
    gh = _gh()
    host = _host(
        review_is_approved=True,
        poll_ci_status=MagicMock(
            side_effect=[
                [_FAIL_CHECK],  # entry snapshot
                [_FAIL_CHECK],  # re-poll before decision
            ]
        ),
    )
    deps = _deps(host, gh)

    result = pr_review_phase.handle(
        _ctx(_workflow(current_round=0, max_pr_review_rounds=1)), deps
    )

    assert result.outcome == "retry"
    host.start_ci_repair_round.assert_called_once()


# ── Widened guard: transient / no-change overwrite also skips review ─────


def test_transient_deferred_sentinel_skips_full_review_advances_to_report_when_ci_green():
    """start_ci_repair_round overwrites error_message with
    'CI repair deferred: transient API error ...' on 503/429.  The widened
    guard ('CI repair deferred:' prefix family) must catch this so a transient
    deferral mid-repair does not spawn a phantom review round (#2711 review
    comment — important)."""
    gh = _gh()
    host = _host(poll_ci_status=MagicMock(return_value=_PASS_CHECKS))
    deps = _deps(host, gh)

    transient_sentinel = "CI repair deferred: transient API error - 503 upstream"
    result = pr_review_phase.handle(
        _ctx(_workflow(error_message=transient_sentinel, current_round=3)), deps
    )

    assert result.outcome == "completed"
    assert result.next_phase == "report"
    host.run_agent_with_context_recovery.assert_not_called()


def test_no_change_deferred_sentinel_skips_full_review_triggers_repair_when_ci_failing():
    """start_ci_repair_round overwrites error_message with
    'CI repair deferred: agent produced no code changes' on an empty-commit
    round.  The widened guard must also catch this so no phantom review round
    fires; the handler delegates to start_ci_repair_round for re-entry
    (#2711 review comment — important)."""
    gh = _gh()
    host = _host(poll_ci_status=MagicMock(return_value=[_FAIL_CHECK]))
    deps = _deps(host, gh)

    no_change_sentinel = "CI repair deferred: agent produced no code changes"
    result = pr_review_phase.handle(
        _ctx(_workflow(error_message=no_change_sentinel, current_round=3)), deps
    )

    assert result.outcome == "retry"
    host.start_ci_repair_round.assert_called_once()
    host.run_agent_with_context_recovery.assert_not_called()
    assert "current_round" not in result.workflow_patch
