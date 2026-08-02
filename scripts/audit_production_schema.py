#!/usr/bin/env python3
"""Audit production database schema status.

This script checks all production database instances and generates a report
identifying databases that need migration.

Issue: #2190

Usage:
    python scripts/audit_production_schema.py [--output report.txt]

The script:
1. Checks alembic_version status for each database
2. Identifies missing columns
3. Generates migration priority report
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import sqlalchemy as sa

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.repositories.database import Database
from app.repositories.schema_guard import get_database_revision

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def check_column_exists(connection: sa.engine.Connection, table: str, column: str) -> bool:
    """Check if a column exists in a table."""
    inspector = sa.inspect(connection)
    if table not in inspector.get_table_names():
        return False

    columns = {col["name"] for col in inspector.get_columns(table)}
    return column in columns


def audit_database(db_url: str) -> dict:
    """Audit a single database for schema status."""
    result = {
        "url": db_url.split("@")[-1] if "@" in db_url else db_url,  # Hide credentials
        "status": "unknown",
        "revision": None,
        "missing_columns": [],
        "errors": [],
    }

    try:
        engine = sa.create_engine(db_url)
        with engine.connect() as conn:
            # Check revision
            result["revision"] = get_database_revision(conn)

            # Check for missing columns in agent_sessions
            agent_sessions_columns = [
                "project_id",
                "project_path",
                "request_count",
                "workspace_type",
                "remote_machine_id",
                "paused_at",
            ]
            for col in agent_sessions_columns:
                if not check_column_exists(conn, "agent_sessions", col):
                    result["missing_columns"].append(f"agent_sessions.{col}")

            # Check for missing columns in session_messages
            session_messages_columns = [
                "source",
                "source_timestamp",
                "external_message_id",
                "content_blocks",
                "milestone_id",
            ]
            for col in session_messages_columns:
                if not check_column_exists(conn, "session_messages", col):
                    result["missing_columns"].append(f"session_messages.{col}")

            result["status"] = "ok" if not result["missing_columns"] else "needs_migration"

    except Exception as e:
        result["status"] = "error"
        result["errors"].append(str(e))

    return result


def main() -> int:
    """Run production database schema audit."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", "-o", help="Output file for report")
    parser.add_argument("--json", action="store_true", help="Output in JSON format")
    args = parser.parse_args()

    # For now, we audit the default database from DATABASE_URL
    # In a real deployment, this would be extended to check multiple databases
    from scripts.shared.db import _get_db_url

    db_url = _get_db_url()

    logger.info(f"Auditing database schema status...")
    logger.info(f"Database: {db_url.split('@')[-1] if '@' in db_url else db_url}")

    result = audit_database(db_url)

    # Generate report
    timestamp = datetime.now().isoformat()

    if args.json:
        report = json.dumps(
            {
                "timestamp": timestamp,
                "database": result,
            },
            indent=2,
        )
    else:
        report_lines = [
            "=" * 70,
            "Production Database Schema Audit Report",
            "=" * 70,
            f"Timestamp: {timestamp}",
            "",
            f"Database: {result['url']}",
            f"Status: {result['status']}",
            f"Current Revision: {result['revision'] or 'N/A'}",
            "",
        ]

        if result["missing_columns"]:
            report_lines.extend(
                [
                    "Missing Columns:",
                    "-" * 40,
                ]
            )
            for col in result["missing_columns"]:
                report_lines.append(f"  - {col}")
            report_lines.append("")
            report_lines.append("ACTION REQUIRED: Run 'alembic upgrade head' to migrate database")
        else:
            report_lines.append("All expected columns present ✓")

        if result["errors"]:
            report_lines.extend(
                [
                    "",
                    "Errors:",
                    "-" * 40,
                ]
            )
            for error in result["errors"]:
                report_lines.append(f"  - {error}")

        report_lines.append("")
        report_lines.append("=" * 70)
        report = "\n".join(report_lines)

    # Output report
    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
        logger.info(f"Report written to {args.output}")
    else:
        print(report)

    # Return exit code
    if result["status"] == "error":
        return 2
    elif result["status"] == "needs_migration":
        return 1
    else:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())