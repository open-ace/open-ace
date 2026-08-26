"""
Open ACE - Response Time Cleaner Service

Cleans up old response time data from the database.
Runs as a scheduled task to control storage growth.

Issue #3080: Response time metrics for trend analysis.
"""

import logging
from datetime import datetime, timedelta

from app.repositories.response_time_repo import ResponseTimeRepository

logger = logging.getLogger(__name__)

# Default retention periods
DEFAULT_RAW_DATA_RETENTION_DAYS = 90
DEFAULT_AGGREGATED_DATA_RETENTION_DAYS = 365


class ResponseTimeCleaner:
    """
    Cleaner for response time data.

    Removes old data from:
    - request_performance (raw data, 90 days)
    - response_time_stats (aggregated data, 365 days)
    """

    def __init__(
        self,
        repo: ResponseTimeRepository | None = None,
        raw_retention_days: int = DEFAULT_RAW_DATA_RETENTION_DAYS,
        aggregated_retention_days: int = DEFAULT_AGGREGATED_DATA_RETENTION_DAYS,
    ):
        """
        Initialize cleaner.

        Args:
            repo: Optional ResponseTimeRepository instance.
            raw_retention_days: Days to keep raw data.
            aggregated_retention_days: Days to keep aggregated data.
        """
        self.repo = repo or ResponseTimeRepository()
        self.raw_retention_days = raw_retention_days
        self.aggregated_retention_days = aggregated_retention_days

    def cleanup(self) -> dict:
        """
        Clean up old data.

        Returns:
            Dict with cleanup results.
        """
        logger.info(
            f"Starting response time cleanup: "
            f"raw data > {self.raw_retention_days} days, "
            f"aggregated data > {self.aggregated_retention_days} days"
        )

        results = {
            "raw_data_deleted": 0,
            "aggregated_data_deleted": 0,
            "errors": [],
        }

        try:
            # Clean up raw data
            results["raw_data_deleted"] = self.repo.cleanup_old_data(
                days_to_keep=self.raw_retention_days
            )
            logger.info(f"Deleted {results['raw_data_deleted']} raw performance records")
        except Exception as e:
            logger.error(f"Failed to clean up raw data: {e}")
            results["errors"].append(f"raw_data: {str(e)}")

        try:
            # Clean up aggregated data
            results["aggregated_data_deleted"] = self.repo.cleanup_old_stats(
                days_to_keep=self.aggregated_retention_days
            )
            logger.info(f"Deleted {results['aggregated_data_deleted']} aggregated stats records")
        except Exception as e:
            logger.error(f"Failed to clean up aggregated data: {e}")
            results["errors"].append(f"aggregated_data: {str(e)}")

        return results


def run_cleanup() -> dict:
    """
    Run the cleanup task.

    This function is intended to be called by a scheduler.

    Returns:
        Dict with cleanup results.
    """
    cleaner = ResponseTimeCleaner()
    return cleaner.cleanup()
