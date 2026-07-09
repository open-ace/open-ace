#!/usr/bin/env python3
"""
Unit tests for tenant usage aggregation functionality.

Tests cover:
- Aggregation lock mechanism
- Data quality checking
- Billing cycle calculation
- Period reset functionality
- Tenant usage aggregation
- Idempotency checks
"""

import sys
import os
import unittest
from datetime import datetime, timedelta, date
from unittest.mock import Mock, patch, MagicMock

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from scripts.shared import tenant_aggregation


class TestAggregationLock(unittest.TestCase):
    """Test aggregation lock mechanism."""

    @patch('scripts.shared.tenant_aggregation.get_connection')
    @patch('scripts.shared.tenant_aggregation.is_postgresql')
    def test_acquire_lock_sqlite(self, mock_is_postgres, mock_get_connection):
        """Test acquiring lock in SQLite."""
        mock_is_postgres.return_value = False

        # Mock connection
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_connection.return_value = mock_conn

        # Acquire lock
        lock = tenant_aggregation.acquire_aggregation_lock(timeout_seconds=60)

        # Verify lock was acquired
        self.assertIsNotNone(lock)
        mock_cursor.execute.assert_called()

    @patch('scripts.shared.tenant_aggregation.get_connection')
    @patch('scripts.shared.tenant_aggregation.is_postgresql')
    def test_release_lock_sqlite(self, mock_is_postgres, mock_get_connection):
        """Test releasing lock in SQLite."""
        mock_is_postgres.return_value = False

        # Mock connection
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        # Release lock
        tenant_aggregation.release_aggregation_lock(mock_conn)

        # Verify lock was released
        mock_cursor.execute.assert_called()

    def test_aggregation_lock_context_manager(self):
        """Test aggregation lock context manager."""
        with patch('scripts.shared.tenant_aggregation.acquire_aggregation_lock') as mock_acquire, \
             patch('scripts.shared.tenant_aggregation.release_aggregation_lock') as mock_release:

            mock_conn = MagicMock()
            mock_acquire.return_value = mock_conn

            with tenant_aggregation.AggregationLock(timeout_seconds=60):
                pass

            # Verify lock was acquired and released
            mock_acquire.assert_called_once_with(60)
            mock_release.assert_called_once_with(mock_conn)


class TestDataQualityCheck(unittest.TestCase):
    """Test data quality checking."""

    @patch('scripts.shared.tenant_aggregation.get_connection')
    def test_check_quota_usage_quality(self, mock_get_connection):
        """Test checking quota_usage data quality."""
        # Mock connection
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_connection.return_value = mock_conn

        # Mock query results
        mock_cursor.fetchone.side_effect = [
            {"count": 100},  # total_records
            {"count": 0},    # null_user_id
            {"count": 0},    # negative_tokens
            {"count": 0},    # abnormal_tokens
        ]

        result = tenant_aggregation.check_quota_usage_quality(
            start_date="2026-07-01",
            end_date="2026-07-09"
        )

        self.assertEqual(result["total_records"], 100)
        self.assertEqual(result["null_user_id"], 0)
        self.assertEqual(result["negative_tokens"], 0)
        self.assertEqual(result["quality_score"], 100.0)

    @patch('scripts.shared.tenant_aggregation.get_connection')
    def test_quality_check_with_null_user_ids(self, mock_get_connection):
        """Test quality check with NULL user_id records."""
        # Mock connection
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_connection.return_value = mock_conn

        # Mock query results with NULL user_ids
        mock_cursor.fetchone.side_effect = [
            {"count": 100},  # total_records
            {"count": 10},   # null_user_id
            {"count": 0},    # negative_tokens
            {"count": 0},    # abnormal_tokens
        ]

        result = tenant_aggregation.check_quota_usage_quality()

        self.assertEqual(result["null_user_id"], 10)
        self.assertEqual(result["quality_score"], 90.0)


