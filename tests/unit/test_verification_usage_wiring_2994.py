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

from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator, WorkflowPaused
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
    # #3000: the settle milestone id is pre-minted and the session's untagged
    # messages are swept onto it.
    assert milestone["milestone_id"]
    deps.host.tag_session_messages.assert_called_once_with(SESSION_ID, milestone["milestone_id"])


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
    deps.host.tag_session_messages.assert_called_once_with(SESSION_ID, milestone["milestone_id"])


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
    deps.host.tag_session_messages.assert_called_once_with(SESSION_ID, milestone["milestone_id"])


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
    deps.host.tag_session_messages.assert_called_once_with(SESSION_ID, milestone["milestone_id"])


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
    # No settle milestone exists yet — nothing to tag messages onto (#3000).
    deps.host.tag_session_messages.assert_not_called()


def test_settle_without_session_skips_tagging():
    """Legacy host dicts carry no session id — tagging must be skipped."""
    deps = _deps_confirmed()
    agent_out = _agent_out_confirmed()
    agent_out.pop("session_id")
    deps.host.run_verification_agent.return_value = agent_out

    result = av.handle(_ctx(_workflow()), deps)

    assert result.outcome == "completed"
    deps.host.tag_session_messages.assert_not_called()


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


# ── handle(): early-milestone finalize path (#3003) ──────────────────────────

BASELINE = {
    "total_tokens": 500,
    "total_input_tokens": 400,
    "total_output_tokens": 100,
    "request_count": 4,
}


def _agent_out_early(**extra):
    out = _agent_out_confirmed(milestone_id=EARLY_MILESTONE_ID, usage_baseline=dict(BASELINE))
    out.update(extra)
    return out


def test_confirmed_settle_finalizes_early_milestone():
    deps = _deps_confirmed()
    deps.host.run_verification_agent.return_value = _agent_out_early()

    result = av.handle(_ctx(_workflow()), deps)

    assert result.outcome == "completed"
    # No create-events: the early row is finalized in place.
    assert result.milestone_events == []
    fields = deps.host.finalize_acceptance_milestone.call_args[0][1]
    assert fields["status"] == "confirmed"
    assert fields["session_id"] == SESSION_ID
    # Resume baseline + this attempt's increment.
    assert fields["phase_total_tokens"] == BASELINE["total_tokens"] + USAGE["total_tokens"]
    assert fields["phase_request_count"] == BASELINE["request_count"] + USAGE["request_count"]
    assert result.usage_delta["total_tokens"] == BASELINE["total_tokens"] + USAGE["total_tokens"]
    deps.host.tag_session_messages.assert_called_once_with(SESSION_ID, EARLY_MILESTONE_ID)


def test_rejected_settle_finalizes_early_milestone():
    deps = _deps_confirmed()
    deps.host.run_verification_agent.return_value = _agent_out_early(
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
    assert result.milestone_events == []
    assert deps.host.finalize_acceptance_milestone.call_args[0][1]["status"] == "rejected"
    deps.host.tag_session_messages.assert_called_once_with(SESSION_ID, EARLY_MILESTONE_ID)


def test_infra_retry_with_early_milestone_finalizes_failed_and_tags():
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
        "milestone_id": EARLY_MILESTONE_ID,
        "usage_baseline": dict(BASELINE),
    }

    result = av.handle(_ctx(_workflow()), deps)

    assert result.outcome == "retry"
    assert result.milestone_events == []
    fields = deps.host.finalize_acceptance_milestone.call_args[0][1]
    assert fields["status"] == "failed"
    # The row's realtime-recorded usage is preserved (no phase_* overwrite).
    assert "phase_total_tokens" not in fields
    deps.host.tag_session_messages.assert_called_once_with(SESSION_ID, EARLY_MILESTONE_ID)


