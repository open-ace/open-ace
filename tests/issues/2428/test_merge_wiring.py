"""Issue #2428: pin the required/optional split to the live merge path.

``test_blocking_failures_filter.py`` imports ``_blocking_failures`` directly and
proves the helper's logic. That says nothing about whether ``merge.handle``
actually calls it — and the bug this issue exists to fix was precisely that:
``ReadinessService.collect_actionable_ci_failures`` already implemented the
split correctly, and nothing ever called it, so the live merge path repaired
every failing check for months.

Shipping ``_blocking_failures`` with only direct-import coverage would recreate
that exact exposure. A review of PR #2430 confirmed it: reverting BOTH call
sites in ``phases/merge.py`` back to

    failed = [c for c in checks if c.get("bucket") == "fail"]

left 240 tests passing. These tests drive ``merge.handle`` end to end so that
mutation fails.

The concrete production incident: workflow ``2d0c317d`` (issue #2328, PR #2425)
burned all five CI-repair rounds and died reporting ``test (3.13)`` — a check
``main`` does not require. Required on this repo is exactly
``lint``, ``test (3.10/3.11/3.12)``, ``build`` (verified against the live
ruleset API).
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from unittest.mock import MagicMock

from app.modules.workspace.autonomous.evidence import Evidence, Verdict
from app.modules.workspace.autonomous.github_ops import GitHubOpsError
from app.modules.workspace.autonomous.phase_contract import WorkflowContext
from app.modules.workspace.autonomous.phases import merge as merge_phase

# The live required set for open-ace/open-ace main, from
# `gh api repos/open-ace/open-ace/rules/branches/main`.
REQUIRED = ["lint", "test (3.10)", "test (3.11)", "test (3.12)", "build"]


def _ctx(workflow: dict) -> WorkflowContext:
    return WorkflowContext(
        workflow=workflow,
        definition_snapshot=None,
        repository_context=None,
        session_bindings={},
        cancellation=threading.Event(),
    )


def _deps(*, checks, required=REQUIRED, merge_raises=None, protection_raises=None):
    host = MagicMock(name="host")
    host.perform_git_cleanup.return_value = ("completed", "")
    host.validate_pre_merge_change_scope.return_value = ""
    host.sync_failed_pr_with_main.return_value = False
    host.branch_contains_main.return_value = False

    gh = MagicMock(name="gh")
    # "clean" (not "unstable") so the handler enters the sync + CI-repair block;
    # an unstable PR skips that block entirely via a pre-existing #2034 path.
    gh.get_pr_merge_state.return_value = {"mergeable": True, "mergeable_state": "clean"}
    gh.get_pr_checks.return_value = checks
    if protection_raises is not None:
        gh.get_branch_protection.side_effect = protection_raises
    else:
        gh.get_branch_protection.return_value = {
            "required_status_checks": {"contexts": list(required)}
        }
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


def test_non_required_failure_neither_repairs_nor_blocks_the_merge():
    """The #2328 incident, as a test.

    `test (3.13)` failing must not consume a CI-repair round and must not stop
    the merge. Reverting call site 1 to the unfiltered comprehension makes
    start_ci_repair_round fire and merge_pr never run.
    """
    deps, host, gh = _deps(checks=[{"name": "test (3.13)", "bucket": "fail"}])
    merge_phase.handle(_ctx(_wf()), deps)
    host.start_ci_repair_round.assert_not_called()
    gh.merge_pr.assert_called_once()


def test_required_failure_still_starts_a_repair_round_and_defers():
    """The filter must not swing the other way: a required failure still gates."""
    deps, host, gh = _deps(checks=[{"name": "lint", "bucket": "fail"}])
    result = merge_phase.handle(_ctx(_wf()), deps)
    host.start_ci_repair_round.assert_called_once()
    gh.merge_pr.assert_not_called()
    assert result.outcome == "retry"


def test_mixed_failures_repair_only_the_blocking_ones():
    deps, host, gh = _deps(
        checks=[
            {"name": "test (3.13)", "bucket": "fail"},
            {"name": "build", "bucket": "fail"},
            {"name": "Critical PR E2E", "bucket": "fail"},
        ]
    )
    merge_phase.handle(_ctx(_wf()), deps)
    host.start_ci_repair_round.assert_called_once()
    repaired = [c.get("name") for c in host.start_ci_repair_round.call_args[0][2]]
    assert repaired == ["build"], f"repaired non-blocking checks: {repaired}"


# ── call site 2: the post-rejection refresh ───────────────────────────────


def test_non_required_failure_on_the_rejection_refresh_does_not_repair():
    """Call site 2 is reached only after merge_pr is rejected.

    Covered separately because reverting site 1 and site 2 are independent
    mutations — the review confirmed each survives the suite on its own.
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
    host.start_ci_repair_round.assert_not_called()


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


# ── the pending split (same bug class, other four lines) ──────────────────


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

    Without this the mocked gh ignores the argument, so `get_branch_protection("master")`
    survives every test in the suite.
    """
    deps, host, gh = _deps(checks=[{"name": "lint", "bucket": "fail"}])
    merge_phase.handle(_ctx(_wf(base="release/1.x")), deps)
    assert gh.get_branch_protection.call_args[0][0] == "release/1.x"


def test_base_branch_defaults_to_main_when_unset():
    deps, host, gh = _deps(checks=[{"name": "lint", "bucket": "fail"}])
    merge_phase.handle(_ctx(_wf()), deps)
    assert gh.get_branch_protection.call_args[0][0] == "main"


# ── degradation: an unobservable required set must fail closed ────────────


def test_undeterminable_required_set_repairs_everything():
    """Fail closed on blindness: better a wasted repair round than a bad merge.

    Note this is exactly the path that reproduces the original bug, which is why
    github_ops retries transient probe failures before giving up.
    """
    deps, host, gh = _deps(
        checks=[{"name": "test (3.13)", "bucket": "fail"}],
        protection_raises=GitHubOpsError("could not determine required checks"),
    )
    merge_phase.handle(_ctx(_wf()), deps)
    host.start_ci_repair_round.assert_called_once()


def test_branch_with_no_required_checks_repairs_everything():
    deps, host, gh = _deps(checks=[{"name": "test (3.13)", "bucket": "fail"}], required=[])
    merge_phase.handle(_ctx(_wf()), deps)
    host.start_ci_repair_round.assert_called_once()
