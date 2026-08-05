#!/usr/bin/env python3
"""
Daily Usage Conflict Detection (Issue #1824)

Detects potential conflicts in daily_usage before tenant_id backfill:
- Rows with same (date, tool_name, host_name) but different tenant_id
- Would violate new unique constraint (tenant_id, date, tool_name, host_name)

Usage:
    python scripts/check_daily_usage_conflicts.py
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.repositories.database import Database


def find_conflicts() -> list:
    """Find groups of rows with same (date, tool, host) but different tenant."""
    db = Database()
    rows = db.fetch_all(
        """
        SELECT
            date,
            tool_name,
            host_name,
            COUNT(DISTINCT COALESCE(tenant_id, 1)) as tenant_count,
            GROUP_CONCAT(COALESCE(tenant_id, 1)) as tenant_ids
        FROM daily_usage
        GROUP BY date, tool_name, host_name
        HAVING COUNT(DISTINCT COALESCE(tenant_id, 1)) > 1
        ORDER BY tenant_count DESC, date DESC
        """
    )
    return rows if rows else []


def count_conflict_rows() -> int:
    """Count total rows involved in conflicts."""
    db = Database()
    result = db.fetch_one(
        """
        SELECT COUNT(*) as count
        FROM daily_usage du
        INNER JOIN (
            SELECT date, tool_name, host_name
            FROM daily_usage
            GROUP BY date, tool_name, host_name
            HAVING COUNT(DISTINCT COALESCE(tenant_id, 1)) > 1
        ) conflicts
        ON du.date = conflicts.date
           AND du.tool_name = conflicts.tool_name
           AND du.host_name = conflicts.host_name
        """
    )
    return result["count"] if result else 0


def main():
    """Run conflict detection."""
    print("=" * 60)
    print("Daily Usage Conflict Detection (Issue #1824)")
    print("=" * 60)
    print()

    # Find conflicts
    conflicts = find_conflicts()
    conflict_count = len(conflicts)

    if conflict_count == 0:
        print("✅ No conflicts found")
        print()
        print("All (date, tool_name, host_name) tuples have unique tenant_id")
        print("Migration can proceed safely")
        sys.exit(0)

    # Report conflicts
    print(f"⚠️  Found {conflict_count} conflict groups")
    print()

    total_rows = count_conflict_rows()
    print(f"Total rows involved in conflicts: {total_rows}")
    print()

    # Show first 10 conflicts
    print("Top 10 conflict groups:")
    print("-" * 60)
    for i, row in enumerate(conflicts[:10]):
        print(f"{i+1}. date={row['date']}, tool={row['tool_name']}, host={row['host_name']}")
        print(f"   tenant_count={row['tenant_count']}, tenant_ids={row['tenant_ids']}")
    print()

    # Warning
    print("=" * 60)
    print("⚠️  WARNING: Conflicts detected")
    print()
    print("Migration will fail with duplicate key error if conflicts not resolved.")
    print("Options:")
    print("  1. Run scripts/resolve_daily_usage_conflicts.py --strategy=earliest")
    print("  2. Manual resolution (recommended for production data)")
    print("=" * 60)

    # Exit code based on conflict count
    if conflict_count > 10:
        print()
        print("❌ CRITICAL: Large number of conflicts detected")
        print("   Review data and resolve conflicts before migration")
        sys.exit(1)

    print()
    print("⚠️  Small number of conflicts detected")
    print("   Can proceed with resolution script")
    sys.exit(1)


if __name__ == "__main__":
    main()