def test_infra_exhausted_with_early_milestone_finalizes_with_report():
    deps = MagicMock()
    deps.gh.get_issue.return_value = {"body": "## Scope\n- `app/x.py`"}
    deps.gh.get_changed_files.return_value = ["app/x.py"]
    deps.gh._run_git.return_value.stdout = ""
    deps.host.issue_is_open.return_value = True
    exhausted = {
        "verdicts": [],
        "snapshot": None,
        "infra_error": "verification agent timed out",
        "session_id": SESSION_ID,
        "usage": USAGE,
        "milestone_id": EARLY_MILESTONE_ID,
        "usage_baseline": dict(BASELINE),
    }
    deps.host.run_verification_agent.side_effect = lambda **_kwargs: exhausted

    workflow = _workflow()
    outcomes = []
    for _ in range(av.MAX_VERIFIER_INFRA_RETRIES):
        result = av.handle(_ctx(workflow), deps)
        outcomes.append(result)
        workflow = {**workflow, **result.workflow_patch}

    assert outcomes[-1].outcome == "pause"
    assert outcomes[-1].milestone_events == []
    fields = deps.host.finalize_acceptance_milestone.call_args[0][1]
    assert fields["status"] == "indeterminate"
    assert fields["phase_total_tokens"] == BASELINE["total_tokens"] + USAGE["total_tokens"]


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


EARLY_MILESTONE_ID = "ms-early-3003"
ZERO_BASELINE = {
    "total_tokens": 0,
    "total_input_tokens": 0,
    "total_output_tokens": 0,
    "request_count": 0,
}


def _early_row(**overrides):
    row = {
        "milestone_id": EARLY_MILESTONE_ID,
        "phase_total_tokens": 0,
        "phase_input_tokens": 0,
        "phase_output_tokens": 0,
        "phase_request_count": 0,
    }
    row.update(overrides)
    return row


def _orch_for_agent_run(run_agent_result, early_row=None):
    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._workflow_id = "wf-2994"
    orch._build_verification_prompt = MagicMock(return_value="verify prompt")
    orch._checkout_merged_main = MagicMock(return_value="/tmp/merged-checkout")
    orch._remove_verification_worktree = MagicMock()
    orch._parse_verifier_output = MagicMock(return_value={"verdicts": [], "snapshot": None})
    orch._run_agent = MagicMock(return_value=run_agent_result)

    def _ensure(_wf):
        row = _early_row(**(early_row or {}))
        # Mirror the real method's side effect: the row becomes the writers'
        # preferred target for the duration of the run.
        orch._verification_milestone_id = row["milestone_id"]
        return row

    orch._ensure_verification_milestone = MagicMock(side_effect=_ensure)
    orch._verification_milestone_id = None
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


def test_run_verification_agent_success_attaches_runtime_and_early_milestone():
    stash_during_run = {}
    result = _run_agent_result()

    def _capture_stash(*_args, **_kwargs):
        stash_during_run["value"] = getattr(orch, "_verification_milestone_id", None)
        return result

    orch = _orch_for_agent_run(result)
    orch._run_agent = MagicMock(side_effect=_capture_stash)

    with _wf_property(orch):
        out = orch._run_verification_agent(
            snapshot=MagicMock(), merge_sha="m", base_sha="b", issue_number=1, pr_number=2
        )

    assert out["session_id"] == SESSION_ID
    assert out["usage"] == USAGE
    assert out["milestone_id"] == EARLY_MILESTONE_ID
    assert out["usage_baseline"] == ZERO_BASELINE
    # The early row exists (and is the writers' preferred target) DURING the run.
    assert stash_during_run["value"] == EARLY_MILESTONE_ID
    # #3003: the call links the early row and carries the resume baseline.
    call_kwargs = orch._run_agent.call_args[1]
    assert call_kwargs["milestone_id"] == EARLY_MILESTONE_ID
    assert call_kwargs["prior_usage"] == ZERO_BASELINE


def test_run_verification_agent_passes_reuse_baseline_as_prior_usage():
    baseline = {
        "total_tokens": 500,
        "total_input_tokens": 400,
        "total_output_tokens": 100,
        "request_count": 4,
    }
    orch = _orch_for_agent_run(
        _run_agent_result(),
        early_row=dict(
            phase_total_tokens=500,
            phase_input_tokens=400,
            phase_output_tokens=100,
            phase_request_count=4,
        ),
    )

    with _wf_property(orch):
        out = orch._run_verification_agent(
            snapshot=MagicMock(), merge_sha="m", base_sha="b", issue_number=1, pr_number=2
        )

    assert out["usage_baseline"] == baseline
    assert orch._run_agent.call_args[1]["prior_usage"] == baseline


