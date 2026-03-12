#!/usr/bin/env python3
"""
Test script to measure Messages API query performance.
Issue #20: Messages page loading slowly.

This script tests the query performance for different filter combinations.
"""

import os
import sys
import time
from datetime import datetime

# Add shared directory to path
script_dir = os.path.dirname(os.path.abspath(__file__))
shared_dir = os.path.join(script_dir, 'shared')
if shared_dir not in sys.path:
    sys.path.insert(0, shared_dir)

import db
import utils

def test_query_performance():
    """Test query performance for different filter combinations."""
    today = utils.get_today()
    
    print(f"Testing Messages API query performance")
    print(f"Today's date: {today}")
    print("=" * 60)
    
    # Test cases with different filter combinations
    test_cases = [
        {
            'name': 'Date only (no filters)',
            'params': {'date': today}
        },
        {
            'name': 'Date + Tool',
            'params': {'date': today, 'tool_name': 'claude'}
        },
        {
            'name': 'Date + Host',
            'params': {'date': today, 'host_name': 'localhost'}
        },
        {
            'name': 'Date + Tool + Host',
            'params': {'date': today, 'tool_name': 'claude', 'host_name': 'localhost'}
        },
        {
            'name': 'Date + Roles',
            'params': {'date': today, 'roles': ['user', 'assistant']}
        },
        {
            'name': 'Date + Tool + Roles',
            'params': {'date': today, 'tool_name': 'claude', 'roles': ['user', 'assistant']}
        },
        {
            'name': 'Full filters',
            'params': {'date': today, 'tool_name': 'claude', 'host_name': 'localhost', 'roles': ['user', 'assistant']}
        },
    ]
    
    results = []
    
    for test_case in test_cases:
        name = test_case['name']
        params = test_case['params']
        
        # Measure query time
        start_time = time.time()
        result = db.get_messages_by_date(**params)
        end_time = time.time()
        
        query_time_ms = (end_time - start_time) * 1000
        total_messages = result.get('total', 0)
        
        results.append({
            'name': name,
            'time_ms': query_time_ms,
            'total': total_messages
        })
        
        print(f"\nTest: {name}")
        print(f"  Query time: {query_time_ms:.2f} ms")
        print(f"  Total messages: {total_messages}")
        
        # Performance rating
        if query_time_ms < 100:
            print(f"  Rating: ✓ Excellent (< 100ms)")
        elif query_time_ms < 500:
            print(f"  Rating: ✓ Good (< 500ms)")
        elif query_time_ms < 1000:
            print(f"  Rating: ⚠ Acceptable (< 1s)")
        else:
            print(f"  Rating: ✗ Slow (> 1s) - Needs optimization!")
    
    print("\n" + "=" * 60)
    print("Summary:")
    print("-" * 60)
    
    # Find slowest query
    slowest = max(results, key=lambda x: x['time_ms'])
    fastest = min(results, key=lambda x: x['time_ms'])
    
    print(f"Fastest query: {fastest['name']} ({fastest['time_ms']:.2f} ms)")
    print(f"Slowest query: {slowest['name']} ({slowest['time_ms']:.2f} ms)")
    
    # Check if any query is too slow
    slow_queries = [r for r in results if r['time_ms'] > 1000]
    if slow_queries:
        print(f"\n⚠ Warning: {len(slow_queries)} queries are slower than 1 second!")
        print("Consider optimizing these queries or adding indexes.")
    else:
        print("\n✓ All queries are performing well!")

if __name__ == '__main__':
    test_query_performance()