class TestBillingCycleCalculation(unittest.TestCase):
    """Test billing cycle calculation."""

    def test_calculate_next_billing_cycle_end_monthly(self):
        """Test calculating next monthly billing cycle end."""
        # January 31 -> February (use last day)
        current_end = datetime(2026, 1, 31)
        next_end = tenant_aggregation.calculate_next_billing_cycle_end(
            current_end, billing_day=31, cycle_type="monthly"
        )
        # February 2026 has 28 days
        self.assertEqual(next_end.day, 28)
        self.assertEqual(next_end.month, 2)

        # February 28 -> March 31
        current_end = datetime(2026, 2, 28)
        next_end = tenant_aggregation.calculate_next_billing_cycle_end(
            current_end, billing_day=31, cycle_type="monthly"
        )
        self.assertEqual(next_end.day, 31)
        self.assertEqual(next_end.month, 3)

    def test_calculate_next_billing_cycle_end_february_special(self):
        """Test handling February special cases."""
        # Billing day 30 in February
        current_end = datetime(2026, 1, 30)
        next_end = tenant_aggregation.calculate_next_billing_cycle_end(
            current_end, billing_day=30, cycle_type="monthly"
        )
        # Should use last day of February (28)
        self.assertEqual(next_end.day, 28)
        self.assertEqual(next_end.month, 2)

        # Billing day 29 in non-leap year February
        current_end = datetime(2027, 1, 29)  # 2027 is not a leap year
        next_end = tenant_aggregation.calculate_next_billing_cycle_end(
            current_end, billing_day=29, cycle_type="monthly"
        )
        # Should use last day of February (28)
        self.assertEqual(next_end.day, 28)
        self.assertEqual(next_end.month, 2)

    def test_calculate_next_billing_cycle_end_quarterly(self):
        """Test calculating quarterly billing cycle."""
        current_end = datetime(2026, 3, 31)  # Q1 end
        next_end = tenant_aggregation.calculate_next_billing_cycle_end(
            current_end, billing_day=1, cycle_type="quarterly"
        )
        # Should be end of Q2 (June 30)
        self.assertEqual(next_end.month, 6)

    def test_calculate_next_billing_cycle_end_yearly(self):
        """Test calculating yearly billing cycle."""
        current_end = datetime(2026, 12, 31)
        next_end = tenant_aggregation.calculate_next_billing_cycle_end(
            current_end, billing_day=1, cycle_type="yearly"
        )
        # Should be end of next year
        self.assertEqual(next_end.year, 2027)


class TestPeriodReset(unittest.TestCase):
    """Test period reset functionality."""

    @patch('scripts.shared.tenant_aggregation.get_connection')
    def test_reset_tenant_period(self, mock_get_connection):
        """Test resetting tenant billing period."""
        # Mock connection
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_connection.return_value = mock_conn

        # Mock tenant query
        mock_cursor.fetchone.return_value = {
            "id": 1,
            "billing_cycle_start": date(2026, 7, 1),
            "billing_cycle_end": date(2026, 7, 31),
            "current_cycle_tokens": 5000000,
            "billing_day": 1,
            "billing_cycle_type": "monthly"
        }

        # Reset period
        result = tenant_aggregation.reset_tenant_period(1)

        # Verify reset was attempted
        self.assertTrue(result or mock_conn.commit.called)

    @patch('scripts.shared.tenant_aggregation.get_connection')
    def test_reset_expired_tenant_periods(self, mock_get_connection):
        """Test resetting all expired tenant periods."""
        # Mock connection
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_connection.return_value = mock_conn

        # Mock expired tenants query
        mock_cursor.fetchall.return_value = [{"id": 1}, {"id": 2}]

        with patch('scripts.shared.tenant_aggregation.reset_tenant_period') as mock_reset:
            mock_reset.return_value = True

            count = tenant_aggregation.reset_expired_tenant_periods()

            # Verify reset was called for each tenant
            self.assertEqual(mock_reset.call_count, 2)


class TestTenantUsageAggregation(unittest.TestCase):
    """Test tenant usage aggregation."""

    @patch('scripts.shared.tenant_aggregation.get_connection')
    def test_aggregate_tenant_usage_from_quota(self, mock_get_connection):
        """Test aggregating tenant usage from quota_usage."""
        # Mock connection
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_connection.return_value = mock_conn

        # Mock usage records
        mock_cursor.fetchall.return_value = [
            {"tenant_id": 1, "date": "2026-07-01", "total_tokens": 1000, "total_requests": 10},
            {"tenant_id": 1, "date": "2026-07-02", "total_tokens": 2000, "total_requests": 20},
            {"tenant_id": 2, "date": "2026-07-01", "total_tokens": 500, "total_requests": 5},
        ]

        # Mock tenant info query
        mock_cursor.fetchone.return_value = {
            "billing_cycle_start": date(2026, 7, 1),
            "billing_cycle_end": date(2026, 7, 31)
        }

        records, report = tenant_aggregation.aggregate_tenant_usage_from_quota()

        self.assertEqual(records, 3)
        self.assertEqual(report["tenants_updated"], 2)

    @patch('scripts.shared.tenant_aggregation.get_connection')
    def test_aggregate_with_no_data(self, mock_get_connection):
        """Test aggregation when quota_usage has no data."""
        # Mock connection
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_connection.return_value = mock_conn

        # Mock no usage records
        mock_cursor.fetchall.return_value = []

        records, report = tenant_aggregation.aggregate_tenant_usage_from_quota()

        self.assertEqual(records, 0)
        self.assertEqual(report["tenants_updated"], 0)


