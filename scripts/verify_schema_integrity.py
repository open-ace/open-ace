#!/usr/bin/env python3
"""Verify database schema integrity after migration.

This script verifies that all expected tables, columns, and indexes exist
after running migrations.

Issue: #2190

Usage:
    python scripts/verify_schema_integrity.py

The script:
1. Checks all expected tables exist
2. Checks all expected columns exist
3. Checks critical indexes exist
4. Reports any discrepancies
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import sqlalchemy as sa

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# Expected tables (core subset for verification)
EXPECTED_TABLES = [
    "users",
    "sessions",
    "agent_sessions",
    "session_messages",
    "autonomous_workflows",
    "projects",
    "tenants",
]

# Expected columns for critical tables
EXPECTED_COLUMNS = {
    "agent_sessions": [
        "id",
        "session_id",
        "user_id",
        "tenant_id",
        "project_id",
        "project_path",
        "request_count",
        "workspace_type",
        "remote_machine_id",
        "paused_at",
    ],
    "session_messages": [
        "id",
        "session_id",
        "timestamp",
        "tenant_id",
        "source",
        "source_timestamp",
        "external_message_id",
        "content_blocks",
        "milestone_id",
    ],
}

# Critical indexes to verify
EXPECTED_INDEXES = {
    "agent_sessions": [
        "idx_agent_sessions_tenant_user",
        "idx_agent_sessions_tenant_updated",
    ],
}


def verify_tables(inspector: sa.Inspector) -> list[str]:
    """Verify all expected tables exist."""
    errors = []
    existing_tables = set(inspector.get_table_names())

    for table in EXPECTED_TABLES:
        if table not in existing_tables:
            errors.append(f"Missing table: {table}")

    return errors


def verify_columns(inspector: sa.Inspector) -> list[str]:
    """Verify all expected columns exist."""
    errors = []

    for table, expected_cols in EXPECTED_COLUMNS.items():
        try:
            existing_cols = {col["name"] for col in inspector.get_columns(table)}
            for col in expected_cols:
                if col not in existing_cols:
                    errors.append(f"Missing column: {table}.{col}")
        except Exception as e:
            errors.append(f"Error checking columns for {table}: {e}")

    return errors


def verify_indexes(inspector: sa.Inspector) -> list[str]:
    """Verify critical indexes exist."""
    errors = []

    for table, expected_idxs in EXPECTED_INDEXES.items():
        try:
            existing_idxs = {idx["name"] for idx in inspector.get_indexes(table)}
            for idx in expected_idxs:
                if idx not in existing_idxs:
                    errors.append(f"Missing index: {table}.{idx}")
        except Exception as e:
            errors.append(f"Error checking indexes for {table}: {e}")

    return errors


def main() -> int:
    """Run schema integrity verification."""
    from scripts.shared.db import _get_db_url

    db_url = _get_db_url()

    logger.info("Verifying database schema integrity...")
    logger.info(f"Database: {db_url.split('@')[-1] if '@' in db_url else db_url}")

    engine = sa.create_engine(db_url)

    all_errors = []

    try:
        with engine.connect() as conn:
            inspector = sa.inspect(conn)

            # Verify tables
            logger.info("Checking tables...")
            table_errors = verify_tables(inspector)
            all_errors.extend(table_errors)

            # Verify columns
            logger.info("Checking columns...")
            column_errors = verify_columns(inspector)
            all_errors.extend(column_errors)

            # Verify indexes
            logger.info("Checking indexes...")
            index_errors = verify_indexes(inspector)
            all_errors.extend(index_errors)

    except Exception as e:
        logger.error(f"Failed to connect to database: {e}")
        return 2

    # Report results
    if all_errors:
        logger.error("Schema integrity check FAILED:")
        for error in all_errors:
            logger.error(f"  - {error}")
        return 1
    else:
        logger.info("Schema integrity check PASSED ✓")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
