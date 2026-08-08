"""Regression coverage for the verifier hardening that enables #2335 safely."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from app.modules.workspace.autonomous.acceptance_gates import (
    legacy_pattern_gate,
    run_mechanical_gates,
)
from app.modules.workspace.autonomous.acceptance_snapshot import AcceptanceSnapshot
from app.modules.workspace.autonomous.evidence import Verdict
from app.modules.workspace.autonomous.github_ops import GitHubOps
from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator
from app.modules.workspace.autonomous.phase_contract import WorkflowContext
from app.modules.workspace.autonomous.phases import acceptance_verification as av


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

    verdicts = av.run_scope_gate(gh, ["app/x.py"], "base", "merge")

    assert len(verdicts) == 1
    assert verdicts[0].verdict is Verdict.INDETERMINATE
    assert verdicts[0].item == "scope:changed-files"


def test_broad_legacy_regex_is_retired_from_production_aggregate():
    gh = MagicMock()
    gh.get_changed_files.return_value = ["app/views/tenant.py"]

    def reader(_path):
        return "if current.tenant_id == tenant_id:\n    return allowed\n"

    legacy = legacy_pattern_gate(gh, AcceptanceSnapshot(), "base", "merge", read_file=reader)
    aggregate = run_mechanical_gates(gh, AcceptanceSnapshot(), "base", "merge", read_file=reader)

    assert any(item.verdict is Verdict.REJECTED for item in legacy)
    assert all(item.verdict is not Verdict.REJECTED for item in aggregate)


def test_explicit_verifier_infra_error_forces_indeterminate_pause():
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

    assert result.outcome == "pause"
    assert result.workflow_patch["verification_status"] == "indeterminate"
    report = json.loads(result.workflow_patch["verification_report"])
    assert report["verifier"][0]["item"] == "verifier:infrastructure"
    assert "timed out" in report["verifier"][0]["evidence"][0]["note"]


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


def test_issue_comment_exact_body_is_idempotent():
    gh = GitHubOps("/repo")
    gh.list_issue_comments = MagicMock(return_value=[{"id": 7, "body": "same report"}])
    gh._run_gh = MagicMock()

    result = gh.add_issue_comment(42, "same report")

    assert result == {"number": 42, "id": 7, "deduplicated": True}
    gh._run_gh.assert_not_called()


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
