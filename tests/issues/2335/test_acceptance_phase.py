from unittest.mock import MagicMock

from app.modules.workspace.autonomous.phase_contract import PhaseResult, WorkflowContext
from app.modules.workspace.autonomous.phases import acceptance_verification as av


def _ctx(wf):
    return WorkflowContext(
        workflow=wf,
        definition_snapshot=None,
        repository_context=None,
        session_bindings=MagicMock(),
        cancellation=MagicMock(),
    )


def _deps(**kw):
    d = MagicMock()
    for k, v in kw.items():
        setattr(d, k, v)
    return d


def test_confirmed_closes_issue_and_completes():
    wf = {
        "id": 1,
        "github_issue_number": 42,
        "github_pr_number": 99,
        "base_commit_sha": "base",
        "verification_merge_sha": "merge",
        "issue_acceptance_hash": "h1",
        "verification_status": None,
        "issue_acceptance_snapshot": None,
    }
    gh = MagicMock()
    gh.get_issue.return_value = {"body": "## Scope\n- `app/x.py`"}
    gh.get_changed_files.return_value = ["app/x.py"]  # scope gate passes
    # Mechanical gates (#2335 S4) read changed-file content via git show; wire
    # _run_git so the legacy-pattern gate can read app/x.py (clean -> CONFIRMED,
    # not INDETERMINATE, so the issue can still reach `confirmed`).
    gh._run_git.return_value.stdout = "def f():\n    return 1\n"
    deps = _deps(gh=gh)
    deps.host.run_verification_agent.return_value = {
        "verdicts": [
            {
                "item": "works",
                "verdict": "confirmed",
                "evidence": [{"ref": "app/x.py:1", "note": "implementation present"}],
                "rationale": "",
            }
        ],
        "snapshot": None,
    }
    deps.host.issue_is_open.return_value = True
    result = av.handle(_ctx(wf), deps)
    assert result.outcome == "completed"
    assert result.next_phase == "completed"
    deps.gh.close_issue.assert_called_once_with(42)
    assert result.milestone_events  # verification milestone recorded
    assert result.workflow_patch.get("verification_status") == "confirmed"
    assert result.workflow_patch.get("issue_closed_by_workflow_at")


def test_rejected_pauses_without_reentering_deleted_development_worktree():
    wf = {
        "id": 1,
        "github_issue_number": 42,
        "github_pr_number": 99,
        "base_commit_sha": "base",
        "verification_merge_sha": "merge",
        "issue_acceptance_hash": "h1",
        "verification_status": None,
        "issue_acceptance_snapshot": None,
        "dev_round": 1,
    }
    gh = MagicMock()
    gh.get_issue.return_value = {"body": "## Scope\n- `app/services/retention.py`"}
    gh.get_changed_files.return_value = []  # scope gate: required path missing -> REJECTED
    deps = _deps(gh=gh)
    deps.host.run_verification_agent.return_value = {"verdicts": [], "snapshot": None}
    result = av.handle(_ctx(wf), deps)
    assert result.outcome == "pause"
    assert result.workflow_patch.get("dev_round") is None
    assert result.workflow_patch["verification_status"] == "rejected"
    deps.host.dev_round_cap_remaining.assert_not_called()
    deps.gh.close_issue.assert_not_called()


def test_rejected_pauses_for_review_regardless_of_old_round_cap():
    wf = {
        "id": 1,
        "github_issue_number": 42,
        "github_pr_number": 99,
        "base_commit_sha": "base",
        "verification_merge_sha": "merge",
        "issue_acceptance_hash": "h1",
        "verification_status": None,
        "issue_acceptance_snapshot": None,
        "dev_round": 5,
    }
    gh = MagicMock()
    gh.get_issue.return_value = {"body": "## Scope\n- `app/services/retention.py`"}
    gh.get_changed_files.return_value = []
    deps = _deps(gh=gh)
    deps.host.run_verification_agent.return_value = {"verdicts": [], "snapshot": None}
    deps.host.dev_round_cap_remaining.return_value = 0
    result = av.handle(_ctx(wf), deps)
    assert result.outcome == "pause"
    assert result.workflow_patch["verification_status"] == "rejected"
    deps.host.dev_round_cap_remaining.assert_not_called()
    deps.gh.close_issue.assert_not_called()


