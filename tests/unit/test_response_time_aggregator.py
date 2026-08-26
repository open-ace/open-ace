"""
Unit tests for ResponseTimeAggregator.

Tests the aggregation of request performance data into daily statistics.
"""

import unittest
from unittest.mock import MagicMock, patch

from app.services.response_time_aggregator import ResponseTimeAggregator


class TestResponseTimeAggregator(unittest.TestCase):
    """Tests for ResponseTimeAggregator class."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_db = MagicMock()
        self.aggregator = ResponseTimeAggregator(db=self.mock_db)

    def test_calculate_group_stats_empty(self):
        """Test calculating stats for empty group."""
        stats = self.aggregator._calculate_group_stats([])

        self.assertEqual(stats["sample_count"], 0)
        self.assertEqual(stats["success_count"], 0)
        self.assertEqual(stats["failed_count"], 0)

    def test_calculate_group_stats_basic(self):
        """Test calculating stats for a basic group."""
        rows = [
            {"ttft_ms": 100, "status": "success", "tool_call_duration_ms": 0},
            {"ttft_ms": 200, "status": "success", "tool_call_duration_ms": 0},
            {"ttft_ms": 300, "status": "failed", "tool_call_duration_ms": 0},
        ]

        stats = self.aggregator._calculate_group_stats(rows)

        self.assertEqual(stats["sample_count"], 3)
        self.assertEqual(stats["success_count"], 2)
        self.assertEqual(stats["failed_count"], 1)
        self.assertAlmostEqual(stats["avg_ms"], 200.0, places=1)
        self.assertEqual(stats["min_ms"], 100)
        self.assertEqual(stats["max_ms"], 300)

    def test_calculate_percentiles(self):
        """Test percentile calculation."""
        # Create 100 values from 1 to 100
        rows = [
            {"ttft_ms": i, "status": "success", "tool_call_duration_ms": 0} for i in range(1, 101)
        ]

        stats = self.aggregator._calculate_group_stats(rows)

        # P50 should be around 50
        self.assertGreater(stats["p50_ms"], 40)
        self.assertLess(stats["p50_ms"], 60)

        # P95 should be around 95
        self.assertGreater(stats["p95_ms"], 90)
        self.assertLess(stats["p95_ms"], 100)

    def test_tool_call_stats(self):
        """Test tool call statistics."""
        rows = [
            {"ttft_ms": 1000, "status": "success", "tool_call_duration_ms": 300},
            {"ttft_ms": 1000, "status": "success", "tool_call_duration_ms": 500},
            {"ttft_ms": 1000, "status": "success", "tool_call_duration_ms": 200},
        ]

        stats = self.aggregator._calculate_group_stats(rows)

        self.assertEqual(stats["tool_call_avg_ms"], 333.3333333333333)
        self.assertAlmostEqual(stats["tool_call_ratio"], 0.333, places=2)

    def test_negative_ttft_filtered(self):
        """Test that negative TTFT values are filtered."""
        rows = [
            {"ttft_ms": 100, "status": "success", "tool_call_duration_ms": 0},
            {"ttft_ms": -50, "status": "success", "tool_call_duration_ms": 0},  # Should be ignored
            {"ttft_ms": 200, "status": "success", "tool_call_duration_ms": 0},
        ]

        stats = self.aggregator._calculate_group_stats(rows)

        # Only 2 valid samples
        self.assertEqual(stats["sample_count"], 3)  # sample_count is all rows
        self.assertEqual(stats["avg_ms"], 150)  # Average of 100 and 200

    def test_aggregate_no_data(self):
        """Test aggregation with no data."""
        self.mock_db.fetch_all.return_value = []

        result = self.aggregator.aggregate("2026-01-01")

        self.assertEqual(result["rows_processed"], 0)
        self.assertEqual(result["groups_created"], 0)

    def test_aggregate_with_data(self):
        """Test aggregation with sample data."""
        self.mock_db.fetch_all.return_value = [
            {
                "tool_name": "tool-1",
                "host_name": "localhost",
                "tenant_id": 1,
                "ttft_ms": 100,
                "tool_call_duration_ms": 0,
                "total_duration_ms": 150,
                "status": "success",
                "sample_type": "streaming",
            },
            {
                "tool_name": "tool-1",
                "host_name": "localhost",
                "tenant_id": 1,
                "ttft_ms": 200,
                "tool_call_duration_ms": 50,
                "total_duration_ms": 250,
                "status": "success",
                "sample_type": "streaming",
            },
        ]

        result = self.aggregator.aggregate("2026-01-01")

        self.assertEqual(result["rows_processed"], 2)
        self.assertEqual(result["groups_created"], 1)

        # Verify database write was called
        self.mock_db.execute.assert_called_once()


if __name__ == "__main__":
    unittest.main()
