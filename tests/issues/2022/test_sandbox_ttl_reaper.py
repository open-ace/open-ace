"""#2022 P6.6 — periodic TTL reaper for stuck 'running' sandbox rows.

Catches live-process orphans the startup reconcile misses: a workflow whose
``sandbox_state`` still claims ``running`` but which the scheduler is not
actively advancing (not in ``_in_progress_ids``) and has not been touched for
longer than the TTL. The double guard (not-driven + stale) avoids reaping a
long task in flight; paused workflows are left alone (sandbox retained for
resume).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.services.autonomous_scheduler import AutonomousScheduler


def _make_scheduler(in_progress=()) -> AutonomousScheduler:
    sched = AutonomousScheduler()
    sched.remote_session_manager = MagicMock()
    sched._in_progress_ids = set(in_progress)
    return sched


def _fake_repo(rows):
    repo = MagicMock()
    repo.get_workflows_with_active_sandbox.return_value = rows
    return repo


NOW = 1_700_000_000.0


def test_reap_destroys_stale_remote_running_orphan_not_driven() -> None:
    sched = _make_scheduler(in_progress=())
    repo = _fake_repo(
        [
            {
                "workflow_id": "wf-stale",
                "status": "developing",
                "sandbox_state": "running",
                "sandbox_provider": "remote_machine",
                "sandbox_id": "sb-1",
                "sandbox_remote_session_id": "rs-1",
                "updated_at": NOW - 8000,  # well past TTL (7200)
            }
        ]
    )
    sched._reap_stale_running_sandboxes(repo=repo, now_epoch=NOW)

    sched.remote_session_manager.stop_session.assert_called_once_with("rs-1")
    wid, patch = repo.update_workflow.call_args.args
    assert wid == "wf-stale"
    assert patch["sandbox_state"] == "destroyed"
    assert patch["sandbox_remote_session_id"] is None


def test_reap_skips_workflow_being_driven() -> None:
    # The scheduler is actively advancing this workflow → leave its sandbox alone
    # even if updated_at looks stale (the in-progress guard wins).
    sched = _make_scheduler(in_progress={"wf-driven"})
    repo = _fake_repo(
        [
            {
                "workflow_id": "wf-driven",
                "status": "developing",
                "sandbox_state": "running",
                "sandbox_provider": "remote_machine",
                "sandbox_id": "sb",
                "sandbox_remote_session_id": "rs",
                "updated_at": NOW - 8000,
            }
        ]
    )
    sched._reap_stale_running_sandboxes(repo=repo, now_epoch=NOW)

    sched.remote_session_manager.stop_session.assert_not_called()
    repo.update_workflow.assert_not_called()


def test_reap_skips_fresh_running_row() -> None:
    # Recently-touched 'running' row — the task may genuinely still be in flight.
    sched = _make_scheduler(in_progress=())
    repo = _fake_repo(
        [
            {
                "workflow_id": "wf-fresh",
                "status": "developing",
                "sandbox_state": "running",
                "sandbox_provider": "remote_machine",
                "sandbox_id": "sb",
                "sandbox_remote_session_id": "rs",
                "updated_at": NOW - 10,  # fresh
            }
        ]
    )
    sched._reap_stale_running_sandboxes(repo=repo, now_epoch=NOW)

    sched.remote_session_manager.stop_session.assert_not_called()
    repo.update_workflow.assert_not_called()


def test_reap_skips_paused_workflow() -> None:
    # Paused is intentional — keep the sandbox for a later resume.
    sched = _make_scheduler(in_progress=())
    repo = _fake_repo(
        [
            {
                "workflow_id": "wf-paused",
                "status": "paused",
                "sandbox_state": "running",
                "sandbox_provider": "remote_machine",
                "sandbox_id": "sb",
                "sandbox_remote_session_id": "rs",
                "updated_at": NOW - 8000,
            }
        ]
    )
    sched._reap_stale_running_sandboxes(repo=repo, now_epoch=NOW)

    sched.remote_session_manager.stop_session.assert_not_called()
    repo.update_workflow.assert_not_called()


def test_reap_skips_non_running_states() -> None:
    # 'created'/'paused' sandbox_state are not 'running' — not the reaper's target
    # (startup reconcile owns the full active set).
    sched = _make_scheduler(in_progress=())
    repo = _fake_repo(
        [
            {
                "workflow_id": "wf-created",
                "status": "developing",
                "sandbox_state": "created",
                "sandbox_provider": "remote_machine",
                "sandbox_id": "sb",
                "sandbox_remote_session_id": "rs",
                "updated_at": NOW - 8000,
            }
        ]
    )
    sched._reap_stale_running_sandboxes(repo=repo, now_epoch=NOW)

    sched.remote_session_manager.stop_session.assert_not_called()
    repo.update_workflow.assert_not_called()


def test_reap_local_orphan_db_resets_only() -> None:
    # Local 'running' orphan: the proc died, so no external session to stop.
    sched = _make_scheduler(in_progress=())
    repo = _fake_repo(
        [
            {
                "workflow_id": "wf-local",
                "status": "developing",
                "sandbox_state": "running",
                "sandbox_provider": "legacy_posix",
                "sandbox_id": "sb",
                "sandbox_remote_session_id": None,
                "updated_at": NOW - 8000,
            }
        ]
    )
    sched._reap_stale_running_sandboxes(repo=repo, now_epoch=NOW)

    sched.remote_session_manager.stop_session.assert_not_called()
    patch = repo.update_workflow.call_args.args[1]
    assert patch["sandbox_state"] == "destroyed"


def test_reap_destroy_failure_continues() -> None:
    # stop_session raises on the first row; the sweep must still DB-reset it and
    # continue to the second row.
    sched = _make_scheduler(in_progress=())
    sched.remote_session_manager.stop_session.side_effect = RuntimeError("rpc down")
    repo = _fake_repo(
        [
            {
                "workflow_id": "wf-a",
                "status": "developing",
                "sandbox_state": "running",
                "sandbox_provider": "remote_machine",
                "sandbox_id": "sb-a",
                "sandbox_remote_session_id": "rs-a",
                "updated_at": NOW - 8000,
            },
            {
                "workflow_id": "wf-b",
                "status": "developing",
                "sandbox_state": "running",
                "sandbox_provider": "remote_machine",
                "sandbox_id": "sb-b",
                "sandbox_remote_session_id": "rs-b",
                "updated_at": NOW - 8000,
            },
        ]
    )
    sched._reap_stale_running_sandboxes(repo=repo, now_epoch=NOW)  # no raise

    assert repo.update_workflow.call_count == 2  # both reset despite stop failure
