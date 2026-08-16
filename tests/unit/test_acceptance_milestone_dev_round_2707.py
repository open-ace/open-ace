"""Regression: acceptance_verification milestone must record the workflow's
current dev_round, not the DB default (1).

Issue #2707: _acceptance_milestone omitted dev_round from the returned dict,
so acceptance milestones in multi-round workflows were always stored with the
DB default of 1 regardless of the actual round.

Fix is targeted to the acceptance path only: dev_round is now an explicit
parameter of _acceptance_milestone and filled from wf["dev_round"] at the
call site.  _create_milestone itself is unchanged so other callers (cleaned_up,
branch_created, etc.) keep their existing match-any-round idempotency.
"""

import json

import pytest

from app.modules.workspace.autonomous.phases.acceptance_verification import (
    _acceptance_milestone,
)

pytestmark = [pytest.mark.regression, pytest.mark.issue(2707)]

_REPORT = {
    "merge_sha": "abc123",
    "status": "confirmed",
    "scope": [],
    "gates": [],
    "verifier": [],
}


def test_acceptance_milestone_includes_dev_round():
    """The milestone dict must carry the caller-supplied dev_round (#2707)."""
    ms = _acceptance_milestone(
        workflow_id="wf-test",
        dev_round=3,
        attempt=1,
        status="confirmed",
        report=_REPORT,
    )

    assert ms["dev_round"] == 3


def test_acceptance_milestone_dev_round_one():
    """dev_round=1 is stored verbatim, not silently elevated."""
    ms = _acceptance_milestone(
        workflow_id="wf-test",
        dev_round=1,
        attempt=2,
        status="rejected",
        report=_REPORT,
    )

    assert ms["dev_round"] == 1


def test_acceptance_milestone_string_fields_still_valid():
    """Existing contract: result_summary and metadata must remain strings (#2394)."""
    ms = _acceptance_milestone(
        workflow_id="wf-test",
        dev_round=2,
        attempt=1,
        status="confirmed",
        report=_REPORT,
    )

    assert isinstance(ms["result_summary"], str)
    assert isinstance(ms["metadata"], str)
    assert json.loads(ms["metadata"])["merge_sha"] == "abc123"
