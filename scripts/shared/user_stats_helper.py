#!/usr/bin/env python3
"""
Helper function to refresh user_daily_stats table.
This is imported by scripts/shared/db.py
"""

from datetime import datetime
from typing import Set


def _refresh_user_daily_stats_for_dates(dates: Set[str]) -> None:
    """
    Refresh user_daily_stats for the given dates.
    
    This function is called after messages are saved to keep
    user_daily_stats in sync with daily_messages.
    
    Args:
        dates: Set of date strings (YYYY-MM-DD) to refresh
    """
    if not dates:
        return
    
    # Import here to avoid circular imports
    from app.services.user_stats_aggregator import UserDailyStatsAggregator
    from app.repositories.user_repo import UserRepository
    
    aggregator = UserDailyStatsAggregator()
    user_repo = UserRepository()
    
    users = user_repo.get_all_users(include_inactive=False)
    if not users:
        return
    
    for user in users:
        user_id = user.get("id")
        username = user.get("username")
        system_account = user.get("system_account")
        
        if not user_id or not username:
            continue
        
        # Aggregate each date for this user
        for date in dates:
            try:
                # Use a simple query to aggregate for this specific date
                _aggregate_user_for_date(user_id, username, system_account, date)
            except Exception as e:
                print(f"Warning: Failed to aggregate user {username} for {date}: {e}")


def _aggregate_user_for_date(user_id: int, username: str, system_account: str, date: str) -> None:
    """
    Aggregate a single user's stats for a specific date.
    """
    from app.repositories.database import Database, is_postgresql
    
    db = Database()
    sender_prefix = system_account or username
    now = datetime.utcnow().isoformat()
    
    try:
        with db.connection() as conn:
            cursor = conn.cursor()
            
            if is_postgresql():
                cursor.execute("""
                    INSERT INTO user_daily_stats 
                    (user_id, date, requests, tokens, input_tokens, output_tokens, updated_at)
                    SELECT 
                        %s as user_id,
                        dm.date::date,
                        COUNT(*) as requests,
                        COALESCE(SUM(dm.tokens_used), 0) as tokens,
                        COALESCE(SUM(dm.input_tokens), 0) as input_tokens,
                        COALESCE(SUM(dm.output_tokens), 0) as output_tokens,
                        CURRENT_TIMESTAMP
                    FROM daily_messages dm
                    WHERE dm.date = %s
                      AND dm.sender_name LIKE %s
                      AND dm.role = 'assistant'
                    GROUP BY dm.date::date
                    ON CONFLICT (user_id, date) DO UPDATE SET
                        requests = EXCLUDED.requests,
                        tokens = EXCLUDED.tokens,
                        input_tokens = EXCLUDED.input_tokens,
                        output_tokens = EXCLUDED.output_tokens,
                        updated_at = CURRENT_TIMESTAMP
                """, (user_id, date, f"{sender_prefix}%"))
            else:
                cursor.execute("""
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
                    WHERE dm.date = ?
                      AND dm.sender_name LIKE ?
                      AND dm.role = 'assistant'
                    GROUP BY dm.date
                """, (user_id, now, date, f"{sender_prefix}%"))
            
            conn.commit()
    except Exception as e:
        raise e