def test_run_verification_agent_failure_still_attaches_runtime():
    orch = _orch_for_agent_run(_run_agent_result(success=False))

    with _wf_property(orch):
        out = orch._run_verification_agent(
            snapshot=MagicMock(), merge_sha="m", base_sha="b", issue_number=1, pr_number=2
        )

    assert out["infra_error"]
    assert out["session_id"] == SESSION_ID
    assert out["usage"]["total_tokens"] == USAGE["total_tokens"]
    # Post-checkout failures keep the early milestone facts (#3003).
    assert out["milestone_id"] == EARLY_MILESTONE_ID


def test_run_verification_agent_spawn_exception_carries_early_milestone():
    orch = _orch_for_agent_run(_run_agent_result())
    orch._run_agent = MagicMock(side_effect=RuntimeError("spawn blew up"))

    with _wf_property(orch):
        out = orch._run_verification_agent(
            snapshot=MagicMock(), merge_sha="m", base_sha="b", issue_number=1, pr_number=2
        )

    assert out["infra_error"] == "verification agent spawn failed"
    assert out["milestone_id"] == EARLY_MILESTONE_ID
    assert out["usage_baseline"] == ZERO_BASELINE


def test_run_verification_agent_none_result_has_no_runtime_keys():
    orch = _orch_for_agent_run(None)

    with _wf_property(orch):
        out = orch._run_verification_agent(
            snapshot=MagicMock(), merge_sha="m", base_sha="b", issue_number=1, pr_number=2
        )

    assert "session_id" not in out
    assert "usage" not in out
    # The early row still exists — the handler must finalize it.
    assert out["milestone_id"] == EARLY_MILESTONE_ID


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
    # No row was minted — the handler's create fallback applies.
    assert "milestone_id" not in out
    orch._run_agent.assert_not_called()
    orch._ensure_verification_milestone.assert_not_called()


def test_run_verification_agent_tracking_id_wins_over_blank_session_id():
    orch = _orch_for_agent_run(_run_agent_result(tracking_session_id=SESSION_ID, session_id=""))

    with _wf_property(orch):
        out = orch._run_verification_agent(
            snapshot=MagicMock(), merge_sha="m", base_sha="b", issue_number=1, pr_number=2
        )

    assert out["session_id"] == SESSION_ID


def test_workflow_pause_leaves_early_row_open_for_resume():
    orch = _orch_for_agent_run(_run_agent_result())
    orch._run_agent = MagicMock(side_effect=WorkflowPaused("quota window"))

    with _wf_property(orch), pytest.raises(WorkflowPaused):
        orch._run_verification_agent(
            snapshot=MagicMock(), merge_sha="m", base_sha="b", issue_number=1, pr_number=2
        )

    # The row stays the writers' target across the pause — resume re-enters
    # with the same attempt number and reuses it (#3003).
    assert orch._verification_milestone_id == EARLY_MILESTONE_ID


# ── orchestrator: realtime writers target the verifier's row (#3003) ───────


def _orch_for_writers():
    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._workflow_id = "wf-2994"
    orch.repo = MagicMock()
    orch._verification_milestone_id = None
    return orch


def test_realtime_writers_prefer_stashed_verification_milestone():
    """Even when a leaked row sorts last, the stashed verifier row wins."""
    orch = _orch_for_writers()
    orch._verification_milestone_id = "ms-verif"
    orch.repo.list_milestones.return_value = [
        {"milestone_id": "ms-leak", "milestone_type": "pr_zero_check_runs"},
        {"milestone_id": "ms-verif", "milestone_type": "acceptance_verification"},
    ]

    orch._write_realtime_phase_usage({"total_tokens": 5, "request_count": 1})
    orch._link_session_to_current_milestone("sess-1")

    usage_target = orch.repo.update_milestone.call_args_list[0][0][0]
    link_fields = orch.repo.update_milestone.call_args_list[1][0][1]
    assert usage_target == "ms-verif"
    assert orch.repo.update_milestone.call_args_list[1][0][0] == "ms-verif"
    assert "sess-1" in link_fields.values()


def test_realtime_writers_stale_stash_is_inert():
    """A stash whose row went terminal (not in_progress) must not redirect."""
    orch = _orch_for_writers()
    orch._verification_milestone_id = "ms-gone"
    orch.repo.list_milestones.return_value = [
        {"milestone_id": "ms-1", "milestone_type": "dev_started"}
    ]

    orch._write_realtime_phase_usage({"total_tokens": 5, "request_count": 1})
    orch._link_session_to_current_milestone("sess-1")

    assert orch.repo.update_milestone.call_args_list[0][0][0] == "ms-1"
    assert orch.repo.update_milestone.call_args_list[1][0][0] == "ms-1"


