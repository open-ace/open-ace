"""Regression (#2706): scheduler selection must tiebreak on batch_order.

Batch siblings are inserted in one loop, so they share (or nearly share)
``created_at``. The selection sort keyed only on ``(waiting, created_at)``,
leaving same-timestamp siblings ordered by whatever the DB returned — a later
sibling could repeatedly win the batch's single slot and hold the conflict
lock while the earlier one starved behind it, surfacing as a batch "stuck for
hours". ``batch_order`` is persisted at insert time
(autonomous_repo.py) but was never read back here.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.services.autonomous_scheduler import AutonomousScheduler

pytestmark = [pytest.mark.regression, pytest.mark.issue(2706)]

CREATED_AT = "2026-08-15 12:00:00"


def _wf(workflow_id, batch_id, batch_order, **overrides):
    base = {
        "workflow_id": workflow_id,
        "status": "pending",
        "created_at": CREATED_AT,
        "batch_id": batch_id,
        "batch_order": batch_order,
        "worktree_path": f"/wt/{workflow_id}",
        "branch_name": f"auto/{workflow_id}",
    }
    base.update(overrides)
    return base


def _run_one_cycle(scheduler, workflows):
    mock_repo = MagicMock()
    mock_repo.get_active_workflows.return_value = workflows
    mock_repo.get_queued_workflows.return_value = []
    with (
        patch("app.routes.autonomous._get_repo", return_value=mock_repo),
        patch.object(
            scheduler, "_advance_single", side_effect=lambda workflow_id: workflow_id
        ) as advance,
    ):
        scheduler._process_workflows()
    return [call.args[0] for call in advance.call_args_list]


def test_same_created_at_batch_siblings_selected_in_batch_order():
    """With identical created_at, the batch's earlier sibling (batch_order 0)
    must win the batch's single slot even when the DB returns rows in reverse
    order — Python's stable sort otherwise preserves the wrong order."""
    scheduler = AutonomousScheduler()
    workflows = [
        _wf("wf-later", "batch-1", 1),
        _wf("wf-earlier", "batch-1", 0),
    ]

    selected = _run_one_cycle(scheduler, workflows)

    assert selected == ["wf-earlier"]


def test_batch_order_tiebreak_honors_waiting_priority_first(monkeypatch):
    """The waiting-last priority is unchanged: a waiting sibling still sorts
    behind pending work even with a smaller batch_order. Concurrency is capped
    to 1 so the single slot deterministically goes to the pending sibling
    (ThreadPool invocation order is otherwise non-deterministic)."""
    monkeypatch.setattr("app.services.autonomous_scheduler.MAX_CONCURRENT_WORKFLOWS", 1)
    scheduler = AutonomousScheduler()
    workflows = [
        _wf("wf-waiting", "batch-1", 0, status="waiting"),
        _wf("wf-pending", "batch-1", 1),
    ]

    selected = _run_one_cycle(scheduler, workflows)

    assert selected == ["wf-pending"]


def test_null_batch_order_same_created_at_falls_back_to_workflow_id(monkeypatch):
    """Non-batch rows (batch_order NULL) with identical created_at must order
    by workflow_id — a stable total order, and no TypeError from comparing
    None with int."""
    monkeypatch.setattr("app.services.autonomous_scheduler.MAX_CONCURRENT_WORKFLOWS", 1)
    scheduler = AutonomousScheduler()
    workflows = [
        _wf("wf-b", "", None),
        _wf("wf-a", "", None),
    ]

    selected = _run_one_cycle(scheduler, workflows)

    assert selected == ["wf-a"]
