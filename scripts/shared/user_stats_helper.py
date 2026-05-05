#!/usr/bin/env python3
"""
Helper function to refresh user_daily_stats table.
This is imported by scripts/shared/db.py
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def _refresh_user_daily_stats_for_dates(dates: set[str]) -> None:
    """
    Refresh user_daily_stats for the given dates.

    This function is called after messages are saved to keep
    user_daily_stats in sync with daily_messages.

    Args:
        dates: Set of date strings (YYYY-MM-DD) to refresh
    """
    if not dates:
        return

    from app.services.user_stats_aggregator import UserDailyStatsAggregator

    # Calculate how many days back to aggregate from the date range
    datetime.now().strftime("%Y-%m-%d")
    date_list = sorted(dates)
    oldest = date_list[0]
    days_back = max(1, (datetime.now() - datetime.strptime(oldest, "%Y-%m-%d")).days + 1)
    days_back = min(days_back, 7)  # Cap at 7 days

    try:
        aggregator = UserDailyStatsAggregator()
        records = aggregator.aggregate_all_users(days=days_back)
        logger.info(
            f"Refreshed user_daily_stats for {len(dates)} date(s), {records} records updated"
        )
    except Exception as e:
        logger.warning(f"Failed to refresh user_daily_stats: {e}")
