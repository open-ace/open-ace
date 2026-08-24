"""#2335 S6: the confirmed-terminal ``current_phase`` rests at the last phase.

``_commit_phase_result`` previously defaulted the persisted ``current_phase``
to the hardcoded literal ``"merge"`` when a handler returned
``PhaseResult.completed(next_phase="completed")`` without carrying
``current_phase`` in ``workflow_patch``. With the acceptance_verification
phase in place, a confirmed workflow should rest at
``current_phase="acceptance_verification"`` (the last entry in ``PHASE_ORDER``),
consistent with ``_COMPLETED_TERMINAL_PHASES`` and the phase-wiring docstring.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.orchestrator import PHASE_ORDER, AutonomousOrchestrator
from app.modules.workspace.autonomous.phase_contract import PhaseResult

pytestmark = [
    pytest.mark.regression,
    pytest.mark.issue(2335),
    pytest.mark.usefixtures("_enable_acceptance_verification"),
]


def _make_orchestrator(workflow: dict) -> AutonomousOrchestrator:
    with (
        patch("app.modules.workspace.autonomous.orchestrator.Database"),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
        ) as mock_repo_cls,
        patch("app.modules.workspace.session_manager.SessionManager"),
        patch("app.modules.workspace.autonomous.agent_runner.AutonomousAgentRunner"),
    ):
        mock_repo = MagicMock()
        mock_repo.get_workflow.return_value = dict(workflow)
        mock_repo_cls.return_value = mock_repo
        o = AutonomousOrchestrator(workflow.get("workflow_id", "wf-s6"))
        o.repo = mock_repo
    return o


def _active_workflow(phase: str = "acceptance_verification") -> dict:
    return {
        "workflow_id": "wf-s6",
        "status": "verification_pending",
        "current_phase": phase,
        "project_path": "/srv/open-ace",
        "worktree_path": "/srv/open-ace/.worktrees/wf-s6",
        "branch_name": "auto-dev/wf-s6",
        "branch_strategy": "worktree",
        "workspace_type": "local",
        "dev_round": 1,
        "current_round": 0,
        "transient_retry_count": 0,
    }


def test_confirmed_acceptance_rests_at_acceptance_verification():
    """The default terminal current_phase derives from PHASE_ORDER[-1]."""
    assert PHASE_ORDER[-1] == "acceptance_verification"
    o = _make_orchestrator(_active_workflow())

    # A confirmed acceptance_verification handler signals completion without
    # carrying current_phase in the patch (the handler relies on the default).
    o._commit_phase_result(
        PhaseResult.completed(
            next_phase="completed",
            workflow_patch={"verification_status": "confirmed"},
        )
    )

    last_updates = o.repo.update_workflow.call_args_list[-1].args[1]
    assert last_updates["current_phase"] == "acceptance_verification"
    assert last_updates["status"] == "completed"
    assert "completed_at" in last_updates


def test_idempotent_reentry_confirmed_stays_at_acceptance_verification():
    """Re-entering an already-confirmed workflow is a terminal no-op that still
    rests at acceptance_verification (mirrors the handler's confirmed no-op)."""
    o = _make_orchestrator(_active_workflow())
    o._commit_phase_result(PhaseResult.completed(next_phase="completed"))

    last_updates = o.repo.update_workflow.call_args_list[-1].args[1]
    assert last_updates["current_phase"] == "acceptance_verification"


def test_patch_carried_current_phase_still_honoured():
    """A handler that explicitly carries current_phase in the patch wins over
    the default (the whitelist path is unchanged)."""
    o = _make_orchestrator(_active_workflow())
    o._commit_phase_result(
        PhaseResult.completed(
            next_phase="completed",
            workflow_patch={"current_phase": "completed"},
        )
    )

    last_updates = o.repo.update_workflow.call_args_list[-1].args[1]
    assert last_updates["current_phase"] == "completed"
