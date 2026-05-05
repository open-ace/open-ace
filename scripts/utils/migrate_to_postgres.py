#!/usr/bin/env python3
"""
Open ACE - SQLite to PostgreSQL Migration Script

This script migrates data from SQLite to PostgreSQL.

Usage:
    export DATABASE_URL="postgresql://user:password@localhost:5432/ace"
    python scripts/utils/migrate_to_postgres.py

Prerequisites:
    1. PostgreSQL database created
    2. DATABASE_URL environment variable set
    3. Run alembic migrations first: alembic upgrade head
"""

import logging
import os
import sqlite3
import sys
from typing import Any, Optional

# Add project root to path
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(script_dir))
sys.path.insert(0, project_root)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# SQLite database path
SQLITE_PATH = os.path.expanduser("~/.open-ace/ace.db")


def get_sqlite_connection() -> sqlite3.Connection:
    """Get SQLite connection."""
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_postgresql_connection():
    """Get PostgreSQL connection."""
    try:
        import psycopg2
        from psycopg2.extras import execute_values

        url = os.environ.get("DATABASE_URL")
        if not url:
            raise ValueError("DATABASE_URL environment variable not set")

        return psycopg2.connect(url)
    except ImportError:
        raise ImportError("psycopg2 is required. Install with: pip install psycopg2-binary")


def get_table_columns(cursor, table_name: str) -> list[str]:
    """Get column names for a table."""
    cursor.execute(f"PRAGMA table_info({table_name})")
    return [row["name"] for row in cursor.fetchall()]


