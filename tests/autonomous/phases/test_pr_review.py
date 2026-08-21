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
- review agent success-but-empty → fresh retry; retry non-empty → continues
- review agent success-but-empty → fresh retry; retry also empty → PhaseResult.failed
- review agent genuine failure (success=False) → PhaseResult.failed, no retry
- failure path: read-only tool unsupported → PhaseResult.failed
"""

from __future__ import annotations

import json
import threading
from unittest.mock import MagicMock

import pytest

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
    # The default workflow fixture records PR #1234; the pr_review liveness
    # check probes the recorded PR's state, so the default probe must be OPEN
    # to keep the reuse semantics the older tests were written against.
    gh.get_pr.return_value = {"number": 1234, "state": "OPEN"}
    gh.create_pr.return_value = {"number": 5001, "url": "https://x/pull/5001"}
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


def test_empty_branch_name_fails_loudly():
    """An empty branch_name is a broken state (merge cleanup cleared it and
    nothing recreated the workspace), NOT "no changes": the handler must
    fail with a structured error instead of falling into the no-changes
    completed terminal (which masked the breakage in #322/#329/#340)."""
    gh = _gh()
    host = _host()
    deps = _deps(host, gh)

    result = pr_review_phase.handle(_ctx(_workflow(branch_name="")), deps)

    assert isinstance(result, PhaseResult)
    assert result.outcome == "failed"
    assert result.next_phase is None
    msg = (result.structured_error or {}).get("message", "")
    assert "empty branch_name" in msg, msg
    # NOT the no-changes terminal.
    assert result.next_phase != "completed"
    assert not any(
        ms.get("milestone_type") == "no_changes" for ms in result.milestone_events
    ), result.milestone_events
    host.emit_phase_change.assert_not_called()
    host.post_github_comment.assert_not_called()


# ── acceptance-rejected re-entry: reopen development, not completed ───────


def _gh_ancestor() -> MagicMock:
    """gh fake whose branch IS an ancestor of main (timing-issue shape)."""
    gh = _gh()
    gh._run_git.side_effect = lambda args, check=True: (
        MagicMock(stdout="", returncode=0)
        if args[:2] == ["merge-base", "--is-ancestor"]
        else _run_git(args, check)
    )
    return gh


_REJECTED_REPORT = json.dumps(
    {
        "status": "rejected",
        "gates": [
            {
                "item": "call-chain:tenant_repo",
                "verdict": "rejected",
                "rationale": "no production caller wires tenant_repo",
            }
        ],
    }
)


def test_timing_issue_with_rejected_verification_reopens_development():
    """#331: branch behind main + a recorded rejected acceptance + a PR number
    means the previous delivery already merged — reopen development with the
    failed items as feedback instead of the timing-issue completed terminal."""
    gh = _gh_ancestor()
    host = _host()
    host.dev_round_cap_remaining.return_value = 2
    deps = _deps(host, gh)
    wf = _workflow(
        github_pr_number=2851,
        verification_status="rejected",
        verification_report=_REJECTED_REPORT,
        dev_round=1,
    )

    result = pr_review_phase.handle(_ctx(wf), deps)

    assert result.outcome == "completed"
    assert result.next_phase == "development"
    assert result.next_status == "developing"
    patch = result.workflow_patch
    assert patch.get("dev_round") == 2
    assert patch.get("current_round") == 0
    assert patch.get("verification_status") is None  # single auto-reopen guard
    # Stale merge SHA dropped so acceptance re-resolves the NEXT merge instead
    # of replaying the rejected verdict on the previous delivery.
    assert patch.get("verification_merge_sha") == ""
    assert "call-chain:tenant_repo" in (patch.get("user_feedback") or "")
    reopened = [
        ms
        for ms in result.milestone_events
        if ms.get("milestone_type") == "acceptance_rejected_reopened"
    ]
    assert len(reopened) == 1, result.milestone_events
    assert reopened[0].get("dev_round") == 2
    assert "2851" in reopened[0].get("title", "")
    # Handler emits its own phase_change (development), never completed.
    emitted = [c.args[0] for c in host.emit_phase_change.call_args_list]
    assert {"phase": "development", "dev_round": 2, "resumed": True} in emitted
    assert all(e.get("phase") != "completed" for e in emitted)
    host.post_github_comment.assert_not_called()
    gh.create_pr.assert_not_called()


def test_reopen_respects_dev_round_cap_and_fails():
    """A persistent rejection past MAX_ACCEPTANCE_DEV_ROUNDS fails the workflow
    (#2335 semantics) instead of looping or silently completing."""
    gh = _gh_ancestor()
    host = _host()
    host.dev_round_cap_remaining.return_value = 0
    deps = _deps(host, gh)
    wf = _workflow(
        github_pr_number=2851,
        verification_status="rejected",
        verification_report=_REJECTED_REPORT,
        dev_round=3,
    )

    result = pr_review_phase.handle(_ctx(wf), deps)

    assert result.outcome == "failed"
    msg = (result.structured_error or {}).get("message", "")
    assert "dev-round cap" in msg and "2851" in msg
    assert any(
        ms.get("milestone_type") == "acceptance_rejected_cap_exhausted"
        for ms in result.milestone_events
    ), result.milestone_events
    emitted = [c.args[0] for c in host.emit_phase_change.call_args_list]
    assert all(e.get("phase") != "completed" for e in emitted)
    host.post_github_comment.assert_not_called()


def test_reopen_with_unparseable_report_uses_default_feedback():
    """An unparseable/missing verification report still reopens development —
    the default feedback text points the dev round at the issue comment."""
    gh = _gh_ancestor()
    host = _host()
    host.dev_round_cap_remaining.return_value = 2
    deps = _deps(host, gh)
    wf = _workflow(
        github_pr_number=2851,
        verification_status="rejected",
        verification_report="{broken json",
        dev_round=1,
    )

    result = pr_review_phase.handle(_ctx(wf), deps)

    assert result.outcome == "completed"
    assert result.next_phase == "development"
    feedback = result.workflow_patch.get("user_feedback") or ""
    assert "REJECTED" in feedback and "2851" in feedback and feedback.strip()


@pytest.mark.parametrize("verification_status", [None, "confirmed", "indeterminate"])
def test_reopen_omitted_when_verification_not_rejected(verification_status):
    """Only 'rejected' reroutes the timing-issue path; anything else keeps the
    Issue #1552 completed terminal (indeterminate is human-guarded)."""
    gh = _gh_ancestor()
    host = _host()
    deps = _deps(host, gh)
    wf = _workflow(github_pr_number=1234, verification_status=verification_status)

    result = pr_review_phase.handle(_ctx(wf), deps)

    assert result.outcome == "completed"
    assert result.next_phase == "completed"
    assert any(
        ms.get("milestone_type") == "timing_issue" for ms in result.milestone_events
    ), result.milestone_events
    host.dev_round_cap_remaining.assert_not_called()


def test_reopen_reads_pr_and_status_from_host_fallback():
    """The reopen trigger's PR number / verification status fall back to the
    host's live DB read when the wf snapshot omits them (v2 review defence)."""
    gh = _gh_ancestor()
    host = _host()
    host.dev_round_cap_remaining.return_value = 2
    host.get_workflow_field.side_effect = {
        "github_pr_number": 2851,
        "verification_status": "rejected",
    }.get
    deps = _deps(host, gh)
    wf = _workflow(
        github_pr_number=None,
        verification_status=None,
        verification_report=_REJECTED_REPORT,
        dev_round=1,
    )

    result = pr_review_phase.handle(_ctx(wf), deps)

    assert result.outcome == "completed"
    assert result.next_phase == "development"
    assert result.next_status == "developing"
    assert result.workflow_patch.get("dev_round") == 2
    assert "call-chain:tenant_repo" in (result.workflow_patch.get("user_feedback") or "")


def test_reopen_requires_recorded_pr():
    """Without a recorded PR number the reopen trigger's third condition fails
    and the existing timing-issue completed terminal is preserved."""
    gh = _gh_ancestor()
    host = _host()  # get_workflow_field defaults to None
    deps = _deps(host, gh)
    wf = _workflow(
        github_pr_number=None,
        verification_status="rejected",
        verification_report=_REJECTED_REPORT,
    )

    result = pr_review_phase.handle(_ctx(wf), deps)

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


@pytest.mark.regression
@pytest.mark.issue(2715)
def test_review_agent_no_result_returns_failed():
    """When BOTH the initial review run and the fresh retry return empty text,
    the phase fails closed: PhaseResult.failed with the no-result message.
    (Both calls share the same always-empty mock return value.)"""
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


# ── review agent empty-result retry ──────────────────────────────────────


@pytest.mark.regression
@pytest.mark.issue(2715)
def test_review_empty_first_call_retries_fresh_and_succeeds():
    """When the review agent (session_line='review') returns success but EMPTY
    artifact text — a transient resume no-op — handle() retries ONCE with a
    fresh session (session_line='fresh'). If the retry returns non-empty text
    the phase does NOT fail: it continues normally."""
    gh = _gh()
    empty_review = _agent(text="")
    fresh_review = _agent(text="LGTM: all criteria met.\n")
    summary_result = _agent(text="Summary: changes look good.")
    host = _host(
        review_is_approved=True,
        run_agent_with_context_recovery=MagicMock(
            side_effect=[empty_review, fresh_review, summary_result]
        ),
    )
    host.artifact_text.side_effect = lambda r: getattr(r, "response_text", "") or ""
    deps = _deps(host, gh)

    result = pr_review_phase.handle(_ctx(_workflow(current_round=0, max_pr_review_rounds=1)), deps)

    assert result.outcome != "failed", result
    # Second call must be the fresh retry.
    assert host.run_agent_with_context_recovery.call_count >= 2
    second_kwargs = host.run_agent_with_context_recovery.call_args_list[1].kwargs
    assert second_kwargs.get("session_line") == "fresh"


@pytest.mark.regression
@pytest.mark.issue(2715)
def test_review_genuine_failure_does_not_trigger_fresh_retry():
    """A genuine review-agent failure (success=False) takes the existing failure
    path — it must NOT trigger the fresh-retry backstop, which is reserved for a
    SUCCEEDED-but-empty run. run_agent is called exactly once."""
    gh = _gh()
    failed_review = _agent(text="", success=False)
    failed_review.error = "agent crashed"
    host = _host(
        run_agent_with_context_recovery=MagicMock(return_value=failed_review),
    )
    host.artifact_text.side_effect = lambda r: getattr(r, "response_text", "") or ""
    deps = _deps(host, gh)

    result = pr_review_phase.handle(_ctx(_workflow()), deps)

    assert result.outcome == "failed"
    # Exactly one review call — no fresh retry.
    assert host.run_agent_with_context_recovery.call_count == 1


# ── summary agent empty-result retry ─────────────────────────────────────


def _summary_workflow():
    """A workflow at the round cap (round 1 of max 1) with an approved review,
    so the summary branch fires after exactly one review agent run. The summary
    agent is the SECOND run_agent_with_context_recovery call."""
    return _workflow(current_round=0, max_pr_review_rounds=1)


def test_summary_empty_first_call_retries_fresh_and_succeeds():
    """When the summary agent (session_line='main') returns success but EMPTY
    artifact text — a transient resume no-op — handle() retries ONCE with a
    fresh session (session_line='fresh'). If the retry returns non-empty text,
    the phase does NOT fail: the milestone is recorded with the fresh summary
    and the phase advances to report."""
    gh = _gh()
    # First call = review agent (non-empty). Second call = summary 'main'
    # (empty, the resume no-op). Third call = summary 'fresh' (non-empty).
    review_result = _agent(text="approved")
    empty_summary = _agent(text="   \n")
    fresh_summary = _agent(text="Summary: all review comments addressed.")
    host = _host(
        review_is_approved=True,
        run_agent_with_context_recovery=MagicMock(
            side_effect=[review_result, empty_summary, fresh_summary]
        ),
    )
    # artifact_text maps each result to its response_text.
    host.artifact_text.side_effect = lambda r: getattr(r, "response_text", "") or ""
    deps = _deps(host, gh)

    result = pr_review_phase.handle(_ctx(_summary_workflow()), deps)

    assert result.outcome == "completed", result
    assert result.next_phase == "report"
    # The summary agent ran twice (main + fresh) plus the one review run.
    assert host.run_agent_with_context_recovery.call_count == 3
    # The retry (3rd call) must use a fresh session line.
    third_kwargs = host.run_agent_with_context_recovery.call_args_list[2].kwargs
    assert third_kwargs.get("session_line") == "fresh"
    # The milestone is recorded with the fresh (non-empty) summary text — it
    # must NOT carry the empty main-run text. The summary milestone update is
    # the last update_milestone call in this path.
    update_calls = deps.repo.update_milestone.call_args_list
    summary_update = update_calls[-1]
    assert summary_update.args[1]["review_content"] == ("Summary: all review comments addressed.")
    assert summary_update.args[1]["status"] == "completed"


def test_summary_empty_after_fresh_retry_still_fails():
    """If BOTH the main summary run and the fresh retry return empty text, the
    existing fail-closed behaviour is preserved: PhaseResult.failed with the
    'returned no result' message."""
    gh = _gh()
    review_result = _agent(text="approved")
    empty_main = _agent(text="")
    empty_fresh = _agent(text="")
    host = _host(
        review_is_approved=True,
        run_agent_with_context_recovery=MagicMock(
            side_effect=[review_result, empty_main, empty_fresh]
        ),
    )
    host.artifact_text.side_effect = lambda r: getattr(r, "response_text", "") or ""
    deps = _deps(host, gh)

    result = pr_review_phase.handle(_ctx(_summary_workflow()), deps)

    assert result.outcome == "failed"
    assert result.next_status == "failed"
    assert "no result" in result.structured_error.get("message", "")
    # Summary ran twice (main + fresh) plus the review run.
    assert host.run_agent_with_context_recovery.call_count == 3


def test_summary_genuine_failure_does_not_trigger_fresh_retry():
    """A genuine summary-agent failure (success=False) or context overflow takes
    the existing failure path — it must NOT trigger the fresh-retry backstop,
    which is reserved for a SUCCEEDED-but-empty run. run_agent is called once
    for review and once for summary (no third call)."""
    gh = _gh()
    review_result = _agent(text="approved")
    failed_summary = _agent(text="", success=False)
    failed_summary.error = "agent crashed"
    host = _host(
        review_is_approved=True,
        run_agent_with_context_recovery=MagicMock(side_effect=[review_result, failed_summary]),
    )
    host.artifact_text.side_effect = lambda r: getattr(r, "response_text", "") or ""
    deps = _deps(host, gh)

    result = pr_review_phase.handle(_ctx(_summary_workflow()), deps)

    assert result.outcome == "failed"
    # No fresh retry: only review + the failed summary run.
    assert host.run_agent_with_context_recovery.call_count == 2


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


def test_ensure_branch_and_push_commits_uncommitted_changes_before_push():
    """Uncommitted changes left after the dev phase's auto-commit (a review-fix
    retry, a merge-main sync on a prior round) must be committed before push.
    Otherwise the branch HEAD carries nothing new and create_pr 422s with "No
    commits between main and branch" (#2468 fa40beec, #2477 b6348aac)."""
    gh = _gh()
    gh.get_current_branch.return_value = "feature-x"
    gh.has_uncommitted_changes.return_value = True
    host = _host()

    pr_review_phase._ensure_branch_and_push(gh, host, "feature-x", "feat-sha", "main-sha")

    gh.git_add_all.assert_called_once()
    gh.git_commit.assert_called_once()
    gh.git_push.assert_called_once()
    # Ordering: stage the pending changes BEFORE the push.
    kinds = [c[0] for c in gh.mock_calls if c[0] in ("git_add_all", "git_commit", "git_push")]
    assert kinds.index("git_add_all") < kinds.index("git_push")
    assert kinds.index("git_commit") < kinds.index("git_push")


def test_ensure_branch_and_push_skips_commit_when_worktree_clean():
    """A clean worktree must not get an empty safety commit (avoids noise / the
    'nothing to commit' error path). Only stage when there are pending changes."""
    gh = _gh()
    gh.get_current_branch.return_value = "feature-x"
    gh.has_uncommitted_changes.return_value = False
    host = _host()

    pr_review_phase._ensure_branch_and_push(gh, host, "feature-x", "feat-sha", "main-sha")

    gh.git_add_all.assert_not_called()
    gh.git_commit.assert_not_called()
    gh.git_push.assert_called_once()


# ── merged-PR reuse regression (#331) ─────────────────────────────────────


@pytest.mark.regression
@pytest.mark.issue(331)
def test_round1_creates_new_pr_when_recorded_pr_merged():
    """An acceptance-verification rejection resets current_round to 0 but keeps
    the OLD github_pr_number on the row. If that PR is already MERGED, reusing
    it makes the second review pass — and the merge phase — operate on the
    merged PR's head, so the merge resolution computes no new commit and fails
    ("Merge resolution made no commit; refusing unchanged push", #331). A
    non-OPEN recorded PR on the round-1 creation window must therefore be
    treated as absent: a fresh PR is created and the new number/url land in
    workflow_patch."""
    gh = _gh()
    gh.get_pr.return_value = {"number": 1234, "state": "MERGED"}
    host = _host(review_is_approved=True)
    deps = _deps(host, gh)

    result = pr_review_phase.handle(_ctx(_workflow(current_round=0, max_pr_review_rounds=1)), deps)

    # A fresh PR replaced the merged one...
    gh.create_pr.assert_called_once()
    assert result.workflow_patch.get("github_pr_number") == 5001
    assert result.workflow_patch.get("github_pr_url") == "https://x/pull/5001"
    # ...and the pr_created milestone records the FRESH number.
    pr_created = [
        c
        for c in host.create_milestone_idempotent.call_args_list
        if c.kwargs.get("milestone_type") == "pr_created"
    ]
    assert pr_created, host.create_milestone_idempotent.call_args_list
    assert pr_created[0].kwargs.get("github_pr_number") == 5001


@pytest.mark.regression
@pytest.mark.issue(331)
def test_later_round_keeps_recorded_pr_id_when_not_open():
    """On rounds > 1 downstream code expects a non-None pr_number, so a
    non-OPEN recorded PR is kept (warning logged) instead of forcing a fresh
    creation mid-review — the reachable path is a human merging the PR
    mid-review. No new PR is created and the recorded number survives."""
    gh = _gh()
    gh.get_pr.return_value = {"number": 1234, "state": "MERGED"}
    host = _host(review_is_approved=True)
    deps = _deps(host, gh)

    result = pr_review_phase.handle(_ctx(_workflow(current_round=2, max_pr_review_rounds=3)), deps)

    gh.create_pr.assert_not_called()
    # The recorded PR number is not rewritten to a fresh PR.
    assert "github_pr_number" not in result.workflow_patch


@pytest.mark.regression
@pytest.mark.issue(331)
def test_probe_failure_keeps_recorded_pr_id():
    """A FAILED state probe (transient gh/API error) is not evidence that the
    recorded PR is non-OPEN: nulling the number would attempt a doomed
    create_pr through the same flaky gh and could fail the workflow outright.
    The recorded number is kept on every round and no PR is created."""
    gh = _gh()
    gh.get_pr.side_effect = Exception("gh: rate limit exceeded")
    host = _host(review_is_approved=True)
    deps = _deps(host, gh)

    result = pr_review_phase.handle(_ctx(_workflow(current_round=0, max_pr_review_rounds=1)), deps)

    gh.create_pr.assert_not_called()
    # The recorded PR number is not rewritten to a fresh PR.
    assert "github_pr_number" not in result.workflow_patch
