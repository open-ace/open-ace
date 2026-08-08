"""End-to-end-ish acceptance flow across a rejected delivery and a later fix.

Rejected delivered code pauses for review.  A separately delivered fix can then
be verified and confirmed; the old, already-cleaned worktree is never re-entered.
The verifier agent is mocked (no real LLM in CI).
"""

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


def test_reject_pauses_then_later_delivery_confirms_and_closes_issue():
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

    # Delivery 1: required path missing -> REJECTED -> pause, issue stays open.
    deps = MagicMock()
    deps.gh.get_issue.return_value = {"body": "## Scope\n- `app/services/retention.py`"}
    deps.gh.get_changed_files.return_value = []
    deps.host.run_verification_agent.return_value = {"verdicts": [], "snapshot": None}
    deps.host.issue_is_open.return_value = True
    r1 = av.handle(_ctx(wf), deps)
    assert r1.outcome == "pause"
    assert r1.workflow_patch["verification_status"] == "rejected"
    deps.host.dev_round_cap_remaining.assert_not_called()
    deps.gh.close_issue.assert_not_called()

    # Simulate a later, separately delivered fix and re-verify. The fix
    # includes a failure-path test (so the negative-test mechanical gate #2335
    # S4 sees coverage for the security/data-loss path) and wires the new
    # service module's caller (so the call-chain gate passes).
    wf.update(r1.workflow_patch)
    wf["verification_status"] = None
    wf["verification_merge_sha"] = "merge-fixed"
    deps.gh.get_changed_files.return_value = [
        "app/services/retention.py",
        "app/services/retention_runner.py",  # production caller for the service module
        "tests/test_retention.py",  # failure-path test for the retention path
    ]
    # Mechanical gates read changed-file content via git show; wire _run_git to
    # return clean content (a test with pytest.raises, a caller that imports).
    deps.gh._run_git.return_value.stdout = (
        "from app.services.retention import run\n"
        "import pytest\n"
        "def test_retention_fail():\n"
        "    with pytest.raises(ValueError):\n"
        "        run('bad')\n"
    )
    deps.host.run_verification_agent.return_value = {
        "verdicts": [
            {
                "item": "retention runs",
                "verdict": "confirmed",
                "evidence": [
                    {"ref": "app/services/retention.py:1", "note": "implementation present"}
                ],
                "rationale": "",
            }
        ],
        "snapshot": None,
    }
    r2 = av.handle(_ctx(wf), deps)
    assert r2.outcome == "completed" and r2.next_phase == "completed"
    deps.gh.close_issue.assert_called_once_with(42)
    assert r2.workflow_patch["verification_status"] == "confirmed"
    assert r2.workflow_patch["issue_closed_by_workflow_at"]
