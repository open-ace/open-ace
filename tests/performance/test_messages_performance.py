#!/usr/bin/env python3
"""
Test script to measure Messages API query performance.
Issue #20: Messages page loading slowly.

This script tests the query performance for different filter combinations
via the HTTP API.
"""

import os
import time

import requests

BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")
USERNAME = os.environ.get("TEST_USERNAME", "admin")
PASSWORD = os.environ.get("TEST_PASSWORD", "admin123")


def _get_authenticated_session():
    """Create an authenticated requests session."""
    session = requests.Session()
    resp = session.post(
        f"{BASE_URL}/api/auth/login", json={"username": USERNAME, "password": PASSWORD}
    )
    data = resp.json()
    if not data.get("success"):
        raise Exception(f"Login failed: {data}")
    return session


def test_query_performance():
    """Test query performance for different filter combinations."""
    today = time.strftime("%Y-%m-%d")

    print("Testing Messages API query performance")
    print(f"Today's date: {today}")
    print("=" * 60)

    session = _get_authenticated_session()

    # Test cases with different filter combinations
    test_cases = [
        {"name": "Date only (no filters)", "params": {"date": today}},
        {"name": "Date + Tool", "params": {"date": today, "tool_name": "claude"}},
        {"name": "Date + Host", "params": {"date": today, "host_name": "localhost"}},
        {
            "name": "Date + Tool + Host",
            "params": {"date": today, "tool_name": "claude", "host_name": "localhost"},
        },
        {"name": "Date + Roles", "params": {"date": today, "roles": "user,assistant"}},
        {
            "name": "Date + Tool + Roles",
            "params": {"date": today, "tool_name": "claude", "roles": "user,assistant"},
        },
        {
            "name": "Full filters",
            "params": {
                "date": today,
                "tool_name": "claude",
                "host_name": "localhost",
                "roles": "user,assistant",
            },
        },
    ]

    results = []

    for test_case in test_cases:
        name = test_case["name"]
        params = test_case["params"]

        # Measure API request time
        start_time = time.time()
        try:
            response = session.get(f"{BASE_URL}/api/analysis/messages", params=params, timeout=10)
            query_time_ms = (time.time() - start_time) * 1000
            data = response.json()
            total_messages = data.get("total", 0)
        except Exception as e:
            query_time_ms = (time.time() - start_time) * 1000
            total_messages = 0
            print(f"\nTest: {name}")
            print(f"  Error: {e}")
            results.append({"name": name, "time_ms": query_time_ms, "total": total_messages})
            continue

        results.append({"name": name, "time_ms": query_time_ms, "total": total_messages})

        print(f"\nTest: {name}")
        print(f"  Query time: {query_time_ms:.2f} ms")
        print(f"  Total messages: {total_messages}")

        # Performance rating
        if query_time_ms < 100:
            print("  Rating: ✓ Excellent (< 100ms)")
        elif query_time_ms < 500:
            print("  Rating: ✓ Good (< 500ms)")
        elif query_time_ms < 1000:
            print("  Rating: ⚠ Acceptable (< 1s)")
        else:
            print("  Rating: ✗ Slow (> 1s) - Needs optimization!")

    print("\n" + "=" * 60)
    print("Summary:")
    print("-" * 60)

    if not results:
        print("No results to summarize.")
        return

    slowest = max(results, key=lambda x: x["time_ms"])
    fastest = min(results, key=lambda x: x["time_ms"])

    print(f"Fastest query: {fastest['name']} ({fastest['time_ms']:.2f} ms)")
    print(f"Slowest query: {slowest['name']} ({slowest['time_ms']:.2f} ms)")

    slow_queries = [r for r in results if r["time_ms"] > 1000]
    if slow_queries:
        print(f"\nWarning: {len(slow_queries)} queries are slower than 1 second!")
        print("Consider optimizing these queries or adding indexes.")
    else:
        print("\nAll queries are performing well!")

    # Assert no query takes longer than 5 seconds
    max_time = max(r["time_ms"] for r in results) if results else 0
    assert max_time < 5000, (
        f"Query performance too slow: {max_time:.0f}ms exceeds 5s threshold. "
        f"Slowest: {slowest['name']}"
    )


if __name__ == "__main__":
    test_query_performance()
