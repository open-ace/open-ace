"""Unit tests for AutonomousScheduler."""

import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.autonomous_scheduler import AutonomousScheduler


class TestSchedulerSingleton:
    """Tests for scheduler singleton pattern."""

    def setup_method(self):
        AutonomousScheduler._instance = None

    def test_singleton(self):
        s1 = AutonomousScheduler.instance()
        s2 = AutonomousScheduler.instance()
        assert s1 is s2

    def test_instance_creates_new(self):
        s = AutonomousScheduler.instance()
        assert s is not None
        assert s._thread is None
        assert isinstance(s._in_progress_ids, set)
        assert len(s._in_progress_ids) == 0


class TestSchedulerStartStop:
    """Tests for start/stop lifecycle."""

    def setup_method(self):
        AutonomousScheduler._instance = None

    def test_stop_sets_event(self):
        scheduler = AutonomousScheduler.instance()
        scheduler._stop_event.clear()
        scheduler.stop()
        assert scheduler._stop_event.is_set()

    def test_stop_interrupts_active_orchestrators_before_join(self):
        scheduler = AutonomousScheduler()
        orchestrator = MagicMock()
        scheduler._running_orchestrators = {"wf-1": orchestrator}
        thread = MagicMock()
        thread.is_alive.return_value = False
        scheduler._thread = thread

        scheduler.stop()

        orchestrator.prepare_for_shutdown.assert_called_once_with()
        thread.join.assert_called_once_with(timeout=20)

    def test_server_shutdown_stops_autonomous_scheduler(self):
        server_source = (Path(__file__).resolve().parents[3] / "server.py").read_text(
            encoding="utf-8"
        )

        scheduler_stop = server_source.index("AutonomousScheduler.instance().stop()")
        webui_stop = server_source.index("shutdown_webui_manager()")
        server_stop = server_source.index("server.stop()")

        assert scheduler_stop < webui_stop < server_stop

    def test_start_creates_daemon_thread(self):
        scheduler = AutonomousScheduler()
        # Patch _process_workflows so _run_loop exits immediately after one iteration
        scheduler._stop_event.set()
        scheduler.start()
        scheduler._thread.join(timeout=5)
        assert scheduler._thread is not None
        assert scheduler._thread.daemon is True
        assert scheduler._thread.name == "autonomous-scheduler"

    def test_start_skips_if_already_alive(self):
        scheduler = AutonomousScheduler()
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        scheduler._thread = mock_thread

        scheduler.start()
        # Should not create a new thread
        assert scheduler._thread is mock_thread


