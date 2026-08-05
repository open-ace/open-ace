"""End-to-end-ish acceptance flow: the phase handler + scope gate + aggregation
drive the workflow from merged -> rejected -> dev -> confirmed -> issue closed.
The verifier agent is mocked (no real LLM in CI)."""

from unittest.mock import MagicMock

from app.modules.workspace.autonomous.phase_contract import WorkflowContext
from app.modules.workspace.autonomous.phases import acceptance_verification as av


def _ctx(wf):
    return WorkflowContext(
        workflow=wf,
        definition_snapshot=None,
        repository_context=None,
        session_bindings=MagicMock(),
        cancellation=MagicMock(),
    )


def test_reject_then_fix_then_confirm_closes_issue():
    wf = {
        "id": 1,
        "workflow_id": "w1",
        "github_issue_number": 42,
        "github_pr_number": 99,
        "base_commit_sha": "base",
        "verification_merge_sha": "merge",
        "issue_acceptance_hash": None,
        "verification_status": None,
        "issue_acceptance_snapshot": None,
        "dev_round": 1,
    }

    # Round 1: required path missing -> REJECTED -> new dev round, issue stays open.
    deps = MagicMock()
    deps.gh.get_issue.return_value = {"body": "## Scope\n- `app/services/retention.py`"}
    deps.gh.get_changed_files.return_value = []
    deps.host.run_verification_agent.return_value = {"verdicts": [], "snapshot": None}
    deps.host.issue_is_open.return_value = True
    deps.host.dev_round_cap_remaining.return_value = 3
    r1 = av.handle(_ctx(wf), deps)
    assert r1.outcome == "completed" and r1.next_phase == "development"
    assert r1.workflow_patch["dev_round"] == 2
    deps.gh.close_issue.assert_not_called()

    # Simulate the dev round landing the required file; re-verify.
    wf.update(r1.workflow_patch)
    wf["verification_status"] = None  # reset for the new round
    deps.gh.get_changed_files.return_value = ["app/services/retention.py"]
    deps.host.run_verification_agent.return_value = {
        "verdicts": [
            {"item": "retention runs", "verdict": "confirmed", "evidence": [], "rationale": ""}
        ],
        "snapshot": None,
    }
    r2 = av.handle(_ctx(wf), deps)
    assert r2.outcome == "completed" and r2.next_phase == "completed"
    deps.gh.close_issue.assert_called_once_with(42)
    assert r2.workflow_patch["verification_status"] == "confirmed"
    assert r2.workflow_patch["issue_closed_by_workflow_at"]
