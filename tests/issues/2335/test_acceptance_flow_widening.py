"""#2335 S6: widened acceptance state-machine integration.

Multi-round reject -> fix -> confirm -> close, the reopen guard (an externally
closed issue is reopened before verifying), and idempotent re-entry on an
already-confirmed workflow. The verifier agent is mocked (no real LLM in CI);
this exercises the full phase-handler state machine.
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


def _base_wf(**overrides):
    wf = {
        "id": 7,
        "workflow_id": "w-wide",
        "github_issue_number": 777,
        "github_pr_number": 90,
        "base_commit_sha": "base",
        "verification_merge_sha": "merge",
        "issue_acceptance_hash": None,
        "verification_status": None,
        "issue_acceptance_snapshot": None,
        "dev_round": 1,
    }
    wf.update(overrides)
    return wf


def _deps(
    *, issue_open=True, dev_cap=3, changed_files=None, agent_verdicts=None, agent_snapshot=None
):
    deps = MagicMock()
    deps.gh.get_issue.return_value = {"body": "## Scope\n- `app/x.py`\n- `app/y.py`"}
    deps.gh.get_changed_files.return_value = changed_files if changed_files is not None else []
    deps.host.run_verification_agent.return_value = {
        "verdicts": agent_verdicts or [],
        "snapshot": agent_snapshot,
    }
    deps.host.issue_is_open.return_value = issue_open
    deps.host.dev_round_cap_remaining.return_value = dev_cap
    return deps


def test_multi_round_reject_fix_confirm_closes_issue():
    """Round 1: scope missing both paths -> rejected -> dev round 2.
    Round 2: one path present -> still rejected (one required path missing).
    Round 3: both paths present + verifier confirms -> confirmed -> issue closed."""
    wf = _base_wf()
    deps = _deps(dev_cap=5)

    # Round 1: nothing changed -> both required paths REJECTED.
    deps.gh.get_changed_files.return_value = []
    r1 = av.handle(_ctx(wf), deps)
    assert r1.outcome == "completed" and r1.next_phase == "development"
    assert r1.workflow_patch["dev_round"] == 2
    deps.gh.close_issue.assert_not_called()
    wf.update(r1.workflow_patch)
    wf["verification_status"] = None

    # Round 2: only x.py landed -> y.py still REJECTED.
    deps.gh.get_changed_files.return_value = ["app/x.py"]
    r2 = av.handle(_ctx(wf), deps)
    assert r2.outcome == "completed" and r2.next_phase == "development"
    assert r2.workflow_patch["dev_round"] == 3
    deps.gh.close_issue.assert_not_called()
    wf.update(r2.workflow_patch)
    wf["verification_status"] = None

    # Round 3: both paths present + verifier confirms checklist.
    deps.gh.get_changed_files.return_value = ["app/x.py", "app/y.py"]
    deps.host.run_verification_agent.return_value = {
        "verdicts": [
            {"item": "x works", "verdict": "confirmed", "evidence": [], "rationale": ""},
        ],
        "snapshot": None,
    }
    r3 = av.handle(_ctx(wf), deps)
    assert r3.outcome == "completed" and r3.next_phase == "completed"
    assert r3.workflow_patch["verification_status"] == "confirmed"
    deps.gh.close_issue.assert_called_once_with(777)
    assert r3.workflow_patch["issue_closed_by_workflow_at"]


def test_reopen_guard_reopens_externally_closed_issue_before_verifying():
    """If the issue was closed out-of-band (e.g. a stray Closes #N) before
    confirmation, the handler reopens it then proceeds to verify."""
    wf = _base_wf()
    deps = _deps(issue_open=False, changed_files=["app/x.py", "app/y.py"])
    deps.host.run_verification_agent.return_value = {"verdicts": [], "snapshot": None}

    av.handle(_ctx(wf), deps)

    deps.gh.reopen_issue.assert_called_once_with(777)
    deps.host.emit_audit_event.assert_called_once_with("acceptance_reopened_issue", {"issue": 777})


def test_idempotent_reentry_on_confirmed_is_terminal_noop():
    """Re-entering acceptance_verification with verification_status already
    'confirmed' is a terminal no-op (no re-close, no re-comment, no re-verify)."""
    wf = _base_wf(verification_status="confirmed")
    deps = _deps(changed_files=["app/x.py", "app/y.py"])

    result = av.handle(_ctx(wf), deps)

    assert result.outcome == "completed" and result.next_phase == "completed"
    deps.host.run_verification_agent.assert_not_called()
    deps.gh.close_issue.assert_not_called()
    deps.gh.add_issue_comment.assert_not_called()


def test_rejected_at_dev_round_cap_transitions_to_failed():
    """When dev_round_cap_remaining is 0, a rejection fails the workflow
    instead of looping back to development."""
    wf = _base_wf(dev_round=5)
    deps = _deps(dev_cap=0, changed_files=[])  # both required paths missing

    result = av.handle(_ctx(wf), deps)

    assert result.outcome == "failed"
    deps.gh.close_issue.assert_not_called()


def test_indeterminate_pauses_and_keeps_issue_open():
    """An indeterminate verifier verdict (verifier UNKNOWN on a required item)
    pauses the workflow; the issue is NOT closed."""
    wf = _base_wf()
    # Both scope paths present, but verifier returns UNKNOWN on a checklist item.
    deps = _deps(changed_files=["app/x.py", "app/y.py"])
    deps.host.run_verification_agent.return_value = {
        "verdicts": [
            {"item": "checklist item", "verdict": "indeterminate", "evidence": [], "rationale": ""},
        ],
        "snapshot": None,
    }

    result = av.handle(_ctx(wf), deps)

    assert result.outcome == "pause"
    assert result.workflow_patch["verification_status"] == "indeterminate"
    deps.gh.close_issue.assert_not_called()