def test_indeterminate_pauses():
    wf = {
        "id": 1,
        "github_issue_number": 42,
        "github_pr_number": 99,
        "base_commit_sha": "base",
        "verification_merge_sha": "merge",
        "issue_acceptance_hash": "h1",
        "verification_status": None,
        "issue_acceptance_snapshot": None,
    }
    gh = MagicMock()
    gh.get_issue.return_value = {"body": "## Scope\n- `app/x.py`"}
    gh.get_changed_files.return_value = ["app/x.py"]
    deps = _deps(gh=gh)
    deps.host.run_verification_agent.return_value = {
        "verdicts": [
            {"item": "unclear", "verdict": "indeterminate", "evidence": [], "rationale": ""}
        ],
        "snapshot": None,
    }
    result = av.handle(_ctx(wf), deps)
    assert result.outcome == "pause"
    assert result.workflow_patch.get("verification_status") == "indeterminate"
    deps.gh.close_issue.assert_not_called()


def test_idempotent_rerun_is_noop_when_already_confirmed():
    wf = {
        "id": 1,
        "github_issue_number": 42,
        "github_pr_number": 99,
        "base_commit_sha": "base",
        "verification_merge_sha": "merge",
        "issue_acceptance_hash": "h1",
        "verification_status": "confirmed",
        "issue_acceptance_snapshot": None,
    }
    deps = _deps(gh=MagicMock())
    result = av.handle(_ctx(wf), deps)
    assert result.outcome == "completed"
    assert result.next_phase == "completed"
    deps.gh.close_issue.assert_not_called()  # idempotent: do not close twice
    deps.host.run_verification_agent.assert_not_called()


def test_closed_issue_reopened_when_not_confirmed():
    wf = {
        "id": 1,
        "github_issue_number": 42,
        "github_pr_number": 99,
        "base_commit_sha": "base",
        "verification_merge_sha": None,
        "issue_acceptance_hash": "h1",
        "verification_status": None,
        "issue_acceptance_snapshot": None,
    }
    gh = MagicMock()
    gh.get_merge_commit_sha.return_value = "merge"
    gh.get_issue.return_value = {"body": "## Scope\n- `app/x.py`"}
    gh.get_changed_files.return_value = ["app/x.py"]
    deps = _deps(gh=gh)
    deps.host.issue_is_open.return_value = False  # auto-closed externally
    deps.gh.get_issue_closure.return_value = {
        "closed_at": "2026-08-08T00:00:00Z",
        "closer_login": "open-ace-bot",
    }
    deps.gh.get_authenticated_login.return_value = "open-ace-bot"
    deps.host.run_verification_agent.return_value = {"verdicts": [], "snapshot": None}
    av.handle(_ctx(wf), deps)
    deps.gh.reopen_issue.assert_called_once_with(42)  # reopen guard fires before verifying


def test_human_closed_issue_is_not_reopened():
    wf = {
        "id": 1,
        "github_issue_number": 42,
        "github_pr_number": 99,
        "base_commit_sha": "base",
        "verification_merge_sha": "merge",
        "verification_status": None,
        "issue_acceptance_snapshot": None,
    }
    deps = _deps(gh=MagicMock())
    deps.host.issue_is_open.return_value = False
    deps.gh.get_issue_closure.return_value = {
        "closed_at": "2026-08-08T00:00:00Z",
        "closer_login": "human-maintainer",
    }
    deps.gh.get_authenticated_login.return_value = "open-ace-bot"
    deps.host.run_verification_agent.return_value = {"verdicts": [], "snapshot": None}

    av.handle(_ctx(wf), deps)

    deps.gh.reopen_issue.assert_not_called()
    deps.host.emit_audit_event.assert_not_called()


def test_snapshot_persistence_round_trips_source_and_confidence():
    # Regression for review finding #3: the persisted snapshot must preserve
    # source/confidence on reload (only the hash is content-canonicalized).
    import json

    from app.modules.workspace.autonomous.acceptance_snapshot import (
        AcceptanceSnapshot,
        parse_acceptance_snapshot,
    )
    from app.modules.workspace.autonomous.phases.acceptance_verification import _snapshot_to_json

    snap = parse_acceptance_snapshot("## Scope\n- `app/x.py`\n## 验收标准\n- [ ] one\n")
    assert snap.source == "convention" and snap.confidence == "high"
    reloaded = AcceptanceSnapshot(**json.loads(_snapshot_to_json(snap)))
    assert reloaded.source == "convention"
    assert reloaded.confidence == "high"
    assert reloaded.required_paths == ["app/x.py"]


