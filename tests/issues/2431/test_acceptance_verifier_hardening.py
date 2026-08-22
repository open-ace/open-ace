"""Regression coverage for the verifier hardening that enables #2335 safely."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.acceptance_gates import (
    legacy_pattern_gate,
    run_mechanical_gates,
)
from app.modules.workspace.autonomous.acceptance_snapshot import AcceptanceSnapshot
from app.modules.workspace.autonomous.acceptance_verdicts import ItemVerdict
from app.modules.workspace.autonomous.evidence import Verdict
from app.modules.workspace.autonomous.github_ops import GitHubOps
from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator
from app.modules.workspace.autonomous.phase_contract import PhaseResult, WorkflowContext
from app.modules.workspace.autonomous.phases import acceptance_verification as av
from app.repositories.autonomous_repo import AutonomousWorkflowRepository
from app.services import autonomous_scheduler as scheduler_module
from app.services.autonomous_scheduler import AutonomousScheduler


def _result(*, returncode=0, stdout="", stderr=""):
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


def _ctx(workflow):
    return WorkflowContext(
        workflow=workflow,
        definition_snapshot=None,
        repository_context=None,
        session_bindings=MagicMock(),
        cancellation=MagicMock(),
    )


def _workflow():
    return {
        "workflow_id": "wf-hardening",
        "github_issue_number": 42,
        "github_pr_number": 99,
        "base_commit_sha": "base",
        "verification_merge_sha": "merge",
        "verification_status": None,
        "issue_acceptance_snapshot": None,
        "dev_round": 5,
    }


def test_scope_git_error_is_indeterminate_not_an_exception():
    gh = MagicMock()
    gh.get_changed_files.side_effect = RuntimeError("bad object merge")

    verdicts, advisory = av.run_scope_gate(gh, ["app/x.py"], "base", "merge")

    assert len(verdicts) == 1
    assert verdicts[0].verdict is Verdict.INDETERMINATE
    assert verdicts[0].item == "scope:changed-files"
    assert verdicts[0].retryable is True
    assert advisory == []


def test_broad_legacy_regex_is_retired_from_production_aggregate():
    gh = MagicMock()
    gh.get_changed_files.return_value = ["app/views/tenant.py"]

    def reader(_path):
        return "if current.tenant_id == tenant_id:\n    return allowed\n"

    legacy = legacy_pattern_gate(gh, AcceptanceSnapshot(), "base", "merge", read_file=reader)
    aggregate = run_mechanical_gates(gh, AcceptanceSnapshot(), "base", "merge", read_file=reader)

    assert any(item.verdict is Verdict.REJECTED for item in legacy)
    assert all(item.verdict is not Verdict.REJECTED for item in aggregate)


def test_explicit_verifier_infra_error_retries_without_terminal_milestone():
    deps = MagicMock()
    deps.gh.get_issue.return_value = {"body": "## Scope\n- `app/x.py`"}
    deps.gh.get_changed_files.return_value = ["app/x.py"]
    deps.gh._run_git.return_value.stdout = "def f():\n    return 1\n"
    deps.host.issue_is_open.return_value = True
    deps.host.run_verification_agent.return_value = {
        "verdicts": [],
        "snapshot": None,
        "infra_error": "verification agent timed out",
    }

    result = av.handle(_ctx(_workflow()), deps)

    assert result.outcome == "retry"
    assert result.workflow_patch["verification_status"] == "indeterminate"
    assert result.milestone_events == []
    report = json.loads(result.workflow_patch["verification_report"])
    assert report["verifier"][0]["item"] == "verifier:infrastructure"
    assert "timed out" in report["verifier"][0]["evidence"][0]["note"]
    assert report["infra_error"] == "verification agent timed out"


def test_infra_result_is_not_terminally_cached_and_resume_can_confirm():
    deps = MagicMock()
    deps.gh.get_issue.return_value = {
        "body": "## Acceptance Criteria\n- [ ] The endpoint rejects expired tokens"
    }
    deps.gh.get_changed_files.return_value = []
    deps.gh._run_git.return_value.stdout = ""
    deps.host.issue_is_open.return_value = True
    deps.host.run_verification_agent.side_effect = [
        {
            "verdicts": [],
            "snapshot": None,
            "infra_error": "verification agent timed out",
        },
        {
            "verdicts": [
                {
                    "item": "  THE endpoint rejects expired tokens ",
                    "verdict": "confirmed",
                    "evidence": [{"ref": "tests/test_auth.py:42", "note": "covered"}],
                }
            ],
            "snapshot": None,
        },
    ]

    first = av.handle(_ctx(_workflow()), deps)
    resumed_workflow = {**_workflow(), **first.workflow_patch}
    second = av.handle(_ctx(resumed_workflow), deps)

    assert first.outcome == "retry"
    assert second.outcome == "completed"
    assert second.workflow_patch["verification_status"] == "confirmed"
    assert deps.host.run_verification_agent.call_count == 2
    deps.gh.close_issue.assert_called_once_with(42)

    orchestrator = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orchestrator._update_workflow = MagicMock()
    orchestrator._create_milestone = MagicMock()
    orchestrator._commit_phase_result(first)
    orchestrator._commit_phase_result(second)
    orchestrator._create_milestone.assert_called_once_with(**second.milestone_events[0])


def test_infrastructure_retries_are_bounded_then_pause():
    deps = MagicMock()
    deps.gh.get_issue.return_value = {"body": "## Scope\n- `app/x.py`"}
    deps.gh.get_changed_files.return_value = ["app/x.py"]
    deps.gh._run_git.return_value.stdout = "def f():\n    return 1\n"
    deps.host.issue_is_open.return_value = True
    deps.host.run_verification_agent.return_value = {
        "verdicts": [],
        "snapshot": None,
        "infra_error": "verification agent timed out",
    }
    workflow = _workflow()
    outcomes = []

    for _ in range(av.MAX_VERIFIER_INFRA_RETRIES):
        result = av.handle(_ctx(workflow), deps)
        outcomes.append(result)
        workflow = {**workflow, **result.workflow_patch}

    assert [result.outcome for result in outcomes] == ["retry", "retry", "pause"]
    assert outcomes[-1].milestone_events
    final_report = json.loads(outcomes[-1].workflow_patch["verification_report"])
    assert final_report["infra_retry_count"] == av.MAX_VERIFIER_INFRA_RETRIES
    assert deps.host.run_verification_agent.call_count == av.MAX_VERIFIER_INFRA_RETRIES


@pytest.mark.parametrize("failing_probe", ["scope", "gate"])
def test_retryable_probe_error_is_not_terminally_cached_and_can_recover(failing_probe):
    deps = MagicMock()
    deps.gh.get_issue.return_value = {"body": "## Scope\n- `app/x.py`"}
    deps.host.issue_is_open.return_value = True
    deps.host.run_verification_agent.side_effect = lambda **_kwargs: {
        "verdicts": [],
        "snapshot": None,
    }
    confirmed_scope = ItemVerdict(
        item="app/x.py",
        verdict=Verdict.CONFIRMED,
        evidence=[{"ref": "git-diff:app/x.py", "note": "present"}],
    )
    failed_probe = ItemVerdict(
        item=f"{failing_probe}:error",
        verdict=Verdict.INDETERMINATE,
        evidence=[{"ref": "error", "note": "bad object"}],
        retryable=True,
    )
    scope_results = (
        [([failed_probe], []), ([confirmed_scope], [])] if failing_probe == "scope" else None
    )
    gate_results = [[failed_probe], []] if failing_probe == "gate" else None

    with (
        patch.object(
            av,
            "run_scope_gate",
            side_effect=scope_results,
            return_value=([confirmed_scope], []),
        ),
        patch.object(
            av,
            "run_mechanical_gates",
            side_effect=gate_results,
            return_value=[],
        ),
    ):
        first = av.handle(_ctx(_workflow()), deps)
        second = av.handle(_ctx({**_workflow(), **first.workflow_patch}), deps)

    assert first.outcome == "retry"
    assert second.outcome == "completed"
    assert deps.host.run_verification_agent.call_count == 2
    deps.gh.close_issue.assert_called_once_with(42)


def test_missing_checklist_verdict_forces_indeterminate_and_never_closes_issue():
    deps = MagicMock()
    deps.gh.get_issue.return_value = {
        "body": (
            "## Scope\n- `app/x.py`\n\n"
            "## Acceptance Criteria\n- [ ] The endpoint rejects expired tokens"
        )
    }
    deps.gh.get_changed_files.return_value = ["app/x.py"]
    deps.gh._run_git.return_value.stdout = "def f():\n    return 1\n"
    deps.host.issue_is_open.return_value = True
    deps.host.run_verification_agent.return_value = {"verdicts": [], "snapshot": None}

    result = av.handle(_ctx(_workflow()), deps)

    assert result.outcome == "pause"
    assert result.workflow_patch["verification_status"] == "indeterminate"
    report = json.loads(result.workflow_patch["verification_report"])
    assert report["verifier"] == [
        {
            "item": "The endpoint rejects expired tokens",
            "verdict": "indeterminate",
            "evidence": [
                {
                    "ref": "verifier:missing-item",
                    "note": "The verifier returned no verdict for this acceptance item.",
                }
            ],
            "rationale": (
                "The verifier did not cover this checklist item; no acceptance "
                "decision was made for it."
            ),
        }
    ]
    # Indeterminate now surfaces to humans as an issue comment (what's missing).
    deps.gh.add_issue_comment.assert_called_once()
    _, comment = deps.gh.add_issue_comment.call_args.args
    assert "rejects expired tokens" in comment
    deps.gh.close_issue.assert_not_called()


@pytest.mark.parametrize(
    ("returned_snapshot", "error_fragment"),
    [
        (None, "omitted the required extracted acceptance snapshot"),
        ({"bad": 1}, "returned invalid snapshot"),
        (
            {
                "required_paths": [],
                "checklist": [],
                "non_scope": [],
                "closure_constraints": False,
            },
            "contained no verifiable criteria",
        ),
    ],
)
def test_missing_issue_snapshot_cannot_close_from_invented_confirmed_verdict(
    returned_snapshot, error_fragment
):
    deps = MagicMock()
    deps.gh.get_issue.return_value = {"body": "No structured acceptance sections."}
    deps.gh.get_changed_files.return_value = []
    deps.host.issue_is_open.return_value = True
    deps.host.run_verification_agent.return_value = {
        "verdicts": [
            {
                "item": "invented",
                "verdict": "confirmed",
                "evidence": [{"ref": "app/x.py:1", "note": "not tied to issue"}],
            }
        ],
        "snapshot": returned_snapshot,
    }

    result = av.handle(_ctx(_workflow()), deps)

    assert result.outcome == "retry"
    assert result.workflow_patch["verification_status"] == "indeterminate"
    report = json.loads(result.workflow_patch["verification_report"])
    assert error_fragment in report["infra_error"]
    assert any(item["item"] == "verifier:infrastructure" for item in report["verifier"])
    deps.gh.add_issue_comment.assert_not_called()
    deps.gh.close_issue.assert_not_called()


def test_valid_extracted_snapshot_drives_coverage_and_can_confirm():
    deps = MagicMock()
    deps.gh.get_issue.return_value = {"body": "Unstructured request text."}
    deps.gh.get_changed_files.return_value = []
    deps.host.issue_is_open.return_value = True
    deps.host.run_verification_agent.return_value = {
        "verdicts": [
            {
                "item": "The endpoint rejects expired tokens",
                "verdict": "confirmed",
                "evidence": [{"ref": "tests/test_auth.py:42", "note": "covered"}],
            }
        ],
        "snapshot": {
            "required_paths": [],
            "checklist": ["The endpoint rejects expired tokens"],
            "non_scope": [],
            "closure_constraints": False,
        },
    }

    result = av.handle(_ctx(_workflow()), deps)

    assert result.outcome == "completed"
    assert result.workflow_patch["verification_status"] == "confirmed"
    persisted = json.loads(result.workflow_patch["issue_acceptance_snapshot"])
    assert persisted["source"] == "llm"
    assert persisted["confidence"] == "low"
    deps.gh.close_issue.assert_called_once_with(42)


def test_verifier_cannot_replace_structured_issue_snapshot():
    deps = MagicMock()
    deps.gh.get_issue.return_value = {"body": "## Acceptance Criteria\n- [ ] security behavior"}
    deps.gh.get_changed_files.return_value = []
    deps.host.issue_is_open.return_value = True
    deps.host.run_verification_agent.return_value = {
        "verdicts": [
            {
                "item": "easier substitute",
                "verdict": "confirmed",
                "evidence": [{"ref": "app/x.py:1", "note": "unrelated"}],
                "rationale": "",
            }
        ],
        "snapshot": {
            "required_paths": [],
            "checklist": ["easier substitute"],
            "non_scope": [],
            "closure_constraints": False,
        },
    }

    result = av.handle(_ctx(_workflow()), deps)

    assert result.outcome == "retry"
    report = json.loads(result.workflow_patch["verification_report"])
    assert "unexpected snapshot" in report["infra_error"]
    persisted = json.loads(result.workflow_patch["issue_acceptance_snapshot"])
    assert persisted["checklist"] == ["security behavior"]
    deps.gh.close_issue.assert_not_called()


def test_confirmed_checklist_verdict_without_evidence_is_indeterminate():
    deps = MagicMock()
    deps.gh.get_issue.return_value = {"body": "## Acceptance Criteria\n- [ ] security behavior"}
    deps.gh.get_changed_files.return_value = []
    deps.host.issue_is_open.return_value = True
    deps.host.run_verification_agent.return_value = {
        "verdicts": [
            {
                "item": "security behavior",
                "verdict": "confirmed",
                "evidence": [],
                "rationale": "looks good",
            }
        ],
        "snapshot": None,
    }

    result = av.handle(_ctx(_workflow()), deps)

    assert result.outcome == "pause"
    report = json.loads(result.workflow_patch["verification_report"])
    assert report["verifier"][0]["verdict"] == "indeterminate"
    assert report["verifier"][0]["evidence"][0]["ref"] == "verifier:missing-evidence"
    deps.gh.close_issue.assert_not_called()


def test_malformed_verdict_evidence_is_retryable_infrastructure():
    deps = MagicMock()
    deps.gh.get_issue.return_value = {"body": "## Acceptance Criteria\n- [ ] security behavior"}
    deps.gh.get_changed_files.return_value = []
    deps.host.issue_is_open.return_value = True
    deps.host.run_verification_agent.return_value = {
        "verdicts": [
            {
                "item": "security behavior",
                "verdict": "confirmed",
                "evidence": {"ref": "app/x.py:1"},
                "rationale": [],
            }
        ],
        "snapshot": None,
    }

    result = av.handle(_ctx(_workflow()), deps)

    assert result.outcome == "retry"
    report = json.loads(result.workflow_patch["verification_report"])
    assert "verdict fields were malformed" in report["infra_error"]
    deps.gh.close_issue.assert_not_called()


def test_lost_scheduler_lock_blocks_confirmed_github_mutations():
    deps = MagicMock()
    deps.gh.get_issue.return_value = {"body": "## Scope\n- `app/x.py`"}
    deps.gh.get_changed_files.return_value = ["app/x.py"]
    deps.gh._run_git.return_value.stdout = "def f():\n    return 1\n"
    deps.host.issue_is_open.return_value = True
    deps.host.ensure_scheduler_lock.return_value = False
    deps.host.run_verification_agent.return_value = {"verdicts": [], "snapshot": None}

    result = av.handle(_ctx(_workflow()), deps)

    assert result.outcome == "retry"
    deps.gh.add_issue_comment.assert_not_called()
    deps.gh.close_issue.assert_not_called()


def test_lock_loss_after_comment_blocks_close_for_replacement_retry():
    deps = MagicMock()
    deps.gh.get_issue.return_value = {"body": "## Scope\n- `app/x.py`"}
    deps.gh.get_changed_files.return_value = ["app/x.py"]
    deps.gh._run_git.return_value.stdout = "def f():\n    return 1\n"
    deps.host.issue_is_open.return_value = True
    deps.host.ensure_scheduler_lock.side_effect = [True, False]
    deps.host.run_verification_agent.return_value = {"verdicts": [], "snapshot": None}

    result = av.handle(_ctx(_workflow()), deps)

    assert result.outcome == "retry"
    deps.gh.add_issue_comment.assert_called_once()
    deps.gh.close_issue.assert_not_called()


def test_rejection_pauses_instead_of_reentering_or_failing_delivered_work():
    deps = MagicMock()
    deps.gh.get_issue.return_value = {"body": "## Scope\n- `app/x.py`"}
    deps.gh.get_changed_files.return_value = []
    deps.host.issue_is_open.return_value = True
    deps.host.run_verification_agent.return_value = {"verdicts": [], "snapshot": None}

    result = av.handle(_ctx(_workflow()), deps)

    assert result.outcome == "pause"
    assert result.workflow_patch["verification_status"] == "rejected"
    assert "awaiting review" in result.workflow_patch["error_message"]
    deps.host.dev_round_cap_remaining.assert_not_called()


def test_reopen_guard_uses_actual_closer_identity():
    deps = MagicMock()
    deps.host.issue_is_open.return_value = False
    deps.gh.get_issue_closure.return_value = {
        "closed_at": "2026-08-08T00:00:00Z",
        "closer_login": "human-maintainer",
    }
    deps.gh.get_authenticated_login.return_value = "open-ace-bot"
    deps.gh.get_issue.return_value = {"body": ""}
    deps.host.run_verification_agent.return_value = {"verdicts": [], "snapshot": None}

    av.handle(_ctx(_workflow()), deps)

    deps.gh.reopen_issue.assert_not_called()

    deps.gh.get_issue_closure.return_value["closer_login"] = "Open-Ace-Bot"
    av.handle(_ctx(_workflow()), deps)
    deps.gh.reopen_issue.assert_called_once_with(42)


def test_merge_sha_is_fetched_and_verified_when_missing():
    gh = GitHubOps("/repo")
    gh._run_git = MagicMock(
        side_effect=[
            _result(returncode=1),
            _result(returncode=0),
            _result(returncode=0),
        ]
    )

    assert gh.ensure_commit_available("a" * 40) is True
    assert gh._run_git.call_args_list[1].args[0] == [
        "fetch",
        "--no-tags",
        "origin",
        "a" * 40,
    ]
    assert gh._run_git.call_args_list[-1].args[0] == [
        "cat-file",
        "-e",
        f"{'a' * 40}^{{commit}}",
    ]


def test_issue_comment_exact_body_from_same_bot_is_idempotent():
    gh = GitHubOps("/repo")
    gh.get_authenticated_login = MagicMock(return_value="open-ace-bot")
    gh.list_issue_comments = MagicMock(
        return_value=[{"id": 7, "body": "same report", "author": {"login": "Open-Ace-Bot"}}]
    )
    gh._run_gh = MagicMock()

    result = gh.add_issue_comment(42, "same report")

    assert result == {"number": 42, "id": 7, "deduplicated": True}
    gh._run_gh.assert_not_called()


def test_issue_comment_same_body_from_human_does_not_deduplicate():
    gh = GitHubOps("/repo")
    gh.get_authenticated_login = MagicMock(return_value="open-ace-bot")
    gh.list_issue_comments = MagicMock(
        return_value=[{"id": 7, "body": "same report", "author": {"login": "maintainer"}}]
    )
    gh._run_gh = MagicMock()

    result = gh.add_issue_comment(42, "same report")

    assert result == {"number": 42, "deduplicated": False}
    gh._run_gh.assert_called_once_with(
        ["issue", "comment", "42", "--body", "same report"], api_only=True
    )


def test_issue_comment_unknown_bot_identity_does_not_trust_existing_author():
    gh = GitHubOps("/repo")
    gh.get_authenticated_login = MagicMock(return_value=None)
    gh.list_issue_comments = MagicMock()
    gh._run_gh = MagicMock()

    result = gh.add_issue_comment(42, "same report")

    assert result == {"number": 42, "deduplicated": False}
    gh.list_issue_comments.assert_not_called()


def test_issue_comment_listing_uses_paginated_rest_and_normalizes_pages():
    gh = GitHubOps("/repo")
    gh.get_repo_name = MagicMock(return_value="open-ace/open-ace")
    gh._gh_api_args = MagicMock(side_effect=lambda args: ["api", *args])
    gh._run_gh = MagicMock(
        return_value=_result(
            stdout=(
                '{"id":1,"body":"first","createdAt":"2026-08-08T00:00:00Z",'
                '"author":{"login":"bot"}}\n'
                '{"id":2,"body":"second","createdAt":"2026-08-08T01:00:00Z",'
                '"author":{"login":"human"}}\n'
            )
        )
    )

    comments = gh.list_issue_comments(42)

    assert [comment["id"] for comment in comments] == [1, 2]
    api_args = gh._gh_api_args.call_args.args[0]
    assert "--paginate" in api_args
    assert "repos/open-ace/open-ace/issues/42/comments" in api_args
    gh._run_gh.assert_called_once_with(["api", *api_args], repo_scoped=False, api_only=True)


def test_pause_result_commits_its_milestone_event():
    milestone = {
        "workflow_id": "wf-hardening",
        "phase": "acceptance_verification",
        "milestone_type": "acceptance_verification",
        "status": "rejected",
        "title": "Acceptance verification: rejected",
    }
    result = PhaseResult.pause(
        workflow_patch={"verification_status": "rejected"},
        milestone_events=[milestone],
    )
    orchestrator = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orchestrator._update_workflow = MagicMock()
    orchestrator._create_milestone = MagicMock()

    orchestrator._commit_phase_result(result)

    committed_patch = orchestrator._update_workflow.call_args.args[0]
    assert committed_patch["status"] == "paused"
    assert committed_patch["verification_status"] == "rejected"
    orchestrator._create_milestone.assert_called_once_with(**milestone)


def test_ci_executes_the_issue_2335_verifier_regressions():
    repo_root = Path(__file__).resolve().parents[3]
    workflow = (repo_root / ".github" / "workflows" / "ci.yml").read_text()
    suites = (repo_root / "ci" / "suites.json").read_text()
    promoted = (repo_root / "tests" / "issues" / "pr-gate-directories.txt").read_text().splitlines()

    assert "scripts/ci.py run legacy-pr" in workflow
    assert '"legacy-pr"' in suites
    assert "priority_p0 and not postgres and not performance" in suites
    assert {"2335", "2390", "2401", "2403"} <= set(promoted)


class _SQLiteDB:
    def __init__(self, path: str):
        self.path = path

    def get_connection(self):
        return sqlite3.connect(self.path)

    def fetch_one(self, sql, params=()):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row is not None else None
        finally:
            conn.close()


def test_heartbeat_prevents_takeover_after_a_31_minute_agent_run(tmp_path):
    """Two repository instances model two scheduler processes sharing the DB."""
    db_path = str(tmp_path / "workflow-lock.db")
    stale = (datetime.now(timezone.utc) - timedelta(minutes=31)).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE autonomous_workflows (
            workflow_id TEXT PRIMARY KEY,
            locked_at TEXT,
            locked_by TEXT,
            agent_pid INTEGER
        )""")
    conn.execute(
        "INSERT INTO autonomous_workflows VALUES (?, ?, ?, ?)",
        ("wf-live", stale, "host/100/worker", 4242),
    )
    conn.commit()
    conn.close()

    first_repo = AutonomousWorkflowRepository(_SQLiteDB(db_path))
    second_repo = AutonomousWorkflowRepository(_SQLiteDB(db_path))
    with patch("app.repositories.database.adapt_sql", side_effect=lambda sql: sql):
        # At minute 31 the original timestamp is stale, but the live scheduler
        # heartbeat renews it before a peer's acquisition attempt.
        assert first_repo.refresh_lock("wf-live", "host/100/worker") is True
        assert second_repo.acquire_lock("wf-live", "host/200/worker") is False

    conn = sqlite3.connect(db_path)
    owner = conn.execute(
        "SELECT locked_by FROM autonomous_workflows WHERE workflow_id = 'wf-live'"
    ).fetchone()[0]
    conn.close()
    assert owner == "host/100/worker"


