"""Merge-phase CI-repair targeting (#2428 → #27).

CI-repair targets EVERY failing check, not just the required ones. The
``#2428`` required-filter (``failing ∩ required``) was removed because
``#2455`` made the repo's required check an aggregate gate (``PR Gate``) whose
own failure is only a summary of its underlying jobs — the filter then returned
just the (unrepairable) gate, or nothing on a propagation lag, and workflows
stalled at the merge-policy pause instead of repairing the real failures.

The #2428 concern — spending the bounded repair budget on checks that do not
gate the merge — is retained by the ``mergeable_state == "unstable"``
short-circuit in ``handle``: a PR that is mergeable despite failing non-required
checks is merged directly, so CI-repair is only reached when a required check
is actually failing (every failing check at that point is a real merge-gating
failure). These tests drive ``merge.handle`` end to end to pin that contract.

History: workflow ``2d0c317d`` (issue #2328, PR #2425) once burned all five
CI-repair rounds on ``test (3.13)`` — the original #2428 incident. That exact
shape (only a non-required check red, PR mergeable) is now caught by the
unstable short-circuit rather than a required-filter.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from app.modules.workspace.autonomous.evidence import Evidence, Verdict
from app.modules.workspace.autonomous.github_ops import GitHubOpsError
from app.modules.workspace.autonomous.phase_contract import WorkflowContext
from app.modules.workspace.autonomous.phases import merge as merge_phase

pytestmark = [pytest.mark.regression, pytest.mark.issue(2428)]

# The live required set for open-ace/open-ace main, from
# `gh api repos/open-ace/open-ace/rules/branches/main`. Still used by the
# pending-check path (_blocking_pending), which filters pending checks the same
# way #2428 filtered failures.
REQUIRED = ["lint", "test (3.10)", "test (3.11)", "test (3.12)", "build"]


def _ctx(workflow: dict) -> WorkflowContext:
    return WorkflowContext(
        workflow=workflow,
        definition_snapshot=None,
        repository_context=None,
        session_bindings={},
        cancellation=threading.Event(),
    )


def _deps(*, checks, required=REQUIRED, merge_raises=None):
    host = MagicMock(name="host")
    host.perform_git_cleanup.return_value = ("completed", "")
    # Issue #2673: the zero-check-runs fallback must not take over the
    # cycle in these wiring tests (checks are reported non-empty or the
    # gating is asserted separately).
    host.zero_check_runs_fallback.return_value = False
    host.validate_pre_merge_change_scope.return_value = ""
    host.sync_failed_pr_with_main.return_value = False
    host.branch_contains_main.return_value = False

    gh = MagicMock(name="gh")
    # "clean" (not "unstable") so the handler enters the sync + CI-repair block;
    # an unstable PR skips that block entirely via the #2034 path. Tests that
    # need the unstable short-circuit override this below.
    gh.get_pr_merge_state.return_value = {"mergeable": True, "mergeable_state": "clean"}
    gh.get_pr_checks.return_value = checks
    gh.get_branch_protection.return_value = {"required_status_checks": {"contexts": list(required)}}
    gh.merge_pr.side_effect = merge_raises
    gh.merge_pr.return_value = None

    evidence = MagicMock(name="evidence")
    evidence.resolve_verified_pr_head.return_value = Evidence(
        source="github_api",
        subject="pr_head",
        verdict=Verdict.CONFIRMED,
        observed_at=datetime.now(timezone.utc),
        verified_at=datetime.now(timezone.utc),
        verification_method="cat-file -e",
        commit_shas=("abc123",),
        reason="",
    )

    deps = MagicMock(name="deps")
    deps.host = host
    deps.gh = gh
    deps.evidence = evidence
    deps.git_workspace = MagicMock(name="git_workspace")
    return deps, host, gh


def _wf(base: str | None = None) -> dict:
    wf = {"github_pr_number": 2425, "branch_name": "auto-dev/2d0c317d-310"}
    if base is not None:
        wf["original_branch_name"] = base
    return wf


# ── call site 1: the pre-merge check query ────────────────────────────────


def test_non_required_failure_on_unstable_pr_neither_repairs_nor_blocks():
    """The wf227/#2328 shape: only a non-required check is red, PR is mergeable.

    CI-repair must not fire and the merge must proceed. #2428 held this with a
    required-filter; #27 removed that filter (aggregate gates made it wrong), so
    the protection now lives in the ``mergeable_state='unstable'`` short-circuit
    in handle().
    """
    deps, host, gh = _deps(checks=[{"name": "test (3.13)", "bucket": "fail"}])
    gh.get_pr_merge_state.return_value = {"mergeable": True, "mergeable_state": "unstable"}
    merge_phase.handle(_ctx(_wf()), deps)
    host.start_ci_repair_round.assert_not_called()
    gh.merge_pr.assert_called_once()


def test_required_failure_still_starts_a_repair_round_and_defers():
    """A required failure still gates the merge and starts a repair round."""
    deps, host, gh = _deps(checks=[{"name": "lint", "bucket": "fail"}])
    result = merge_phase.handle(_ctx(_wf()), deps)
    host.start_ci_repair_round.assert_called_once()
    gh.merge_pr.assert_not_called()
    assert result.outcome == "retry"


def test_all_failing_checks_are_repaired():
    """#27: CI-repair targets every failing check. The required-filter that
    previously skipped non-required failures (test (3.13), Critical PR E2E) was
    removed — aggregate gates make it wrong, and the unstable short-circuit
    already keeps non-gating failures out of this path.
    """
    deps, host, gh = _deps(
        checks=[
            {"name": "test (3.13)", "bucket": "fail"},
            {"name": "build", "bucket": "fail"},
            {"name": "Critical PR E2E", "bucket": "fail"},
        ]
    )
    merge_phase.handle(_ctx(_wf()), deps)
    host.start_ci_repair_round.assert_called_once()
    repaired = {c.get("name") for c in host.start_ci_repair_round.call_args[0][2]}
    assert repaired == {"test (3.13)", "build", "Critical PR E2E"}


# ── call site 2: the post-rejection refresh ─────────────────────────────


def test_failing_check_on_rejection_refresh_triggers_repair():
    """Call site 2 is reached only after merge_pr is rejected. Any failing check
    on the refresh triggers a repair round (the filter that previously skipped
    non-required ones like postgres-test is gone).
    """
    deps, host, gh = _deps(
        checks=[],
        merge_raises=GitHubOpsError("base branch policy prohibits the merge"),
    )
    gh.get_pr_checks.side_effect = [
        [],  # pre-merge: nothing failing, so the merge is attempted
        [{"name": "postgres-test", "bucket": "fail"}],  # refresh after rejection
    ]
    merge_phase.handle(_ctx(_wf()), deps)
    host.start_ci_repair_round.assert_called_once()
    repaired = [c.get("name") for c in host.start_ci_repair_round.call_args[0][2]]
    assert repaired == ["postgres-test"]


def test_required_failure_on_the_rejection_refresh_does_repair():
    deps, host, gh = _deps(
        checks=[],
        merge_raises=GitHubOpsError("base branch policy prohibits the merge"),
    )
    gh.get_pr_checks.side_effect = [
        [],
        [{"name": "test (3.11)", "bucket": "fail"}],
    ]
    merge_phase.handle(_ctx(_wf()), deps)
    host.start_ci_repair_round.assert_called_once()


# ── the pending split (required-pending still defers; optional does not) ──


def test_non_required_pending_check_does_not_defer_the_merge():
    """A slow optional job must not re-defer the merge every scheduler cycle."""
    deps, host, gh = _deps(checks=[{"name": "Critical PR E2E", "bucket": "pending"}])
    merge_phase.handle(_ctx(_wf()), deps)
    gh.merge_pr.assert_called_once()


def test_required_pending_check_still_defers_the_merge():
    deps, host, gh = _deps(checks=[{"name": "build", "bucket": "pending"}])
    result = merge_phase.handle(_ctx(_wf()), deps)
    gh.merge_pr.assert_not_called()
    assert result.outcome == "retry"


# ── the base branch is the PR's, not a hardcoded "main" ───────────────────


def test_required_checks_are_read_for_the_prs_actual_base_branch():
    """Hardcoding "main" reports the wrong required set for any other base.

    get_branch_protection is now called only by the pending-check path
    (_blocking_pending), so drive it with a pending check.
    """
    deps, host, gh = _deps(checks=[{"name": "build", "bucket": "pending"}])
    merge_phase.handle(_ctx(_wf(base="release/1.x")), deps)
    assert gh.get_branch_protection.call_args[0][0] == "release/1.x"


def test_base_branch_defaults_to_main_when_unset():
    deps, host, gh = _deps(checks=[{"name": "build", "bucket": "pending"}])
    merge_phase.handle(_ctx(_wf()), deps)
    assert gh.get_branch_protection.call_args[0][0] == "main"