# --- Surface rejection/inconclusive verdicts to humans (issue comment + milestone) ---


def test_rejected_posts_human_readable_comment():
    """A rejected verdict must be surfaced as a GitHub comment so the issue
    author sees WHAT failed + what to fix — not a silent DB-only pause."""
    wf = {
        "id": 1,
        "github_issue_number": 42,
        "github_pr_number": 99,
        "base_commit_sha": "base",
        "verification_merge_sha": "merge",
        "issue_acceptance_hash": "h1",
        "verification_status": None,
        "issue_acceptance_snapshot": None,
        "dev_round": 1,
    }
    gh = MagicMock()
    gh.get_issue.return_value = {"body": "## Scope\n- `app/services/retention.py`"}
    gh.get_changed_files.return_value = []  # scope gate rejects (required path missing)
    deps = _deps(gh=gh)
    deps.host.run_verification_agent.return_value = {"verdicts": [], "snapshot": None}
    av.handle(_ctx(wf), deps)
    deps.gh.add_issue_comment.assert_called_once()
    issue_no, comment = deps.gh.add_issue_comment.call_args.args
    assert issue_no == 42
    assert "❌" in comment or "not verified" in comment.lower()
    assert "retention.py" in comment  # the rejected required path is named


def test_indeterminate_posts_human_readable_comment():
    wf = {
        "id": 1,
        "github_issue_number": 42,
        "github_pr_number": 99,
        "base_commit_sha": "base",
        "verification_merge_sha": "merge",
        "issue_acceptance_hash": "h1",
        "verification_status": None,
        "issue_acceptance_snapshot": None,
    }
    gh = MagicMock()
    gh.get_issue.return_value = {"body": "## Scope\n- `app/x.py`"}
    gh.get_changed_files.return_value = ["app/x.py"]  # scope gate confirmed
    deps = _deps(gh=gh)
    deps.host.run_verification_agent.return_value = {
        "verdicts": [
            {
                "item": "unclear criterion",
                "verdict": "indeterminate",
                "evidence": [{"ref": "verifier:missing", "note": "no concrete proof"}],
                "rationale": "",
            }
        ],
        "snapshot": None,
    }
    av.handle(_ctx(wf), deps)
    deps.gh.add_issue_comment.assert_called_once()
    _, comment = deps.gh.add_issue_comment.call_args.args
    assert "⚠️" in comment or "inconclusive" in comment.lower()
    assert "unclear criterion" in comment


def test_format_report_comment_confirmed_uses_check_mark():
    from app.modules.workspace.autonomous.phases.acceptance_verification import (
        _format_report_comment,
    )

    report = {
        "status": "confirmed",
        "merge_sha": "s",
        "verified_by": "v",
        "scope": [{"item": "app/x.py", "verdict": "confirmed"}],
        "gates": [],
        "verifier": [],
    }
    assert "✅" in _format_report_comment(report)


def test_format_report_comment_rejected_lists_failed_items():
    from app.modules.workspace.autonomous.phases.acceptance_verification import (
        _format_report_comment,
    )

    report = {
        "status": "rejected",
        "merge_sha": "s",
        "verified_by": "v",
        "scope": [
            {
                "item": "app/utils/datetime_utils.py",
                "verdict": "rejected",
                "evidence": [{"ref": "missing:datetime", "note": "absent"}],
            }
        ],
        "gates": [
            {"item": "regression:decorators", "verdict": "rejected", "rationale": "dec regressed"}
        ],
        "verifier": [],
    }
    comment = _format_report_comment(report)
    assert "❌" in comment
    assert "datetime_utils.py" in comment
    assert "regression:decorators" in comment


def test_acceptance_milestone_summary_includes_rejected_items():
    from app.modules.workspace.autonomous.phases.acceptance_verification import (
        _acceptance_milestone,
    )

    report = {
        "status": "rejected",
        "scope": [{"item": "app/utils/datetime_utils.py", "verdict": "rejected"}],
        "gates": [],
        "verifier": [],
    }
    ms = _acceptance_milestone(
        workflow_id="wf", dev_round=1, attempt=1, status="rejected", report=report
    )
    assert "datetime_utils.py" in ms["result_summary"]
