"""
Unit tests for ResponseTimeCleaner.

Tests the cleanup service for old response time data.
"""

import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from app.services.response_time_cleaner import (
    DEFAULT_AGGREGATED_DATA_RETENTION_DAYS,
    DEFAULT_RAW_DATA_RETENTION_DAYS,
    ResponseTimeCleaner,
    run_cleanup,
)


class TestResponseTimeCleaner(unittest.TestCase):
    """Tests for ResponseTimeCleaner class."""

    def test_init_default_values(self):
        """Test initialization with default values."""
        mock_repo = MagicMock()
        cleaner = ResponseTimeCleaner(repo=mock_repo)

        self.assertEqual(cleaner.raw_retention_days, DEFAULT_RAW_DATA_RETENTION_DAYS)
        self.assertEqual(cleaner.aggregated_retention_days, DEFAULT_AGGREGATED_DATA_RETENTION_DAYS)
        self.assertIsNotNone(cleaner.repo)

    def test_init_custom_values(self):
        """Test initialization with custom retention days."""
        mock_repo = MagicMock()
        cleaner = ResponseTimeCleaner(
            repo=mock_repo,
            raw_retention_days=30,
            aggregated_retention_days=180,
        )

        self.assertEqual(cleaner.raw_retention_days, 30)
        self.assertEqual(cleaner.aggregated_retention_days, 180)

    def test_cleanup_success(self):
        """Test successful cleanup."""
        mock_repo = MagicMock()
        mock_repo.cleanup_old_data.return_value = 100
        mock_repo.cleanup_old_stats.return_value = 50

        cleaner = ResponseTimeCleaner(repo=mock_repo)
        results = cleaner.cleanup()

        self.assertEqual(results["raw_data_deleted"], 100)
        self.assertEqual(results["aggregated_data_deleted"], 50)
        self.assertEqual(results["errors"], [])
        mock_repo.cleanup_old_data.assert_called_once_with(
            days_to_keep=DEFAULT_RAW_DATA_RETENTION_DAYS
        )
        mock_repo.cleanup_old_stats.assert_called_once_with(
            days_to_keep=DEFAULT_AGGREGATED_DATA_RETENTION_DAYS
        )

    def test_cleanup_with_errors(self):
        """Test cleanup with database errors."""
        mock_repo = MagicMock()
        mock_repo.cleanup_old_data.side_effect = Exception("Database error")
        mock_repo.cleanup_old_stats.return_value = 0  # Second call still executes

        cleaner = ResponseTimeCleaner(repo=mock_repo)
        results = cleaner.cleanup()

        self.assertEqual(results["raw_data_deleted"], 0)
        self.assertEqual(results["aggregated_data_deleted"], 0)
        self.assertEqual(len(results["errors"]), 1)
        self.assertIn("raw_data", results["errors"][0])

    def test_cleanup_partial_failure(self):
        """Test cleanup with partial failures."""
        mock_repo = MagicMock()
        mock_repo.cleanup_old_data.return_value = 100
        mock_repo.cleanup_old_stats.side_effect = Exception("Stats cleanup failed")

        cleaner = ResponseTimeCleaner(repo=mock_repo)
        results = cleaner.cleanup()

        self.assertEqual(results["raw_data_deleted"], 100)
        self.assertEqual(results["aggregated_data_deleted"], 0)
        self.assertEqual(len(results["errors"]), 1)

    @patch("app.services.response_time_cleaner.ResponseTimeCleaner")
    def test_run_cleanup_function(self, mock_cleaner_class):
        """Test the run_cleanup function."""
        mock_cleaner = MagicMock()
        mock_cleaner.cleanup.return_value = {
            "raw_data_deleted": 10,
            "aggregated_data_deleted": 5,
            "errors": [],
        }
        mock_cleaner_class.return_value = mock_cleaner

        results = run_cleanup()

        self.assertEqual(results["raw_data_deleted"], 10)
        self.assertEqual(results["aggregated_data_deleted"], 5)
        mock_cleaner_class.assert_called_once()


if __name__ == "__main__":
    unittest.main()