"""Unit tests for AutonomousScheduler."""

import threading
from unittest.mock import MagicMock, patch

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
        assert s._active_count == 0


class TestSchedulerStartStop:
    """Tests for start/stop lifecycle."""

    def setup_method(self):
        AutonomousScheduler._instance = None

    def test_stop_sets_event(self):
        scheduler = AutonomousScheduler.instance()
        scheduler._stop_event.clear()
        scheduler.stop()
        assert scheduler._stop_event.is_set()

    def test_start_creates_daemon_thread(self):
        scheduler = AutonomousScheduler()
        # Mock _run_loop to avoid actual loop
        with patch.object(scheduler, "_run_loop"):
            scheduler.start()
            try:
                assert scheduler._thread is not None
                assert scheduler._thread.daemon is True
                assert scheduler._thread.name == "autonomous-scheduler"
            finally:
                scheduler.stop()

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

    def test_processes_active_workflows(self):
        scheduler = AutonomousScheduler()

        mock_repo = MagicMock()
        mock_repo.get_active_workflows.return_value = [
            {"workflow_id": "wf-1", "status": "pending"},
            {"workflow_id": "wf-2", "status": "planning"},
        ]

        with patch(
            "app.repositories.autonomous_repo.AutonomousWorkflowRepository",
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

    def test_skips_paused_workflows(self):
        scheduler = AutonomousScheduler()

        mock_repo = MagicMock()
        mock_repo.get_active_workflows.return_value = [
            {"workflow_id": "wf-1", "status": "paused"},
        ]

        with patch(
            "app.repositories.autonomous_repo.AutonomousWorkflowRepository",
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
            "app.repositories.autonomous_repo.AutonomousWorkflowRepository",
            return_value=mock_repo,
        ):
            # Should not raise
            scheduler._process_workflows()

    def test_db_error_handled(self):
        scheduler = AutonomousScheduler()

        mock_repo = MagicMock()
        mock_repo.get_active_workflows.side_effect = Exception("DB error")

        with patch(
            "app.repositories.autonomous_repo.AutonomousWorkflowRepository",
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
            "app.repositories.autonomous_repo.AutonomousWorkflowRepository",
            return_value=mock_repo,
        ):
            with patch(
                "app.modules.workspace.autonomous.orchestrator.AutonomousOrchestrator"
            ) as mock_orch_cls:
                mock_orch = MagicMock()
                mock_orch_cls.return_value = mock_orch

                scheduler._process_workflows()

                assert mock_orch.advance.call_count == MAX_CONCURRENT_WORKFLOWS

    def test_single_workflow_error_continues(self):
        """Error in one workflow should not stop others."""
        scheduler = AutonomousScheduler()

        mock_repo = MagicMock()
        mock_repo.get_active_workflows.return_value = [
            {"workflow_id": "wf-1", "status": "pending"},
            {"workflow_id": "wf-2", "status": "planning"},
        ]

        with patch(
            "app.repositories.autonomous_repo.AutonomousWorkflowRepository",
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