def test_scheduler_heartbeats_with_a_pid_unique_owner():
    scheduler = AutonomousScheduler()
    repo = MagicMock()
    repo.get_workflow.return_value = {"workflow_id": "wf-heartbeat", "status": "planning"}
    repo.acquire_lock.return_value = True
    repo.refresh_lock.return_value = True
    heartbeat_seen = threading.Event()
    repo.refresh_lock.side_effect = lambda *_args: heartbeat_seen.set() or True

    def advance_until_heartbeat():
        assert heartbeat_seen.wait(timeout=1)

    with (
        patch("app.routes.autonomous._get_repo", return_value=repo),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousOrchestrator"
        ) as orchestrator_cls,
        patch.object(scheduler_module, "WORKFLOW_LOCK_HEARTBEAT_SECONDS", 0.001),
    ):
        orchestrator_cls.return_value.advance.side_effect = advance_until_heartbeat
        scheduler._advance_single("wf-heartbeat")

    owner = repo.acquire_lock.call_args.args[1]
    assert f"/{os.getpid()}/" in owner
    repo.refresh_lock.assert_called_with("wf-heartbeat", owner)
    repo.release_lock.assert_called_once_with("wf-heartbeat", owner)


def test_lost_heartbeat_stops_the_old_orchestrator():
    scheduler = AutonomousScheduler.__new__(AutonomousScheduler)
    orchestrator = MagicMock()
    scheduler._orchestrator_lock = threading.Lock()
    scheduler._running_orchestrators = {"wf-lost": orchestrator}
    repo = MagicMock()
    repo.refresh_lock.return_value = False
    stop_event = MagicMock()
    stop_event.wait.return_value = False
    lock_lost = threading.Event()

    scheduler._heartbeat_workflow_lock(repo, "wf-lost", "old-owner", stop_event, lock_lost)

    repo.refresh_lock.assert_called_once_with("wf-lost", "old-owner")
    assert lock_lost.is_set()
    orchestrator.prepare_for_shutdown.assert_called_once_with()