def test_realtime_writers_default_to_last_in_progress_row():
    orch = _orch_for_writers()
    orch.repo.list_milestones.return_value = [
        {"milestone_id": "ms-1", "milestone_type": "pr_zero_check_runs"}
    ]

    orch._write_realtime_phase_usage({"total_tokens": 5, "request_count": 1})
    orch._link_session_to_current_milestone("sess-1")

    assert orch.repo.list_milestones.call_count == 2
    assert orch.repo.update_milestone.call_count == 2


# ── orchestrator: _ensure_verification_milestone lifecycle (#3003) ──────────


def _orch_for_ensure(in_progress_rows):
    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._workflow_id = "wf-2994"
    orch.repo = MagicMock()
    orch.repo.list_milestones.return_value = in_progress_rows
    orch._create_milestone = MagicMock(side_effect=lambda **kw: {"milestone_id": "ms-new", **kw})
    orch._verification_milestone_id = None
    return orch


def test_ensure_creates_row_for_current_attempt():
    orch = _orch_for_ensure([])

    row = orch._ensure_verification_milestone({"verification_attempt": 2, "dev_round": 1})

    assert row["milestone_id"] == "ms-new"
    assert orch._create_milestone.call_args[1]["round_number"] == 3
    assert orch._create_milestone.call_args[1]["status"] == "in_progress"
    assert orch._verification_milestone_id == "ms-new"


def test_ensure_reuses_same_attempt_row():
    orch = _orch_for_ensure(
        [{"milestone_id": "ms-resume", "round_number": 3, "phase_total_tokens": 500}]
    )

    row = orch._ensure_verification_milestone({"verification_attempt": 2, "dev_round": 1})

    assert row["milestone_id"] == "ms-resume"
    orch._create_milestone.assert_not_called()
    orch.repo.update_milestone.assert_not_called()


def test_ensure_sweeps_interrupted_older_rows():
    orch = _orch_for_ensure(
        [
            {"milestone_id": "ms-old", "round_number": 1},
            {"milestone_id": "ms-resume", "round_number": 3},
        ]
    )

    row = orch._ensure_verification_milestone({"verification_attempt": 2, "dev_round": 1})

    assert row["milestone_id"] == "ms-resume"
    swept = orch.repo.update_milestone.call_args_list[0]
    assert swept[0][0] == "ms-old"
    assert swept[0][1]["status"] == "failed"


def test_finalize_updates_row_emits_event_and_clears_stash():
    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch.repo = MagicMock()
    orch._emit = MagicMock()
    orch._verification_milestone_id = "ms-verif"

    orch.finalize_acceptance_milestone("ms-verif", {"status": "confirmed"})

    orch.repo.update_milestone.assert_called_once_with("ms-verif", {"status": "confirmed"})
    assert orch._emit.call_args[0][0] == "milestone_updated"
    assert orch._verification_milestone_id is None


def test_finalize_with_empty_id_is_noop():
    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch.repo = MagicMock()

    orch.finalize_acceptance_milestone("", {"status": "confirmed"})

    orch.repo.update_milestone.assert_not_called()


# ── commit path: usage_delta triggers the totals refresh ─────────────────────


def test_commit_phase_result_refreshes_totals_after_milestone_insert():
    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    # Shared parent so mock_calls records the load-bearing order:
    # the milestone insert must land before the totals refresh reads it.
    calls = MagicMock()
    orch._update_workflow = MagicMock()
    orch._create_milestone = calls.create_milestone
    orch._accumulate_tokens = calls.accumulate_tokens
    result = av.handle(_ctx(_workflow()), _deps_confirmed())

    orch._commit_phase_result(result)

    orch._create_milestone.assert_called_once_with(**result.milestone_events[0])
    orch._accumulate_tokens.assert_called_once()
    # usage_delta carries the verifier usage dict verbatim.
    assert orch._accumulate_tokens.call_args[0][0] == USAGE
    assert [c[0] for c in calls.mock_calls] == ["create_milestone", "accumulate_tokens"]


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
