"""Regression (#2867): a deterministic verifier parse failure must not burn the
full transient-retry budget.

When the verifier emits output that cannot be parsed, re-running it usually
reproduces the same unparseable form. The old code treated every infra failure
as transient and retried up to ``MAX_VERIFIER_INFRA_RETRIES`` (3), so a
certain-to-fail parse failure wasted the whole budget before pausing. The phase
now tags parse failures (``infra_error_kind == "unparseable_output"``) and caps
CONSECUTIVE repeats at ``DETERMINISTIC_PARSE_MAX_RETRIES`` (2), while genuinely
transient infra errors keep the full budget.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.phase_contract import WorkflowContext
from app.modules.workspace.autonomous.phases import acceptance_verification as av

pytestmark = [pytest.mark.regression, pytest.mark.issue(2867)]

UNPARSEABLE = {
    "verdicts": [],
    "snapshot": None,
    "infra_error": "verification agent output was not valid JSON",
    "infra_error_kind": "unparseable_output",
}
TRANSIENT = {
    "verdicts": [],
    "snapshot": None,
    "infra_error": "verification agent returned empty output",
}


def _ctx(wf):
    return WorkflowContext(
        workflow=wf,
        definition_snapshot=None,
        repository_context=None,
        session_bindings=MagicMock(),
        cancellation=MagicMock(),
    )


def _base_wf():
    # Empty snapshot -> source "missing" -> requires extraction -> the idempotent
    # "already verified" reuse is skipped, so each call re-runs the verifier.
    snap = {
        "required_paths": [],
        "checklist": [],
        "non_scope": [],
        "closure_constraints": False,
        "source": "missing",
        "confidence": "low",
    }
    return {
        "id": 1,
        "workflow_id": "wf-2867",
        "github_issue_number": 42,
        "github_pr_number": 99,
        "base_commit_sha": "base",
        "verification_merge_sha": "merge",
        "issue_acceptance_snapshot": json.dumps(snap),
        "verification_status": None,
        "dev_round": 1,
    }


def _deps(agent_out):
    d = MagicMock()
    d.gh = MagicMock()
    d.host.run_verification_agent.return_value = agent_out
    d.host.issue_is_open.return_value = True
    return d


def _run(wf, agent_out):
    # Isolate the infra-retry decision from the mechanical gates.
    with (
        patch.object(av, "is_acceptance_verification_enabled", return_value=True),
        patch.object(av, "run_scope_gate", return_value=[]),
        patch.object(av, "run_mechanical_gates", return_value=[]),
    ):
        return av.handle(_ctx(wf), _deps(agent_out))


def test_first_unparseable_failure_retries_and_tags_kind():
    result = _run(_base_wf(), UNPARSEABLE)
    assert result.outcome == "retry"
    report = json.loads(result.workflow_patch["verification_report"])
    assert report["infra_error_kind"] == "unparseable_output"
    assert report["infra_retry_count"] == 1


def test_second_consecutive_unparseable_failure_pauses_early():
    r1 = _run(_base_wf(), UNPARSEABLE)
    assert r1.outcome == "retry"

    wf2 = _base_wf()
    wf2["verification_report"] = r1.workflow_patch["verification_report"]
    r2 = _run(wf2, UNPARSEABLE)

    # Capped at 2 (deterministic), not the transient budget of 3.
    assert r2.outcome == "pause"
    assert "unparseable" in r2.workflow_patch["error_message"].lower()
    report2 = json.loads(r2.workflow_patch["verification_report"])
    assert report2["infra_retry_count"] == 2
    assert report2["infra_error_kind"] == "unparseable_output"


def test_transient_infra_error_keeps_full_budget_at_same_count():
    # Contrast: a non-parse infra error at the SAME count (2) still retries,
    # proving the early cap is specific to deterministic parse failures.
    r1 = _run(_base_wf(), TRANSIENT)
    assert r1.outcome == "retry"
    report1 = json.loads(r1.workflow_patch["verification_report"])
    assert report1["infra_error_kind"] is None

    wf2 = _base_wf()
    wf2["verification_report"] = r1.workflow_patch["verification_report"]
    r2 = _run(wf2, TRANSIENT)
    assert r2.outcome == "retry"  # budget is 3; count 2 still retries
    report2 = json.loads(r2.workflow_patch["verification_report"])
    assert report2["infra_retry_count"] == 2


def test_prior_infra_error_kind_helper_matches_pair():
    snap_hash = "h1"
    prior = json.dumps(
        {
            "merge_sha": "merge",
            "issue_acceptance_hash": snap_hash,
            "infra_error": "verification agent output was not valid JSON",
            "infra_error_kind": "unparseable_output",
            "infra_retry_count": 1,
        }
    )
    wf = {"verification_report": prior}
    assert av._prior_infra_error_kind(wf, "merge", snap_hash) == "unparseable_output"
    # Different merge/hash pair -> not consecutive, no kind carried over.
    assert av._prior_infra_error_kind(wf, "other", snap_hash) is None
    assert av._prior_infra_error_kind(wf, "merge", "other") is None
    # Older report predating the field -> None.
    wf_old = {"verification_report": json.dumps({"merge_sha": "merge", "infra_error": "x"})}
    assert av._prior_infra_error_kind(wf_old, "merge", snap_hash) is None
