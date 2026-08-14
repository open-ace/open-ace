"""
Quota Health Check Script

Detects quota allocation issues:
- Users with quotas exceeding tenant limits
- Tenants with total allocated quota exceeding their limits

Usage:
    python scripts/check_quota_health.py --tenant-id 1
    python scripts/check_quota_health.py --all
    python scripts/check_quota_health.py --json
"""

import argparse
import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.repositories.database import Database
from app.repositories.tenant_repo import TenantRepository
from app.repositories.user_repo import UserRepository
from app.schemas.quota import TOKEN_QUOTA_MULTIPLIER


def check_tenant_quota_health(tenant_id: int, db: Database) -> dict:
    """
    Check quota health for a specific tenant.

    Returns:
        Dict with tenant_id, status, allocated, limit, and over_by (if applicable)
    """
    tenant_repo = TenantRepository(db)
    user_repo = UserRepository(db)

    tenant = tenant_repo.get_by_id(tenant_id)
    if not tenant:
        return {
            "tenant_id": tenant_id,
            "status": "error",
            "error": "Tenant not found",
        }

    tenant_quota = tenant.quota

    # Calculate allocated quotas from all users
    users = user_repo.get_all_users(tenant_id=tenant_id)

    allocated = {
        "daily_token": 0,
        "monthly_token": 0,
        "daily_request": 0,
        "monthly_request": 0,
    }

    active_users = 0
    for user in users:
        if user.get("is_active", True):
            active_users += 1
            if user.get("daily_token_quota"):
                allocated["daily_token"] += user["daily_token_quota"]
            if user.get("monthly_token_quota"):
                allocated["monthly_token"] += user["monthly_token_quota"]
            if user.get("daily_request_quota"):
                allocated["daily_request"] += user["daily_request_quota"]
            if user.get("monthly_request_quota"):
                allocated["monthly_request"] += user["monthly_request_quota"]

    # Convert allocated tokens to actual count for comparison
    allocated_daily_tokens_actual = allocated["daily_token"] * TOKEN_QUOTA_MULTIPLIER
    allocated_monthly_tokens_actual = allocated["monthly_token"] * TOKEN_QUOTA_MULTIPLIER

    # Check if over-allocated
    is_over_allocated = False
    over_by = {}

    if (
        tenant_quota.daily_token_limit
        and allocated_daily_tokens_actual > tenant_quota.daily_token_limit
    ):
        is_over_allocated = True
        over_by["daily_token"] = allocated_daily_tokens_actual - tenant_quota.daily_token_limit

    if (
        tenant_quota.monthly_token_limit
        and allocated_monthly_tokens_actual > tenant_quota.monthly_token_limit
    ):
        is_over_allocated = True
        over_by["monthly_token"] = (
            allocated_monthly_tokens_actual - tenant_quota.monthly_token_limit
        )

    if (
        tenant_quota.daily_request_limit
        and allocated["daily_request"] > tenant_quota.daily_request_limit
    ):
        is_over_allocated = True
        over_by["daily_request"] = allocated["daily_request"] - tenant_quota.daily_request_limit

    if (
        tenant_quota.monthly_request_limit
        and allocated["monthly_request"] > tenant_quota.monthly_request_limit
    ):
        is_over_allocated = True
        over_by["monthly_request"] = (
            allocated["monthly_request"] - tenant_quota.monthly_request_limit
        )

    result = {
        "tenant_id": tenant_id,
        "tenant_name": tenant.name,
        "status": "over_allocated" if is_over_allocated else "ok",
        "allocated": {
            "daily_token": allocated["daily_token"],
            "monthly_token": allocated["monthly_token"],
            "daily_request": allocated["daily_request"],
            "monthly_request": allocated["monthly_request"],
        },
        "limit": {
            "daily_token": tenant_quota.daily_token_limit,
            "monthly_token": tenant_quota.monthly_token_limit,
            "daily_request": tenant_quota.daily_request_limit,
            "monthly_request": tenant_quota.monthly_request_limit,
        },
        "user_count": {
            "total": len(users),
            "active": active_users,
            "max": tenant_quota.max_users,
        },
    }

    if is_over_allocated:
        result["over_by"] = over_by

    return result


