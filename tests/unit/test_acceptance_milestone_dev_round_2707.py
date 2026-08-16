"""Regression: acceptance_verification milestone must record the workflow's
current dev_round, not the DB default (1).

Issue #2707: _create_milestone did not fall back to the workflow's dev_round
when the caller omitted it.  Acceptance-verification milestones were always
stored with dev_round=1 regardless of the actual round, breaking per-round
timeline aggregation on multi-round workflows.

Intentional scope expansion: all 14 call sites that omit dev_round (not only
acceptance_verification but also cleaned_up, branch_created, pr_created-reused,
etc.) now record and dedup against the current round rather than match-any-round.
This is more accurate—each round gets its own timeline entries—and is consistent
with the #2707 regression anchor.  The third test below anchors this behaviour.
"""

from unittest.mock import MagicMock

import pytest

from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

pytestmark = [pytest.mark.regression, pytest.mark.issue(2707)]


def _make_orch(dev_round: int) -> AutonomousOrchestrator:
    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._workflow_id = "wf-2707"
    orch._emit = lambda *_a, **_k: None

    repo = MagicMock()
    repo.get_workflow.return_value = {"workflow_id": "wf-2707", "dev_round": dev_round}
    repo.list_milestones.return_value = []
    repo.create_milestone.side_effect = lambda data: {"milestone_id": "ms-new", **data}
    orch.repo = repo

    return orch


def test_create_milestone_defaults_dev_round_from_workflow():
    """_create_milestone must use the live workflow dev_round when omitted."""
    orch = _make_orch(dev_round=3)

    ms = orch._create_milestone(
        phase="acceptance_verification",
        milestone_type="acceptance_verification",
        status="confirmed",
        title="Acceptance verification: confirmed",
    )

    assert ms["dev_round"] == 3, f"expected dev_round=3, got {ms['dev_round']!r}"


def test_create_milestone_explicit_dev_round_not_overridden():
    """An explicit dev_round kwarg must never be replaced by the workflow default."""
    orch = _make_orch(dev_round=5)

    ms = orch._create_milestone(
        phase="merge",
        milestone_type="merge_completed",
        dev_round=2,
        status="completed",
        title="Merge completed",
    )

    assert ms["dev_round"] == 2, f"explicit dev_round=2 was overwritten; got {ms['dev_round']!r}"
    # No extra DB call when dev_round is already set.
    orch.repo.get_workflow.assert_not_called()


def test_implicit_callers_dedup_per_round():
    """Round-2 milestones that omit dev_round must not dedup against round-1 rows.

    Anchors the intentional expansion: callers that omit dev_round now get
    match-current-round idempotency rather than match-any-round.  A round-2
    cleaned_up milestone must create a new row even when round-1's exists.
    """
    orch = _make_orch(dev_round=2)
    prior = {
        "milestone_id": "ms-round1",
        "milestone_type": "cleaned_up",
        "phase": "completed",
        "dev_round": 1,
        "round_number": None,
        "status": "completed",
    }
    orch.repo.list_milestones.side_effect = (
        lambda wf_id, phase=None, status=None: [prior] if status == "completed" else []
    )

    ms = orch._create_milestone(
        phase="completed",
        milestone_type="cleaned_up",
        status="completed",
        title="Cleanup round 2",
        # dev_round intentionally omitted — must be filled from workflow (round 2)
    )

    assert ms["dev_round"] == 2
    orch.repo.create_milestone.assert_called_once()
