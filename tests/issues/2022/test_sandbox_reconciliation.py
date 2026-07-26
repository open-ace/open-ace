"""Orphan-sandbox reconciliation sweep (#2022 P2).

At startup the scheduler reconciles workflows whose ``sandbox_state`` claims an
active sandbox but whose owning process is gone (crash / restart mid-task): it
resets the state to ``destroyed`` and bumps ``sandbox_generation`` so a stale
handle minted before the restart cannot operate on a future sandbox. P2 is
state reconciliation only — real ``provider.destroy()`` resource cleanup lands
in P3 (LegacyPosixProvider).

Mock-repo driven (no DB), mirroring tests/issues/2050's reconciliation test.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.modules.workspace.autonomous.sandbox.provider import is_current_generation
from app.services.autonomous_scheduler import _reconcile_orphan_sandboxes


def _mock_repo(rows):
    repo = MagicMock()
    repo.get_workflows_with_active_sandbox.return_value = rows
    return repo


def test_restart_reconciles_orphan_sandbox_by_generation():
    # Workflow crashed mid-sandbox: sandbox_state claims running at gen 2.
    repo = _mock_repo(
        [
            {
                "workflow_id": "wf-orphan",
                "sandbox_state": "running",
                "sandbox_generation": 2,
                "sandbox_id": "sb-x",
            }
        ]
    )

    _reconcile_orphan_sandboxes(repo)

    repo.update_workflow.assert_called_once()
    wid, patch = repo.update_workflow.call_args.args
    assert wid == "wf-orphan"
    assert patch["sandbox_state"] == "destroyed"
    assert patch["sandbox_generation"] == 3  # bumped: stale-handle guard
    assert patch["sandbox_id"] is None
    assert "reconcil" in patch["sandbox_last_error"].lower()

    # The handle minted at gen 2 is now stale vs the workflow's new gen 3 —
    # a provider must reject it (P3/P4) rather than act on a future sandbox.
    assert is_current_generation(2, 3) is False
    assert is_current_generation(3, 3) is True


def test_reconcile_no_orphans_is_noop():
    repo = _mock_repo([])
    _reconcile_orphan_sandboxes(repo)
    repo.update_workflow.assert_not_called()


def test_reconcile_bumps_null_generation_to_one():
    # Defensive: a row with active state but NULL generation (shouldn't happen,
    # but the sweep must not crash on it).
    repo = _mock_repo(
        [
            {
                "workflow_id": "wf-nullgen",
                "sandbox_state": "created",
                "sandbox_generation": None,
                "sandbox_id": "sb-y",
            }
        ]
    )
    _reconcile_orphan_sandboxes(repo)
    patch = repo.update_workflow.call_args.args[1]
    assert patch["sandbox_generation"] == 1
    assert patch["sandbox_state"] == "destroyed"


def test_reconcile_handles_multiple_orphans_independently():
    repo = _mock_repo(
        [
            {
                "workflow_id": "wf-a",
                "sandbox_state": "running",
                "sandbox_generation": 1,
                "sandbox_id": "a",
            },
            {
                "workflow_id": "wf-b",
                "sandbox_state": "paused",
                "sandbox_generation": 5,
                "sandbox_id": "b",
            },
        ]
    )
    _reconcile_orphan_sandboxes(repo)

    assert repo.update_workflow.call_count == 2
    by_id = {c.args[0]: c.args[1] for c in repo.update_workflow.call_args_list}
    assert by_id["wf-a"]["sandbox_generation"] == 2
    assert by_id["wf-b"]["sandbox_generation"] == 6  # 5 -> 6
    assert all(p["sandbox_state"] == "destroyed" for p in by_id.values())


def test_reconcile_uses_injected_repo_without_constructing_default():
    # The sweep must accept an injected repo (test path) and NOT construct a
    # Database() — otherwise tests would hit a real DB.
    repo = _mock_repo([])
    _reconcile_orphan_sandboxes(repo)
    repo.get_workflows_with_active_sandbox.assert_called_once()
