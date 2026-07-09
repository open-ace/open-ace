"""Unit tests for DataRetentionManager.estimate_storage."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

import app.repositories.database as db_mod
from app.modules.compliance.retention import DataRetentionManager, RetentionReport


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


class TestPostgresPlaceholderAdaptation:
    """Guard against regression for issue #860.

    The retention persistence SQL (INSERT/DELETE/UPDATE) must run its
    placeholders through ``adapt_sql()`` so it works on PostgreSQL. On SQLite
    ``?`` is valid; psycopg2 (PostgreSQL) only accepts ``%s``. These tests
    monkeypatch ``is_postgresql()`` and spy on the SQL handed to the driver, so
    they catch the regression in CI without a live PostgreSQL server.
    """

    def _make_manager_with_spy(self):
        """Build a manager backed by a mock db whose ``cursor.execute`` is spied."""
        mock_db = MagicMock()
        manager = DataRetentionManager(db=mock_db)
        captured = []
        cursor = mock_db.connection.return_value.__enter__.return_value.cursor.return_value
        cursor.execute.side_effect = lambda query, *args, **kwargs: captured.append(query)
        return manager, mock_db, captured

    def _sample_report(self) -> RetentionReport:
        return RetentionReport(
            timestamp=datetime(2026, 1, 1),
            rules_applied=[],
            records_deleted=0,
            records_archived=0,
            records_anonymized=0,
        )

    def test_save_report_adapts_placeholders_for_postgres(self, monkeypatch):
        """INSERT into retention_history must use %s (not ?) under PostgreSQL."""
        monkeypatch.setattr(db_mod, "is_postgresql", lambda: True)

        manager, _mock_db, captured = self._make_manager_with_spy()
        manager._save_report(self._sample_report())

        insert_sqls = [q for q in captured if "INSERT" in q.upper()]
        assert len(insert_sqls) == 1
        assert "%s" in insert_sqls[0]
        assert "?" not in insert_sqls[0]

    def test_save_report_keeps_sqlite_placeholders(self, monkeypatch):
        """Under SQLite the '?' placeholder is preserved (adapt_sql is a no-op)."""
        monkeypatch.setattr(db_mod, "is_postgresql", lambda: False)

        manager, _mock_db, captured = self._make_manager_with_spy()
        manager._save_report(self._sample_report())

        insert_sqls = [q for q in captured if "INSERT" in q.upper()]
        assert len(insert_sqls) == 1
        assert "?" in insert_sqls[0]

    def test_delete_old_data_adapts_placeholders_for_postgres(self, monkeypatch):
        """DELETE must use %s (not ?) under PostgreSQL (issue #860 root cause)."""
        monkeypatch.setattr(db_mod, "is_postgresql", lambda: True)

        manager, mock_db, captured = self._make_manager_with_spy()
        # The COUNT(*) pre-check goes through fetch_one (already auto-adapted).
        mock_db.fetch_one.return_value = {"count": 5}

        manager._delete_old_data("messages", datetime(2020, 1, 1), dry_run=False)

        delete_sqls = [q for q in captured if "DELETE" in q.upper()]
        assert len(delete_sqls) == 1
        assert "%s" in delete_sqls[0]
        assert "?" not in delete_sqls[0]

    def test_anonymize_old_data_adapts_placeholders_for_postgres(self, monkeypatch):
        """Anonymize UPDATE must use %s (not ?) under PostgreSQL (issue #1491)."""
        monkeypatch.setattr(db_mod, "is_postgresql", lambda: True)

        manager, mock_db, captured = self._make_manager_with_spy()
        mock_db.fetch_one.return_value = {"count": 5}  # Mock COUNT(*) result

        manager._anonymize_old_data("messages", datetime(2020, 1, 1), dry_run=False)

        update_sqls = [
            q for q in captured if "UPDATE" in q.upper() and "daily_messages" in q.lower()
        ]
        assert len(update_sqls) == 1

        # Verify WHERE clause uses %s placeholder (not ?)
        sql = update_sqls[0]
        assert "WHERE" in sql.upper()
        assert "%s" in sql  # PostgreSQL placeholder
        assert "?" not in sql  # SQLite placeholder should not appear

    def test_anonymize_old_data_uses_correct_field_names(self, monkeypatch):
        """Anonymize UPDATE must use sender_id/sender_name (not sender/recipient)."""
        monkeypatch.setattr(db_mod, "is_postgresql", lambda: False)

        manager, mock_db, captured = self._make_manager_with_spy()
        mock_db.fetch_one.return_value = {"count": 5}

        manager._anonymize_old_data("messages", datetime(2020, 1, 1), dry_run=False)

        update_sqls = [
            q for q in captured if "UPDATE" in q.upper() and "daily_messages" in q.lower()
        ]
        assert len(update_sqls) == 1

        # Verify correct field names
        sql = update_sqls[0]
        assert "sender_id" in sql.lower()
        assert "sender_name" in sql.lower()
        assert "sender" not in sql.lower().replace("sender_id", "").replace("sender_name", "")
        assert "recipient" not in sql.lower()

    def test_run_cleanup_surfaces_save_report_failure(self, monkeypatch):
        """A _save_report failure must surface into report.errors, not be silent.

        Guards the follow-up to issue #860: persistence problems must be visible
        to the caller (and frontend toast), never silently dropped.
        """
        monkeypatch.setattr(db_mod, "is_postgresql", lambda: False)

        manager, _mock_db, _captured = self._make_manager_with_spy()
        manager.rules = {}  # skip per-rule processing; isolate the save path

        def boom(_report):
            raise RuntimeError("DB write failed")

        monkeypatch.setattr(manager, "_save_report", boom)

        report = manager.run_cleanup(dry_run=False)

        assert any(
            "Failed to save cleanup report" in msg and "DB write failed" in msg
            for msg in report.errors
        )
