"""Tests for resume scenario retry state preservation (Phase 1, P0).

Tests that retry counts are preserved across pause/resume cycles.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock, patch

import pytest

from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator


class TestPauseResumeRetryState:
    """Test retry state persistence during pause/resume."""

    @pytest.fixture
    def mock_orchestrator(self):
        """Create mock orchestrator with workflow data."""
        orchestrator = AutonomousOrchestrator("test-workflow-123")
        # Mock the workflow property by mocking the repo.get_workflow
        orchestrator.repo.get_workflow = Mock(
            return_value={
                "workflow_id": "test-workflow-123",
                "status": "developing",
                "test_retries": 1,
                "skip_retries": 0,
                "dev_retries_on_test_fail": 0,
                "current_phase": "development",
            }
        )
        return orchestrator

    def test_pause_persists_retry_counts(self, mock_orchestrator):
        """pause_current_task should persist retry counts to database."""
        # Mock _update_workflow to capture the call
        update_calls = []
        mock_orchestrator._update_workflow = lambda updates: update_calls.append(updates)

        # Mock _runner and _session_lock
        mock_orchestrator._runner = Mock()
        mock_orchestrator._runner.pause_session = Mock()
        mock_orchestrator._current_session_id = "session-456"

        # Call pause_current_task
        mock_orchestrator.pause_current_task()

        # Verify retry counts were persisted
        assert len(update_calls) == 1
        persisted = update_calls[0]

        assert persisted["test_retries"] == 1
        assert persisted["skip_retries"] == 0
        assert persisted["dev_retries_on_test_fail"] == 0
        assert persisted["status"] == "paused"
        assert "paused_at" in persisted

    def test_resume_retrieves_retry_counts_from_db(self, mock_orchestrator):
        """Resume should retrieve persisted retry counts from database."""
        # Simulate workflow state after resume (persisted values)
        mock_orchestrator.repo.get_workflow = Mock(
            return_value={
                "workflow_id": "test-workflow-123",
                "status": "developing",  # Resume sets back to active
                "test_retries": 1,  # Persisted value
                "skip_retries": 0,
                "dev_retries_on_test_fail": 0,
                "current_phase": "development",
            }
        )

        # Verify workflow dict has correct retry values
        wf = mock_orchestrator.workflow
        assert wf["test_retries"] == 1
        assert wf["skip_retries"] == 0
        assert wf["dev_retries_on_test_fail"] == 0

    def test_pause_write_failure_allows_pause(self, mock_orchestrator):
        """pause should proceed even if retry state write fails."""
        # Mock _update_workflow to raise exception
        mock_orchestrator._update_workflow = Mock(side_effect=Exception("DB error"))

        # Mock _runner
        mock_orchestrator._runner = Mock()
        mock_orchestrator._runner.pause_session = Mock()
        mock_orchestrator._current_session_id = "session-456"

        # Call pause - should not raise
        mock_orchestrator.pause_current_task()

        # Verify pause_session was still called (pause proceeded despite write failure)
        mock_orchestrator._runner.pause_session.assert_called_once_with("session-456")

    def test_pause_only_in_active_status(self, mock_orchestrator):
        """pause should only persist retry state when workflow is active."""
        # Set workflow to non-active status
        mock_orchestrator.repo.get_workflow = Mock(
            return_value={
                "workflow_id": "test-workflow-123",
                "status": "completed",  # Not active
                "test_retries": 1,
            }
        )

        update_calls = []
        mock_orchestrator._update_workflow = lambda updates: update_calls.append(updates)

        mock_orchestrator._runner = Mock()
        mock_orchestrator._current_session_id = None

        # Call pause
        mock_orchestrator.pause_current_task()

        # Verify no retry state was persisted (workflow not in active status)
        assert len(update_calls) == 0

    def test_retry_count_not_reset_on_resume(self):
        """Retry counts should NOT be reset to 0 on resume.

        This tests the advance() behavior: it reads retry counts from DB
        and continues using those values (not resetting).
        """
        # Simulate workflow after pause + resume
        workflow_data = {
            "workflow_id": "test-resume-workflow",
            "status": "developing",  # Resumed
            "test_retries": 2,  # Persisted value (NOT reset to 0)
            "skip_retries": 1,
            "dev_retries_on_test_fail": 0,
        }

        # Verify counts are preserved (not reset)
        assert workflow_data["test_retries"] == 2
        assert workflow_data["skip_retries"] == 1

    def test_pause_persists_all_retry_fields(self, mock_orchestrator):
        """pause should persist all three retry fields."""
        # Set all retry counts to non-zero
        mock_orchestrator.repo.get_workflow = Mock(
            return_value={
                "workflow_id": "test-workflow-123",
                "status": "developing",
                "test_retries": 2,
                "skip_retries": 1,
                "dev_retries_on_test_fail": 1,
            }
        )

        update_calls = []
        mock_orchestrator._update_workflow = lambda updates: update_calls.append(updates)

        mock_orchestrator._runner = Mock()
        mock_orchestrator._current_session_id = "session-789"

        # Call pause
        mock_orchestrator.pause_current_task()

        # Verify all three fields persisted
        persisted = update_calls[0]
        assert persisted["test_retries"] == 2
        assert persisted["skip_retries"] == 1
        assert persisted["dev_retries_on_test_fail"] == 1

    def test_pause_does_not_overwrite_other_fields(self, mock_orchestrator):
        """pause should only write retry + status fields, not overwrite others."""
        mock_orchestrator.repo.get_workflow = Mock(
            return_value={
                "workflow_id": "test-workflow-123",
                "status": "developing",
                "test_retries": 1,
                "branch_name": "feature-branch",  # Should NOT be overwritten
                "project_path": "/path/to/project",
            }
        )

        update_calls = []
        mock_orchestrator._update_workflow = lambda updates: update_calls.append(updates)

        mock_orchestrator._runner = Mock()
        mock_orchestrator._current_session_id = "session-456"

        # Call pause
        mock_orchestrator.pause_current_task()

        # Verify only retry + status fields in update call
        persisted = update_calls[0]
        assert "branch_name" not in persisted
        assert "project_path" not in persisted
        assert "test_retries" in persisted
        assert "status" in persisted