class TestSchedulerProcessWorkflows:
    """Tests for _process_workflows method."""

    def setup_method(self):
        AutonomousScheduler._instance = None

    @pytest.fixture(autouse=True)
    def _allow_quota(self):
        """Stub QuotaManager to allow-by-default. The runtime quota gate in
        _advance_single calls check_quota; without this stub it reaches the
        environment's default DB and fail-closed-pauses the workflow before
        advance() runs, breaking these scheduler-logic tests (which mock the
        orchestrator, not quota)."""
        mock = MagicMock()
        mock.return_value.check_quota.return_value = {"allowed": True, "reason": None}
        with patch("app.modules.governance.quota_manager.QuotaManager", mock):
            yield

    def test_processes_active_workflows(self):
        scheduler = AutonomousScheduler()

        mock_repo = MagicMock()
        mock_repo.get_active_workflows.return_value = [
            {"workflow_id": "wf-1", "status": "pending"},
            {"workflow_id": "wf-2", "status": "planning"},
        ]

        with patch(
            "app.routes.autonomous._get_repo",
            return_value=mock_repo,
        ):
            with patch(
                "app.modules.workspace.autonomous.orchestrator.AutonomousOrchestrator"
            ) as mock_orch_cls:
                mock_orch = MagicMock()
                mock_orch_cls.return_value = mock_orch

                scheduler._process_workflows()

                assert mock_orch_cls.call_count == 2
                assert mock_orch.advance.call_count == 2

    def test_worker_created_during_shutdown_never_advances(self):
        scheduler = AutonomousScheduler()
        scheduler._stop_event.set()
        mock_repo = MagicMock()
        mock_repo.get_workflow.return_value = {
            "workflow_id": "wf-race",
            "status": "planning",
        }
        mock_repo.acquire_lock.return_value = True

        with (
            patch("app.routes.autonomous._get_repo", return_value=mock_repo),
            patch(
                "app.modules.workspace.autonomous.orchestrator.AutonomousOrchestrator"
            ) as orchestrator_cls,
        ):
            orchestrator = orchestrator_cls.return_value
            scheduler._advance_single("wf-race")

        orchestrator.prepare_for_shutdown.assert_called_once_with()
        orchestrator.advance.assert_not_called()
        mock_repo.release_lock.assert_called_once()

    def test_skips_paused_workflows(self):
        scheduler = AutonomousScheduler()

        mock_repo = MagicMock()
        mock_repo.get_active_workflows.return_value = [
            {"workflow_id": "wf-1", "status": "paused"},
        ]

        with patch(
            "app.routes.autonomous._get_repo",
            return_value=mock_repo,
        ):
            with patch(
                "app.modules.workspace.autonomous.orchestrator.AutonomousOrchestrator"
            ) as mock_orch_cls:
                scheduler._process_workflows()
                mock_orch_cls.assert_not_called()

    def test_empty_workflows(self):
        scheduler = AutonomousScheduler()

        mock_repo = MagicMock()
        mock_repo.get_active_workflows.return_value = []

        with patch(
            "app.routes.autonomous._get_repo",
            return_value=mock_repo,
        ):
            # Should not raise
            scheduler._process_workflows()

    def test_db_error_handled(self):
        scheduler = AutonomousScheduler()

        mock_repo = MagicMock()
        mock_repo.get_active_workflows.side_effect = Exception("DB error")

        with patch(
            "app.routes.autonomous._get_repo",
            return_value=mock_repo,
        ):
            # Should not raise
            scheduler._process_workflows()

    def test_max_concurrency(self):
        """Should not process more than MAX_CONCURRENT_WORKFLOWS."""
        from app.services.autonomous_scheduler import MAX_CONCURRENT_WORKFLOWS

        scheduler = AutonomousScheduler()

        workflows = [{"workflow_id": f"wf-{i}", "status": "pending"} for i in range(10)]
        mock_repo = MagicMock()
        mock_repo.get_active_workflows.return_value = workflows

        with patch(
            "app.routes.autonomous._get_repo",
            return_value=mock_repo,
        ):
            with patch(
                "app.modules.workspace.autonomous.orchestrator.AutonomousOrchestrator"
            ) as mock_orch_cls:
                mock_orch = MagicMock()
                mock_orch_cls.return_value = mock_orch

                scheduler._process_workflows()

                assert mock_orch.advance.call_count == MAX_CONCURRENT_WORKFLOWS

    def test_same_poll_selects_only_one_workflow_per_batch(self):
        scheduler = AutonomousScheduler()
        mock_repo = MagicMock()
        mock_repo.get_active_workflows.return_value = [
            {
                "workflow_id": "batch-wf-1",
                "status": "planning",
                "batch_id": "batch-1",
                "worktree_path": "/wt/1",
                "branch_name": "auto/1",
            },
            {
                "workflow_id": "batch-wf-2",
                "status": "planning",
                "batch_id": "batch-1",
                "worktree_path": "/wt/2",
                "branch_name": "auto/2",
            },
            {
                "workflow_id": "independent-wf",
                "status": "planning",
                "batch_id": "batch-2",
                "worktree_path": "/wt/3",
                "branch_name": "auto/3",
            },
        ]

        with (
            patch("app.routes.autonomous._get_repo", return_value=mock_repo),
            patch.object(
                scheduler, "_advance_single", side_effect=lambda workflow_id: workflow_id
            ) as advance,
        ):
            scheduler._process_workflows()

        selected = [call.args[0] for call in advance.call_args_list]
        assert selected == ["batch-wf-1", "independent-wf"]

    def test_single_workflow_error_continues(self):
        """Error in one workflow should not stop others."""
        scheduler = AutonomousScheduler()

        mock_repo = MagicMock()
        mock_repo.get_active_workflows.return_value = [
            {"workflow_id": "wf-1", "status": "pending"},
            {"workflow_id": "wf-2", "status": "planning"},
        ]

        with patch(
            "app.routes.autonomous._get_repo",
            return_value=mock_repo,
        ):
            with patch(
                "app.modules.workspace.autonomous.orchestrator.AutonomousOrchestrator"
            ) as mock_orch_cls:
                mock_orch = MagicMock()
                mock_orch.advance.side_effect = [Exception("Boom"), None]
                mock_orch_cls.return_value = mock_orch

                # Should not raise
                scheduler._process_workflows()
                # Both should be attempted
                assert mock_orch.advance.call_count == 2

    def test_in_progress_ids_prevent_duplicate(self):
        """Workflows already in progress should not be picked up again."""
        scheduler = AutonomousScheduler()

        mock_repo = MagicMock()
        mock_repo.get_active_workflows.return_value = [
            {"workflow_id": "wf-1", "status": "pending"},
            {"workflow_id": "wf-2", "status": "planning"},
        ]

        # Mark wf-1 as already in progress
        scheduler._in_progress_ids.add("wf-1")
        # ... backed by a fresh locked_at lease: the lease-anchored reclaim
        # (c0ebbf7f) reaps in-progress memory entries with no live lease
        # behind them, so the fixture must give wf-1 one.
        mock_repo.get_active_workflows.return_value[0]["locked_at"] = datetime.now(
            timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S")

        with patch(
            "app.routes.autonomous._get_repo",
            return_value=mock_repo,
        ):
            with patch(
                "app.modules.workspace.autonomous.orchestrator.AutonomousOrchestrator"
            ) as mock_orch_cls:
                mock_orch = MagicMock()
                mock_orch_cls.return_value = mock_orch

                scheduler._process_workflows()

                # Only wf-2 should be processed (wf-1 is in progress)
                assert mock_orch.advance.call_count == 1

    def test_promotes_next_queued_workflow_after_waiting(self):
        """Queued batch workflow should start once previous sibling reaches waiting."""
        scheduler = AutonomousScheduler()
        mock_repo = MagicMock()
        mock_repo.get_queued_workflows.return_value = [
            {"workflow_id": "wf-2", "status": "queued", "batch_id": "batch-1", "batch_order": 2}
        ]
        mock_repo.list_batch_workflows.return_value = [
            {"workflow_id": "wf-1", "status": "waiting", "batch_id": "batch-1", "batch_order": 1},
            {"workflow_id": "wf-2", "status": "queued", "batch_id": "batch-1", "batch_order": 2},
        ]

        scheduler._promote_queued_workflows(mock_repo)

        mock_repo.update_workflow.assert_called_once_with("wf-2", {"status": "pending"})

    def test_does_not_promote_queued_workflow_when_previous_paused(self):
        """Queued batch workflow should remain queued when previous sibling is paused."""
        scheduler = AutonomousScheduler()
        mock_repo = MagicMock()
        mock_repo.get_queued_workflows.return_value = [
            {"workflow_id": "wf-2", "status": "queued", "batch_id": "batch-1", "batch_order": 2}
        ]
        mock_repo.list_batch_workflows.return_value = [
            {"workflow_id": "wf-1", "status": "paused", "batch_id": "batch-1", "batch_order": 1},
            {"workflow_id": "wf-2", "status": "queued", "batch_id": "batch-1", "batch_order": 2},
        ]

        scheduler._promote_queued_workflows(mock_repo)

        mock_repo.update_workflow.assert_not_called()

    def test_pending_workflows_are_prioritized_ahead_of_waiting(self, monkeypatch):
        """Execution slots should prefer pending work over wait-phase polling."""
        # Cap concurrency to 1 slot so the prioritized workflow deterministically
        # wins it (the ThreadPool's construction order is otherwise non-deterministic,
        # making called_ids[0] racy).
        monkeypatch.setattr("app.services.autonomous_scheduler.MAX_CONCURRENT_WORKFLOWS", 1)
        scheduler = AutonomousScheduler()
        mock_repo = MagicMock()
        mock_repo.get_queued_workflows.return_value = []
        mock_repo.get_active_workflows.return_value = [
            {"workflow_id": "wf-wait", "status": "waiting", "created_at": "2026-06-10 00:00:00"},
            {"workflow_id": "wf-pending", "status": "pending", "created_at": "2026-06-10 00:01:00"},
        ]

        with patch(
            "app.routes.autonomous._get_repo",
            return_value=mock_repo,
        ):
            with patch(
                "app.modules.workspace.autonomous.orchestrator.AutonomousOrchestrator"
            ) as mock_orch_cls:
                mock_orch = MagicMock()
                mock_orch_cls.return_value = mock_orch

                scheduler._process_workflows()

                called_ids = [call.args[0] for call in mock_orch_cls.call_args_list]
                assert called_ids == ["wf-pending"]


class TestNonBlockingAdvancePath:
    """Tests for the non-blocking advance submission path
    (_process_workflows(wait=False) + _reap_completed_futures).

    Regression guard for the 2026-08-20 starvation incident: _run_loop waited
    for a tens-of-minutes advance inside _process_workflows, parking
    promotion/auto-resume/reclaim/cleanup and delaying newly created batches
    by ~49 minutes (batches 280f009c / 3fc727cb). The loop now submits to a
    persistent background executor and returns; completed futures are reaped
    on later ticks."""

    def setup_method(self):
        AutonomousScheduler._instance = None

    @pytest.fixture(autouse=True)
    def _allow_quota(self):
        """Same quota stub as TestSchedulerProcessWorkflows: _advance_single
        is patched wholesale in these tests, but keep the guard so a future
        edit routing these tests through the real advance stays green."""
        mock = MagicMock()
        mock.return_value.check_quota.return_value = {"allowed": True, "reason": None}
        with patch("app.modules.governance.quota_manager.QuotaManager", mock):
            yield

    def test_wait_false_returns_before_slow_advance_finishes(self):
        """wait=False must submit the advance and return immediately — a slow
        advance (agent runs + CI polling can run tens of minutes) must not
        park the scheduler tick. The slot is registered before submission and
        the future is tracked for later reaping."""
        scheduler = AutonomousScheduler()
        started = threading.Event()
        release = threading.Event()

        def slow_advance(workflow_id):
            started.set()
            release.wait(timeout=10)
            # Mirror the real _advance_single's finally-block cleanup so the
            # slot accounting is exercised end-to-end.
            scheduler._in_progress_ids.discard(workflow_id)
            return workflow_id

        mock_repo = MagicMock()
        mock_repo.get_queued_workflows.return_value = []
        mock_repo.get_active_workflows.return_value = [
            {"workflow_id": "wf-slow", "status": "pending"}
        ]

        try:
            with (
                patch("app.routes.autonomous._get_repo", return_value=mock_repo),
                patch.object(scheduler, "_advance_single", side_effect=slow_advance),
            ):
                t0 = time.monotonic()
                scheduler._process_workflows(wait=False)
                submit_elapsed = time.monotonic() - t0

                # The submit path returned without waiting for the advance...
                assert started.wait(timeout=5), "advance never started"
                assert not release.is_set(), "submit path blocked until advance finished"
                assert submit_elapsed < 4.5, f"submit blocked for {submit_elapsed:.2f}s"
                # ...but the slot is reserved and the future is tracked.
                assert "wf-slow" in scheduler._in_progress_ids
                assert len(scheduler._in_flight_futures) == 1

            release.set()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not all(
                f.done() for f in scheduler._in_flight_futures
            ):
                time.sleep(0.01)
            scheduler._reap_completed_futures()
            assert scheduler._in_flight_futures == {}
            assert "wf-slow" not in scheduler._in_progress_ids
        finally:
            # Drain the executor so the test leaves no worker threads behind.
            scheduler._bg_executor.shutdown(wait=True) if scheduler._bg_executor else None

    def test_reap_completed_futures_logs_errors(self, caplog):
        """An exception escaping _advance_single on the background path must
        surface in the log via _reap_completed_futures — without it the error
        would vanish silently (the legacy as_completed loop logged it)."""
        scheduler = AutonomousScheduler()

        def boom(workflow_id):
            raise RuntimeError(f"boom {workflow_id}")

        mock_repo = MagicMock()
        mock_repo.get_queued_workflows.return_value = []
        mock_repo.get_active_workflows.return_value = [
            {"workflow_id": "wf-boom", "status": "pending"}
        ]

        try:
            with (
                patch("app.routes.autonomous._get_repo", return_value=mock_repo),
                patch.object(scheduler, "_advance_single", side_effect=boom),
            ):
                scheduler._process_workflows(wait=False)

            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not all(
                f.done() for f in scheduler._in_flight_futures
            ):
                time.sleep(0.01)

            with caplog.at_level(logging.ERROR, logger="app.services.autonomous_scheduler"):
                scheduler._reap_completed_futures()

            assert any(
                "wf-boom" in r.message and "boom" in r.message for r in caplog.records
            ), caplog.messages
            # Reaped: a second reap is a no-op.
            assert scheduler._in_flight_futures == {}
        finally:
            scheduler._bg_executor.shutdown(wait=True) if scheduler._bg_executor else None

    def test_stop_reaps_and_shuts_down_background_executor(self):
        """stop() must reap completed background advances (so their errors are
        logged) and release the executor without waiting for in-flight work
        (in-flight advances were already told to shut down via
        prepare_for_shutdown)."""
        scheduler = AutonomousScheduler()
        scheduler._get_bg_executor()
        executor = scheduler._bg_executor
        scheduler._stop_event.set()
        scheduler.stop()

        with pytest.raises(RuntimeError):
            executor.submit(lambda: None)

    def test_wait_true_still_blocks_until_advances_finish(self):
        """The legacy wait=True semantics (used by direct callers and relied on
        by the older unit tests) are unchanged: the call returns only after
        every submitted advance finished."""
        scheduler = AutonomousScheduler()
        finished = threading.Event()

        def quick_advance(workflow_id):
            scheduler._in_progress_ids.discard(workflow_id)
            finished.set()
            return workflow_id

        mock_repo = MagicMock()
        mock_repo.get_queued_workflows.return_value = []
        mock_repo.get_active_workflows.return_value = [
            {"workflow_id": "wf-quick", "status": "pending"}
        ]

        with (
            patch("app.routes.autonomous._get_repo", return_value=mock_repo),
            patch.object(scheduler, "_advance_single", side_effect=quick_advance),
        ):
            scheduler._process_workflows(wait=True)

        assert finished.is_set()
