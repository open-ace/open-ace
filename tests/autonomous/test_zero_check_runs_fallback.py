"""Issue #2673: zero-check-runs fallback state machine (merge phase).

A GitHub event-delivery gap leaves a PR's head with ZERO check-runs: no push,
no pull_request synchronize, no check-run is ever created. Required checks
then stay pending forever, mergeState stays BLOCKED, and the merge phase used
to retry silently every scheduler cycle with no detection, no fallback and no
alert (workflow #2550 / PR #2578 spun 4h+).

These tests pin the orchestrator-side fallback host helper
(``zero_check_runs_fallback``) that the merge handler calls when the PR's
checks come back empty:

- cycle accounting persists in a ``pr_zero_check_runs`` milestone (no schema
  change), keyed on the verified head SHA so a new push resets the window;
- after ``ZERO_CHECK_RUNS_RETRIGGER_CYCLES`` consecutive zero-check cycles the
  helper mechanically retriggers event delivery via PR close+reopen;
- one cycle after the retrigger, still-zero check-runs escalates to a
  transient-classified ``GitHubOpsError`` so advance()'s existing transient
  machinery makes the stall VISIBLE (error_message + bounded retries) instead
  of silent spinning;
- a PR on a base branch with NO required checks never enters the fallback
  (zero check-runs is the normal state there, the merge proceeds directly).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.constants import _TRANSIENT_ORCHESTRATOR_KEYWORDS
from app.modules.workspace.autonomous.github_ops import GitHubOpsError
from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

# ── Harness ──────────────────────────────────────────────────────────────────


def _make_orchestrator(workflow: dict) -> AutonomousOrchestrator:
    """Build an orchestrator whose repo/emitter are fully mocked.

    Follows tests/autonomous/test_orchestrator_characterization.py: patch the
    DB/repo/session constructions at import time so __init__ does no real
    work, then drive ``o.repo`` as a MagicMock.
    """
    with (
        patch("app.modules.workspace.autonomous.orchestrator.Database"),
        patch("app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"),
        patch("app.modules.workspace.session_manager.SessionManager"),
        patch("app.modules.workspace.autonomous.agent_runner.AutonomousAgentRunner"),
    ):
        o = AutonomousOrchestrator(workflow.get("workflow_id", "wf-test"))
    o.repo = MagicMock(name="repo")
    o.repo.get_workflow.return_value = dict(workflow)
    o.emitter = MagicMock(name="emitter")
    return o


def _workflow(**overrides) -> dict:
    wf = {
        "workflow_id": "wf-test",
        "status": "merging",
        "current_phase": "merge",
        "github_pr_number": 2578,
        "branch_name": "auto-dev/wf-test",
        "original_branch_name": "main",
        "dev_round": 1,
        "transient_retry_count": 0,
    }
    wf.update(overrides)
    return wf


def _tracker_milestone(
    head_sha: str = "abc123", cycles: int = 1, retriggered: bool = False
) -> dict:
    return {
        "milestone_id": "ms-zero-1",
        "workflow_id": "wf-test",
        "phase": "merge",
        "milestone_type": "pr_zero_check_runs",
        "status": "in_progress",
        "metadata": json.dumps(
            {"head_sha": head_sha, "cycles": cycles, "retriggered": retriggered}
        ),
    }


def _gh(
    *,
    required: list[str] | None = None,
    pr_state: str = "open",
) -> MagicMock:
    gh = MagicMock(name="gh")
    gh.get_branch_protection.return_value = {
        "required_status_checks": {"contexts": required if required is not None else ["PR Gate"]}
    }
    gh.get_pr.return_value = {"state": pr_state, "number": 2578}
    return gh


# ── Cycle accounting ─────────────────────────────────────────────────────────


class TestZeroCheckRunsCycleAccounting:
    def test_first_zero_check_cycle_records_tracker_and_defers(self):
        o = _make_orchestrator(_workflow())
        o.repo.list_milestones.return_value = []
        gh = _gh()

        took_over = o.zero_check_runs_fallback(gh, 2578, "abc123", "main", [])

        assert took_over is True
        gh.close_pr.assert_not_called()
        gh.reopen_pr.assert_not_called()
        o.repo.create_milestone.assert_called_once()
        kwargs = o.repo.create_milestone.call_args[0][0] or o.repo.create_milestone.call_args.kwargs
        assert kwargs["milestone_type"] == "pr_zero_check_runs"
        assert kwargs["status"] == "in_progress"
        meta = json.loads(kwargs["metadata"])
        assert meta["head_sha"] == "abc123"
        assert meta["cycles"] == 1
        assert meta["retriggered"] is False

    def test_new_head_resets_counter_without_retrigger(self):
        """A new push (new head SHA) must start a fresh observation window —
        the tracker left by the old head neither skips cycles nor fires a
        stale close+reopen."""
        o = _make_orchestrator(_workflow())
        o.repo.list_milestones.return_value = [_tracker_milestone(head_sha="oldsha", cycles=1)]
        gh = _gh()

        took_over = o.zero_check_runs_fallback(gh, 2578, "newsha", "main", [])

        assert took_over is True
        gh.close_pr.assert_not_called()
        o.repo.update_milestone.assert_called_once()
        updates = o.repo.update_milestone.call_args[0][1]
        meta = json.loads(updates["metadata"])
        assert meta["head_sha"] == "newsha"
        assert meta["cycles"] == 1
        assert meta["retriggered"] is False

    def test_checks_present_closes_tracker_and_returns_false(self):
        """When check-runs appear (delivery recovered or CI started), the
        helper closes out any open tracker and lets the merge proceed."""
        o = _make_orchestrator(_workflow())
        o.repo.list_milestones.return_value = [_tracker_milestone(cycles=1)]
        gh = _gh()

        took_over = o.zero_check_runs_fallback(
            gh, 2578, "abc123", "main", [{"name": "test (3.12)", "bucket": "pass"}]
        )

        assert took_over is False
        gh.get_branch_protection.assert_not_called()
        gh.close_pr.assert_not_called()
        o.repo.update_milestone.assert_called_once()
        assert o.repo.update_milestone.call_args[0][1]["status"] == "completed"
        o.repo.create_milestone.assert_not_called()

    def test_no_required_checks_never_enters_fallback(self):
        """Zero check-runs on an unprotected base is the NORMAL state (repos
        without CI gating); the merge must proceed untouched."""
        o = _make_orchestrator(_workflow())
        o.repo.list_milestones.return_value = []
        gh = _gh(required=[])

        took_over = o.zero_check_runs_fallback(gh, 2578, "abc123", "main", [])

        assert took_over is False
        o.repo.create_milestone.assert_not_called()
        gh.close_pr.assert_not_called()

    def test_protection_probe_failure_degrades_to_no_fallback(self):
        """An undeterminable required set must not fabricate a zero-check
        incident — degrade to the legacy behaviour (return False)."""
        o = _make_orchestrator(_workflow())
        o.repo.list_milestones.return_value = []
        gh = _gh()
        gh.get_branch_protection.side_effect = GitHubOpsError("blind probe")

        took_over = o.zero_check_runs_fallback(gh, 2578, "abc123", "main", [])

        assert took_over is False
        o.repo.create_milestone.assert_not_called()


# ── Mechanical retrigger (close + reopen) ────────────────────────────────────


class TestZeroCheckRunsRetrigger:
    def test_threshold_cycle_retriggers_via_close_and_reopen(self):
        """Second consecutive zero-check cycle: close+reopen the PR through
        the API (a CI nudge — no code/PR-content change), record it on the
        tracker + an audit event, and defer one more cycle."""
        o = _make_orchestrator(_workflow())
        o.repo.list_milestones.return_value = [_tracker_milestone(cycles=1)]
        gh = _gh()

        took_over = o.zero_check_runs_fallback(gh, 2578, "abc123", "main", [])

        assert took_over is True
        gh.close_pr.assert_called_once_with(2578)
        gh.reopen_pr.assert_called_once_with(2578)
        o.repo.update_milestone.assert_called()
        retrigger_updates = [
            call.args[1]
            for call in o.repo.update_milestone.call_args_list
            if json.loads(call.args[1].get("metadata", "{}")).get("retriggered")
        ]
        assert retrigger_updates, "tracker must record the retrigger durably"
        emitted_types = [c.args[1] for c in o.emitter.emit.call_args_list]
        assert "zero_check_runs_retrigger" in emitted_types

    def test_closed_pr_skips_retrigger(self):
        """close+reopen only makes sense on an open PR; a closed one falls
        through to the legacy path."""
        o = _make_orchestrator(_workflow())
        o.repo.list_milestones.return_value = [_tracker_milestone(cycles=1)]
        gh = _gh(pr_state="closed")

        took_over = o.zero_check_runs_fallback(gh, 2578, "abc123", "main", [])

        assert took_over is False
        gh.close_pr.assert_not_called()


# ── Escalation to visible transient infrastructure error ─────────────────────


class TestZeroCheckRunsEscalation:
    def test_still_zero_after_retrigger_raises_transient_githubopserror(self):
        """One cycle after the retrigger, still-zero check-runs must raise a
        transient-classified GitHubOpsError so advance()'s existing transient
        machinery (error_message + bounded retries, then visible failure)
        takes over — never a silent infinite wait."""
        o = _make_orchestrator(_workflow())
        o.repo.list_milestones.return_value = [_tracker_milestone(cycles=2, retriggered=True)]
        gh = _gh()

        with pytest.raises(GitHubOpsError) as excinfo:
            o.zero_check_runs_fallback(gh, 2578, "abc123", "main", [])

        err = str(excinfo.value)
        assert "2578" in err and "abc123" in err
        # advance() classifies transients by keyword match against the
        # lowercased message — the escalation must actually classify.
        lowered = err.lower()
        assert any(kw in lowered for kw in _TRANSIENT_ORCHESTRATOR_KEYWORDS), err

    def test_retrigger_failure_propagates(self):
        """If the close/reopen API call itself fails, propagate so advance()
        handles it like any other GitHubOpsError (visible, retried)."""
        o = _make_orchestrator(_workflow())
        o.repo.list_milestones.return_value = [_tracker_milestone(cycles=1)]
        gh = _gh()
        gh.close_pr.side_effect = GitHubOpsError("api refused")

        with pytest.raises(GitHubOpsError):
            o.zero_check_runs_fallback(gh, 2578, "abc123", "main", [])
