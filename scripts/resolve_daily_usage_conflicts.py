#!/usr/bin/env python3
"""
Daily Usage Conflict Resolution (Issue #1824)

Resolves conflicts in daily_usage before tenant_id backfill:
- Detects rows with same (date, tool_name, host_name) but different tenant_id
- Applies resolution strategy to eliminate conflicts

Strategies:
  --strategy=earliest: Keep earliest tenant_id, update others to match
  --strategy=dry-run: Show what would be changed without making changes

Usage:
    python scripts/resolve_daily_usage_conflicts.py --strategy=earliest
    python scripts/resolve_daily_usage_conflicts.py --strategy=dry-run
"""

import argparse
import sys
from datetime import datetime
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
            GROUP_CONCAT(COALESCE(tenant_id, 1)) as tenant_ids,
            MIN(COALESCE(tenant_id, 1)) as earliest_tenant
        FROM daily_usage
        GROUP BY date, tool_name, host_name
        HAVING COUNT(DISTINCT COALESCE(tenant_id, 1)) > 1
        ORDER BY tenant_count DESC, date DESC
        """
    )
    return rows if rows else []


def resolve_conflict_earliest(date, tool_name, host_name, target_tenant, dry_run=False):
    """Resolve conflict by updating all rows to earliest tenant_id."""
    db = Database()

    if dry_run:
        # Show what would be changed
        rows = db.fetch_all(
            """
            SELECT id, tenant_id
            FROM daily_usage
            WHERE date = ? AND tool_name = ? AND host_name = ?
              AND COALESCE(tenant_id, 1) != ?
            """,
            (date, tool_name, host_name, target_tenant),
        )
        return len(rows) if rows else 0

    # Perform update
    db.execute(
        """
        UPDATE daily_usage
        SET tenant_id = ?
        WHERE date = ? AND tool_name = ? AND host_name = ?
          AND COALESCE(tenant_id, 1) != ?
        """,
        (target_tenant, date, tool_name, host_name, target_tenant),
    )

    # Return rows updated
    result = db.fetch_one("SELECT changes() as count")
    return result["count"] if result else 0


def main():
    """Run conflict resolution."""
    parser = argparse.ArgumentParser(description="Resolve daily_usage tenant conflicts")
    parser.add_argument(
        "--strategy",
        choices=["earliest", "dry-run"],
        default="dry-run",
        help="Resolution strategy (earliest or dry-run)",
    )
    args = parser.parse_args()

    dry_run = args.strategy == "dry-run"

    print("=" * 60)
    print("Daily Usage Conflict Resolution (Issue #1824)")
    print(f"Strategy: {args.strategy}")
    print("=" * 60)
    print()

    # Find conflicts
    conflicts = find_conflicts()

    if len(conflicts) == 0:
        print("✅ No conflicts found")
        sys.exit(0)

    print(f"Found {len(conflicts)} conflict groups")
    print()

    if dry_run:
        print("DRY RUN - No changes will be made")
        print()

    # Process each conflict
    total_updated = 0
    for i, row in enumerate(conflicts):
        date = row["date"]
        tool_name = row["tool_name"]
        host_name = row["host_name"]
        earliest_tenant = row["earliest_tenant"]

        print(f"{i+1}. Resolving: date={date}, tool={tool_name}, host={host_name}")
        print(f"   Target tenant_id: {earliest_tenant}")

        updated = resolve_conflict_earliest(
            date, tool_name, host_name, earliest_tenant, dry_run=dry_run
        )
        total_updated += updated

        if dry_run:
            print(f"   Would update: {updated} rows")
        else:
            print(f"   Updated: {updated} rows")

    # Summary
    print()
    print("=" * 60)
    print("Summary:")
    print(f"  Conflict groups: {len(conflicts)}")
    if dry_run:
        print(f"  Rows that would be updated: {total_updated}")
        print()
        print("To apply changes, run:")
        print("  python scripts/resolve_daily_usage_conflicts.py --strategy=earliest")
    else:
        print(f"  Rows updated: {total_updated}")
        print()
        print("✅ Conflicts resolved")
        print("Run scripts/check_daily_usage_conflicts.py to verify")
    print("=" * 60)

    sys.exit(0)


if __name__ == "__main__":
    main()