def get_pg_column_types(pg_cur, table_name: str) -> dict[str, str]:
    """Get column data types for a PostgreSQL table."""
    pg_cur.execute(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = %s
        ORDER BY ordinal_position
    """,
        (table_name,),
    )
    return {row[0]: row[1] for row in pg_cur.fetchall()}


def migrate_table(
    sqlite_conn: sqlite3.Connection,
    pg_conn: Any,
    table_name: str,
    columns: Optional[list[str]] = None,
    batch_size: int = 5000,
) -> int:
    """
    Migrate a single table from SQLite to PostgreSQL.

    Args:
        sqlite_conn: SQLite connection.
        pg_conn: PostgreSQL connection.
        table_name: Name of the table to migrate.
        columns: Optional list of columns to migrate.
        batch_size: Number of rows per batch (default: 5000).

    Returns:
        int: Number of rows migrated.
    """
    sqlite_cur = sqlite_conn.cursor()
    pg_cur = pg_conn.cursor()

    # Get columns from both SQLite and PostgreSQL
    sqlite_columns = get_table_columns(sqlite_cur, table_name)

    # Get PostgreSQL columns and their types
    pg_column_types = get_pg_column_types(pg_cur, table_name)
    pg_columns = list(pg_column_types.keys())

    # Find common columns
    if columns is None:
        columns = [col for col in sqlite_columns if col in pg_columns]
    else:
        columns = [col for col in columns if col in pg_columns]

    if not columns:
        logger.warning(f"No common columns found for table {table_name}")
        return 0

    logger.info(f"  {table_name}: Common columns: {', '.join(columns)}")

    # Get total count
    sqlite_cur.execute(f"SELECT COUNT(*) FROM {table_name}")
    total_rows = sqlite_cur.fetchone()[0]

    if total_rows == 0:
        logger.info(f"  {table_name}: 0 rows (empty)")
        return 0

    logger.info(f"  {table_name}: Migrating {total_rows} rows...")

    # Read data from SQLite in batches
    cols_str = ", ".join(columns)
    total_migrated = 0
    offset = 0

    while offset < total_rows:
        sqlite_cur.execute(
            f"SELECT {cols_str} FROM {table_name} LIMIT {batch_size} OFFSET {offset}"
        )
        rows = sqlite_cur.fetchall()

        if not rows:
            break

        # Convert rows to list of tuples, cleaning NUL characters and converting types
        data = []
        for row in rows:
            cleaned_row = []
            for i, val in enumerate(row):
                col_name = columns[i]
                col_type = pg_column_types.get(col_name, "")
                # Convert INTEGER to BOOLEAN for PostgreSQL boolean columns
                if col_type == "boolean" and val is not None:
                    cleaned_row.append(bool(val))
                elif isinstance(val, str):
                    # Remove NUL characters (0x00)
                    cleaned_row.append(val.replace("\x00", ""))
                else:
                    cleaned_row.append(val)
            data.append(tuple(cleaned_row))

        # Insert into PostgreSQL using execute_values
        # execute_values uses a single %s placeholder for all values
        sql = f"INSERT INTO {table_name} ({cols_str}) VALUES %s ON CONFLICT DO NOTHING"

        try:
            from psycopg2.extras import execute_values

            # Use execute_values for better performance
            execute_values(pg_cur, sql, data)
            pg_conn.commit()

            total_migrated += len(data)
            offset += batch_size

            # Progress logging
            progress = min(offset, total_rows)
            logger.info(f"    {table_name}: {progress}/{total_rows} rows processed")

        except Exception as e:
            logger.error(f"  {table_name}: Error at offset {offset} - {e}")
            pg_conn.rollback()
            return total_migrated

    logger.info(f"  {table_name}: {total_migrated} rows migrated")
    return total_migrated


def migrate_all_tables():
    """Migrate all tables from SQLite to PostgreSQL."""
    logger.info("Starting migration from SQLite to PostgreSQL...")
    logger.info(f"SQLite source: {SQLITE_PATH}")
    logger.info(f"PostgreSQL target: {os.environ.get('DATABASE_URL', 'Not set')}")

    # Check prerequisites
    if not os.environ.get("DATABASE_URL"):
        logger.error("DATABASE_URL environment variable not set")
        sys.exit(1)

    if not os.path.exists(SQLITE_PATH):
        logger.error(f"SQLite database not found: {SQLITE_PATH}")
        sys.exit(1)

    # Connect to databases
    sqlite_conn = get_sqlite_connection()
    pg_conn = get_postgresql_connection()

    # Tables to migrate (in dependency order)
    tables = [
        ("users", None),
        ("sessions", None),
        ("tenants", None),
        ("tenant_usage", None),
        ("tenant_quotas", None),
        ("tenant_settings", None),
        ("daily_usage", None),
        ("daily_messages", None),
        ("content_filter_rules", None),
        ("quota_usage", None),
        ("quota_alerts", None),
        ("audit_logs", None),
        ("security_settings", None),
    ]

    total_migrated = 0

    for table_name, columns in tables:
        try:
            # Check if table exists in SQLite
            sqlite_cur = sqlite_conn.cursor()
            sqlite_cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
            )
            if not sqlite_cur.fetchone():
                logger.info(f"  {table_name}: Skipped (not in SQLite)")
                continue

            migrated = migrate_table(sqlite_conn, pg_conn, table_name, columns)
            total_migrated += migrated

        except Exception as e:
            logger.error(f"  {table_name}: Failed - {e}")

    # Close connections
    sqlite_conn.close()
    pg_conn.close()

    logger.info(f"\nMigration completed! Total rows migrated: {total_migrated}")


def refresh_aggregated_tables():
    """
    Refresh pre-aggregated tables after migration.

    These tables are derived from daily_messages and need to be rebuilt
    after migration since they don't exist in SQLite:
    - daily_stats: Daily statistics for trend analysis
    - hourly_stats: Hourly statistics for heatmaps
    - usage_summary: Summary statistics for dashboard
    """
    logger.info("\nRefreshing pre-aggregated tables...")

    try:
        # Import here to avoid circular imports and allow standalone execution
        sys.path.insert(
            0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        )
        from app.repositories.daily_stats_repo import DailyStatsRepository
        from app.services.summary_service import SummaryService

        # Refresh daily_stats
        logger.info("  Refreshing daily_stats...")
        daily_stats_repo = DailyStatsRepository()
        daily_stats_repo.refresh_stats()
        logger.info("  daily_stats refreshed")

        # Refresh hourly_stats
        logger.info("  Refreshing hourly_stats...")
        daily_stats_repo.refresh_hourly_stats()
        logger.info("  hourly_stats refreshed")

        # Refresh usage_summary
        logger.info("  Refreshing usage_summary...")
        summary_service = SummaryService()
        summary_service.refresh_summary()
        logger.info("  usage_summary refreshed")

        logger.info("\n✓ All pre-aggregated tables refreshed successfully!")

    except Exception as e:
        logger.warning(f"\n⚠ Failed to refresh pre-aggregated tables: {e}")
        logger.warning("  You may need to manually refresh them:")
        logger.warning("  - daily_stats_repo.refresh_stats()")
        logger.warning("  - daily_stats_repo.refresh_hourly_stats()")
        logger.warning("  - summary_service.refresh_summary()")


def verify_migration():
    """Verify migration by comparing row counts."""
    logger.info("\nVerifying migration...")

    sqlite_conn = get_sqlite_connection()
    pg_conn = get_postgresql_connection()

    tables = [
        "users",
        "sessions",
        "tenants",
        "tenant_usage",
        "daily_usage",
        "daily_messages",
        "content_filter_rules",
        "quota_usage",
        "quota_alerts",
        "audit_logs",
    ]

    print(f"\n{'Table':<25} {'SQLite':>10} {'PostgreSQL':>12} {'Match':>8}")
    print("-" * 60)

    all_match = True

    for table in tables:
        # SQLite count
        sqlite_cur = sqlite_conn.cursor()
        try:
            sqlite_cur.execute(f"SELECT COUNT(*) FROM {table}")
            sqlite_count = sqlite_cur.fetchone()[0]
        except:
            sqlite_count = "N/A"

        # PostgreSQL count
        pg_cur = pg_conn.cursor()
        try:
            pg_cur.execute(f"SELECT COUNT(*) FROM {table}")
            pg_count = pg_cur.fetchone()[0]
        except:
            pg_count = "N/A"

        # Check match
        if isinstance(sqlite_count, int) and isinstance(pg_count, int):
            match = "✓" if sqlite_count == pg_count else "✗"
            if sqlite_count != pg_count:
                all_match = False
        else:
            match = "-"

        print(f"{table:<25} {str(sqlite_count):>10} {str(pg_count):>12} {match:>8}")

    sqlite_conn.close()
    pg_conn.close()

    if all_match:
        logger.info("\n✓ All tables verified successfully!")
    else:
        logger.warning("\n⚠ Some tables have mismatched row counts")


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Migrate data from SQLite to PostgreSQL")
    parser.add_argument(
        "--verify", action="store_true", help="Verify migration by comparing row counts"
    )
    parser.add_argument(
        "--source",
        type=str,
        default=None,
        help="Source SQLite database path (default: ~/.open-ace/ace.db)",
    )

    args = parser.parse_args()

    global SQLITE_PATH
    if args.source:
        SQLITE_PATH = os.path.expanduser(args.source)

    if args.verify:
        verify_migration()
    else:
        migrate_all_tables()
        refresh_aggregated_tables()
        verify_migration()


if __name__ == "__main__":
    main()
