"""MergePhase handler unit tests (#2044 Phase B T10).

These tests prove the handler is independently testable: they pass fakes via
``PhaseDeps`` and NEVER construct ``AutonomousOrchestrator``. The decoupling
surface they exercise (the methods the handler actually calls on deps) is the
real measure of how far the merge phase has been decoupled from the ~10k-line
orchestrator concrete class.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from unittest.mock import MagicMock

from app.modules.workspace.autonomous import phases as phases_pkg
from app.modules.workspace.autonomous.evidence import Evidence, Verdict
from app.modules.workspace.autonomous.phase_contract import PhaseResult, WorkflowContext
from app.modules.workspace.autonomous.phases import merge as merge_phase


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


def _workflow(pr_number: int | None = 123) -> dict:
    return {
        "github_pr_number": pr_number,
        "branch_name": "feature-x",
    }


# ── registration ─────────────────────────────────────────────────────────


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
    assert result.next_phase == "completed"
    # The merge-pr call and the merged milestone went through deps/host.
    deps.gh.merge_pr.assert_called_once_with(123, strategy="merge")
    host.create_milestone_idempotent.assert_called()
    # Cleanup ran and its milestone rode in milestone_events.
    host.perform_git_cleanup.assert_called_once_with()
    assert any(
        ms.get("milestone_type") == "cleaned_up" for ms in result.milestone_events
    ), result.milestone_events
    # Terminal phase_change emitted through the host.
    host.emit_phase_change.assert_called_with({"phase": "completed"})


def test_merge_handle_no_pr_number_skips_to_cleanup():
    """A workflow without github_pr_number skips the entire PR probe and goes
    straight to delivery completion + cleanup."""
    deps, host = _build_deps()
    result = merge_phase.handle(_ctx(_workflow(pr_number=None)), deps)

    assert isinstance(result, PhaseResult)
    assert result.outcome == "completed"
    assert result.next_phase == "completed"
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
