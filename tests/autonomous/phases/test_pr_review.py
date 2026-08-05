"""ReviewPhase handler unit tests (#2044 Phase B T11).

These tests prove the handler is independently testable: they pass fakes via
``PhaseDeps`` and NEVER construct ``AutonomousOrchestrator``. The decoupling
surface they exercise (the methods the handler actually calls on deps) is the
real measure of how far the pr_review phase has been decoupled from the
~10k-line orchestrator concrete class.

Coverage:
- registered + resolves to phases.pr_review.handle
- no-changes path → PhaseResult.completed(next_phase="completed") with the
  literal current_phase="completed" (the pr_review terminal skips report/merge)
- timing-issue path → same terminal shape, timing_issue milestone
- review approved + at cap → summary runs → PhaseResult.completed("report")
- changes-requested (not approved, under cap) with fix succeeding →
  PhaseResult.retry (scheduler re-enters pr_review for the next round)
- CI pending after approved review → start_ci_repair_round + PhaseResult.retry
- failure path: review agent returns no result → PhaseResult.failed
- failure path: read-only tool unsupported → PhaseResult.failed
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

from app.modules.workspace.autonomous import phases as phases_pkg
from app.modules.workspace.autonomous.models import AgentTaskResult
from app.modules.workspace.autonomous.phase_contract import PhaseResult, WorkflowContext
from app.modules.workspace.autonomous.phases import pr_review as pr_review_phase


def _ctx(workflow: dict) -> WorkflowContext:
    return WorkflowContext(
        workflow=workflow,
        definition_snapshot=None,
        repository_context=None,
        session_bindings={},
        cancellation=threading.Event(),
    )


def _run_git(args, check=True):
    """Synthetic _run_git: distinct rev-parse shas, branch NOT an ancestor of
    main (so the diff-check path runs and has_changes is driven by
    get_diff_stats)."""
    if args[:1] == ["rev-parse"]:
        return MagicMock(stdout=f"{args[1]}-sha\n", returncode=0)
    if args[:2] == ["merge-base", "--is-ancestor"]:
        # returncode 1 → NOT an ancestor → diff-check path
        return MagicMock(stdout="", returncode=1)
    return MagicMock(stdout="", returncode=0)


def _gh(*, commits: int = 1, branch: str = "feature-x") -> MagicMock:
    gh = MagicMock(name="gh")
    gh._run_git.side_effect = _run_git
    gh.get_current_branch.return_value = branch
    gh.get_diff_stats.return_value = {
        "commits": commits,
        "additions": 5,
        "deletions": 1,
        "files": 1,
    }
    gh.git_push.return_value = None
    return gh


def _agent(text: str = "approved", *, success: bool = True) -> AgentTaskResult:
    return AgentTaskResult(
        session_id="sess-1",
        response_text=text,
        total_tokens=10,
        total_input_tokens=5,
        total_output_tokens=5,
        success=success,
        error=None,
    )


def _host(**overrides) -> MagicMock:
    """Build a host fake. Defaults satisfy the success path; tests override the
    methods they need (e.g. review_is_approved, run_agent_with_context_recovery)."""
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
    host.poll_ci_status.return_value = []
    host.run_agent_with_context_recovery.return_value = _agent()
    host.accumulate_tokens.return_value = None
    host.abort_on_repo_integrity_violation.return_value = False
    host.is_context_overflow.return_value = False
    host.artifact_text.side_effect = lambda r: getattr(r, "response_text", "") or ""
    host.artifact_tldr.return_value = "tldr"
    host.review_is_approved.return_value = True
    host.review_is_approved.side_effect = None
    host.post_github_comment.return_value = None
    host.apply_pr_review_fix.return_value = True
    host.cancel_milestone_for_shutdown.return_value = None
    host.start_ci_repair_round.return_value = None
    for k, v in overrides.items():
        if callable(v) or isinstance(v, MagicMock):
            # A MagicMock / callable override: assign directly (the handler
            # calls it).
            setattr(host, k, v)
        else:
            # A plain value (e.g. review_is_approved=True): wrap as a MagicMock
            # returning it, so the handler's ``host.<name>(...)`` call works.
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


# ── registration ─────────────────────────────────────────────────────────


def test_pr_review_handle_is_registered():
    """The pr_review phase must resolve to phases.pr_review.handle in the registry."""
    assert phases_pkg.resolve_phase_handler("pr_review") is pr_review_phase.handle


# ── no-changes / timing-issue terminal path → completed ───────────────────


def test_no_changes_returns_completed_with_literal_current_phase():
    """A branch with no commits vs main terminates the workflow: status=completed
    AND the literal current_phase='completed' (pr_review skips report/merge).
    The commit entrypoint honours a current_phase carried in workflow_patch for
    next_phase='completed' (defaults to 'merge' otherwise)."""
    gh = _gh(commits=0)
    host = _host()
    deps = _deps(host, gh)

    result = pr_review_phase.handle(_ctx(_workflow(github_pr_number=None)), deps)

    assert isinstance(result, PhaseResult)
    assert result.outcome == "completed"
    assert result.next_phase == "completed"
    # The literal terminal current_phase travels in workflow_patch (the entrypoint
    # honours it for next_phase="completed").
    assert result.workflow_patch.get("current_phase") == "completed"
    # The no_changes milestone rides in milestone_events.
    assert any(
        ms.get("milestone_type") == "no_changes" for ms in result.milestone_events
    ), result.milestone_events
    # phase_change{completed} emitted through the host.
    host.emit_phase_change.assert_called_with({"phase": "completed"})
    # No PR created.
    gh.create_pr.assert_not_called()


def test_timing_issue_marks_completed_with_timing_milestone():
    """When the branch is an ancestor of main (behind main), the handler records
    a timing_issue milestone and terminates completed."""
    gh = _gh()
    # Override _run_git so the branch IS an ancestor of main → timing issue.
    gh._run_git.side_effect = lambda args, check=True: (
        MagicMock(stdout="", returncode=0)
        if args[:2] == ["merge-base", "--is-ancestor"]
        else _run_git(args, check)
    )
    host = _host()
    deps = _deps(host, gh)

    result = pr_review_phase.handle(_ctx(_workflow(github_pr_number=None)), deps)

    assert result.outcome == "completed"
    assert result.next_phase == "completed"
    assert any(
        ms.get("milestone_type") == "timing_issue" for ms in result.milestone_events
    ), result.milestone_events


# ── review approved → report ──────────────────────────────────────────────


def test_review_approved_runs_summary_and_advances_to_report():
    """A passing review at the round cap runs the summary agent and advances to
    report (PhaseResult.completed next_phase='report', status='reporting')."""
    gh = _gh()
    host = _host(review_is_approved=True)
    deps = _deps(host, gh)

    # At cap (round 1 of max 1) so the summary branch fires even on approval.
    result = pr_review_phase.handle(_ctx(_workflow(current_round=0, max_pr_review_rounds=1)), deps)

    assert isinstance(result, PhaseResult)
    assert result.outcome == "completed"
    assert result.next_phase == "report"
    assert result.next_status == "reporting"
    # current_round bumps to 1 in workflow_patch.
    assert result.workflow_patch.get("current_round") == 1
    # phase_change{report} emitted.
    host.emit_phase_change.assert_called_with({"phase": "report"})
    # Both review + summary agent runs fired.
    assert host.run_agent_with_context_recovery.call_count == 2


# ── changes-requested (not approved, under cap) → retry ───────────────────


def test_changes_requested_with_fix_succeeding_returns_retry():
    """A non-passing review under the cap applies a fix; on success the phase
    stays on pr_review for the next round (PhaseResult.retry)."""
    gh = _gh()
    host = _host(
        review_is_approved=False,
        apply_pr_review_fix=MagicMock(return_value=True),
    )
    deps = _deps(host, gh)

    result = pr_review_phase.handle(_ctx(_workflow(current_round=0, max_pr_review_rounds=5)), deps)

    assert isinstance(result, PhaseResult)
    assert result.outcome == "retry"
    # Fix applied.
    host.apply_pr_review_fix.assert_called_once()
    # No phase_change emitted on the retry path (phase unchanged).
    host.emit_phase_change.assert_not_called()


def test_ci_pending_after_approved_review_starts_repair_and_retries():
    """When CI fails after an approved review at the cap, the handler enters the
    CI repair loop and returns retry (phase stays on pr_review)."""
    gh = _gh()
    host = _host(
        review_is_approved=True,
        poll_ci_status=MagicMock(
            return_value=[{"name": "ci", "bucket": "fail", "state": "failure"}]
        ),
    )
    deps = _deps(host, gh)

    result = pr_review_phase.handle(_ctx(_workflow(current_round=0, max_pr_review_rounds=1)), deps)

    assert isinstance(result, PhaseResult)
    assert result.outcome == "retry"
    # CI repair loop started.
    host.start_ci_repair_round.assert_called_once()
    # Did NOT advance to report.
    host.emit_phase_change.assert_not_called()


# ── failure paths → failed ────────────────────────────────────────────────


def test_review_agent_no_result_returns_failed():
    """A review agent that succeeds but returns an empty artifact fails closed:
    PhaseResult.failed with the no-result message."""
    gh = _gh()
    empty_result = _agent(text="   \n")
    host = _host(
        run_agent_with_context_recovery=MagicMock(return_value=empty_result),
        artifact_text=MagicMock(side_effect=lambda r: ""),
    )
    deps = _deps(host, gh)

    result = pr_review_phase.handle(_ctx(_workflow()), deps)

    assert isinstance(result, PhaseResult)
    assert result.outcome == "failed"
    assert result.next_status == "failed"
    assert "no result" in result.structured_error.get("message", "")


def test_read_only_tool_unsupported_returns_failed():
    """A CLI tool without an enforceable read-only review sandbox (e.g. openclaw)
    fails closed before running any review agent."""
    gh = _gh()
    host = _host()
    deps = _deps(host, gh)

    result = pr_review_phase.handle(_ctx(_workflow(cli_tool="openclaw")), deps)

    assert isinstance(result, PhaseResult)
    assert result.outcome == "failed"
    msg = result.structured_error.get("message", "")
    assert "read-only sandbox" in msg
    # Review agent never ran.
    host.run_agent_with_context_recovery.assert_not_called()


def test_pre_review_scope_violation_returns_failed():
    """A change-scope violation detected before the review runs returns
    PhaseResult.failed (the commit entrypoint writes status=failed)."""
    gh = _gh()
    host = _host(
        validate_autonomous_change_scope=MagicMock(
            return_value="scope violation: file outside allowed set"
        )
    )
    deps = _deps(host, gh)

    result = pr_review_phase.handle(_ctx(_workflow()), deps)

    assert isinstance(result, PhaseResult)
    assert result.outcome == "failed"
    assert "scope violation" in result.structured_error.get("message", "")
