#!/usr/bin/env python3
"""Scan existing API key base_url configurations for SSRF policy violations.

This script scans the api_key_store table for custom base_url entries
and generates a migration report for administrator review before enabling
SSRF protection (Issue #1894).

Usage:
    python scripts/scan_existing_base_urls.py [--tenant TENANT_ID] [--output REPORT.json]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _get_db_connection():
    """Get database connection."""
    try:
        from app.repositories.database import get_connection

        return get_connection()
    except ImportError:
        # Fallback for standalone execution
        import sqlite3

        db_path = Path(__file__).parent.parent / "openace.db"
        if not db_path.exists():
            db_path = Path(__file__).parent.parent / "data" / "openace.db"
        if not db_path.exists():
            raise FileNotFoundError("Database not found")
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn


def scan_base_urls(tenant_filter: int | None = None) -> dict[str, Any]:
    """Scan all base_url entries and check SSRF policy.

    Args:
        tenant_filter: Optional tenant ID to filter scan.

    Returns:
        Dictionary with scan results and recommendations.
    """
    from app.utils.llm_proxy_url_validator import (
        get_allowed_hosts,
        sanitize_error_message,
        validate_llm_proxy_url,
    )

    conn = _get_db_connection()
    cursor = conn.cursor()

    try:
        # Query all API keys with base_url
        if tenant_filter is not None:
            cursor.execute(
                """
                SELECT id, tenant_id, provider, key_name, base_url, scope
                FROM api_key_store
                WHERE base_url IS NOT NULL AND base_url != '' AND tenant_id = ?
                ORDER BY tenant_id, provider, key_name
                """,
                (tenant_filter,),
            )
        else:
            cursor.execute(
                """
                SELECT id, tenant_id, provider, key_name, base_url, scope
                FROM api_key_store
                WHERE base_url IS NOT NULL AND base_url != ''
                ORDER BY tenant_id, provider, key_name
                """
            )

        rows = cursor.fetchall()
    finally:
        conn.close()

    results = {
        "scan_time": datetime.now(timezone.utc).isoformat(),
        "total_scanned": 0,
        "blocked": [],
        "allowed": [],
        "in_allowlist": [],
        "recommendations": {
            "global_allowlist": [],
            "tenant_allowlists": {},
        },
    }

    allowed_hosts = get_allowed_hosts()
    global_allowlist = set(allowed_hosts.get(0, []))

    for row in rows:
        results["total_scanned"] += 1
        key_id = row["id"]
        tenant_id = row["tenant_id"]
        provider = row["provider"]
        key_name = row["key_name"]
        base_url = row["base_url"]

        # Check if already in allowlist
        tenant_allowlist = set(allowed_hosts.get(tenant_id, []))
        in_global = any(host.lower() in base_url.lower() for host in global_allowlist)
        in_tenant = any(host.lower() in base_url.lower() for host in tenant_allowlist)

        if in_global or in_tenant:
            results["in_allowlist"].append(
                {
                    "key_id": key_id,
                    "tenant_id": tenant_id,
                    "provider": provider,
                    "key_name": key_name,
                    "base_url": base_url,
                    "in_allowlist": "global" if in_global else "tenant",
                }
            )
            continue

        # Validate URL
        result = validate_llm_proxy_url(base_url, tenant_id, provider)

        if result.allowed:
            results["allowed"].append(
                {
                    "key_id": key_id,
                    "tenant_id": tenant_id,
                    "provider": provider,
                    "key_name": key_name,
                    "base_url": base_url,
                }
            )
        else:
            sanitized_error = sanitize_error_message(result.error or "Invalid URL")
            results["blocked"].append(
                {
                    "key_id": key_id,
                    "tenant_id": tenant_id,
                    "provider": provider,
                    "key_name": key_name,
                    "base_url": base_url,
                    "reason": result.error,
                    "sanitized_reason": sanitized_error,
                }
            )

            # Add to recommendations
            from urllib.parse import urlparse

            try:
                parsed = urlparse(base_url)
                host = parsed.hostname or ""
                if host:
                    if tenant_id not in results["recommendations"]["tenant_allowlists"]:
                        results["recommendations"]["tenant_allowlists"][tenant_id] = []
                    results["recommendations"]["tenant_allowlists"][tenant_id].append(host)
            except Exception:
                pass

    return results


def print_report(results: dict[str, Any]) -> None:
    """Print formatted migration report."""
    print("=" * 80)
    print("LLM Proxy Base URL Migration Report")
    print(f"Generated: {results['scan_time']}")
    print("=" * 80)
    print()

    print("Summary:")
    print(f"  Total base_urls scanned: {results['total_scanned']}")
    print(f"  Blocked by SSRF policy: {len(results['blocked'])}")
    print(f"  Already in allowlist: {len(results['in_allowlist'])}")
    print(f"  Public URLs (allowed): {len(results['allowed'])}")
    print()

    if results["blocked"]:
        print("Blocked Configurations:")
        print("-" * 80)
        for i, entry in enumerate(results["blocked"], 1):
            print(
                f"  {i}. tenant_id={entry['tenant_id']}, provider={entry['provider']}, key_name={entry['key_name']}"
            )
            print(
                f"     base_url: {entry['base_url'][:80]}{'...' if len(entry['base_url']) > 80 else ''}"
            )
            print(f"     reason: {entry['sanitized_reason']}")
            print()

    if results["blocked"]:
        print("Recommended Actions:")
        print("-" * 80)

        # Global allowlist recommendation
        all_hosts = set()
        for tenant_hosts in results["recommendations"]["tenant_allowlists"].values():
            all_hosts.update(tenant_hosts)

        if all_hosts:
            print()
            print("  Option A: Add to global allowlist (if shared across tenants)")
            print(f"    export OPENACE_LLM_PROXY_ALLOWED_HOSTS=\"{','.join(sorted(all_hosts))}\"")

        # Tenant-specific allowlists
        if results["recommendations"]["tenant_allowlists"]:
            tenant_lists = {}
            for tenant_id, hosts in results["recommendations"]["tenant_allowlists"].items():
                tenant_lists[str(tenant_id)] = sorted(set(hosts))
            print()
            print("  Option B: Add to tenant-specific allowlist")
            print(f"    export OPENACE_LLM_PROXY_TENANT_ALLOWLISTS='{json.dumps(tenant_lists)}'")

        print()
        print("Migration Status: PENDING")
        print()
        print("Next Steps:")
        print("  1. Review blocked configurations above")
        print("  2. Add required hosts to allowlist")
        print("  3. Re-run this script to verify")
        print("  4. Deploy SSRF protection code")

    print("=" * 80)


def main():
    parser = argparse.ArgumentParser(description="Scan API key base_url for SSRF policy")
    parser.add_argument(
        "--tenant",
        type=int,
        help="Filter by tenant ID",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output JSON report to file",
    )
    args = parser.parse_args()

    try:
        results = scan_base_urls(tenant_filter=args.tenant)

        if args.output:
            with open(args.output, "w") as f:
                json.dump(results, f, indent=2)
            logger.info("Report written to %s", args.output)
        else:
            print_report(results)

        # Return non-zero exit code if blocked configs found
        if results["blocked"]:
            sys.exit(1)

    except Exception as e:
        logger.error("Scan failed: %s", e)
        sys.exit(2)


if __name__ == "__main__":
    main()
