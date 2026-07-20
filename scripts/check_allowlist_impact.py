#!/usr/bin/env python3
"""
Allowlist Impact Assessment Tool (Issue #1894)

This script helps administrators assess the impact of allowlist changes
on existing API keys before making modifications.

Usage:
    python scripts/check_allowlist_impact.py --host <hostname>

Example:
    python scripts/check_allowlist_impact.py --host private-llm.internal
    python scripts/check_allowlist_impact.py --host 10.0.0.5
"""

import argparse
import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.repositories.database import Database


def check_allowlist_impact(host: str) -> dict:
    """
    Check which API keys depend on a specific host in their base_url.

    Args:
        host: The hostname or IP to check.

    Returns:
        Dict with:
        - host: The checked host
        - total_keys: Number of matching API keys
        - tenants_affected: Number of unique tenants
        - keys: List of key info (without sensitive data)
    """
    db = Database()

    # Find API keys whose base_url contains the host
    # Use LIKE for flexible matching (handles ports, paths, etc.)
    results = db.fetch_all(
        """
        SELECT id, tenant_id, provider, key_name, base_url, scope, is_active
        FROM api_key_store
        WHERE base_url IS NOT NULL
          AND (
            base_url LIKE ?
            OR base_url LIKE ?
            OR base_url LIKE ?
            OR base_url LIKE ?
          )
        ORDER BY tenant_id, provider, key_name
        """,
        (
            f"%{host}%",  # Hostname anywhere
            f"%{host}/%",  # Hostname with path
            f"%{host}:%",  # Hostname with port
            f"//{host}%",  # After scheme
        ),
    )

    keys = []
    tenants = set()

    for row in results:
        tenants.add(row["tenant_id"])
        keys.append(
            {
                "key_id": row["id"],
                "tenant_id": row["tenant_id"],
                "provider": row["provider"],
                "key_name": row["key_name"],
                "base_url": row["base_url"],
                "scope": row["scope"],
                "is_active": bool(row["is_active"]),
            }
        )

    return {
        "host": host,
        "total_keys": len(keys),
        "tenants_affected": len(tenants),
        "tenant_ids": sorted(tenants),
        "keys": keys,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Assess allowlist impact on API keys",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Check impact of removing a host:
    python scripts/check_allowlist_impact.py --host private-llm.internal

  Output format:
    - host: The checked hostname
    - total_keys: Number of API keys that reference this host
    - tenants_affected: Number of unique tenants impacted
    - keys: List of affected keys (without sensitive data)
        """,
    )
    parser.add_argument(
        "--host",
        required=True,
        help="Hostname or IP to check for dependencies",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output in JSON format (machine-readable)",
    )
    parser.add_argument(
        "--tenant",
        type=int,
        help="Filter results to a specific tenant ID",
    )

    args = parser.parse_args()

    result = check_allowlist_impact(args.host)

    # Apply tenant filter if specified
    if args.tenant:
        result["keys"] = [k for k in result["keys"] if k["tenant_id"] == args.tenant]
        result["total_keys"] = len(result["keys"])
        result["tenants_affected"] = len(set(k["tenant_id"] for k in result["keys"]))
        result["tenant_ids"] = sorted(set(k["tenant_id"] for k in result["keys"]))

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"\nAllowlist Impact Assessment for: {args.host}")
        print("=" * 50)
        print(f"Total API keys affected: {result['total_keys']}")
        print(f"Tenants affected: {result['tenants_affected']}")

        if result["tenant_ids"]:
            print(f"Tenant IDs: {', '.join(map(str, result['tenant_ids']))}")

        if result["keys"]:
            print("\nAffected Keys:")
            print("-" * 50)
            for key in result["keys"]:
                status = "active" if key["is_active"] else "inactive"
                print(
                    f"  - Key #{key['key_id']}: {key['key_name']} "
                    f"(tenant={key['tenant_id']}, provider={key['provider']}, scope={key['scope']}, {status})"
                )
                print(f"    base_url: {key['base_url']}")

        print("\nRecommendation:")
        if result["total_keys"] == 0:
            print("  No API keys depend on this host. Safe to remove from allowlist.")
        else:
            print(
                f"  WARNING: {result['total_keys']} API key(s) will be affected. "
                "Review before removing from allowlist."
            )


if __name__ == "__main__":
    main()