def test_lock_lost_before_orchestrator_registration_never_advances():
    scheduler = AutonomousScheduler()
    repo = MagicMock()
    repo.get_workflow.return_value = {"workflow_id": "wf-race", "status": "planning"}
    repo.acquire_lock.return_value = True
    repo.refresh_lock.return_value = False

    class _InlineThread:
        def __init__(self, *, target, args, **_kwargs):
            self.target = target
            self.args = args

        def start(self):
            self.target(*self.args)

        def join(self, timeout=None):
            return None

    with (
        patch("app.routes.autonomous._get_repo", return_value=repo),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousOrchestrator"
        ) as orchestrator_cls,
        patch.object(scheduler_module.threading, "Thread", _InlineThread),
        patch.object(scheduler_module, "WORKFLOW_LOCK_HEARTBEAT_SECONDS", 0),
    ):
        scheduler._advance_single("wf-race")

    orchestrator = orchestrator_cls.return_value
    orchestrator.bind_scheduler_lock.assert_called_once()
    orchestrator.prepare_for_shutdown.assert_called_once_with()
    orchestrator.advance.assert_not_called()
    repo.clear_agent_pid_if_lock_owner.assert_called_once()
    repo.update_workflow.assert_not_called()


def test_heartbeat_start_failure_still_releases_distributed_lock():
    scheduler = AutonomousScheduler()
    repo = MagicMock()
    repo.get_workflow.return_value = {"workflow_id": "wf-start", "status": "planning"}
    repo.acquire_lock.return_value = True
    heartbeat_thread = MagicMock()
    heartbeat_thread.start.side_effect = RuntimeError("thread unavailable")

    with (
        patch("app.routes.autonomous._get_repo", return_value=repo),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousOrchestrator"
        ) as orchestrator_cls,
        patch.object(scheduler_module.threading, "Thread", return_value=heartbeat_thread),
    ):
        scheduler._advance_single("wf-start")

    orchestrator_cls.assert_not_called()
    heartbeat_thread.join.assert_not_called()
    owner = repo.acquire_lock.call_args.args[1]
    repo.release_lock.assert_called_once_with("wf-start", owner)


