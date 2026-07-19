"""Tests for CI repair no-excerpt fingerprint handling (issue #1855).

When `get_check_failure_excerpt` returns empty (old gh CLI / token /
REST-API URL-format issues), the fingerprint degrades to a name-only
`<no-excerpt>` sentinel. The "signature unchanged → give up" guard must
NOT fire in that case — it would misfire and kill workflows whose real
failure did change, because a name-only fingerprint can't tell whether the
agent's fix changed the error set. The MAX_CI_REPAIR_ATTEMPTS cap still
bounds retries.

Also covers layer-1 fix: the REST-API check-run `link` (`/runs/<id>`)
falls back to `gh run list --commit` + `gh run view --log-failed`.
"""

from unittest.mock import MagicMock, patch


def _make_workflow(**overrides):
    base = {
        "workflow_id": "wf-1855",
        "user_id": 1,
        "status": "merging",
        "current_phase": "merge",
        "ci_repair_attempts": 1,
        "last_ci_failure_signature": "",
        "last_ci_failure_head_sha": "",
        "branch_name": "auto-dev/wf-1855",
        "branch_strategy": "worktree",
        "worktree_path": "/tmp/repo",
        "preferred_worktree_path": "/tmp/repo",
        "dev_round": 1,
    }
    base.update(overrides)
    return base


def _make_orchestrator(wf_data):
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    with (
        patch("app.modules.workspace.autonomous.orchestrator.Database"),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
        ) as mock_repo_cls,
    ):
        mock_repo = MagicMock()
        mock_repo.get_workflow.return_value = wf_data
        mock_repo.list_milestones.return_value = []
        mock_repo.create_milestone.return_value = {
            "milestone_id": "ms-1",
            "workflow_id": wf_data["workflow_id"],
        }
        mock_repo.create_event.return_value = {"id": 1}
        mock_repo.update_workflow.return_value = wf_data
        mock_repo_cls.return_value = mock_repo

        orch = AutonomousOrchestrator(wf_data["workflow_id"])
        orch.repo = mock_repo
        orch.emitter = MagicMock()
    return orch, mock_repo


_FAILED_CHECKS = [
    {
        "name": "lint",
        "state": "failure",
        "bucket": "fail",
        "link": "https://github.com/open-ace/open-ace/runs/123",
    }
]


# ── Layer 2: empty-excerpt fingerprint does not misfire give-up guard ──


def test_no_excerpt_does_not_give_up_even_if_head_changed():
    """When excerpt is unavailable, the fingerprint is name-only (<no-excerpt>).
    Even if previous_sig == sig AND head_sha changed, the give-up guard must
    NOT fire — it cannot tell whether the agent's fix changed the error set.
    Reproduces #1855 where lint failed, agent fixed trailing newlines, but
    fingerprint stayed lint::<empty-hash> and the workflow was wrongly killed."""
    wf = _make_workflow(
        # Previous round also had no excerpt → both sigs are lint::<no-excerpt>
        last_ci_failure_signature="lint::<no-excerpt>",
        last_ci_failure_head_sha="sha-old",
    )
    orch, mock_repo = _make_orchestrator(wf)
    gh = MagicMock()
    gh.get_pr_head_sha.return_value = "sha-new"  # head changed (agent pushed)
    gh.get_check_failure_excerpt.return_value = ""  # excerpt unavailable
    gh.get_check_failure_excerpt  # explicit
    orch._get_gh = MagicMock(return_value=gh)
    orch._run_merge_ci_repair = MagicMock()

    orch._start_ci_repair_round(wf, 1873, _FAILED_CHECKS)

    # Must proceed to repair, NOT give up — guard skipped because <no-excerpt>.
    orch._run_merge_ci_repair.assert_called_once()
    # And the workflow stayed merging (not failed).
    last_updates = [c.args[1] for c in mock_repo.update_workflow.call_args_list]
    assert not any(
        u.get("status") == "failed" for u in last_updates
    ), "workflow wrongly failed on no-excerpt fingerprint"


def test_meaningful_fingerprint_still_gives_up_when_unchanged():
    """Sanity: when excerpt IS available and signature truly unchanged, the
    guard still fires (regression guard for the layer-2 change)."""
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    excerpt = "mypy....Failed\napp/baz.py:5 error: no-any-return\n"
    import hashlib

    expected_digest = hashlib.sha256(
        AutonomousOrchestrator._normalize_failure_excerpt(excerpt).encode()
    ).hexdigest()[:12]
    expected_fingerprint = f"lint::{expected_digest}"

    wf = _make_workflow(
        last_ci_failure_signature=expected_fingerprint,
        last_ci_failure_head_sha="sha-old",
    )
    orch, mock_repo = _make_orchestrator(wf)
    gh = MagicMock()
    gh.get_pr_head_sha.return_value = "sha-new"
    gh.get_check_failure_excerpt.return_value = excerpt
    orch._get_gh = MagicMock(return_value=gh)
    orch._run_merge_ci_repair = MagicMock()

    orch._start_ci_repair_round(wf, 1873, _FAILED_CHECKS)

    # Guard fires → no repair, workflow failed.
    orch._run_merge_ci_repair.assert_not_called()
    last_updates = [c.args[1] for c in mock_repo.update_workflow.call_args_list]
    assert any(u.get("status") == "failed" for u in last_updates)


