"""S5 (#2335): (merge_sha, issue_acceptance_hash) in-flight idempotency.

Re-entering ``acceptance_verification`` with the SAME pair AND a terminal
prior status (confirmed/rejected/indeterminate) reuses the prior result and
does NOT re-run the expensive verifier. A changed merge_sha or hash re-runs.
"""

from __future__ import annotations

import dataclasses
import json
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


def _prior_report(merge_sha, snap_hash, status="indeterminate"):
    return json.dumps(
        {
            "merge_sha": merge_sha,
            "issue_acceptance_hash": snap_hash,
            "verified_by": "acceptance-verifier-v1/test-model",
            "status": status,
        }
    )


def _persisted_snapshot_json():
    """A canonical persisted snapshot whose hash we control in tests."""
    from app.modules.workspace.autonomous.acceptance_snapshot import (
        AcceptanceSnapshot,
        hash_snapshot,
    )

    snap = AcceptanceSnapshot(
        required_paths=[],
        checklist=[],
        non_scope=[],
        closure_constraints=False,
        source="missing",
        confidence="low",
    )
    return json.dumps(dataclasses.asdict(snap), ensure_ascii=False), hash_snapshot(snap)


_SNAP_JSON, _SNAP_HASH = _persisted_snapshot_json()


def _base_wf(**overrides):
    wf = {
        "id": 1,
        "github_issue_number": 42,
        "github_pr_number": 99,
        "base_commit_sha": "base",
        "verification_merge_sha": "mergeA",
        "issue_acceptance_hash": _SNAP_HASH,
        "verification_status": "indeterminate",
        "verification_report": _prior_report("mergeA", _SNAP_HASH, "indeterminate"),
        "issue_acceptance_snapshot": _SNAP_JSON,
        "verified_by": "acceptance-verifier-v1/test-model",
    }
    wf.update(overrides)
    return wf


def test_matching_pair_terminal_reuses_prior_no_verifier_spawn():
    """Same (merge_sha, hash) + terminal status -> reuse, verifier NOT called."""
    wf = _base_wf()  # status=indeterminate is terminal for reuse purposes
    gh = MagicMock()
    deps = _deps(gh=gh)
    deps.host.issue_is_open.return_value = True

    result = av.handle(_ctx(wf), deps)

    deps.host.run_verification_agent.assert_not_called()
    # Prior indeterminate result is replayed as a pause.
    assert result.outcome == "pause"
    assert result.workflow_patch.get("verification_status") == "indeterminate"


def test_matching_pair_confirmed_reuses_prior_completed():
    """Same pair + confirmed prior -> completed, no re-close."""
    wf = _base_wf(
        verification_status="confirmed",
        verification_report=_prior_report("mergeA", _SNAP_HASH, "confirmed"),
    )
    gh = MagicMock()
    deps = _deps(gh=gh)
    deps.host.issue_is_open.return_value = True

    result = av.handle(_ctx(wf), deps)

    deps.host.run_verification_agent.assert_not_called()
    gh.close_issue.assert_not_called()  # idempotent: already closed
    assert result.outcome == "completed"
    assert result.next_phase == "completed"


def test_changed_hash_reruns_verifier():
    """Issue edited mid-flight (new hash) -> re-verify."""
    # Drop the persisted snapshot so it's re-parsed from the (richer) issue
    # body; the new hash differs from the prior empty-snapshot hash.
    wf = _base_wf(issue_acceptance_snapshot=None)
    gh = MagicMock()
    gh.get_issue.return_value = {"body": "## Scope\n- `app/x.py`"}
    gh.get_changed_files.return_value = ["app/x.py"]
    deps = _deps(gh=gh)
    deps.host.issue_is_open.return_value = True
    deps.host.run_verification_agent.return_value = {"verdicts": [], "snapshot": None}

    av.handle(_ctx(wf), deps)

    deps.host.run_verification_agent.assert_called_once()


def test_changed_merge_sha_reruns_verifier():
    """New merge (different merge_sha) -> re-verify even if hash unchanged."""
    wf = _base_wf(verification_merge_sha="mergeB")  # differs from prior report
    gh = MagicMock()
    gh.get_issue.return_value = {"body": "## Scope\n- `app/x.py`"}
    gh.get_changed_files.return_value = ["app/x.py"]
    deps = _deps(gh=gh)
    deps.host.issue_is_open.return_value = True
    deps.host.run_verification_agent.return_value = {"verdicts": [], "snapshot": None}

    av.handle(_ctx(wf), deps)

    deps.host.run_verification_agent.assert_called_once()


def test_non_terminal_pending_status_reruns():
    """verification_status=pending (non-terminal) -> re-run regardless of pair."""
    wf = _base_wf(verification_status="pending")
    gh = MagicMock()
    gh.get_issue.return_value = {"body": "## Scope\n- `app/x.py`"}
    gh.get_changed_files.return_value = ["app/x.py"]
    deps = _deps(gh=gh)
    deps.host.issue_is_open.return_value = True
    deps.host.run_verification_agent.return_value = {"verdicts": [], "snapshot": None}

    av.handle(_ctx(wf), deps)

    deps.host.run_verification_agent.assert_called_once()


def test_missing_prior_report_reruns():
    """No persisted verification_report -> cannot reuse, re-run."""
    wf = _base_wf(verification_report=None)
    gh = MagicMock()
    gh.get_issue.return_value = {"body": "## Scope\n- `app/x.py`"}
    gh.get_changed_files.return_value = ["app/x.py"]
    deps = _deps(gh=gh)
    deps.host.issue_is_open.return_value = True
    deps.host.run_verification_agent.return_value = {"verdicts": [], "snapshot": None}

    av.handle(_ctx(wf), deps)

    deps.host.run_verification_agent.assert_called_once()