def test_phase_commit_is_fenced_after_scheduler_lock_loss():
    orchestrator = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orchestrator.repo = MagicMock()
    orchestrator._workflow_id = "wf-lost"
    orchestrator._scheduler_lock_owner = "old-owner"
    orchestrator._scheduler_lock_lost = threading.Event()
    orchestrator._scheduler_lock_lost.set()
    orchestrator._shutdown_requested = threading.Event()
    orchestrator._create_milestone = MagicMock()

    with pytest.raises(RuntimeError, match="Distributed workflow lock was lost"):
        orchestrator._commit_phase_result(
            PhaseResult.completed(next_phase="completed", milestone_events=[{"phase": "x"}])
        )

    orchestrator.repo.update_workflow.assert_not_called()
    orchestrator._create_milestone.assert_not_called()


def test_old_owner_cannot_clear_replacement_agent_pid(tmp_path):
    db_path = str(tmp_path / "agent-pid-fence.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE autonomous_workflows (
            workflow_id TEXT PRIMARY KEY,
            locked_by TEXT,
            agent_pid INTEGER,
            agent_session_id TEXT,
            updated_at TEXT
        )""")
    conn.execute(
        "INSERT INTO autonomous_workflows VALUES (?, ?, ?, ?, ?)",
        ("wf-new", "new-owner", 999, "new-session", ""),
    )
    conn.commit()
    conn.close()
    repo = AutonomousWorkflowRepository(_SQLiteDB(db_path))

    with patch("app.repositories.database.adapt_sql", side_effect=lambda sql: sql):
        assert repo.clear_agent_pid_if_lock_owner("wf-new", "old-owner") is False

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT locked_by, agent_pid, agent_session_id FROM autonomous_workflows"
    ).fetchone()
    conn.close()
    assert row == ("new-owner", 999, "new-session")


def test_workflow_update_requires_current_lock_owner(tmp_path):
    db_path = str(tmp_path / "workflow-update-fence.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE autonomous_workflows (
            workflow_id TEXT PRIMARY KEY,
            status TEXT,
            locked_by TEXT,
            updated_at TEXT
        )""")
    conn.execute(
        "INSERT INTO autonomous_workflows VALUES (?, ?, ?, ?)",
        ("wf-fenced", "verification_pending", "new-owner", ""),
    )
    conn.commit()
    conn.close()
    repo = AutonomousWorkflowRepository(_SQLiteDB(db_path))

    with patch("app.repositories.autonomous_repo.adapt_sql", side_effect=lambda sql: sql):
        assert (
            repo.update_workflow(
                "wf-fenced", {"status": "completed"}, required_lock_owner="old-owner"
            )
            is None
        )
        updated = repo.update_workflow(
            "wf-fenced", {"status": "paused"}, required_lock_owner="new-owner"
        )

    assert updated is not None
    assert updated["status"] == "paused"


