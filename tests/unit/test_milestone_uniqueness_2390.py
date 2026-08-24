"""Milestone-uniqueness invariant the #2390 scoping depends on.

Scoping the verdict to ``test_ms["milestone_id"]`` isolates dev rounds only if a
retry gets a *distinct* milestone id. That holds because every retry path marks
the tests_run milestone ``failed`` before re-entry, and ``_find_existing_milestone``
(the idempotency guard) only ever looks at ``in_progress``/``completed`` — a
failed milestone is invisible to it, so a re-entry creates a fresh one. These
tests pin that invariant so a future refactor cannot silently defeat #2390.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

pytestmark = [pytest.mark.regression, pytest.mark.issue(2390)]


def _orch() -> AutonomousOrchestrator:
    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._workflow_id = "wf-test"
    orch.repo = MagicMock()
    return orch


def test_find_existing_milestone_only_queries_inprogress_and_completed():
    # A failed milestone lives only in a status="failed" result set, which is
    # never queried — so it can never be reused. If a refactor added "failed"
    # here, a retry would merge into the prior round's milestone and defeat the
    # #2390 scoping; this guards against that.
    orch = _orch()
    orch.repo.list_milestones.return_value = []
    orch._find_existing_milestone(phase="tests_run", milestone_type="tests_run", dev_round=3)
    queried_statuses = {
        call.kwargs.get("status") for call in orch.repo.list_milestones.call_args_list
    }
    assert queried_statuses == {"in_progress", "completed"}
    assert "failed" not in queried_statuses


def test_failed_prior_tests_run_milestone_yields_no_match():
    # With only a failed prior milestone (absent from in_progress/completed
    # results), the guard returns None → a retry creates a fresh milestone id.
    orch = _orch()
    orch.repo.list_milestones.return_value = []  # failed one is in neither set
    match = orch._find_existing_milestone(
        phase="tests_run", milestone_type="tests_run", dev_round=3
    )
    assert match is None


def test_completed_tests_run_milestone_at_same_round_is_reused():
    # Documents the one benign reuse case: a *completed* (passed) tests_run
    # milestone at the same dev_round is idempotently reused — but a passed round
    # advances the workflow, it is not re-entered, so no distinct rounds merge.
    orch = _orch()
    completed = {"milestone_id": "ms-done", "milestone_type": "tests_run", "dev_round": 3}

    def _list(_wf, phase, status):
        return [completed] if status == "completed" else []

    orch.repo.list_milestones.side_effect = _list
    match = orch._find_existing_milestone(
        phase="tests_run", milestone_type="tests_run", dev_round=3
    )
    assert match is completed
