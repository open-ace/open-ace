"""Issue #2431: a workflow parked at acceptance_verification must not stall its batch.

#2335 added an ``acceptance_verification`` phase and, via
``orchestrator.PHASE_STATUS_MAP``, a ``verification_pending`` status. That
string was added to exactly one line of production code and to none of the sets
that make the system act on a workflow. In ``_promote_queued_workflows`` it is
in NEITHER ``QUEUE_ADVANCE_STATUSES`` nor ``QUEUE_BLOCKING_STATUSES``, so the
"not in advance" branch stalls every queued sibling behind it, forever.

Measured in production on 2026-08-08: workflow ``f332e86f`` (issue #2329, PR
#2426) sat in ``verification_pending`` at ``current_phase='acceptance_verification'``
with ``cleanup_status='completed'`` and an EMPTY ``worktree_path``, holding five
siblings (#2330-#2334) at ``queued`` for over 14 hours. No agent had spawned in
that window at all.

The fix keys on PHASE rather than status, because the same rest point can carry
verification_pending / paused / failed / completed and all of them mean "the
merge is done, the worktree is released, the slot is free".
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.services.autonomous_scheduler import (
    QUEUE_ADVANCE_STATUSES,
    QUEUE_BLOCKING_STATUSES,
    AutonomousScheduler,
    _slot_released,
)


def _wf(wid: str, status: str, phase: str, order: int) -> dict:
    return {
        "workflow_id": wid,
        "status": status,
        "current_phase": phase,
        "batch_id": "b1",
        "batch_order": order,
    }


def _promote(head: dict) -> bool:
    """Run one promotion pass over a 2-workflow batch; True if the tail started."""
    tail = _wf("tail", "queued", "preparation", 2)
    repo = MagicMock()
    repo.get_queued_workflows.return_value = [tail]
    repo.list_batch_workflows.return_value = [head, tail]
    scheduler = AutonomousScheduler.__new__(AutonomousScheduler)
    scheduler._promote_queued_workflows(repo)
    return any(
        call.args[0] == "tail" and call.args[1].get("status") == "pending"
        for call in repo.update_workflow.call_args_list
    )


class TestDeliveredPredecessorsReleaseTheQueue:
    def test_verification_pending_head_no_longer_stalls_the_batch(self):
        """The incident itself."""
        assert _promote(_wf("head", "verification_pending", "acceptance_verification", 1))

    def test_paused_at_acceptance_also_releases(self):
        """The `indeterminate` verdict pauses the workflow.

        `paused` is in QUEUE_BLOCKING_STATUSES, which is tested one branch
        EARLIER than the advance set, so fixing only the advance set would
        re-stall the batch under a different status name.
        """
        assert _promote(_wf("head", "paused", "acceptance_verification", 1))

    def test_failed_at_acceptance_releases(self):
        assert _promote(_wf("head", "failed", "acceptance_verification", 1))


class TestNonDeliveredPredecessorsStillBlock:
    def test_paused_mid_flight_still_holds_the_queue(self):
        """A user's manual pause during development must still block.

        This is the guard against over-broadening: the fix must key on the
        phase, not simply drop `paused` from the blocking set.
        """
        assert not _promote(_wf("head", "paused", "development", 1))

    def test_developing_head_still_holds_the_queue(self):
        assert not _promote(_wf("head", "developing", "development", 1))

    def test_cancelled_at_acceptance_still_blocks(self):
        """An operator stopped this batch; reaching acceptance must not revive it."""
        assert not _promote(_wf("head", "cancelled", "acceptance_verification", 1))

    def test_queued_head_still_blocks(self):
        assert not _promote(_wf("head", "queued", "preparation", 1))


class TestPredicate:
    def test_only_acceptance_verification_counts_as_delivered(self):
        for phase in ("preparation", "planning", "development", "pr_review", "report", "merge"):
            assert not _slot_released({"current_phase": phase}), phase
        assert _slot_released({"current_phase": "acceptance_verification"})

    def test_missing_or_null_phase_is_not_delivered(self):
        """Legacy rows must fall through to the original status-based logic."""
        assert not _slot_released({})
        assert not _slot_released({"current_phase": None})
        assert not _slot_released({"current_phase": ""})

    def test_status_sets_are_untouched(self):
        """The fix adds a branch; it must not quietly re-define the old sets."""
        assert {"waiting", "completed", "failed", "planning_timeout"} == QUEUE_ADVANCE_STATUSES
        assert {"paused", "cancelled"} == QUEUE_BLOCKING_STATUSES
        assert "verification_pending" not in QUEUE_ADVANCE_STATUSES
