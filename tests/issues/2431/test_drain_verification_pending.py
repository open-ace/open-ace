"""Issue #2431: parked verification rows drain safely and the verifier is controllable.

The scheduler query and phase/status mapping are an atomic change.  Once the
row is selected, an explicit flag-off guard must run before it touches workflow
context or verifier dependencies and complete the workflow.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

from app.modules.workspace.autonomous.models import AutonomousWorkflow
from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator
from app.modules.workspace.autonomous.phases import acceptance_verification as av
from app.repositories.autonomous_repo import AutonomousWorkflowRepository
from app.routes.autonomous import PHASE_TO_STATUS
from app.services.autonomous_scheduler import ACTIVE_WORKFLOW_STATUSES
from app.utils import config as config_module


class _SQLiteDB:
    def __init__(self, path: str):
        self.path = path

    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def fetch_all(self, sql: str, params=()):
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def fetch_one(self, sql: str, params=()):
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row is not None else None


def _repository_with_parked_row(tmp_path) -> AutonomousWorkflowRepository:
    db_path = str(tmp_path / "parked.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE autonomous_workflows (
                workflow_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        conn.executemany(
            "INSERT INTO autonomous_workflows VALUES (?, ?, ?, ?)",
            [
                ("parked", 7, "verification_pending", "2026-08-08T00:00:00Z"),
                ("done", 7, "completed", "2026-08-08T00:01:00Z"),
            ],
        )
    return AutonomousWorkflowRepository(_SQLiteDB(db_path))


def test_parked_row_is_selected_and_counts_against_user_limit(tmp_path):
    repo = _repository_with_parked_row(tmp_path)

    assert [row["workflow_id"] for row in repo.get_active_workflows()] == ["parked"]
    assert repo.count_active_workflows_by_user(7) == 1


def test_phase_mapping_and_active_sets_move_together():
    assert PHASE_TO_STATUS["acceptance_verification"] == "verification_pending"
    assert "verification_pending" in ACTIVE_WORKFLOW_STATUSES
    assert "verification_pending" in AutonomousWorkflow.ACTIVE_STATUSES


def test_acceptance_verification_defaults_on_after_hardening():
    with patch.object(config_module, "get_config_value", return_value=True) as get_value:
        assert config_module.is_acceptance_verification_enabled() is True

    get_value.assert_called_once_with("autonomous", "acceptance_verification_enabled", True)


def test_acceptance_verification_can_be_explicitly_disabled():
    with patch.object(config_module, "get_config_value", return_value=False):
        assert config_module.is_acceptance_verification_enabled() is False


def test_acceptance_verification_rejects_truthy_non_boolean_values():
    with patch.object(config_module, "get_config_value", return_value="false"):
        assert config_module.is_acceptance_verification_enabled() is False


def test_flag_off_guard_runs_before_context_or_dependency_access():
    """Broken sentinels prove the guard is the handler's first operation."""
    with patch.object(av, "is_acceptance_verification_enabled", return_value=False):
        result = av.handle(object(), object())

    assert result.outcome == "completed"
    assert result.next_phase == "completed"
    assert result.workflow_patch == {}


def test_flag_off_result_commits_parked_row_to_completed():
    """Exercise the real commit entrypoint used after the handler returns."""
    with patch.object(av, "is_acceptance_verification_enabled", return_value=False):
        result = av.handle(object(), object())

    orchestrator = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orchestrator._workflow_id = "parked"
    orchestrator.repo = MagicMock()
    orchestrator._emit = MagicMock()
    orchestrator._commit_phase_result(result)

    update = orchestrator.repo.update_workflow.call_args.args[1]
    assert update["status"] == "completed"
    assert update["current_phase"] == "acceptance_verification"
    assert "completed_at" in update


def test_flag_on_keeps_the_existing_verifier_path():
    ctx = MagicMock()
    ctx.workflow = {"verification_status": "confirmed"}
    deps = MagicMock()

    with patch.object(av, "is_acceptance_verification_enabled", return_value=True):
        result = av.handle(ctx, deps)

    assert result.outcome == "completed"
    assert result.next_phase == "completed"
