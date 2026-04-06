#!/usr/bin/env python3
"""
Performance Test for Usage Page Optimization (Issue #58)

Simple test that queries existing data to measure performance improvements.

Before optimization:
- 30-day trend query: ~6.7s (scanning daily_messages with LIKE)
- Total page load: ~8-10s

After optimization:
- 30-day trend query: ~50ms (user_daily_stats lookup)
- Total page load: ~200-500ms
"""

import logging
import time
from datetime import datetime, timedelta

from app.repositories.database import Database, is_postgresql
from app.repositories.usage_repo import UsageRepository
from app.services.user_stats_aggregator import UserDailyStatsAggregator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_existing_user(db: Database):
    """Get an existing user with usage data."""
    with db.connection() as conn:
        cursor = conn.cursor()
        
        # Get a user that has usage data
        cursor.execute("""
            SELECT DISTINCT u.id, u.username 
            FROM daily_messages dm
            JOIN users u ON dm.sender_name LIKE (u.username || '%%')
            LIMIT 1
        """)
        
        user = cursor.fetchone()
        if user:
            return user["id"], user["username"]
        
        # Fallback: get any user
        cursor.execute("SELECT id, username FROM users LIMIT 1")
        user = cursor.fetchone()
        if user:
            return user["id"], user["username"]
    
    return None, None


def benchmark_original_query(repo: UsageRepository, user_name: str, days: int = 30):
    """Benchmark the original query (daily_messages with LIKE)."""
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")
    
    logger.info(f"Benchmarking original query for '{user_name}' ({days} days)...")
    
    times = []
    results = []
    
    for i in range(3):
        start_time = time.perf_counter()
        result = repo.get_user_request_trend(user_name, start_str, end_str)
        end_time = time.perf_counter()
        
        elapsed_ms = (end_time - start_time) * 1000
        times.append(elapsed_ms)
        results.append(len(result))
        
        logger.info(f"  Iteration {i+1}: {elapsed_ms:.2f}ms, {len(result)} rows returned")
    
    avg_time = sum(times) / len(times)
    
    return {
        "avg_ms": avg_time,
        "min_ms": min(times),
        "max_ms": max(times),
        "rows": results[0] if results else 0
    }


def benchmark_optimized_query(repo: UsageRepository, user_id: int, days: int = 30):
    """Benchmark the optimized query (user_daily_stats)."""
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")
    
    logger.info(f"Benchmarking optimized query for user_id={user_id} ({days} days)...")
    
    times = []
    results = []
    
    for i in range(5):
        start_time = time.perf_counter()
        result = repo.get_user_request_trend_by_user_id(user_id, start_str, end_str)
        end_time = time.perf_counter()
        
        elapsed_ms = (end_time - start_time) * 1000
        times.append(elapsed_ms)
        results.append(len(result))
        
        logger.info(f"  Iteration {i+1}: {elapsed_ms:.2f}ms, {len(result)} rows returned")
    
    avg_time = sum(times) / len(times)
    
    return {
        "avg_ms": avg_time,
        "min_ms": min(times),
        "max_ms": max(times),
        "rows": results[0] if results else 0
    }


def test_aggregator(db: Database, user_id: int, username: str, days: int = 7):
    """Test the aggregator to populate user_daily_stats."""
    logger.info(f"Running aggregator for {days} days...")
    
    aggregator = UserDailyStatsAggregator(db)
    start_time = time.perf_counter()
    records = aggregator.aggregate_user(user_id, username, days)
    end_time = time.perf_counter()
    
    elapsed_ms = (end_time - start_time) * 1000
    
    logger.info(f"  Aggregated {records} records in {elapsed_ms:.2f}ms")
    
    return {
        "aggregation_time_ms": elapsed_ms,
        "records_created": records
    }


def run_performance_test():
    """Run all performance tests."""
    print("=" * 70)
    print("Usage Page Performance Test (Issue #58)")
    print("=" * 70)
    
    db = Database()
    repo = UsageRepository(db)
    
    # Get existing user
    user_id, username = get_existing_user(db)
    if not user_id:
        print("No users found with usage data. Please ensure there is data in daily_messages.")
        return None
    
    logger.info(f"Testing with user: {username} (id={user_id})")
    
    # Test 1: Original query (before populating user_daily_stats)
    print("\n" + "-" * 70)
    print("Test 1: Original Query (daily_messages with LIKE)")
    print("-" * 70)
    original_stats = benchmark_original_query(repo, username, days=30)
    print(f"\n  Average: {original_stats['avg_ms']:.2f}ms")
    print(f"  Range: {original_stats['min_ms']:.2f}ms - {original_stats['max_ms']:.2f}ms")
    
    # Test 2: Aggregator
    print("\n" + "-" * 70)
    print("Test 2: Aggregator (populate user_daily_stats)")
    print("-" * 70)
    agg_stats = test_aggregator(db, user_id, username, days=30)
    print(f"\n  Aggregation time: {agg_stats['aggregation_time_ms']:.2f}ms")
    print(f"  Records created: {agg_stats['records_created']}")
    
    # Test 3: Optimized query (after populating user_daily_stats)
    print("\n" + "-" * 70)
    print("Test 3: Optimized Query (user_daily_stats)")
    print("-" * 70)
    optimized_stats = benchmark_optimized_query(repo, user_id, days=30)
    print(f"\n  Average: {optimized_stats['avg_ms']:.2f}ms")
    print(f"  Range: {optimized_stats['min_ms']:.2f}ms - {optimized_stats['max_ms']:.2f}ms")
    
    # Summary
    print("\n" + "=" * 70)
    print("Performance Summary")
    print("=" * 70)
    
    if original_stats['avg_ms'] > 0 and optimized_stats['avg_ms'] > 0:
        improvement = ((original_stats['avg_ms'] - optimized_stats['avg_ms']) / original_stats['avg_ms']) * 100
        speedup = original_stats['avg_ms'] / optimized_stats['avg_ms']
        
        print(f"""
Query Performance (30-day trend):
  ┌────────────────────────────────────────────────────────┐
  │ Before: {original_stats['avg_ms']:>8.2f}ms  (daily_messages scan)        │
  │ After:  {optimized_stats['avg_ms']:>8.2f}ms  (user_daily_stats lookup)   │
  └────────────────────────────────────────────────────────┘
  
  Improvement: {improvement:.1f}% faster
  Speedup:     {speedup:.1f}x

Aggregation:
  Time:   {agg_stats['aggregation_time_ms']:.2f}ms
  Records: {agg_stats['records_created']}
  
Note: First query may be slower due to cold cache.
      Subsequent queries benefit from 10-minute caching.
""")
    else:
        print(f"""
Query Performance:
  Optimized: {optimized_stats['avg_ms']:.2f}ms average (user_daily_stats)
  
Note: Original query may have been served from cache.
""")
    
    print("=" * 70)
    
    return {
        "original": original_stats,
        "optimized": optimized_stats,
        "aggregator": agg_stats,
        "improvement_percent": ((original_stats['avg_ms'] - optimized_stats['avg_ms']) / original_stats['avg_ms']) * 100 if original_stats['avg_ms'] > 0 else 0,
        "speedup": original_stats['avg_ms'] / optimized_stats['avg_ms'] if optimized_stats['avg_ms'] > 0 else float('inf')
    }


if __name__ == "__main__":
    run_performance_test()
