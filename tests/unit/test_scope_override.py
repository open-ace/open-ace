"""Per-workflow autonomous change-scope override (#2309).

The global ``MAX_AUTONOMOUS_CHANGED_FILES=60`` cap (orchestrator.py:916) is a
safety rail against runaway rounds, but large legit issues (e.g. #2306's 412
tests/e2e false-positive fixes) exceed it in one round. Rather than weaken the
global bound, each workflow can carry ``max_changed_files_override``; the scope
guard honors it when set, falling back to the global otherwise. ``retry`` accepts
the override so a user can bump it on failure without re-creating the workflow.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.orchestrator import (
    MAX_AUTONOMOUS_CHANGED_FILES,
    AutonomousOrchestrator,
)

pytestmark = [pytest.mark.regression, pytest.mark.issue(2309)]


def _make_orchestrator():
    with (
        patch("app.modules.workspace.autonomous.orchestrator.Database"),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
        ) as mock_repo_cls,
    ):
        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo
        o = AutonomousOrchestrator("wf-2309abcd")
        o.repo = mock_repo
    o._update_workflow = MagicMock()
    return o


# ── _scope_violation: honors a passed limit, falls back to global ─────────


def test_scope_violation_uses_passed_limit():
    """A passed limit overrides the global so a workflow can widen its cap."""
    files = [f"file_{i}.py" for i in range(100)]
    # 100 files: exceeds the global 60, but under the passed limit 150 → no violation.
    assert AutonomousOrchestrator._scope_violation(files, limit=150) == ""


def test_scope_violation_falls_back_to_global_when_no_limit():
    """limit=None → use the global MAX_AUTONOMOUS_CHANGED_FILES."""
    files = [f"file_{i}.py" for i in range(MAX_AUTONOMOUS_CHANGED_FILES + 5)]
    msg = AutonomousOrchestrator._scope_violation(files, limit=None)
    assert "scope exceeded" in msg.lower()
    assert f"limit {MAX_AUTONOMOUS_CHANGED_FILES}" in msg


def test_scope_violation_default_arg_is_global():
    """Calling without limit (legacy callers) keeps the historical global behavior."""
    files = [f"file_{i}.py" for i in range(MAX_AUTONOMOUS_CHANGED_FILES + 1)]
    msg = AutonomousOrchestrator._scope_violation(files)
    assert "scope exceeded" in msg.lower()


def test_scope_violation_non_positive_limit_falls_back_to_global():
    """A non-positive override (e.g. persisted out-of-band) must never silently
    disable the cap — fall back to the global bound, not 'no limit'."""
    files = [f"file_{i}.py" for i in range(MAX_AUTONOMOUS_CHANGED_FILES + 1)]
    for bad in (-1, 0):
        msg = AutonomousOrchestrator._scope_violation(files, limit=bad)
        assert "scope exceeded" in msg.lower(), f"limit={bad} should fall back to global"
        assert f"limit {MAX_AUTONOMOUS_CHANGED_FILES}" in msg


# ── _validate_autonomous_change_scope: reads wf["max_changed_files_override"] ─


def test_validate_scope_honors_workflow_override():
    """A workflow with max_changed_files_override=200 admits 150 files (which the
    global 60 would reject)."""
    o = _make_orchestrator()
    gh = MagicMock()
    gh._is_merge_commit.return_value = False  # skip merge-base path
    gh.get_changed_files.return_value = [f"f{i}.py" for i in range(150)]
    wf = {"base_commit_sha": "base-commit", "max_changed_files_override": 200}

    assert o._validate_autonomous_change_scope(gh, wf, "base-commit", "after-commit") == ""


def test_validate_scope_falls_back_to_global_without_override():
    """Without an override, 150 files exceeds the global 60 → violation."""
    o = _make_orchestrator()
    gh = MagicMock()
    gh._is_merge_commit.return_value = False
    gh.get_changed_files.return_value = [f"f{i}.py" for i in range(150)]
    wf = {"base_commit_sha": "base-commit", "max_changed_files_override": None}

    msg = o._validate_autonomous_change_scope(gh, wf, "base-commit", "after-commit")
    assert "scope exceeded" in msg.lower()
    assert f"limit {MAX_AUTONOMOUS_CHANGED_FILES}" in msg


# ── retry endpoint: persists max_changed_files_override ───────────────────


def _run_retry(body):
    """Invoke retry_workflow.__wrapped__ with a fresh mock repo (bypassing the
    module-level _get_repo singleton so each test sees its own mock). Returns
    the dict passed to update_workflow."""
    mock_repo = MagicMock()
    mock_repo.get_workflow.return_value = {
        "workflow_id": "wf-2309abcd",
        "user_id": 5,
        "status": "failed",
        "current_phase": "development",
        "retry_count": 0,
        "preferred_worktree_path": "/srv/repo/.worktrees/wf-2309abcd",
        "branch_strategy": "worktree",
        "max_changed_files_override": None,
    }
    with (
        patch("app.routes.autonomous._get_repo", return_value=mock_repo),
        patch("app.services.autonomous_scheduler.AutonomousScheduler") as mock_sched_cls,
    ):
        mock_sched_cls.instance.return_value.clear_in_progress = MagicMock()
        from flask import Flask, g

        from app.routes.autonomous import autonomous_bp, retry_workflow

        app = Flask(__name__)
        app.register_blueprint(autonomous_bp, url_prefix="/autonomous")
        kwargs = {"method": "POST"}
        if body is not None:
            kwargs["json"] = body
        with app.test_request_context("/autonomous/workflows/wf-2309abcd/retry", **kwargs):
            g.user_id = 5
            g.user_role = "user"
            resp = retry_workflow.__wrapped__("wf-2309abcd")
            status = resp.status_code if hasattr(resp, "status_code") else resp[1]
            if status != 200:
                raise AssertionError(f"expected 200, got {status}")
    return mock_repo.update_workflow.call_args.args[1]


def test_retry_persists_max_changed_files_override():
    """POST /workflows/<id>/retry with {max_changed_files_override: N} persists N
    on the workflow alongside the normal failure reset."""
    updates = _run_retry({"max_changed_files_override": 200})
    assert updates.get("max_changed_files_override") == 200
    assert updates.get("status")  # status reset too
    assert updates.get("error_message") == ""


def test_retry_without_override_does_not_set_field():
    """A plain retry (no body) must not touch max_changed_files_override."""
    updates = _run_retry(None)
    assert "max_changed_files_override" not in updates


def test_retry_rejects_non_positive_override():
    """A zero/negative override is rejected with 400 (no silent fallback)."""
    with pytest.raises(AssertionError, match="expected 200, got 400"):
        _run_retry({"max_changed_files_override": 0})
