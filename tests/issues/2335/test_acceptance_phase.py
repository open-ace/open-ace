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
        "verdicts": [{"item": "works", "verdict": "confirmed", "evidence": [], "rationale": ""}],
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


def test_rejected_starts_new_dev_round():
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
    deps.host.dev_round_cap_remaining.return_value = 2
    result = av.handle(_ctx(wf), deps)
    assert result.outcome == "completed"
    assert result.next_phase == "development"
    assert result.workflow_patch.get("dev_round") == 2
    deps.gh.close_issue.assert_not_called()


def test_rejected_at_cap_fails():
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
    assert result.outcome == "failed"
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
    deps.host.run_verification_agent.return_value = {"verdicts": [], "snapshot": None}
    av.handle(_ctx(wf), deps)
    deps.gh.reopen_issue.assert_called_once_with(42)  # reopen guard fires before verifying


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
