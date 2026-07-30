#!/usr/bin/env python3
"""
Daily Usage Data Quality Check (Issue #1824)

Checks for data quality issues in daily_usage table before tenant_id backfill:
1. Rows with tenant_id=NULL (should be filled by server_default='1')
2. Duplicate (date, tool_name, host_name) across different tenants

Usage:
    python scripts/check_daily_usage_quality.py
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.repositories.database import Database


def check_tenant_id_null() -> int:
    """Count rows where tenant_id is NULL (should be filled by server_default)."""
    db = Database()
    result = db.fetch_one(
        "SELECT COUNT(*) as count FROM daily_usage WHERE tenant_id IS NULL"
    )
    return result["count"] if result else 0


def check_duplicates() -> int:
    """Count (date, tool, host) tuples with multiple tenants."""
    db = Database()
    result = db.fetch_one(
        """
        SELECT COUNT(*) as count
        FROM (
            SELECT date, tool_name, host_name
            FROM daily_usage
            GROUP BY date, tool_name, host_name
            HAVING COUNT(DISTINCT COALESCE(tenant_id, 1)) > 1
        )
        """
    )
    return result["count"] if result else 0


def check_total_rows() -> int:
    """Get total row count in daily_usage."""
    db = Database()
    result = db.fetch_one("SELECT COUNT(*) as count FROM daily_usage")
    return result["count"] if result else 0


def check_tenant_distribution() -> dict:
    """Get tenant distribution."""
    db = Database()
    rows = db.fetch_all(
        """
        SELECT tenant_id, COUNT(*) as count
        FROM daily_usage
        GROUP BY tenant_id
        ORDER BY count DESC
        LIMIT 10
        """
    )
    return {row["tenant_id"]: row["count"] for row in rows} if rows else {}


def main():
    """Run all data quality checks."""
    print("=" * 60)
    print("Daily Usage Data Quality Check (Issue #1824)")
    print("=" * 60)
    print()

    # Total rows
    total = check_total_rows()
    print(f"Total rows in daily_usage: {total}")
    print()

    # Check 1: tenant_id NULL
    null_tenant = check_tenant_id_null()
    print(f"Rows with tenant_id=NULL: {null_tenant}")
    if null_tenant > 0:
        print(f"  ⚠️  Should be filled by server_default='1'")
    print()

    # Check 2: duplicates
    duplicates = check_duplicates()
    print(f"(date, tool, host) tuples with multiple tenants: {duplicates}")
    if duplicates > 0:
        print(f"  ⚠️  Would violate unique constraint (tenant_id, date, tool, host)")
        print(f"     Run scripts/check_daily_usage_conflicts.py for details")
    print()

    # Tenant distribution
    distribution = check_tenant_distribution()
    print("Tenant distribution (top 10):")
    for tenant_id, count in distribution.items():
        pct = (count / total * 100) if total > 0 else 0
        print(f"  tenant_id={tenant_id}: {count} rows ({pct:.1f}%)")
    print()

    # Summary
    print("=" * 60)
    print("Summary:")
    print(f"  Total rows: {total}")
    print(f"  Rows with valid tenant_id: {total - null_tenant}")
    print(f"  Potential conflicts: {duplicates} tuples")
    print("=" * 60)

    # Exit code based on findings
    if null_tenant > 0:
        print()
        print("⚠️  WARNING: Rows with tenant_id=NULL detected")
        print("   Migration should fill these with server_default='1'")
        sys.exit(1)

    if duplicates > 10:
        print()
        print("⚠️  WARNING: Large number of potential conflicts")
        print("   Run scripts/check_daily_usage_conflicts.py for details")
        sys.exit(1)

    print()
    print("✅ Data quality check passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