def test_issue_closure_combines_closed_at_with_latest_timeline_actor():
    gh = GitHubOps("/repo")
    gh._owner_repo_resolved = True
    gh._owner_repo = "open-ace/open-ace"
    gh._repo_slug = "open-ace/open-ace"
    gh._run_gh = MagicMock(
        side_effect=[
            _result(stdout='{"state":"CLOSED","closedAt":"2026-08-08T00:00:00Z"}'),
            _result(
                stdout=(
                    '{"closed_at":"2026-08-07T00:00:00Z","closer_login":"first"}\n'
                    '{"closed_at":"2026-08-08T00:00:00Z","closer_login":"open-ace-bot"}\n'
                )
            ),
        ]
    )

    assert gh.get_issue_closure(42) == {
        "closed_at": "2026-08-08T00:00:00Z",
        "closer_login": "open-ace-bot",
    }


def test_issue_closure_rejects_timeline_actor_from_a_different_close_event():
    gh = GitHubOps("/repo")
    gh._owner_repo_resolved = True
    gh._owner_repo = "open-ace/open-ace"
    gh._repo_slug = "open-ace/open-ace"
    gh._run_gh = MagicMock(
        side_effect=[
            _result(stdout='{"state":"CLOSED","closedAt":"2026-08-08T00:00:00Z"}'),
            _result(
                stdout=('{"closed_at":"2026-08-07T00:00:00Z","closer_login":"open-ace-bot"}\n')
            ),
        ]
    )

    assert gh.get_issue_closure(42) is None


def test_issue_open_check_accepts_github_uppercase_state():
    orchestrator = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    gh = MagicMock()
    gh.get_issue.return_value = {"state": "OPEN"}
    orchestrator._get_gh = MagicMock(return_value=gh)

    assert orchestrator._issue_is_open(42) is True