# ── Layer 1: REST-API check-run link falls back to gh run list ─────────


def test_get_check_failure_excerpt_falls_back_to_run_list_for_rest_api_link():
    """When the check link is a REST-API check-run URL (/runs/<id>, not
    /actions/runs/<run>/job/<job>), the excerpt fetch must fall back to
    `gh run list --commit <sha>` + `gh run view --log-failed` instead of
    returning empty."""
    from app.modules.workspace.autonomous.github_ops import GitHubOps

    gh = GitHubOps.__new__(GitHubOps)

    # Simulate gh command outputs via _run_gh.
    run_list_json = '[{"databaseId": 999, "name": "lint"}]'
    log_failed_output = (
        "end-of-file-fixer................................................Failed\n"
        "- files were without new line at the end.\n"
        "black....................................................Passed\n"
    )

    def fake_run(args, check=True, **_kw):
        m = MagicMock()
        if "run" in args and "list" in args:
            m.returncode = 0
            m.stdout = run_list_json
            m.stderr = ""
        elif "run" in args and "view" in args:
            m.returncode = 0
            m.stdout = log_failed_output
            m.stderr = ""
        else:
            m.returncode = 1
            m.stdout = ""
            m.stderr = "unexpected"
        return m

    with patch.object(gh, "_run_gh", side_effect=fake_run):
        excerpt = gh.get_check_failure_excerpt(
            {
                "name": "lint",
                "link": "https://github.com/open-ace/open-ace/runs/12345678",
                "head_sha": "abc123def456",
                "bucket": "fail",
            }
        )

    # The end-of-file-fixer failure line must be surfaced.
    assert "end-of-file-fixer" in excerpt


def test_get_check_failure_excerpt_run_list_fallback_returns_empty_without_head_sha():
    """No head_sha in the check dict → can't query gh run list → return empty
    (graceful degradation, same as pre-fix behavior for non-Actions checks)."""
    from app.modules.workspace.autonomous.github_ops import GitHubOps

    gh = GitHubOps.__new__(GitHubOps)
    with patch.object(gh, "_run_gh") as mock_run:
        excerpt = gh.get_check_failure_excerpt(
            {"name": "lint", "link": "https://example.com/other-ci/42"}
        )
    assert excerpt == ""
    mock_run.assert_not_called()


# ── Mixed fingerprint: some checks have real excerpt, some don't ───────


def test_mixed_fingerprint_skips_guard_when_any_check_has_no_excerpt():
    """When a batch has multiple failing checks and at least one's excerpt is
    unavailable, the combined signature contains <no-excerpt> and the give-up
    guard must skip (treat the whole signature as non-discriminative). This
    locks the current safe behavior so a future per-check 'optimization' does
    not reintroduce the #1855 misfire.

    Scenario: check 'lint' has a real excerpt (real hash), check 'test (3.9)'
    has no excerpt (REST-API URL issue → <no-excerpt>). Previous round had the
    same mixed signature + head changed. Guard must NOT fire."""
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    excerpt_lint = "black....Failed\nwould reformat app/foo.py\n"
    import hashlib

    lint_digest = hashlib.sha256(
        AutonomousOrchestrator._normalize_failure_excerpt(excerpt_lint).encode()
    ).hexdigest()[:12]
    # Mixed signature: lint has real hash, test (3.9) has sentinel.
    mixed_signature = sorted([f"lint::{lint_digest}", "test (3.9)::<no-excerpt>"])
    mixed_signature_str = "\n".join(mixed_signature)

    wf = _make_workflow(
        last_ci_failure_signature=mixed_signature_str,
        last_ci_failure_head_sha="sha-old",
    )
    orch, mock_repo = _make_orchestrator(wf)
    gh = MagicMock()
    gh.get_pr_head_sha.return_value = "sha-new"
    # lint gets its excerpt back; test (3.9) still gets empty.
    gh.get_check_failure_excerpt.side_effect = lambda check: (
        excerpt_lint if check.get("name") == "lint" else ""
    )
    orch._get_gh = MagicMock(return_value=gh)
    orch._run_merge_ci_repair = MagicMock()

    failed_checks = [
        {"name": "lint", "state": "failure", "bucket": "fail"},
        {"name": "test (3.9)", "state": "failure", "bucket": "fail"},
    ]
    orch._start_ci_repair_round(wf, 1875, failed_checks)

    # Mixed signature → guard skipped → proceeds to repair, not failed.
    orch._run_merge_ci_repair.assert_called_once()
    last_updates = [c.args[1] for c in mock_repo.update_workflow.call_args_list]
    assert not any(
        u.get("status") == "failed" for u in last_updates
    ), "mixed fingerprint with <no-excerpt> wrongly triggered give-up"
