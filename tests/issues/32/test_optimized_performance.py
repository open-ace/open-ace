#!/usr/bin/env python3
"""
Test script to measure optimized query performance for trend analysis.
"""

import os
import sys
import time

# Add project root to path
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
)

from app.services.analysis_service import AnalysisService
from app.utils.helpers import get_days_ago, get_today


def main():
    """Test optimized query performance."""
    print("=" * 60)
    print("Optimized Query Performance Test")
    print("=" * 60)

    service = AnalysisService()

    start_date = get_days_ago(30)
    end_date = get_today()

    print(f"\nDate range: {start_date} to {end_date}")

    # Clear cache to test first request
    from app.utils.cache import get_cache

    cache = get_cache()
    cache.clear()

    # Test first request (no cache)
    print("\nTesting first request (no cache):")
    start = time.time()
    result = service.get_batch_analysis(start_date, end_date)
    elapsed = time.time() - start
    print(f"  Total time: {elapsed:.3f}s")

    # Show key metrics
    key_metrics = result.get("key_metrics", {})
    print(f"\n  Key metrics:")
    print(f"    total_tokens: {key_metrics.get('total_tokens', 0):,}")
    print(f"    total_messages: {key_metrics.get('total_messages', 0):,}")
    print(f"    total_requests: {key_metrics.get('total_requests', 0):,}")
    print(f"    unique_tools: {key_metrics.get('unique_tools', 0)}")
    print(f"    unique_hosts: {key_metrics.get('unique_hosts', 0)}")

    # Test cached request
    print("\nTesting cached request:")
    start = time.time()
    result = service.get_batch_analysis(start_date, end_date)
    elapsed_cached = time.time() - start
    print(f"  Total time: {elapsed_cached:.3f}s")

    print("\n" + "=" * 60)
    print("Performance Summary:")
    print(f"  First request: {elapsed:.3f}s")
    print(f"  Cached request: {elapsed_cached:.3f}s")
    print("=" * 60)

    # Compare with previous performance
    print("\nComparison with previous optimization:")
    print("  Before first optimization: 7.7s")
    print("  After first optimization: 2.2s")
    print(f"  After second optimization: {elapsed:.3f}s")

    improvement = (7.7 - elapsed) / 7.7 * 100
    print(f"\n  Total improvement: {improvement:.1f}%")


if __name__ == "__main__":
    main()