class TestIdempotencyCheck(unittest.TestCase):
    """Test idempotency checking."""

    @patch('scripts.shared.tenant_aggregation.get_connection')
    def test_check_aggregation_idempotency_not_completed(self, mock_get_connection):
        """Test idempotency check when aggregation not yet completed."""
        # Mock connection
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_connection.return_value = mock_conn

        # Mock no completed aggregation
        mock_cursor.fetchone.return_value = {"count": 0}

        result = tenant_aggregation.check_aggregation_idempotency(
            "tenant_usage", "2026-07-01", "2026-07-09"
        )

        self.assertTrue(result)

    @patch('scripts.shared.tenant_aggregation.get_connection')
    def test_check_aggregation_idempotency_already_completed(self, mock_get_connection):
        """Test idempotency check when aggregation already completed."""
        # Mock connection
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_connection.return_value = mock_conn

        # Mock completed aggregation
        mock_cursor.fetchone.return_value = {"count": 1}

        result = tenant_aggregation.check_aggregation_idempotency(
            "tenant_usage", "2026-07-01", "2026-07-09"
        )

        self.assertFalse(result)


class TestRecordAggregationHistory(unittest.TestCase):
    """Test recording aggregation history."""

    @patch('scripts.shared.tenant_aggregation.get_connection')
    def test_record_aggregation_history_success(self, mock_get_connection):
        """Test recording successful aggregation history."""
        # Mock connection
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_connection.return_value = mock_conn

        result = tenant_aggregation.record_aggregation_history(
            "tenant_usage",
            "2026-07-01",
            "2026-07-09",
            "completed",
            records_count=100,
            quality_report={"quality_score": 95}
        )

        self.assertTrue(result)

    @patch('scripts.shared.tenant_aggregation.get_connection')
    def test_record_aggregation_history_failure(self, mock_get_connection):
        """Test recording failed aggregation history."""
        # Mock connection
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_connection.return_value = mock_conn

        result = tenant_aggregation.record_aggregation_history(
            "tenant_usage",
            "2026-07-01",
            "2026-07-09",
            "failed",
            error_message="Test error"
        )

        self.assertTrue(result)


class TestMainAggregation(unittest.TestCase):
    """Test main aggregation function."""

    @patch('scripts.shared.tenant_aggregation.reset_expired_tenant_periods')
    @patch('scripts.shared.tenant_aggregation.check_quota_usage_quality')
    @patch('scripts.shared.tenant_aggregation.AggregationLock')
    @patch('scripts.shared.tenant_aggregation.check_aggregation_idempotency')
    @patch('scripts.shared.tenant_aggregation.aggregate_tenant_usage_from_quota')
    @patch('scripts.shared.tenant_aggregation.record_aggregation_history')
    def test_run_tenant_aggregation_success(
        self,
        mock_record_history,
        mock_aggregate,
        mock_check_idempotency,
        mock_lock,
        mock_quality,
        mock_reset
    ):
        """Test successful tenant aggregation run."""
        # Setup mocks
        mock_reset.return_value = 2
        mock_quality.return_value = {"quality_score": 95}
        mock_check_idempotency.return_value = True
        mock_aggregate.return_value = (100, {"tenants_updated": 5})
        mock_record_history.return_value = True

        # Mock lock context manager
        mock_lock_instance = MagicMock()
        mock_lock.return_value.__enter__ = MagicMock(return_value=mock_lock_instance)
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)

        # Run aggregation
        result = tenant_aggregation.run_tenant_aggregation(
            start_date="2026-07-01",
            end_date="2026-07-09"
        )

        # Verify results
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["periods_reset"], 2)
        self.assertEqual(result["records_aggregated"], 100)
        self.assertEqual(result["tenants_updated"], 5)


if __name__ == "__main__":
    unittest.main()