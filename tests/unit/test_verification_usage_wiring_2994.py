"""#2994: wire the verifier run's session id + usage into the acceptance milestone.

Regression coverage for the accounting/display gap: acceptance milestones were
created with zero ``phase_*`` and no session field, so workflow totals (summed
from milestone ``phase_*``) excluded all verification consumption and the UI
card rendered the "system step" fallback instead of the session button and
token/request chips.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from app.modules.workspace.autonomous.orchestrator import (
    AutonomousOrchestrator,
    WorkflowPaused,
)
from app.modules.workspace.autonomous.phase_contract import PhaseResult
from app.modules.workspace.autonomous.phases import acceptance_verification as av

pytestmark = [pytest.mark.issue(2994)]

USAGE = {
    "total_tokens": 730049,
    "total_input_tokens": 721308,
    "total_output_tokens": 8741,
    "request_count": 33,
}
SESSION_ID = "verif-tracking-session-1"


def _ctx(workflow):
    from app.modules.workspace.autonomous.phase_contract import WorkflowContext

    return WorkflowContext(
        workflow=workflow,
        definition_snapshot=None,
        repository_context=None,
        session_bindings=MagicMock(),
        cancellation=MagicMock(),
    )


def _workflow(**extra):
    base = {
        "workflow_id": "wf-2994",
        "github_issue_number": 42,
        "github_pr_number": 99,
        "base_commit_sha": "base",
        "verification_merge_sha": "merge",
        "verification_status": None,
        "issue_acceptance_snapshot": None,
        "dev_round": 1,
    }
    base.update(extra)
    return base


def _agent_out_confirmed(**extra):
    out = {
        "verdicts": [
            {
                "item": "The endpoint rejects expired tokens",
                "verdict": "confirmed",
                "evidence": [{"ref": "tests/test_auth.py:42", "note": "covered"}],
            }
        ],
        "snapshot": None,
        "session_id": SESSION_ID,
        "usage": dict(USAGE),
    }
    out.update(extra)
    return out


def _deps_confirmed():
    deps = MagicMock()
    deps.gh.get_issue.return_value = {
        "body": "## Acceptance Criteria\n- [ ] The endpoint rejects expired tokens"
    }
    deps.gh.get_changed_files.return_value = []
    deps.gh._run_git.return_value.stdout = ""
    deps.host.issue_is_open.return_value = True
    deps.host.run_verification_agent.return_value = _agent_out_confirmed()
    return deps


# ── _acceptance_milestone unit coverage ──────────────────────────────────────


def test_acceptance_milestone_carries_session_and_usage():
    milestone = av._acceptance_milestone(
        workflow_id="wf-2994",
        dev_round=1,
        attempt=2,
        status="confirmed",
        report={"merge_sha": "merge"},
        session_id=SESSION_ID,
        usage=USAGE,
    )
    assert milestone["session_id"] == SESSION_ID
    assert milestone["phase_total_tokens"] == USAGE["total_tokens"]
    assert milestone["phase_input_tokens"] == USAGE["total_input_tokens"]
    assert milestone["phase_output_tokens"] == USAGE["total_output_tokens"]
    assert milestone["phase_request_count"] == USAGE["request_count"]


def test_acceptance_milestone_defaults_zero_without_runtime():
    milestone = av._acceptance_milestone(
        workflow_id="wf-2994",
        dev_round=1,
        attempt=1,
        status="rejected",
        report={},
    )
    assert milestone["session_id"] == ""
    assert milestone["phase_total_tokens"] == 0
    assert milestone["phase_input_tokens"] == 0
    assert milestone["phase_output_tokens"] == 0
    assert milestone["phase_request_count"] == 0


# ── handle(): settled paths record session + usage + usage_delta ─────────────


def test_confirmed_result_records_session_usage_and_delta():
    deps = _deps_confirmed()

    result = av.handle(_ctx(_workflow()), deps)

    assert result.outcome == "completed"
    assert result.usage_delta == USAGE
    (milestone,) = result.milestone_events
    assert milestone["session_id"] == SESSION_ID
    assert milestone["phase_total_tokens"] == USAGE["total_tokens"]
    assert milestone["phase_request_count"] == USAGE["request_count"]


def test_rejected_pause_carries_session_usage_and_delta():
    deps = _deps_confirmed()
    deps.host.run_verification_agent.return_value = _agent_out_confirmed(
        verdicts=[
            {
                "item": "The endpoint rejects expired tokens",
                "verdict": "rejected",
                "evidence": [{"ref": "src/auth.py:10", "note": "not covered"}],
            }
        ]
    )

    result = av.handle(_ctx(_workflow()), deps)

    assert result.outcome == "pause"
    assert result.usage_delta == USAGE
    (milestone,) = result.milestone_events
    assert milestone["session_id"] == SESSION_ID
    assert milestone["phase_total_tokens"] == USAGE["total_tokens"]


def test_indeterminate_pause_carries_session_usage_and_delta():
    deps = _deps_confirmed()
    # Checklist item uncovered → indeterminate via _cover_missing_checklist_items.
    deps.host.run_verification_agent.return_value = _agent_out_confirmed(verdicts=[])

    result = av.handle(_ctx(_workflow()), deps)

    assert result.outcome == "pause"
    assert result.usage_delta == USAGE
    (milestone,) = result.milestone_events
    assert milestone["session_id"] == SESSION_ID
    assert milestone["phase_total_tokens"] == USAGE["total_tokens"]


def test_infra_exhausted_pause_carries_partial_usage_and_delta():
    deps = MagicMock()
    deps.gh.get_issue.return_value = {"body": "## Scope\n- `app/x.py`"}
    deps.gh.get_changed_files.return_value = ["app/x.py"]
    deps.gh._run_git.return_value.stdout = ""
    deps.host.issue_is_open.return_value = True
    # The last (exhausting) attempt burned tokens before failing.
    exhausted = {
        "verdicts": [],
        "snapshot": None,
        "infra_error": "verification agent timed out",
        "session_id": SESSION_ID,
        "usage": USAGE,
    }
    deps.host.run_verification_agent.side_effect = lambda **_kwargs: exhausted

    workflow = _workflow()
    outcomes = []
    for _ in range(av.MAX_VERIFIER_INFRA_RETRIES):
        result = av.handle(_ctx(workflow), deps)
        outcomes.append(result)
        workflow = {**workflow, **result.workflow_patch}

    assert outcomes[-1].outcome == "pause"
    assert outcomes[-1].usage_delta == USAGE
    (milestone,) = outcomes[-1].milestone_events
    assert milestone["session_id"] == SESSION_ID
    assert milestone["phase_total_tokens"] == USAGE["total_tokens"]


# ── handle(): paths that must NOT record anything new ────────────────────────


def test_infra_retry_creates_no_milestone_and_no_delta():
    deps = MagicMock()
    deps.gh.get_issue.return_value = {"body": "## Scope\n- `app/x.py`"}
    deps.gh.get_changed_files.return_value = ["app/x.py"]
    deps.gh._run_git.return_value.stdout = ""
    deps.host.issue_is_open.return_value = True
    deps.host.run_verification_agent.return_value = {
        "verdicts": [],
        "snapshot": None,
        "infra_error": "verification agent timed out",
        "session_id": SESSION_ID,
        "usage": USAGE,
    }

    result = av.handle(_ctx(_workflow()), deps)

    assert result.outcome == "retry"
    assert result.milestone_events == []
    assert result.usage_delta is None


def test_already_confirmed_noop_creates_no_milestone_and_no_delta():
    deps = _deps_confirmed()

    result = av.handle(_ctx(_workflow(verification_status="confirmed")), deps)

    assert result.outcome == "completed"
    assert result.milestone_events == []
    assert result.usage_delta is None
    deps.host.run_verification_agent.assert_not_called()


def test_legacy_agent_dict_without_runtime_keys_yields_zero_milestone():
    """Older hosts/mocks return no runtime keys — milestone must default to zeros."""
    deps = _deps_confirmed()
    deps.host.run_verification_agent.return_value = _agent_out_confirmed(
        session_id=None, usage=None
    )
    deps.host.run_verification_agent.return_value.pop("session_id")
    deps.host.run_verification_agent.return_value.pop("usage")

    result = av.handle(_ctx(_workflow()), deps)

    assert result.outcome == "completed"
    (milestone,) = result.milestone_events
    assert milestone["session_id"] == ""
    assert milestone["phase_total_tokens"] == 0
    assert milestone["phase_request_count"] == 0
    # Nothing consumed → no refresh signal.
    assert result.usage_delta is None


# ── PhaseResult.pause() usage_delta passthrough ──────────────────────────────


def test_phase_result_pause_accepts_and_passes_usage_delta():
    result = PhaseResult.pause(
        workflow_patch={"k": "v"},
        milestone_events=[{"a": 1}],
        structured_error={"message": "x"},
        usage_delta=USAGE,
    )
    assert result.outcome == "pause"
    assert result.usage_delta == USAGE
    assert PhaseResult.pause().usage_delta is None


# ── orchestrator: _run_verification_agent runtime attachment ────────────────


def _orch_for_agent_run(run_agent_result):
    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._workflow_id = "wf-2994"
    orch._build_verification_prompt = MagicMock(return_value="verify prompt")
    orch._checkout_merged_main = MagicMock(return_value="/tmp/merged-checkout")
    orch._remove_verification_worktree = MagicMock()
    orch._parse_verifier_output = MagicMock(return_value={"verdicts": [], "snapshot": None})
    orch._run_agent = MagicMock(return_value=run_agent_result)
    return orch


def _run_agent_result(**fields):
    result = MagicMock()
    result.success = fields.get("success", True)
    result.tracking_session_id = fields.get("tracking_session_id", SESSION_ID)
    result.session_id = fields.get("session_id", "")
    result.total_tokens = fields.get("total_tokens", USAGE["total_tokens"])
    result.total_input_tokens = fields.get("total_input_tokens", USAGE["total_input_tokens"])
    result.total_output_tokens = fields.get("total_output_tokens", USAGE["total_output_tokens"])
    result.request_count = fields.get("request_count", USAGE["request_count"])
    return result


def _wf_property(orch):
    return patch.object(
        type(orch),
        "workflow",
        new_callable=PropertyMock,
        return_value={"cli_tool": "claude-code", "model": ""},
    )


def test_run_verification_agent_success_attaches_runtime():
    orch = _orch_for_agent_run(_run_agent_result())

    with _wf_property(orch):
        out = orch._run_verification_agent(
            snapshot=MagicMock(), merge_sha="m", base_sha="b", issue_number=1, pr_number=2
        )

    assert out["session_id"] == SESSION_ID
    assert out["usage"] == USAGE
    assert orch._verification_agent_active is False


def test_run_verification_agent_failure_still_attaches_runtime():
    orch = _orch_for_agent_run(_run_agent_result(success=False))

    with _wf_property(orch):
        out = orch._run_verification_agent(
            snapshot=MagicMock(), merge_sha="m", base_sha="b", issue_number=1, pr_number=2
        )

    assert out["infra_error"]
    assert out["session_id"] == SESSION_ID
    assert out["usage"]["total_tokens"] == USAGE["total_tokens"]


def test_run_verification_agent_none_result_has_no_runtime_keys():
    orch = _orch_for_agent_run(None)

    with _wf_property(orch):
        out = orch._run_verification_agent(
            snapshot=MagicMock(), merge_sha="m", base_sha="b", issue_number=1, pr_number=2
        )

    assert "session_id" not in out
    assert "usage" not in out


def test_run_verification_agent_checkout_failure_has_no_runtime_keys():
    orch = _orch_for_agent_run(_run_agent_result())
    orch._checkout_merged_main = MagicMock(return_value=None)

    with _wf_property(orch):
        out = orch._run_verification_agent(
            snapshot=MagicMock(), merge_sha="m", base_sha="b", issue_number=1, pr_number=2
        )

    assert out["infra_error"] == "merged-main checkout failed"
    assert "session_id" not in out
    assert "usage" not in out
    orch._run_agent.assert_not_called()


def test_run_verification_agent_tracking_id_wins_over_blank_session_id():
    orch = _orch_for_agent_run(
        _run_agent_result(tracking_session_id=SESSION_ID, session_id="")
    )

    with _wf_property(orch):
        out = orch._run_verification_agent(
            snapshot=MagicMock(), merge_sha="m", base_sha="b", issue_number=1, pr_number=2
        )

    assert out["session_id"] == SESSION_ID


def test_verification_flag_cleared_even_when_agent_raises_pause():
    orch = _orch_for_agent_run(_run_agent_result())
    orch._run_agent = MagicMock(side_effect=WorkflowPaused("quota window"))

    with _wf_property(orch), pytest.raises(WorkflowPaused):
        orch._run_verification_agent(
            snapshot=MagicMock(), merge_sha="m", base_sha="b", issue_number=1, pr_number=2
        )

    assert orch._verification_agent_active is False


# ── orchestrator: realtime writers suppressed during the verifier run ───────


def _orch_for_writers():
    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._workflow_id = "wf-2994"
    orch.repo = MagicMock()
    return orch


def test_realtime_writers_noop_while_verification_agent_active():
    orch = _orch_for_writers()
    orch._verification_agent_active = True

    orch._write_realtime_phase_usage({"total_tokens": 5})
    orch._link_session_to_current_milestone("sess-1")

    # Zero DB access: the gate must fire before list_milestones.
    orch.repo.list_milestones.assert_not_called()
    orch.repo.update_milestone.assert_not_called()


def test_realtime_writers_resume_after_flag_clears():
    orch = _orch_for_writers()
    orch._verification_agent_active = False
    orch.repo.list_milestones.return_value = [
        {"milestone_id": "ms-1", "milestone_type": "pr_zero_check_runs"}
    ]

    orch._write_realtime_phase_usage({"total_tokens": 5, "request_count": 1})
    orch._link_session_to_current_milestone("sess-1")

    assert orch.repo.list_milestones.call_count == 2
    assert orch.repo.update_milestone.call_count == 2


# ── commit path: usage_delta triggers the totals refresh ─────────────────────


def test_commit_phase_result_refreshes_totals_after_milestone_insert():
    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._update_workflow = MagicMock()
    orch._create_milestone = MagicMock()
    orch._accumulate_tokens = MagicMock()
    result = av.handle(_ctx(_workflow()), _deps_confirmed())

    orch._commit_phase_result(result)

    orch._create_milestone.assert_called_once_with(**result.milestone_events[0])
    orch._accumulate_tokens.assert_called_once()
    # usage_delta carries the verifier usage dict verbatim.
    assert orch._accumulate_tokens.call_args[0][0] == USAGE


def test_commit_phase_result_without_delta_skips_refresh():
    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._update_workflow = MagicMock()
    orch._create_milestone = MagicMock()
    orch._accumulate_tokens = MagicMock()
    result = PhaseResult.pause(workflow_patch={"x": 1})

    orch._commit_phase_result(result)

    orch._accumulate_tokens.assert_not_called()


# ── report contents stay intact (no runtime keys leak into metadata) ────────


def test_report_metadata_untouched_by_runtime_fields():
    deps = _deps_confirmed()

    result = av.handle(_ctx(_workflow()), deps)

    (milestone,) = result.milestone_events
    report = json.loads(milestone["metadata"])
    assert "session_id" not in report
    assert "usage" not in report
