#!/usr/bin/env python3
"""
Backfill daily_usage from agent_sessions for Issue #2732.

Usage:
    # Backfill today
    python scripts/backfill_daily_usage.py

    # Backfill specific date
    python --date 2026-08-15

    # Backfill date range
    python --start-date 2026-08-01 --end-date 2026-08-15

    # Skip existing dates (idempotent)
    python --skip-existing

    # Dry run (show what would be done)
    python --dry-run
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from typing import Any

# Add project root to path
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
sys.path.insert(0, project_root)

from app.repositories.database import Database, is_postgresql
from app.repositories.usage_repo import UsageRepository
from app.utils.tool_names import normalize_tool_name

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Backfill daily_usage from agent_sessions")
    parser.add_argument(
        "--date",
        help="Specific date to backfill (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--start-date",
        help="Start date for range (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end-date",
        help="End date for range (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip dates that already have data in daily_usage",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    return parser.parse_args()


def get_dates_to_backfill(args: argparse.Namespace) -> list[str]:
    """Determine which dates to backfill."""
    if args.date:
        return [args.date]

    if args.start_date and args.end_date:
        start = datetime.strptime(args.start_date, "%Y-%m-%d")
        end = datetime.strptime(args.end_date, "%Y-%m-%d")
        dates = []
        current = start
        while current <= end:
            dates.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)
        return dates

    # Default: today
    return [datetime.now().strftime("%Y-%m-%d")]


def check_date_has_data(repo: UsageRepository, date: str) -> bool:
    """Check if daily_usage already has data for a date."""
    try:
        rows = repo.get_usage_rows_by_date(date)
        return len(rows) > 0
    except Exception:
        return False


def aggregate_sessions_for_date(db: Database, date: str) -> list[dict[str, Any]]:
    """Aggregate agent_sessions data for a specific date.

    Args:
        db: Database instance.
        date: Date string (YYYY-MM-DD).

    Returns:
        List of aggregated usage records by tool_name/host_name.
    """
    # Use database-compatible aggregation function
    # PostgreSQL: string_agg(DISTINCT model, ',')
    # SQLite: GROUP_CONCAT(DISTINCT model)
    if is_postgresql():
        query = """
            SELECT
                COALESCE(tool_name, 'qwen-code') as tool_name,
                COALESCE(host_name, 'localhost') as host_name,
                tenant_id,
                SUM(COALESCE(total_tokens, 0)) as tokens_used,
                SUM(COALESCE(total_input_tokens, 0)) as input_tokens,
                SUM(COALESCE(total_output_tokens, 0)) as output_tokens,
                COUNT(*) as request_count,
                string_agg(DISTINCT model, ',') as models_concat
            FROM agent_sessions
            WHERE CAST(created_at AS DATE) = %s
              AND workspace_type IN ('local', 'remote', 'terminal')
            GROUP BY tool_name, host_name, tenant_id
        """
    else:
        query = """
            SELECT
                COALESCE(tool_name, 'qwen-code') as tool_name,
                COALESCE(host_name, 'localhost') as host_name,
                tenant_id,
                SUM(COALESCE(total_tokens, 0)) as tokens_used,
                SUM(COALESCE(total_input_tokens, 0)) as input_tokens,
                SUM(COALESCE(total_output_tokens, 0)) as output_tokens,
                COUNT(*) as request_count,
                GROUP_CONCAT(DISTINCT model) as models_concat
            FROM agent_sessions
            WHERE CAST(created_at AS DATE) = ?
              AND workspace_type IN ('local', 'remote', 'terminal')
            GROUP BY tool_name, host_name, tenant_id
        """

    rows = db.fetch_all(query, (date,))

    results = []
    for row in rows:
        # Parse models from concat
        models = []
        if row.get("models_concat"):
            models = list(set(row["models_concat"].split(",")))

        results.append(
            {
                "tool_name": normalize_tool_name(row["tool_name"]),
                "host_name": row["host_name"],
                "tenant_id": row["tenant_id"],
                "tokens_used": row["tokens_used"] or 0,
                "input_tokens": row["input_tokens"] or 0,
                "output_tokens": row["output_tokens"] or 0,
                "cache_tokens": 0,  # Not tracked in agent_sessions
                "request_count": row["request_count"] or 0,
                "models_used": models if models else None,
            }
        )

    return results


def backfill_date(
    repo: UsageRepository,
    db: Database,
    date: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Backfill a single date.

    Args:
        repo: UsageRepository instance.
        db: Database instance.
        date: Date string (YYYY-MM-DD).
        dry_run: If True, don't actually write.

    Returns:
        Dict with backfill statistics.
    """
    stats = {
        "date": date,
        "aggregated": 0,
        "written": 0,
        "skipped": 0,
        "errors": 0,
    }

    try:
        # Aggregate from agent_sessions
        records = aggregate_sessions_for_date(db, date)
        stats["aggregated"] = len(records)

        if dry_run:
            logger.info(
                "[DRY RUN] Would write %d records for %s",
                len(records),
                date,
            )
            for rec in records:
                logger.info(
                    "  - %s/%s (tenant=%d): tokens=%d, requests=%d",
                    rec["tool_name"],
                    rec["host_name"],
                    rec["tenant_id"],
                    rec["tokens_used"],
                    rec["request_count"],
                )
            stats["written"] = len(records)
        else:
            # Write each record using increment_usage
            for rec in records:
                try:
                    success = repo.increment_usage(
                        tool_name=rec["tool_name"],
                        host_name=rec["host_name"],
                        tenant_id=rec["tenant_id"],
                        tokens_used=rec["tokens_used"],
                        input_tokens=rec["input_tokens"],
                        output_tokens=rec["output_tokens"],
                        cache_tokens=rec["cache_tokens"],
                        request_count=rec["request_count"],
                        models_used=rec["models_used"],
                    )
                    if success:
                        stats["written"] += 1
                    else:
                        stats["errors"] += 1
                except Exception as e:
                    logger.error(
                        "Failed to write record for %s/%s: %s",
                        rec["tool_name"],
                        rec["host_name"],
                        e,
                    )
                    stats["errors"] += 1

    except Exception as e:
        logger.error("Failed to backfill %s: %s", date, e)
        stats["errors"] += 1

    return stats


def main() -> int:
    """Main entry point."""
    args = parse_args()

    # Initialize database and repository
    db = Database()
    repo = UsageRepository()

    # Get dates to process
    dates = get_dates_to_backfill(args)

    logger.info("Processing %d date(s)", len(dates))

    total_stats = {
        "aggregated": 0,
        "written": 0,
        "skipped": 0,
        "errors": 0,
    }

    for date in dates:
        # Check if skip-existing
        if args.skip_existing and check_date_has_data(repo, date):
            logger.info("Skipping %s (already has data)", date)
            total_stats["skipped"] += 1
            continue

        logger.info("Backfilling %s...", date)

        stats = backfill_date(repo, db, date, dry_run=args.dry_run)

        for key in ["aggregated", "written", "skipped", "errors"]:
            total_stats[key] += stats.get(key, 0)

    # Summary
    logger.info("=" * 60)
    logger.info("Backfill Summary:")
    logger.info("  Dates processed: %d", len(dates))
    logger.info("  Records aggregated: %d", total_stats["aggregated"])
    logger.info("  Records written: %d", total_stats["written"])
    logger.info("  Dates skipped: %d", total_stats["skipped"])
    logger.info("  Errors: %d", total_stats["errors"])

    if args.dry_run:
        logger.info("  (DRY RUN - no changes made)")

    return 0 if total_stats["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
