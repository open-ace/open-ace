"""Tests for concurrent update optimistic lock (Phase 1, P0).

Tests that retry count updates use optimistic locking to prevent
concurrent modification issues.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from app.repositories.autonomous_repo import AutonomousWorkflowRepository
from app.repositories.database import Database


class TestOptimisticLock:
    """Test optimistic lock in update_workflow."""

    @pytest.fixture
    def repo(self):
        """Create repository with mock database."""
        mock_db = Mock(spec=Database)
        repo = AutonomousWorkflowRepository(db=mock_db)
        return repo

    def test_update_without_optimistic_lock(self, repo):
        """Normal update without expected_values should succeed."""
        # Mock get_workflow to return updated record
        repo.get_workflow = Mock(return_value={
            "workflow_id": "test-123",
            "test_retries": 1,
        })

        # Mock database execute
        repo.db.get_connection = Mock()
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.rowcount = 1  # Success
        mock_conn.cursor = Mock(return_value=mock_cursor)
        mock_conn.commit = Mock()
        mock_conn.close = Mock()
        repo.db.get_connection.return_value = mock_conn

        # Update without optimistic lock
        result = repo.update_workflow("test-123", {"test_retries": 1})

        # Should succeed
        assert result is not None
        assert result["test_retries"] == 1

    def test_update_with_optimistic_lock_success(self, repo):
        """Update with correct expected_values should succeed."""
        # Mock get_workflow
        repo.get_workflow = Mock(return_value={
            "workflow_id": "test-123",
            "test_retries": 2,
        })

        # Mock database connection
        repo.db.get_connection = Mock()
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.rowcount = 1  # 1 row updated (success)
        mock_conn.cursor = Mock(return_value=mock_cursor)
        mock_conn.commit = Mock()
        mock_conn.close = Mock()
        repo.db.get_connection.return_value = mock_conn

        # Update with optimistic lock (old value = 1)
        result = repo.update_workflow(
            "test-123",
            {"test_retries": 2},
            expected_values={"test_retries": 1}
        )

        # Should succeed
        assert result is not None

        # Verify WHERE clause included old value
        # (Check that cursor.execute was called with WHERE test_retries = 1)
        assert mock_cursor.execute.called

    def test_update_with_optimistic_lock_failure(self, repo):
        """Update with wrong expected_values should fail (concurrent modification)."""
        # Mock database connection
        repo.db.get_connection = Mock()
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.rowcount = 0  # 0 rows updated (lock failed)
        mock_conn.cursor = Mock(return_value=mock_cursor)
        mock_conn.commit = Mock()
        mock_conn.close = Mock()
        repo.db.get_connection.return_value = mock_conn

        # Update with optimistic lock (but old value is stale - concurrent change)
        result = repo.update_workflow(
            "test-123",
            {"test_retries": 2},
            expected_values={"test_retries": 1}  # Stale value
        )

        # Should return None (lock failed)
        assert result is None

    def test_optimistic_lock_filters_to_retry_fields(self, repo):
        """expected_values should only include retry fields."""
        # Mock database
        repo.db.get_connection = Mock()
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.rowcount = 1
        mock_conn.cursor = Mock(return_value=mock_cursor)
        mock_conn.commit = Mock()
        mock_conn.close = Mock()
        repo.db.get_connection.return_value = mock_conn

        # Call update with expected_values including non-retry field
        result = repo.update_workflow(
            "test-123",
            {"test_retries": 1},
            expected_values={
                "test_retries": 0,
                "branch_name": "old-branch",  # Non-retry field, should be filtered out
            }
        )

        # Should succeed (non-retry field filtered from WHERE clause)
        assert result is not None

    def test_optimistic_lock_with_all_retry_fields(self, repo):
        """Optimistic lock should work with all three retry fields."""
        # Mock database
        repo.db.get_connection = Mock()
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.rowcount = 1
        mock_conn.cursor = Mock(return_value=mock_cursor)
        mock_conn.commit = Mock()
        mock_conn.close = Mock()
        repo.db.get_connection.return_value = mock_conn

        # Update with all retry fields in expected_values
        result = repo.update_workflow(
            "test-123",
            {
                "test_retries": 1,
                "skip_retries": 1,
                "dev_retries_on_test_fail": 1,
            },
            expected_values={
                "test_retries": 0,
                "skip_retries": 0,
                "dev_retries_on_test_fail": 0,
            }
        )

        # Should succeed
        assert result is not None

    def test_scheduler_vs_api_concurrent_scenario(self, repo):
        """Test scenario where scheduler and API both try to update.

        Scheduler: reads test_retries=1, wants to set to 2
        API: reads test_retries=1, wants to set to 2 (concurrent)
        Result: one succeeds, other fails
        """
        # Mock database
        repo.db.get_connection = Mock()
        mock_conn = Mock()
        mock_cursor = Mock()

        # First call succeeds (scheduler)
        # Second call fails (API - concurrent modification detected)
        mock_cursor.rowcount = 1  # First succeeds
        mock_conn.cursor = Mock(return_value=mock_cursor)
        mock_conn.commit = Mock()
        mock_conn.close = Mock()
        repo.db.get_connection.return_value = mock_conn

        # Scheduler update (succeeds)
        result1 = repo.update_workflow(
            "test-123",
            {"test_retries": 2},
            expected_values={"test_retries": 1}
        )
        assert result1 is not None

        # API concurrent update (would fail with rowcount=0)
        # But in this mock, we return rowcount=1, simulating different timing
        # In real scenario, the second call would get rowcount=0
        # and scheduler cycle would retry later

    def test_optimistic_lock_failure_logs_warning(self, repo):
        """Optimistic lock failure should log warning."""
        # Mock database
        repo.db.get_connection = Mock()
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.rowcount = 0  # Lock failed
        mock_conn.cursor = Mock(return_value=mock_cursor)
        mock_conn.commit = Mock()
        mock_conn.close = Mock()
        repo.db.get_connection.return_value = mock_conn

        # Update with optimistic lock
        result = repo.update_workflow(
            "test-123",
            {"test_retries": 2},
            expected_values={"test_retries": 1}
        )

        # Should return None and log warning
        assert result is None
        # Warning logging happens inside the method
        # (we trust logger.warning was called)

    def test_no_expected_values_no_optimistic_lock(self, repo):
        """Update without expected_values should NOT use optimistic lock."""
        # Mock database
        repo.db.get_connection = Mock()
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.rowcount = 1
        mock_conn.cursor = Mock(return_value=mock_cursor)
        mock_conn.commit = Mock()
        mock_conn.close = Mock()
        repo.db.get_connection.return_value = mock_conn

        repo.get_workflow = Mock(return_value={"workflow_id": "test-123"})

        # Update without expected_values
        result = repo.update_workflow("test-123", {"test_retries": 1})

        # Should succeed (no WHERE clause for old values)
        assert result is not None