"""
Open ACE - User Daily Stats Aggregator Service

Provides background aggregation of user usage data from daily_messages
to user_daily_stats table for optimized query performance.

This service should be called:
1. On application startup (aggregate historical data)
2. Periodically (every hour) via cron or background task
3. After new messages are saved (incremental update)
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, cast

from app.repositories.database import Database, escape_like, is_postgresql
from app.repositories.user_repo import UserRepository

logger = logging.getLogger(__name__)


class UserDailyStatsAggregator:
    """
    Aggregates user usage data from daily_messages to user_daily_stats.

    This provides fast lookup for user usage trend queries by pre-computing
    daily aggregations instead of computing them on-the-fly.
    """

    def __init__(self, db: Optional[Database] = None):
        """Initialize aggregator."""
        self.db = db or Database()
        self.user_repo = UserRepository()

    def aggregate_all_users(self, days: int = 30) -> int:
        """
        Aggregate usage data for all users.

        Args:
            days: Number of days to aggregate (default: 30).

        Returns:
            Number of records created/updated.
        """
        logger.info(f"Starting aggregation for all users (last {days} days)...")

        users = self.user_repo.get_all_users(include_inactive=False)
        if not users:
            logger.warning("No users found for aggregation")
            return 0

        total_records = 0
        for user in users:
            user_id = user.get("id")
            username = user.get("username")
            system_account = user.get("system_account")
            if user_id and username:
                records = self.aggregate_user(user_id, username, days, system_account)
                total_records += records
                logger.debug(f"Aggregated {records} records for user {username}")

        logger.info(f"Aggregation completed: {total_records} total records")
        return total_records

    def aggregate_user(
        self, user_id: int, username: str, days: int = 30, system_account: Optional[str] = None
    ) -> int:
        """
        Aggregate usage data for a specific user.

        Args:
            user_id: User ID.
            username: Username (for logging).
            days: Number of days to aggregate.
            system_account: System account name for sender_name matching.
                           If not provided, falls back to username.

        Returns:
            Number of records created/updated.
        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        start_str = start_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")

        # Use system_account for sender_name matching if available
        # sender_name format: {system_account}-{hostname}-{tool}
        sender_prefix = system_account or username

        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                if is_postgresql():
                    cursor.execute(
                        """
                        INSERT INTO user_daily_stats (user_id, date, requests, tokens, input_tokens, output_tokens, updated_at)
                        SELECT %s, dm.date::date, COUNT(*), COALESCE(SUM(dm.tokens_used), 0),
                               COALESCE(SUM(dm.input_tokens), 0), COALESCE(SUM(dm.output_tokens), 0), CURRENT_TIMESTAMP
                        FROM daily_messages dm
                        WHERE dm.date >= %s AND dm.date <= %s AND dm.sender_name LIKE %s AND dm.role = 'assistant'
                        GROUP BY dm.date::date
                        ON CONFLICT (user_id, date) DO UPDATE SET requests = EXCLUDED.requests, tokens = EXCLUDED.tokens,
                            input_tokens = EXCLUDED.input_tokens, output_tokens = EXCLUDED.output_tokens, updated_at = CURRENT_TIMESTAMP""",
                        (user_id, start_str, end_str, f"{escape_like(sender_prefix)}%"),
                    )
                else:
                    now = datetime.utcnow().isoformat()
                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO user_daily_stats
                        (user_id, date, requests, tokens, input_tokens, output_tokens, updated_at)
                        SELECT
                            ? as user_id,
                            dm.date,
                            COUNT(*) as requests,
                            COALESCE(SUM(dm.tokens_used), 0) as tokens,
                            COALESCE(SUM(dm.input_tokens), 0) as input_tokens,
                            COALESCE(SUM(dm.output_tokens), 0) as output_tokens,
                            ?
                        FROM daily_messages dm
                        WHERE dm.date >= ? AND dm.date <= ?
                          AND dm.sender_name LIKE ?
                          AND dm.role = 'assistant'
                        GROUP BY dm.date
                    """,
                        (user_id, now, start_str, end_str, f"{escape_like(sender_prefix)}%"),
                    )

                conn.commit()
                records_updated = cursor.rowcount

                logger.debug(
                    f"Aggregated {records_updated} records for user {username} (sender_prefix: {sender_prefix})"
                )
                return cast("int", records_updated)

        except Exception as e:
            logger.error(f"Failed to aggregate user {username}: {e}")
            return 0

    def aggregate_today(self, user_id: Optional[int] = None) -> int:
        """
        Aggregate today's data for all users or a specific user.

        Args:
            user_id: Optional user ID. If None, aggregates for all users.

        Returns:
            Number of records created/updated.
        """
        datetime.now().strftime("%Y-%m-%d")

        if user_id:
            user = self.user_repo.get_user_by_id(user_id)
            if not user:
                return 0
            return self.aggregate_user(user_id, user.get("username", ""), days=1)
        else:
            return self.aggregate_all_users(days=1)

    def cleanup_old_data(self, days_to_keep: int = 90) -> int:
        """
        Delete old aggregated data.

        Args:
            days_to_keep: Number of days to keep (default: 90).

        Returns:
            Number of records deleted.
        """
        cutoff_date = datetime.now() - timedelta(days=days_to_keep)
        cutoff_str = cutoff_date.strftime("%Y-%m-%d")

        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                if is_postgresql():
                    cursor.execute("DELETE FROM user_daily_stats WHERE date < %s", (cutoff_str,))
                else:
                    cursor.execute("DELETE FROM user_daily_stats WHERE date < ?", (cutoff_str,))
                deleted = cursor.rowcount
                conn.commit()

                logger.info(f"Cleaned up {deleted} old user_daily_stats records")
                return cast("int", deleted)

        except Exception as e:
            logger.error(f"Failed to cleanup old data: {e}")
            return 0


# Singleton instance
_aggregator: Optional[UserDailyStatsAggregator] = None


def get_aggregator() -> UserDailyStatsAggregator:
    """Get or create the aggregator singleton."""
    global _aggregator
    if _aggregator is None:
        _aggregator = UserDailyStatsAggregator()
    return _aggregator


def aggregate_user_stats_background():
    """
    Background task to aggregate user stats.

    This can be called periodically (e.g., every hour) to keep
    user_daily_stats up to date.
    """
    try:
        aggregator = get_aggregator()
        # Only aggregate last 7 days for efficiency
        records = aggregator.aggregate_all_users(days=7)
        logger.info(f"Background aggregation completed: {records} records")
    except Exception as e:
        logger.error(f"Background aggregation failed: {e}")
