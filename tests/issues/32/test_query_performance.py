#!/usr/bin/env python3
"""
Test script to measure query performance for trend analysis optimization.
"""

import os
import sys
import time

# Add project root to path
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
)

from app.repositories.message_repo import MessageRepository
from app.repositories.usage_repo import UsageRepository
from app.utils.helpers import get_days_ago, get_today


def measure_query(name, func, *args, **kwargs):
    """Measure query execution time."""
    start = time.time()
    result = func(*args, **kwargs)
    elapsed = time.time() - start
    count = len(result) if isinstance(result, list) else 1
    print(f"  {name}: {elapsed:.3f}s ({count} rows)")
    return elapsed, result


def main():
    """Test query performance."""
    print("=" * 60)
    print("Query Performance Analysis for Trend Analysis")
    print("=" * 60)

    message_repo = MessageRepository()
    usage_repo = UsageRepository()

    start_date = get_days_ago(30)
    end_date = get_today()

    print(f"\nDate range: {start_date} to {end_date}")
    print("\nCurrent queries in get_batch_analysis:")

    total_time = 0

    # Query 1: get_daily_range_lightweight
    t, _ = measure_query(
        "get_daily_range_lightweight",
        message_repo.get_daily_range_lightweight,
        start_date,
        end_date,
    )
    total_time += t

    # Query 2: get_user_token_totals
    t, _ = measure_query(
        "get_user_token_totals", message_repo.get_user_token_totals, start_date, end_date
    )
    total_time += t

    # Query 3: get_tool_token_totals
    t, _ = measure_query(
        "get_tool_token_totals", message_repo.get_tool_token_totals, start_date, end_date
    )
    total_time += t

    # Query 4: get_daily_token_totals
    t, _ = measure_query(
        "get_daily_token_totals", message_repo.get_daily_token_totals, start_date, end_date
    )
    total_time += t

    # Query 5: get_hourly_usage
    t, _ = measure_query("get_hourly_usage", message_repo.get_hourly_usage, start_date, end_date)
    total_time += t

    # Query 6: get_conversation_stats_summary
    t, _ = measure_query(
        "get_conversation_stats_summary", message_repo.get_conversation_stats_summary
    )
    total_time += t

    # Query 7: get_request_count_total
    t, _ = measure_query(
        "get_request_count_total", usage_repo.get_request_count_total, start_date, end_date
    )
    total_time += t

    print(f"\nTotal query time: {total_time:.3f}s")
    print("\n" + "=" * 60)

    # Test combined query approach
    print("\nTesting combined query approach:")

    start = time.time()
    # Single query to get all aggregations
    combined_query = """
        SELECT
            COUNT(*) as total_messages,
            SUM(tokens_used) as total_tokens,
            SUM(input_tokens) as total_input_tokens,
            SUM(output_tokens) as total_output_tokens,
            COUNT(DISTINCT tool_name) as unique_tools,
            COUNT(DISTINCT host_name) as unique_hosts,
            COUNT(DISTINCT sender_name) as unique_users
        FROM daily_messages
        WHERE date >= ? AND date <= ?
    """
    result = message_repo.db.fetch_one(combined_query, (start_date, end_date))
    elapsed = time.time() - start
    print(f"  Combined aggregation query: {elapsed:.3f}s")
    print(f"  Result: {result}")

    # Test daily aggregation in single query
    start = time.time()
    daily_query = """
        SELECT
            date,
            SUM(tokens_used) as total_tokens,
            COUNT(*) as message_count
        FROM daily_messages
        WHERE date >= ? AND date <= ?
        GROUP BY date
        ORDER BY date
    """
    daily_result = message_repo.db.fetch_all(daily_query, (start_date, end_date))
    elapsed = time.time() - start
    print(f"  Daily aggregation query: {elapsed:.3f}s ({len(daily_result)} rows)")

    # Test hourly aggregation in single query
    start = time.time()
    from app.repositories.database import is_postgresql

    if is_postgresql():
        hourly_query = """
            SELECT
                EXTRACT(HOUR FROM timestamp::timestamp) as hour,
                COUNT(*) as requests,
                SUM(tokens_used) as tokens
            FROM daily_messages
            WHERE date >= %s AND date <= %s AND timestamp IS NOT NULL AND timestamp::text != ''
            GROUP BY EXTRACT(HOUR FROM timestamp::timestamp)
            ORDER BY hour
        """
    else:
        hourly_query = """
            SELECT
                CAST(strftime('%H', timestamp) AS INTEGER) as hour,
                COUNT(*) as requests,
                SUM(tokens_used) as tokens
            FROM daily_messages
            WHERE date >= ? AND date <= ? AND timestamp IS NOT NULL AND timestamp != ''
            GROUP BY strftime('%H', timestamp)
            ORDER BY hour
        """
    hourly_result = message_repo.db.fetch_all(hourly_query, (start_date, end_date))
    elapsed = time.time() - start
    print(f"  Hourly aggregation query: {elapsed:.3f}s ({len(hourly_result)} rows)")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
