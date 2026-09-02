"""MergePhase handler unit tests (#2044 Phase B T10).

These tests prove the handler is independently testable: they pass fakes via
``PhaseDeps`` and NEVER construct ``AutonomousOrchestrator``. The decoupling
surface they exercise (the methods the handler actually calls on deps) is the
real measure of how far the merge phase has been decoupled from the ~10k-line
orchestrator concrete class.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from app.modules.workspace.autonomous import phases as phases_pkg
from app.modules.workspace.autonomous.evidence import Evidence, Verdict
from app.modules.workspace.autonomous.github_ops import GitHubOpsError
from app.modules.workspace.autonomous.phase_contract import PhaseResult, WorkflowContext
from app.modules.workspace.autonomous.phases import merge as merge_phase

import pytest


def _ctx(workflow: dict) -> WorkflowContext:
    return WorkflowContext(
        workflow=workflow,
        definition_snapshot=None,
        repository_context=None,
        session_bindings={},
        cancellation=threading.Event(),
    )


def _evidence(confirmed: bool = True) -> Evidence:
    return Evidence(
        source="github_api",
        subject="pr_head",
        verdict=Verdict.CONFIRMED if confirmed else Verdict.INDETERMINATE,
        observed_at=datetime.now(timezone.utc),
        verified_at=datetime.now(timezone.utc),
        verification_method="cat-file -e",
        commit_shas=("abc123",),
        reason="" if confirmed else "head not in local object db",
    )


def _build_deps(
    *,
    merge_raises=None,
    merge_state: dict | None = None,
    checks: list | None = None,
    verdict_confirmed: bool = True,
) -> tuple[MagicMock, MagicMock]:
    """Build a (deps, host) fake pair sufficient for the handler's success or
    branch paths. Returns the PhaseDeps-shaped MagicMock so the handler can
    reach any attribute it needs without per-test setup."""
    host = MagicMock(name="host")
    # perform_git_cleanup returns (status, error) — default to completed.
    host.perform_git_cleanup.return_value = ("completed", "")
    # zero_check_runs_fallback returns False (did not take over the cycle) by
    # default; only zero-check-runs tests opt into True (#2673).
    host.zero_check_runs_fallback.return_value = False
    # validate_pre_merge_change_scope returns "" (no scope error) by default;
    # tests that want the scope-fail branch override this.
    host.validate_pre_merge_change_scope.return_value = ""
    # sync_failed_pr_with_main returns False (did not take over the cycle) by
    # default; a truthy MagicMock would make the handler defer (retry).
    host.sync_failed_pr_with_main.return_value = False
    host.branch_contains_main.return_value = False

    gh = MagicMock(name="gh")
    if merge_state is not None:
        gh.get_pr_merge_state.return_value = merge_state
    else:
        gh.get_pr_merge_state.return_value = {"mergeable": True, "mergeable_state": "clean"}
    gh.get_pr_checks.return_value = checks if checks is not None else []
    if merge_raises is not None:
        gh.merge_pr.side_effect = merge_raises
    else:
        gh.merge_pr.return_value = None

    evidence = MagicMock(name="evidence")
    evidence.resolve_verified_pr_head.return_value = _evidence(confirmed=verdict_confirmed)

    deps = MagicMock(name="deps")
    deps.host = host
    deps.gh = gh
    deps.evidence = evidence
    deps.git_workspace = MagicMock(name="git_workspace")
    return deps, host


def _workflow(pr_number: int | None = 123, dev_round: int = 1) -> dict:
    return {
        "github_pr_number": pr_number,
        "branch_name": "feature-x",
        "dev_round": dev_round,
    }


def _policy_exhausted_workflow() -> dict:
    """A workflow whose merge-policy settle budget is already exhausted, so the
    residual-settle guard must NOT defer — it falls straight to the pause."""
    wf = _workflow()
    wf["merge_policy_settle_retries"] = merge_phase._MERGE_POLICY_SETTLE_RETRY_MAX
    return wf


# ── registration ─────────────────────────────────────────────────────────


pytestmark = [pytest.mark.regression, pytest.mark.issue(2044)]


def test_merge_handle_is_registered():
    """The merge phase must resolve to phases.merge.handle in the registry."""
    assert phases_pkg.resolve_phase_handler("merge") is merge_phase.handle


# ── success path → completed terminal ─────────────────────────────────────


def test_merge_handle_success_returns_completed_phase_result():
    """A clean merge (head verified, no CI fails, merge_pr succeeds) returns a
    PhaseResult completing the workflow via the 'completed' pseudo-phase."""
    deps, host = _build_deps()
    result = merge_phase.handle(_ctx(_workflow()), deps)

    assert isinstance(result, PhaseResult)
    assert result.outcome == "completed"
    assert result.next_phase == "acceptance_verification"
    # The merge-pr call and the merged milestone went through deps/host.
    deps.gh.merge_pr.assert_called_once_with(123, strategy="merge")
    host.create_milestone_idempotent.assert_called()
    # Cleanup ran and its milestone rode in milestone_events.
    host.perform_git_cleanup.assert_called_once_with()
    assert any(ms.get("milestone_type") == "cleaned_up" for ms in result.milestone_events), (
        result.milestone_events
    )
    # Terminal phase_change emitted through the host.
    host.emit_phase_change.assert_called_with({"phase": "completed"})


def test_merged_milestone_records_workflow_dev_round():
    """The merged milestone carries the workflow's current dev_round, not the
    DB default 1.

    Reproducer #2538: a workflow that merged in round 3 recorded its merged
    milestone with dev_round=1 (the column default) while the round-3 dev /
    CI-repair milestones correctly showed dev_round=3. The timeline UI groups
    by dev_round, so the merged card (the latest by timestamp) was mis-placed
    into the round-1 group, appearing out of order before the round-3 cards.
    """
    deps, host = _build_deps()
    merge_phase.handle(_ctx(_workflow(dev_round=3)), deps)

    merged_calls = [
        c
        for c in host.create_milestone_idempotent.call_args_list
        if c.kwargs.get("milestone_type") == "merged"
    ]
    assert merged_calls, "merged milestone was not recorded on a clean merge"
    assert merged_calls[-1].kwargs.get("dev_round") == 3


def test_merge_handle_no_pr_number_skips_to_cleanup():
    """A workflow without github_pr_number skips the entire PR probe and goes
    straight to delivery completion + cleanup."""
    deps, host = _build_deps()
    result = merge_phase.handle(_ctx(_workflow(pr_number=None)), deps)

    assert isinstance(result, PhaseResult)
    assert result.outcome == "completed"
    assert result.next_phase == "acceptance_verification"
    deps.gh.merge_pr.assert_not_called()
    host.perform_git_cleanup.assert_called_once_with()


# ── deferral branches → retry (phase unchanged) ──────────────────────────


def test_merge_handle_head_unverified_returns_retry():
    """An unverifiable PR head defers to the next cycle without changing
    phase/status — expressed as outcome='retry'."""
    deps, host = _build_deps(verdict_confirmed=False)
    result = merge_phase.handle(_ctx(_workflow()), deps)

    assert isinstance(result, PhaseResult)
    assert result.outcome == "retry"
    # The head-unverified milestone is created inline (correlation record).
    host.create_milestone_idempotent.assert_called_once()
    called_kwargs = host.create_milestone_idempotent.call_args.kwargs
    assert called_kwargs.get("milestone_type") == "pr_head_unverified"


def test_merge_handle_ci_pending_returns_retry():
    """CI still running defers to the next scheduler cycle (legacy 'return'
    None semantics) — outcome='retry' leaves phase/status untouched."""
    deps, host = _build_deps(
        checks=[{"name": "ci", "bucket": "pending"}],
        merge_state={"mergeable": True, "mergeable_state": "blocked"},
    )
    result = merge_phase.handle(_ctx(_workflow()), deps)

    assert isinstance(result, PhaseResult)
    assert result.outcome == "retry"
    deps.gh.merge_pr.assert_not_called()


# ── failure path → failed (status=failed via commit entrypoint) ───────────


def test_merge_handle_scope_error_returns_failed():
    """A pre-merge change-scope violation returns PhaseResult.failed so the
    commit entrypoint writes status=failed + error_message."""
    deps, host = _build_deps()
    host.validate_pre_merge_change_scope.return_value = (
        "scope violation: file X outside allowed set"
    )
    result = merge_phase.handle(_ctx(_workflow()), deps)

    assert isinstance(result, PhaseResult)
    assert result.outcome == "failed"
    assert result.next_status == "failed"
    msg = result.structured_error.get("message") if result.structured_error else ""
    assert "scope violation" in msg


# ── aggregate-gate required check (#27; regression from #2455's PR Gate) ───


def test_merge_aggregate_gate_required_check_repairs_underlying_jobs():
    """A branch may require an AGGREGATE GATE — one status-check context that
    summarizes many underlying jobs (whatever the repo names it; on open-ace
    it is "PR Gate" since #2455) — rather than individually-repairable checks.

    The gate has no actionable failure of its own (its log is a summary of the
    jobs it aggregates), so handing only the gate to CI-repair burns the round
    without fixing anything. CI-repair must receive the underlying failing
    jobs. ``required`` is read from the repo's own ruleset at runtime — no
    check name is hardcoded, so this is generic across repos.

    Regression: with the #2428 required-filter still driving targeting,
    ``_blocking_failures`` returned only ``['PR Gate']`` (or nothing on a
    propagation lag) and workflows stalled at the merge-policy pause instead of
    repairing the real failures (50ba8724/2d0c317d/c0758607/...).
    """
    deps, host = _build_deps(
        checks=[
            {"name": "PR Gate", "bucket": "fail"},
            {"name": "test (3.10)", "bucket": "fail"},
            {"name": "lint", "bucket": "fail"},
        ],
        merge_state={"mergeable": True, "mergeable_state": "blocked"},
    )
    deps.gh.get_branch_protection.return_value = {
        "required_status_checks": {"contexts": ["PR Gate"]}
    }
    result = merge_phase.handle(_ctx(_workflow()), deps)

    assert result.outcome == "retry"
    host.start_ci_repair_round.assert_called_once()
    repaired = {c.get("name") for c in host.start_ci_repair_round.call_args[0][2]}
    # The real failures reach the agent — the bare aggregate gate alone is not.
    assert {"test (3.10)", "lint"} <= repaired, repaired


def test_merge_aggregate_gate_lag_still_repairs_underlying_jobs():
    """The aggregate gate's FAILURE lags its underlying jobs: the individual
    jobs report FAILURE first, the gate (a ``needs``-aggregator) flips later.
    During that window ``required ∩ failing`` is empty even though the merge is
    blocked. CI-repair must still target the underlying failing jobs rather
    than pausing on policy. (The lag path — 50ba8724 etc. paused here.)
    """
    deps, host = _build_deps(
        checks=[{"name": "test (3.10)", "bucket": "fail"}, {"name": "lint", "bucket": "fail"}],
        merge_state={"mergeable": True, "mergeable_state": "blocked"},
        merge_raises=GitHubOpsError("base branch policy prohibits the merge"),
    )
    deps.gh.get_branch_protection.return_value = {
        "required_status_checks": {"contexts": ["PR Gate"]}
    }
    result = merge_phase.handle(_ctx(_workflow()), deps)

    assert result.outcome == "retry"
    host.start_ci_repair_round.assert_called_once()
    repaired = {c.get("name") for c in host.start_ci_repair_round.call_args[0][2]}
    assert {"test (3.10)", "lint"} <= repaired, repaired


# ── policy-pause transient defer (#27 follow-up) ──────────────────────────


def test_merge_policy_block_with_pending_ci_defers_not_pauses():
    """#27 follow-up: right after a sync/repair push, the aggregate required
    gate's own pending status has not propagated, but its underlying jobs ARE
    pending. ``_blocking_pending`` filters them (not in the required set), so the
    merge is attempted and rejected with a generic policy error; GitHub reports
    ``blocked``. The workflow must DEFER (CI has not settled) rather than freeze
    at a manual-recovery pause it can never recover from. Reproduced by
    50ba8724 / c0758607 / 1c1b63f0 / 67241d8d / 2d0c317d / cd939cbf."""
    deps, host = _build_deps(
        checks=[{"name": "test (3.10)", "bucket": "pending"}],
        merge_state={"mergeable": True, "mergeable_state": "blocked"},
        merge_raises=GitHubOpsError("base branch policy prohibits the merge"),
    )
    deps.gh.get_branch_protection.return_value = {
        "required_status_checks": {"contexts": ["PR Gate"]}
    }
    result = merge_phase.handle(_ctx(_workflow()), deps)

    assert result.outcome == "retry"
    host.emit_status_change.assert_not_called()  # did not pause


def test_merge_policy_block_with_unknown_state_defers_not_pauses():
    """GitHub's mergeability is async: right after a push, ``mergeable_state``
    is ``unknown`` while it recomputes. A merge attempted in that window is
    rejected with a generic policy error. Defer (GitHub has not decided) instead
    of pausing for manual recovery."""
    deps, host = _build_deps(
        checks=[],
        merge_state={"mergeable": None, "mergeable_state": "unknown"},
        merge_raises=GitHubOpsError("repository rule violations"),
    )
    deps.gh.get_branch_protection.return_value = {
        "required_status_checks": {"contexts": ["PR Gate"]}
    }
    result = merge_phase.handle(_ctx(_workflow()), deps)

    assert result.outcome == "retry"
    host.emit_status_change.assert_not_called()


def test_merge_policy_block_with_settled_ci_still_pauses():
    """When CI has fully settled (no pending checks, state known and blocked), a
    policy rejection is a genuine non-CI block (missing review / draft /
    signing). The manual-recovery pause must still fire — the defer is only for
    the transient CI-settling window, so genuine blocks are not masked."""
    deps, host = _build_deps(
        checks=[{"name": "PR Gate", "bucket": "pass"}],
        merge_state={"mergeable": True, "mergeable_state": "blocked"},
        merge_raises=GitHubOpsError("review is required"),
    )
    deps.gh.get_branch_protection.return_value = {
        "required_status_checks": {"contexts": ["PR Gate"]}
    }
    result = merge_phase.handle(_ctx(_policy_exhausted_workflow()), deps)

    assert result.outcome == "pause"
    host.emit_status_change.assert_called_once()


# ── policy block vs unsettled head (residual #27 race, workflow #2778) ────


def _policy_unsettled_deps(*, checks: list, committed_at) -> tuple:
    """Shared fixture: merge rejected by policy, state=blocked, ``PR Gate``
    required — the residual-race setup where some checks exist for the head
    but the required aggregate gate has not reported yet."""
    deps, host = _build_deps(
        checks=checks,
        merge_state={"mergeable": True, "mergeable_state": "blocked"},
        merge_raises=GitHubOpsError("base branch policy prohibits the merge"),
    )
    deps.gh.get_branch_protection.return_value = {
        "required_status_checks": {"contexts": ["PR Gate"]}
    }
    deps.gh.get_commit_committed_at.return_value = committed_at
    return deps, host


def test_merge_policy_block_with_missing_required_on_fresh_head_defers():
    """~90s after a CI-repair push the new head SHA has runs only for fast
    non-required jobs — the required aggregate gate has no run yet, so the
    rollup shows no pending bucket while GitHub reports ``blocked``. A head
    pushed within the settle-grace window is unsettled CI, not a policy
    block: defer instead of freezing at a manual-recovery pause (reproduced
    by workflow #2778 / PR #2804 on 2026-08-19)."""
    deps, host = _policy_unsettled_deps(
        checks=[{"name": "Select suites", "bucket": "pass"}],
        committed_at=datetime.now(timezone.utc) - timedelta(seconds=90),
    )
    result = merge_phase.handle(_ctx(_workflow()), deps)

    assert result.outcome == "retry"
    host.emit_status_change.assert_not_called()  # did not pause


def test_merge_policy_block_with_missing_required_on_stale_head_pauses():
    """A required context still absent long after the push is the repo-
    misconfig signature (required context with no provider): the manual-
    recovery pause must keep firing so a human fixes the ruleset."""
    deps, host = _policy_unsettled_deps(
        checks=[{"name": "Select suites", "bucket": "pass"}],
        committed_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    result = merge_phase.handle(_ctx(_policy_exhausted_workflow()), deps)

    assert result.outcome == "pause"
    host.emit_status_change.assert_called_once()


def test_merge_policy_block_with_unresolvable_commit_time_pauses():
    """Fail-closed: an unresolvable head commit time (missing date, or the
    API call failing) must not arm the freshness defer — pause as before and
    let the monitor sweep re-classify (#1989 fail-closed spirit)."""
    deps, _ = _policy_unsettled_deps(
        checks=[{"name": "Select suites", "bucket": "pass"}],
        committed_at=None,
    )
    assert merge_phase.handle(_ctx(_policy_exhausted_workflow()), deps).outcome == "pause"

    deps_raise, _ = _policy_unsettled_deps(
        checks=[{"name": "Select suites", "bucket": "pass"}],
        committed_at=None,
    )
    deps_raise.gh.get_commit_committed_at.side_effect = GitHubOpsError("api down")
    assert merge_phase.handle(_ctx(_policy_exhausted_workflow()), deps_raise).outcome == "pause"


def test_merge_policy_block_with_unobservable_required_still_pauses():
    """Degraded contract: when the required set cannot be observed (branch
    protection query fails), no completeness judgment is possible — keep the
    legacy pause."""
    deps, host = _policy_unsettled_deps(
        checks=[{"name": "Select suites", "bucket": "pass"}],
        committed_at=datetime.now(timezone.utc),
    )
    deps.gh.get_branch_protection.side_effect = GitHubOpsError("no access")
    result = merge_phase.handle(_ctx(_policy_exhausted_workflow()), deps)

    assert result.outcome == "pause"
    host.emit_status_change.assert_called_once()


def test_merge_policy_block_with_required_present_skips_commit_time_lookup():
    """Short-circuit: when every required context has already reported, the
    freshness machinery must not even consult the commit-time API."""
    deps, host = _policy_unsettled_deps(
        checks=[{"name": "PR Gate", "bucket": "pass"}],
        committed_at=datetime.now(timezone.utc),
    )
    result = merge_phase.handle(_ctx(_policy_exhausted_workflow()), deps)

    assert result.outcome == "pause"
    deps.gh.get_commit_committed_at.assert_not_called()
    host.emit_status_change.assert_called_once()


# ── bounded merge-policy settle budget (residual-race guard) ──────────────


def test_settle_retry_before_pause():
    """A 'clean rollup but GitHub still blocked' shape with budget remaining
    defers (retry) and increments the settle counter — not a pause."""
    deps, host = _policy_unsettled_deps(
        checks=[{"name": "PR Gate", "bucket": "pass"}],  # required context present
        committed_at=datetime.now(timezone.utc),
    )
    wf = _workflow()
    wf["merge_policy_settle_retries"] = 0
    result = merge_phase.handle(_ctx(wf), deps)

    assert result.outcome == "retry"
    assert result.workflow_patch["merge_policy_settle_retries"] == 1
    host.emit_status_change.assert_not_called()


def test_settle_retry_accumulates_then_pauses():
    """The settle counter accumulates across scheduler cycles; when it reaches
    the cap the workflow pauses, and the pause resets the counter so a resume
    gets a fresh budget."""
    deps, host = _policy_unsettled_deps(
        checks=[{"name": "PR Gate", "bucket": "pass"}],
        committed_at=datetime.now(timezone.utc),
    )
    cap = merge_phase._MERGE_POLICY_SETTLE_RETRY_MAX
    for counter in range(cap):  # 0,1,2 → retry and increment
        wf = _workflow()
        wf["merge_policy_settle_retries"] = counter
        result = merge_phase.handle(_ctx(wf), deps)
        assert result.outcome == "retry"
        assert result.workflow_patch["merge_policy_settle_retries"] == counter + 1
        host.emit_status_change.assert_not_called()

    # counter == cap → pause, and the pause resets the counter to 0.
    wf = _workflow()
    wf["merge_policy_settle_retries"] = cap
    result = merge_phase.handle(_ctx(wf), deps)
    assert result.outcome == "pause"
    assert result.workflow_patch["merge_policy_settle_retries"] == 0
    host.emit_status_change.assert_called_once()


def test_genuine_block_still_pauses_within_budget():
    """With the budget exhausted, a still-blocked settled rollup is a genuine
    external block: it must still pause (never retry forever), and the pause
    keeps the human-facing message/resume semantics."""
    deps, host = _policy_unsettled_deps(
        checks=[{"name": "PR Gate", "bucket": "pass"}],
        committed_at=datetime.now(timezone.utc),
    )
    result = merge_phase.handle(_ctx(_policy_exhausted_workflow()), deps)

    assert result.outcome == "pause"
    assert result.workflow_patch["merge_policy_settle_retries"] == 0
    assert "not merge-ready" in result.structured_error["message"]
    host.emit_status_change.assert_called_once()


# ── zero check-runs fallback (#2673) ──────────────────────────────────────


def test_merge_zero_check_runs_with_required_gate_defers_to_fallback():
    """A PR whose head reports ZERO check-runs on a check-gated base branch is
    the #2673 signature (GitHub event-delivery gap): required checks can never
    appear, so attempting the merge can only be rejected. The handler must
    hand the cycle to the bounded mechanical fallback instead."""
    deps, host = _build_deps(
        checks=[],
        merge_state={"mergeable": True, "mergeable_state": "blocked"},
    )
    deps.gh.get_branch_protection.return_value = {
        "required_status_checks": {"contexts": ["PR Gate"]}
    }
    host.zero_check_runs_fallback.return_value = True
    result = merge_phase.handle(_ctx(_workflow()), deps)

    assert isinstance(result, PhaseResult)
    assert result.outcome == "retry"
    host.zero_check_runs_fallback.assert_called_once_with(deps.gh, 123, "abc123", "main", [])
    # The merge attempt is deferred to the fallback — merge_pr is not called.
    deps.gh.merge_pr.assert_not_called()


def test_merge_zero_check_runs_ungated_base_merges_directly():
    """Zero check-runs on a base branch with NO required checks is the normal
    state (no CI gating). The handler still consults the fallback (the gate —
    required-contexts — lives inside it), but its False return lets the merge
    proceed untouched."""
    deps, host = _build_deps(
        checks=[],
        merge_state={"mergeable": True, "mergeable_state": "clean"},
    )
    deps.gh.get_branch_protection.return_value = {"required_status_checks": {"contexts": []}}
    result = merge_phase.handle(_ctx(_workflow()), deps)

    assert result.outcome == "completed"
    host.zero_check_runs_fallback.assert_called_once_with(deps.gh, 123, "abc123", "main", [])
    deps.gh.merge_pr.assert_called_once_with(123, strategy="merge")


def test_merge_checks_present_closes_out_tracker_and_merges():
    """With check-runs present the fallback is only a tracker close-out call
    (returns False); the normal merge flow is untouched."""
    deps, host = _build_deps(
        checks=[{"name": "PR Gate", "bucket": "pass"}],
        merge_state={"mergeable": True, "mergeable_state": "clean"},
    )
    result = merge_phase.handle(_ctx(_workflow()), deps)

    assert result.outcome == "completed"
    host.zero_check_runs_fallback.assert_called_once_with(
        deps.gh, 123, "abc123", "main", [{"name": "PR Gate", "bucket": "pass"}]
    )
    deps.gh.merge_pr.assert_called_once_with(123, strategy="merge")
