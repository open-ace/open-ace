"""Regression: acceptance_verification milestone must record the workflow's
current dev_round, not the DB default (1).

Issue #2707: _create_milestone did not fall back to the workflow's dev_round
when the caller omitted it.  Acceptance-verification milestones were always
stored with dev_round=1 regardless of the actual round, breaking per-round
timeline aggregation on multi-round workflows.
"""

import pytest
from unittest.mock import MagicMock

from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

pytestmark = [pytest.mark.regression, pytest.mark.issue(2707)]


def _make_orch(dev_round: int) -> tuple[AutonomousOrchestrator, dict]:
    """Construct a minimal orchestrator stub for _create_milestone tests."""
    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._workflow_id = "wf-2707"
    orch._emit = lambda *_a, **_k: None

    created: dict = {}

    repo = MagicMock()
    repo.get_workflow.return_value = {"workflow_id": "wf-2707", "dev_round": dev_round}
    repo.list_milestones.return_value = []
    repo.create_milestone.side_effect = lambda data: {"milestone_id": "ms-new", **data}
    orch.repo = repo

    return orch, created


def test_create_milestone_defaults_dev_round_from_workflow():
    """_create_milestone must use the live workflow dev_round when omitted."""
    orch, _ = _make_orch(dev_round=3)

    ms = orch._create_milestone(
        phase="acceptance_verification",
        milestone_type="acceptance_verification",
        status="confirmed",
        title="Acceptance verification: confirmed",
    )

    assert ms["dev_round"] == 3, (
        f"expected dev_round=3 (live workflow round), got {ms['dev_round']!r}"
    )


def test_create_milestone_explicit_dev_round_not_overridden():
    """An explicit dev_round kwarg must never be replaced by the workflow default."""
    orch, _ = _make_orch(dev_round=5)

    ms = orch._create_milestone(
        phase="merge",
        milestone_type="merge_completed",
        dev_round=2,
        status="completed",
        title="Merge completed",
    )

    assert ms["dev_round"] == 2, (
        f"explicit dev_round=2 was overwritten; got {ms['dev_round']!r}"
    )
    # Confirm the workflow was NOT queried — no extra DB call when already set.
    orch.repo.get_workflow.assert_not_called()