def check_all_tenants(db: Database) -> list[dict]:
    """Check quota health for all tenants."""
    tenant_repo = TenantRepository(db)

    tenants = tenant_repo.get_all(include_deleted=False)
    results = []

    for tenant in tenants:
        if tenant.id:
            result = check_tenant_quota_health(tenant.id, db)
            results.append(result)

    return results


def format_quota_value(value: int | None, unit: str = "") -> str:
    """Format quota value for display."""
    if value is None:
        return "unlimited"
    if unit == "M":
        return f"{value}M ({value * TOKEN_QUOTA_MULTIPLIER:,} tokens)"
    return f"{value:,}"


def print_health_report(result: dict, json_output: bool = False) -> None:
    """Print health check result."""
    if json_output:
        print(json.dumps(result, indent=2, default=str))
        return

    print(f"\n{'=' * 60}")
    print(f"Tenant: {result.get('tenant_name', 'N/A')} (ID: {result['tenant_id']})")
    print(f"Status: {result['status'].upper()}")
    print(f"{'=' * 60}")

    if result.get("error"):
        print(f"Error: {result['error']}")
        return

    # Print allocated vs limit
    print("\nAllocated Quotas:")
    print(f"  Daily Token:    {format_quota_value(result['allocated']['daily_token'], 'M')}")
    print(f"  Monthly Token:  {format_quota_value(result['allocated']['monthly_token'], 'M')}")
    print(f"  Daily Request:  {format_quota_value(result['allocated']['daily_request'])}")
    print(f"  Monthly Request: {format_quota_value(result['allocated']['monthly_request'])}")

    print("\nTenant Limits:")
    print(f"  Daily Token:    {format_quota_value(result['limit']['daily_token'], 'M')}")
    print(f"  Monthly Token:  {format_quota_value(result['limit']['monthly_token'], 'M')}")
    print(f"  Daily Request:  {format_quota_value(result['limit']['daily_request'])}")
    print(f"  Monthly Request: {format_quota_value(result['limit']['monthly_request'])}")

    print(
        f"\nUsers: {result['user_count']['active']} active / {result['user_count']['total']} total (max: {result['user_count']['max']})"
    )

    if result["status"] == "over_allocated" and result.get("over_by"):
        print("\n⚠️  OVER-ALLOCATED:")
        for key, value in result["over_by"].items():
            unit = "M" if "token" in key else ""
            print(f"  {key}: {format_quota_value(value, unit)} over limit")


def main():
    parser = argparse.ArgumentParser(
        description="Check quota health for tenants",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python scripts/check_quota_health.py --tenant-id 1
    python scripts/check_quota_health.py --all
    python scripts/check_quota_health.py --all --json > quota_health_report.json
        """,
    )

    parser.add_argument(
        "--tenant-id",
        type=int,
        help="Check specific tenant by ID",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Check all tenants",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output in JSON format",
    )

    args = parser.parse_args()

    if not args.tenant_id and not args.all:
        parser.print_help()
        print("\nError: Please specify --tenant-id or --all")
        sys.exit(1)

    db = Database()

    if args.all:
        results = check_all_tenants(db)

        if args.json:
            print(json.dumps(results, indent=2, default=str))
        else:
            over_allocated_count = sum(1 for r in results if r["status"] == "over_allocated")
            ok_count = sum(1 for r in results if r["status"] == "ok")
            error_count = sum(1 for r in results if r["status"] == "error")

            print("\n" + "=" * 60)
            print("QUOTA HEALTH CHECK SUMMARY")
            print("=" * 60)
            print(f"Total tenants checked: {len(results)}")
            print(f"  OK:              {ok_count}")
            print(f"  Over-allocated:  {over_allocated_count}")
            print(f"  Errors:          {error_count}")

            # Print detailed report for over-allocated tenants
            if over_allocated_count > 0:
                print("\n" + "=" * 60)
                print("OVER-ALLOCATED TENANTS:")
                print("=" * 60)

                for result in results:
                    if result["status"] == "over_allocated":
                        print_health_report(result, json_output=False)

        # Exit with error code if any tenant is over-allocated
        sys.exit(1 if over_allocated_count > 0 else 0)

    else:
        # Check specific tenant
        result = check_tenant_quota_health(args.tenant_id, db)
        print_health_report(result, json_output=args.json)

        # Exit with error code if tenant is over-allocated
        sys.exit(1 if result["status"] == "over_allocated" else 0)


if __name__ == "__main__":
    main()
