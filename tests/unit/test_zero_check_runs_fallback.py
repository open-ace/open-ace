"""Issue #2673: zero-check-runs fallback state machine (merge phase).

A GitHub event-delivery gap leaves a PR's head with ZERO check-runs: no push,
no pull_request synchronize, no check-run is ever created. Required checks
then stay pending forever, mergeState stays BLOCKED, and the merge phase used
to retry silently every scheduler cycle with no detection, no fallback and no
alert (workflow #2550 / PR #2578 spun 4h+).

These tests pin the orchestrator-side fallback host helper
(``zero_check_runs_fallback``) that the merge handler calls when the PR's
checks come back empty:

- observation state persists in a ``pr_zero_check_runs`` milestone (no schema
  change), keyed on the verified head SHA so a new push resets the window;
- the PRIMARY gate is a wall-clock observation floor
  (``ZERO_CHECK_RUNS_WALL_CLOCK_FLOOR``, 20 minutes): the scheduler hot loop
  is ``_stop_event.wait(10)`` with no per-phase backoff, so cycles can arrive
  seconds apart — a few rapid zero-check cycles within the floor must merely
  defer (slow CI provisioning), never retrigger or escalate;
- once the floor elapsed AND ``ZERO_CHECK_RUNS_RETRIGGER_CYCLES`` cycles were
  observed, the helper mechanically retriggers event delivery via PR
  close+reopen; if the reopen half fails after a successful close, a
  ``reopen_pending`` tracker state makes the next cycle RETRY the reopen
  (bounded by ``ZERO_CHECK_RUNS_REOPEN_RETRY_MAX``) instead of the closed-PR
  guard permanently skipping while the PR stays closed;
- once the retrigger completed and its own observation floor elapsed with
  still-zero check-runs, the helper escalates to a transient-classified
  ``GitHubOpsError`` (closing the tracker) so advance()'s existing transient
  machinery makes the stall VISIBLE instead of silent spinning;
- a PR on a base branch with NO required checks never enters the fallback
  (zero check-runs is the normal state there, the merge proceeds directly).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.constants import _TRANSIENT_ORCHESTRATOR_KEYWORDS
from app.modules.workspace.autonomous.github_ops import GitHubOpsError
from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

# ── Harness ──────────────────────────────────────────────────────────────────

# Frozen clock: every test drives the patchable module-level ``_utcnow`` so
# wall-clock floor arithmetic is deterministic.
_NOW = datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc)


def _patch_now(moment: datetime):
    return patch(
        "app.modules.workspace.autonomous.orchestrator._utcnow",
        return_value=moment,
    )


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _make_orchestrator(workflow: dict) -> AutonomousOrchestrator:
    """Build an orchestrator whose repo/emitter are fully mocked.

    Follows tests/unit/test_orchestrator_characterization.py: patch the
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
    head_sha: str = "abc123",
    cycles: int = 1,
    retriggered: bool = False,
    *,
    first_seen_at: str | None = None,
    retriggered_at: str | None = None,
    reopen_pending: bool = False,
    reopen_attempts: int = 0,
) -> dict:
    meta: dict = {"head_sha": head_sha, "cycles": cycles, "retriggered": retriggered}
    # Default: the head was first seen 25 minutes ago — past the 20-minute
    # floor, so cycle-count/threshold tests exercise their gate directly.
    meta["first_seen_at"] = (
        first_seen_at if first_seen_at is not None else _iso(_NOW - timedelta(minutes=25))
    )
    if retriggered_at is not None:
        meta["retriggered_at"] = retriggered_at
    if reopen_pending:
        meta["reopen_pending"] = True
        meta["reopen_attempts"] = reopen_attempts
    return {
        "milestone_id": "ms-zero-1",
        "workflow_id": "wf-test",
        "phase": "merge",
        "milestone_type": "pr_zero_check_runs",
        "status": "in_progress",
        "metadata": json.dumps(meta),
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


def _recorded_metadata(o: AutonomousOrchestrator) -> list[dict]:
    """Every tracker metadata payload written this call (create or update)."""
    payloads = [
        json.loads(call.args[1]["metadata"])
        for call in o.repo.update_milestone.call_args_list
        if "metadata" in call.args[1]
    ]
    for call in o.repo.create_milestone.call_args_list:
        kwargs = call.args[0] or call.kwargs
        payloads.append(json.loads(kwargs["metadata"]))
    return payloads


# ── Cycle accounting ─────────────────────────────────────────────────────────


pytestmark = [pytest.mark.regression, pytest.mark.issue(2673)]


class TestZeroCheckRunsCycleAccounting:
    def test_first_zero_check_cycle_records_tracker_and_defers(self):
        o = _make_orchestrator(_workflow())
        o.repo.list_milestones.return_value = []
        gh = _gh()

        with _patch_now(_NOW):
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
        # The observation window starts NOW — the wall-clock floor is measured
        # from this persisted timestamp, not from process uptime.
        assert meta["first_seen_at"] == _iso(_NOW)

    def test_new_head_resets_counter_without_retrigger(self):
        """A new push (new head SHA) must start a fresh observation window —
        the tracker left by the old head neither skips cycles nor fires a
        stale close+reopen."""
        o = _make_orchestrator(_workflow())
        o.repo.list_milestones.return_value = [_tracker_milestone(head_sha="oldsha", cycles=1)]
        gh = _gh()

        with _patch_now(_NOW):
            took_over = o.zero_check_runs_fallback(gh, 2578, "newsha", "main", [])

        assert took_over is True
        gh.close_pr.assert_not_called()
        o.repo.update_milestone.assert_called_once()
        updates = o.repo.update_milestone.call_args[0][1]
        meta = json.loads(updates["metadata"])
        assert meta["head_sha"] == "newsha"
        assert meta["cycles"] == 1
        assert meta["retriggered"] is False
        # Fresh window: first_seen_at restarts for the new head.
        assert meta["first_seen_at"] == _iso(_NOW)

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

    def test_tracker_load_failure_defers_without_duplicate_tracker(self):
        """If the tracker milestone cannot be listed (swallowed DB failure),
        the helper must NOT create a second in_progress tracker (that would
        orphan the unloaded one). It defers; the next cycle re-reads."""
        o = _make_orchestrator(_workflow())
        o.repo.list_milestones.side_effect = Exception("db down")
        gh = _gh()

        with _patch_now(_NOW):
            took_over = o.zero_check_runs_fallback(gh, 2578, "abc123", "main", [])

        assert took_over is True
        o.repo.create_milestone.assert_not_called()
        o.repo.update_milestone.assert_not_called()
        gh.close_pr.assert_not_called()


# ── Wall-clock observation floor ─────────────────────────────────────────────


class TestZeroCheckRunsWallClockFloor:
    def test_rapid_cycles_within_floor_defer_without_retrigger(self):
        """The scheduler hot loop runs every ~10s, so cycles can pile up
        minutes after the push. However many cycles elapsed, the retrigger
        must NOT fire before the 20-minute observation floor — otherwise a
        slow-CI-provisioning head is terminally failed in ~2-3 minutes
        (regression vs. the old recoverable spin)."""
        o = _make_orchestrator(_workflow())
        o.repo.list_milestones.return_value = [
            _tracker_milestone(
                cycles=5,
                first_seen_at=_iso(_NOW - timedelta(minutes=5)),
            )
        ]
        gh = _gh()

        with _patch_now(_NOW):
            took_over = o.zero_check_runs_fallback(gh, 2578, "abc123", "main", [])

        assert took_over is True
        gh.close_pr.assert_not_called()
        gh.reopen_pr.assert_not_called()
        # The observation is still recorded (cycle 6) with the ORIGINAL
        # first_seen_at — the floor window is not restarted by deferrals.
        metas = _recorded_metadata(o)
        assert metas, "observation must still be recorded"
        assert metas[-1]["cycles"] == 6
        assert metas[-1]["first_seen_at"] == _iso(_NOW - timedelta(minutes=5))

    def test_cycles_past_floor_retrigger(self):
        """Floor elapsed AND ≥2 cycles observed: the retrigger fires."""
        o = _make_orchestrator(_workflow())
        o.repo.list_milestones.return_value = [_tracker_milestone(cycles=1)]
        gh = _gh()

        with _patch_now(_NOW):
            took_over = o.zero_check_runs_fallback(gh, 2578, "abc123", "main", [])

        assert took_over is True
        gh.close_pr.assert_called_once_with(2578)
        gh.reopen_pr.assert_called_once_with(2578)
        metas = _recorded_metadata(o)
        assert any(m.get("retriggered") for m in metas), "retrigger must be durable"
        assert any(
            m.get("retriggered_at") == _iso(_NOW) for m in metas
        ), "retrigger timestamp must be persisted for the escalation floor"

    def test_after_retrigger_within_floor_defers_without_escalation(self):
        """The retrigger fired but its own 20-minute floor has not elapsed —
        CI provisioning after the reopened events needs time. No escalation."""
        o = _make_orchestrator(_workflow())
        o.repo.list_milestones.return_value = [
            _tracker_milestone(
                cycles=2,
                retriggered=True,
                retriggered_at=_iso(_NOW - timedelta(minutes=5)),
            )
        ]
        gh = _gh()

        with _patch_now(_NOW):
            took_over = o.zero_check_runs_fallback(gh, 2578, "abc123", "main", [])

        assert took_over is True
        gh.close_pr.assert_not_called()
        gh.reopen_pr.assert_not_called()

    def test_after_retrigger_past_floor_escalates_and_closes_tracker(self):
        """Still zero check-runs one floor after the retrigger: raise the
        transient-classified GitHubOpsError AND close the tracker milestone
        (no orphaned in_progress tracker on the terminal path)."""
        o = _make_orchestrator(_workflow())
        o.repo.list_milestones.return_value = [
            _tracker_milestone(
                cycles=2,
                retriggered=True,
                retriggered_at=_iso(_NOW - timedelta(minutes=25)),
            )
        ]
        gh = _gh()

        with _patch_now(_NOW):
            with pytest.raises(GitHubOpsError) as excinfo:
                o.zero_check_runs_fallback(gh, 2578, "abc123", "main", [])

        err = str(excinfo.value)
        assert "2578" in err and "abc123" in err
        lowered = err.lower()
        assert any(kw in lowered for kw in _TRANSIENT_ORCHESTRATOR_KEYWORDS), err
        finalize_calls = [
            call.args[1]
            for call in o.repo.update_milestone.call_args_list
            if call.args[1].get("status") not in (None, "in_progress")
        ]
        assert (
            finalize_calls and finalize_calls[-1]["status"] == "failed"
        ), "escalation must close the tracker (status failed) with the error recorded"

    def test_tracker_without_first_seen_backfills_floor(self):
        """A pre-floor tracker metadata (no first_seen_at) must not retrigger
        immediately: the observation floor restarts from now."""
        o = _make_orchestrator(_workflow())
        stale = _tracker_milestone(cycles=5)
        meta = json.loads(stale["metadata"])
        meta.pop("first_seen_at", None)
        stale["metadata"] = json.dumps(meta)
        o.repo.list_milestones.return_value = [stale]
        gh = _gh()

        with _patch_now(_NOW):
            took_over = o.zero_check_runs_fallback(gh, 2578, "abc123", "main", [])

        assert took_over is True
        gh.close_pr.assert_not_called()
        assert _recorded_metadata(o)[-1]["first_seen_at"] == _iso(_NOW)


# ── Mechanical retrigger (close + reopen) ────────────────────────────────────


class TestZeroCheckRunsRetrigger:
    def test_threshold_cycle_retriggers_via_close_and_reopen(self):
        """Floor elapsed + second consecutive zero-check cycle: close+reopen
        the PR through the API (a CI nudge — no code/PR-content change),
        record it on the tracker + an audit event, and defer one more
        cycle."""
        o = _make_orchestrator(_workflow())
        o.repo.list_milestones.return_value = [_tracker_milestone(cycles=1)]
        gh = _gh()

        with _patch_now(_NOW):
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

        with _patch_now(_NOW):
            took_over = o.zero_check_runs_fallback(gh, 2578, "abc123", "main", [])

        assert took_over is False
        gh.close_pr.assert_not_called()

    def test_retrigger_failure_propagates(self):
        """If the close API call itself fails, propagate so advance() handles
        it like any other GitHubOpsError (visible, retried)."""
        o = _make_orchestrator(_workflow())
        o.repo.list_milestones.return_value = [_tracker_milestone(cycles=1)]
        gh = _gh()
        gh.close_pr.side_effect = GitHubOpsError("api refused")

        with _patch_now(_NOW):
            with pytest.raises(GitHubOpsError):
                o.zero_check_runs_fallback(gh, 2578, "abc123", "main", [])


# ── Partial state: close succeeded, reopen failed ────────────────────────────


class TestZeroCheckRunsReopenPending:
    def test_reopen_failure_after_close_records_reopen_pending(self):
        """close_pr succeeded but reopen_pr raised: the PR is now CLOSED. The
        tracker must record ``reopen_pending`` so the NEXT cycle retries the
        reopen — the closed-PR guard alone would permanently skip the
        retrigger while the PR stays closed."""
        o = _make_orchestrator(_workflow())
        o.repo.list_milestones.return_value = [_tracker_milestone(cycles=1)]
        gh = _gh()
        gh.reopen_pr.side_effect = GitHubOpsError("reopen refused")

        with _patch_now(_NOW):
            took_over = o.zero_check_runs_fallback(gh, 2578, "abc123", "main", [])

        assert took_over is True
        metas = _recorded_metadata(o)
        assert any(
            m.get("reopen_pending") and m.get("reopen_attempts") == 1 for m in metas
        ), "partial state must be recorded durably"
        # The retrigger is INCOMPLETE — no success event was emitted.
        emitted_types = [c.args[1] for c in o.emitter.emit.call_args_list]
        assert "zero_check_runs_retrigger" not in emitted_types

    def test_reopen_pending_closed_pr_retries_reopen_next_cycle(self):
        """Next cycle sees reopen_pending + PR closed: it attempts the reopen
        (not a skip). Success completes the retrigger durably."""
        o = _make_orchestrator(_workflow())
        o.repo.list_milestones.return_value = [
            _tracker_milestone(cycles=2, retriggered=False, reopen_pending=True, reopen_attempts=1)
        ]
        gh = _gh(pr_state="closed")

        with _patch_now(_NOW):
            took_over = o.zero_check_runs_fallback(gh, 2578, "abc123", "main", [])

        assert took_over is True
        gh.close_pr.assert_not_called()  # close already happened
        gh.reopen_pr.assert_called_once_with(2578)
        metas = _recorded_metadata(o)
        assert any(
            m.get("retriggered") and m.get("retriggered_at") == _iso(_NOW) for m in metas
        ), "completed reopen must start the escalation floor"
        emitted_types = [c.args[1] for c in o.emitter.emit.call_args_list]
        assert "zero_check_runs_retrigger" in emitted_types

    def test_reopen_pending_open_pr_completes_retrigger(self):
        """reopen_pending but the PR reads OPEN (someone reopened it manually,
        or the earlier failure was spurious): treat the retrigger as complete
        and start the escalation floor — do not close an open PR."""
        o = _make_orchestrator(_workflow())
        o.repo.list_milestones.return_value = [
            _tracker_milestone(cycles=2, retriggered=False, reopen_pending=True, reopen_attempts=1)
        ]
        gh = _gh(pr_state="open")

        with _patch_now(_NOW):
            took_over = o.zero_check_runs_fallback(gh, 2578, "abc123", "main", [])

        assert took_over is True
        gh.close_pr.assert_not_called()
        gh.reopen_pr.assert_not_called()
        metas = _recorded_metadata(o)
        assert any(m.get("retriggered") and not m.get("reopen_pending") for m in metas)

    def test_reopen_retry_exhausted_escalates_visibly(self):
        """Reopen attempts hit the retry cap and the PR is still closed:
        escalate as a transient-classified GitHubOpsError (and close the
        tracker) — never a silent skip with a permanently closed PR."""
        o = _make_orchestrator(_workflow())
        o.repo.list_milestones.return_value = [
            _tracker_milestone(cycles=3, retriggered=False, reopen_pending=True, reopen_attempts=2)
        ]
        gh = _gh(pr_state="closed")

        with _patch_now(_NOW):
            with pytest.raises(GitHubOpsError) as excinfo:
                o.zero_check_runs_fallback(gh, 2578, "abc123", "main", [])

        err = str(excinfo.value)
        assert "2578" in err and "abc123" in err
        lowered = err.lower()
        assert any(kw in lowered for kw in _TRANSIENT_ORCHESTRATOR_KEYWORDS), err
        gh.reopen_pr.assert_not_called()  # cap reached — no further attempts
        finalize_calls = [
            call.args[1]
            for call in o.repo.update_milestone.call_args_list
            if call.args[1].get("status") not in (None, "in_progress")
        ]
        assert finalize_calls and finalize_calls[-1]["status"] == "failed"


# ── Escalation to visible transient infrastructure error ─────────────────────


class TestZeroCheckRunsEscalation:
    def test_still_zero_after_retrigger_raises_transient_githubopserror(self):
        """Still-zero check-runs one observation floor after the retrigger
        must raise a transient-classified GitHubOpsError so advance()'s
        existing transient machinery (error_message + bounded retries, then
        visible failure) takes over — never a silent infinite wait."""
        o = _make_orchestrator(_workflow())
        o.repo.list_milestones.return_value = [
            _tracker_milestone(
                cycles=2, retriggered=True, retriggered_at=_iso(_NOW - timedelta(minutes=25))
            )
        ]
        gh = _gh()

        with _patch_now(_NOW):
            with pytest.raises(GitHubOpsError) as excinfo:
                o.zero_check_runs_fallback(gh, 2578, "abc123", "main", [])

        err = str(excinfo.value)
        assert "2578" in err and "abc123" in err
        # advance() classifies transients by keyword match against the
        # lowercased message — the escalation must actually classify.
        lowered = err.lower()
        assert any(kw in lowered for kw in _TRANSIENT_ORCHESTRATOR_KEYWORDS), err
