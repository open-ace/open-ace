"""Regression: the acceptance-verification milestone must store string fields.

The milestone built at the end of ``acceptance_verification.handle`` passed the
raw ``report`` dict as both ``result_summary`` and ``metadata``.
``repo.create_milestone`` inserts those columns verbatim (no json.dumps), so
psycopg2 raised ``can't adapt type 'dict'`` and failed the whole phase the
moment a verification verdict was committed (caught on b48179df / #2394).
``verification_report`` (the workflow column) was already json-dumped; the
milestone fields were the gap.
"""

import json

import pytest

pytestmark = [pytest.mark.regression]


def test_acceptance_milestone_fields_are_strings():
    from app.modules.workspace.autonomous.phases.acceptance_verification import (
        _acceptance_milestone,
    )

    report = {
        "merge_sha": "abc",
        "status": "confirmed",
        "scope": [{"item": "a", "verdict": "confirmed"}],
        "gates": [],
        "verifier": [{"item": "x"}, {"item": "y"}],
    }
    ms = _acceptance_milestone(workflow_id="wf-2394", attempt=1, status="confirmed", report=report)

    # Both DB columns must be strings — a dict here crashes create_milestone.
    assert isinstance(ms["result_summary"], str)
    assert isinstance(ms["metadata"], str)
    # metadata round-trips as JSON carrying the full structured report.
    parsed = json.loads(ms["metadata"])
    assert parsed["merge_sha"] == "abc"
    assert parsed["verifier"] == [{"item": "x"}, {"item": "y"}]
    # result_summary is a short human-readable line, not the raw dict.
    assert "confirmed" in ms["result_summary"]
