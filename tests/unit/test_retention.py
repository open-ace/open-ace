"""Unit tests for DataRetentionManager.estimate_storage."""

from unittest.mock import MagicMock

import pytest

from app.modules.compliance.retention import DataRetentionManager


class TestEstimateStorage:
    """Test estimate_storage method."""

    def _make_manager(self):
        """Create a DataRetentionManager with mocked database."""
        mock_db = MagicMock()
        manager = DataRetentionManager(db=mock_db)
        return manager, mock_db

    def test_estimate_storage_basic(self):
        """Test basic storage estimation with record counts."""
        manager, mock_db = self._make_manager()

        # Mock COUNT(*) results for each table
        mock_db.fetch_one.side_effect = [
            {"count": 1000},  # audit_logs
            {"count": 500},  # quota_alerts
            {"count": 200},  # sessions
            {"count": 10000},  # daily_usage
            {"count": 50000},  # daily_messages
            {"count": 100},  # users
        ]

        estimates = manager.estimate_storage()

        assert len(estimates) == 6
        assert estimates[0]["data_type"] == "audit_logs"
        assert estimates[0]["record_count"] == 1000
        # 1000 * 600 bytes = 600000 bytes ≈ 0.57 MB
        assert estimates[0]["estimated_size_mb"] > 0

    def test_estimate_storage_zero_records(self):
        """Test estimation with zero records."""
        manager, mock_db = self._make_manager()

        mock_db.fetch_one.side_effect = [
            {"count": 0},  # audit_logs
            {"count": 0},  # quota_alerts
            {"count": 0},  # sessions
            {"count": 0},  # daily_usage
            {"count": 0},  # daily_messages
            {"count": 0},  # users
        ]

        estimates = manager.estimate_storage()

        for estimate in estimates:
            assert estimate["record_count"] == 0
            assert estimate["estimated_size_mb"] == 0

    def test_estimate_storage_large_counts(self):
        """Test estimation with large record counts."""
        manager, mock_db = self._make_manager()

        # Simulate large production-like counts
        mock_db.fetch_one.side_effect = [
            {"count": 500000},  # audit_logs: ~286 MB
            {"count": 10000},  # quota_alerts: ~1.9 MB
            {"count": 5000},  # sessions: ~0.7 MB
            {"count": 1000000},  # daily_usage: ~95 MB
            {"count": 2000000},  # daily_messages: ~1907 MB
            {"count": 500},  # users: ~0.14 MB
        ]

        estimates = manager.estimate_storage()

        # Verify daily_messages has largest estimate (2000000 * 1000 bytes)
        daily_messages_estimate = estimates[4]
        assert daily_messages_estimate["data_type"] == "daily_messages"
        assert daily_messages_estimate["record_count"] == 2000000
        expected_mb = round(2000000 * 1000 / (1024 * 1024), 2)
        assert daily_messages_estimate["estimated_size_mb"] == expected_mb

        # Verify all estimates are positive
        for estimate in estimates:
            if estimate["record_count"] > 0:
                assert estimate["estimated_size_mb"] > 0

    def test_estimate_storage_exception_handling(self):
        """Test that exceptions are handled gracefully."""
        manager, mock_db = self._make_manager()

        # First query succeeds, second raises exception
        mock_db.fetch_one.side_effect = [
            {"count": 100},  # audit_logs succeeds
            Exception("Table not found"),  # quota_alerts fails
            {"count": 50},  # sessions succeeds
            {"count": 1000},  # daily_usage succeeds
            {"count": 2000},  # daily_messages succeeds
            {"count": 10},  # users succeeds
        ]

        estimates = manager.estimate_storage()

        assert len(estimates) == 6
        # quota_alerts should have fallback values on exception
        assert estimates[1]["data_type"] == "quota_alerts"
        assert estimates[1]["record_count"] == 0
        assert estimates[1]["estimated_size_mb"] == 0

    def test_estimate_storage_null_result(self):
        """Test handling of null/None query results."""
        manager, mock_db = self._make_manager()

        mock_db.fetch_one.side_effect = [
            None,  # audit_logs returns None
            {"count": 100},  # quota_alerts
            None,  # sessions returns None
            {"count": 500},  # daily_usage
            {"count": 1000},  # daily_messages
            {"count": 50},  # users
        ]

        estimates = manager.estimate_storage()

        # Tables with None results should have 0 count and 0 size
        assert estimates[0]["record_count"] == 0
        assert estimates[0]["estimated_size_mb"] == 0
        assert estimates[2]["record_count"] == 0
        assert estimates[2]["estimated_size_mb"] == 0

    def test_estimated_record_sizes_defined(self):
        """Test that ESTIMATED_RECORD_SIZES constant is properly defined."""
        manager, _ = self._make_manager()

        # Verify all tracked tables have estimated sizes
        expected_tables = [
            "audit_logs",
            "quota_alerts",
            "sessions",
            "daily_usage",
            "daily_messages",
            "users",
        ]

        for table in expected_tables:
            assert table in manager.ESTIMATED_RECORD_SIZES
            assert manager.ESTIMATED_RECORD_SIZES[table] > 0

        # Verify daily_messages has largest estimate (content/full_entry fields)
        assert (
            manager.ESTIMATED_RECORD_SIZES["daily_messages"]
            >= manager.ESTIMATED_RECORD_SIZES["daily_usage"]
        )

    def test_estimate_storage_rounding(self):
        """Test that estimated_size_mb is properly rounded to 2 decimals."""
        manager, mock_db = self._make_manager()

        # Use a count that produces non-round numbers
        mock_db.fetch_one.side_effect = [
            {"count": 12345},  # audit_logs
            {"count": 67890},  # quota_alerts
            {"count": 111},  # sessions
            {"count": 222},  # daily_usage
            {"count": 333},  # daily_messages
            {"count": 444},  # users
        ]

        estimates = manager.estimate_storage()

        for estimate in estimates:
            # Check that the value has at most 2 decimal places
            size = estimate["estimated_size_mb"]
            if size > 0:
                # Verify rounding to 2 decimal places
                rounded = round(size, 2)
                assert size == rounded

    def test_estimate_storage_calculation_accuracy(self):
        """Test that calculation matches expected formula."""
        manager, mock_db = self._make_manager()

        # Use specific counts to verify formula
        test_count = 10000
        mock_db.fetch_one.side_effect = [{"count": test_count}] * 6

        estimates = manager.estimate_storage()

        # Verify formula: record_count * avg_size_bytes / (1024 * 1024)
        for estimate in estimates:
            avg_size = manager.ESTIMATED_RECORD_SIZES.get(estimate["data_type"], 100)
            expected = round((test_count * avg_size) / (1024 * 1024), 2)
            assert estimate["estimated_size_mb"] == expected
