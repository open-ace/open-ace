"""
Open ACE - Clean Invalid Hostnames

This script cleans invalid hostnames from historical data.
It identifies and updates records with invalid hostnames to NULL.

Invalid hostname definition:
- Pure hexadecimal strings (8-32 lowercase hex chars)
- UUID format
- Pure numeric strings (length > 10)
- Placeholder format (<...>)
- Invalid RFC 1123 format

Usage:
    python scripts/clean_invalid_hostnames.py [--backup] [--dry-run]

Options:
    --backup    Create backup before cleaning (recommended)
    --dry-run   Show what would be cleaned without actually modifying data

IMPORTANT:
    - Run this script in low-traffic hours
    - Always backup data before running in production
    - Review the dry-run output first
"""

import argparse
import logging
import re
from datetime import datetime

from app.repositories.database import Database

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Blacklist patterns for invalid hostnames
_HEX_PATTERN = re.compile(r"^[a-f0-9]{8,32}$")
_UUID_PATTERN = re.compile(
    r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$", re.IGNORECASE
)
_NUMERIC_PATTERN = re.compile(r"^\d{11,}$")
_PLACEHOLDER_PATTERN = re.compile(r"^<[A-Za-z_]+>$")
_HOSTNAME_VALID_PATTERN = re.compile(
    r"^[a-zA-Z0-9]([a-zA-Z0-9\-\.]*[a-zA-Z0-9])?$|^[a-zA-Z0-9]$"
)

# Whitelist hostnames that should be preserved
WHITELIST = {"localhost"}


def is_invalid_hostname(name: str) -> bool:
    """
    Check if a hostname is invalid.

    Args:
        name: Hostname string to check.

    Returns:
        bool: True if invalid, False if valid.
    """
    if not name:
        return False

    # Whitelist check
    if name.lower() in WHITELIST:
        return False

    # Blacklist patterns
    if _HEX_PATTERN.match(name):
        return True
    if _UUID_PATTERN.match(name):
        return True
    if _NUMERIC_PATTERN.match(name):
        return True
    if _PLACEHOLDER_PATTERN.match(name):
        return True

    # RFC 1123 validation
    if len(name) < 1 or len(name) > 253:
        return True
    if not _HOSTNAME_VALID_PATTERN.match(name):
        return True

    return False


def find_invalid_hostnames(db: Database, table: str) -> list[str]:
    """
    Find all invalid hostnames in a table.

    Args:
        db: Database instance.
        table: Table name to search.

    Returns:
        list[str]: List of invalid hostnames found.
    """
    query = f"""
        SELECT DISTINCT host_name
        FROM {table}
        WHERE host_name IS NOT NULL AND host_name != ''
        ORDER BY host_name
    """
    rows = db.fetch_all(query)

    invalid_hostnames = []
    for row in rows:
        host_name = row["host_name"]
        if is_invalid_hostname(host_name):
            invalid_hostnames.append(host_name)

    return invalid_hostnames


def backup_table(db: Database, table: str) -> str:
    """
    Create backup of a table.

    Args:
        db: Database instance.
        table: Table name to backup.

    Returns:
        str: Backup table name.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_table_name = f"{table}_backup_{timestamp}"

    query = f"CREATE TABLE {backup_table_name} AS SELECT * FROM {table}"
    db.execute(query)

    logger.info(f"Backup created: {backup_table_name}")
    return backup_table_name


def clean_invalid_hostnames(db: Database, table: str, invalid_hostnames: list[str]) -> int:
    """
    Update invalid hostnames to NULL in a table.

    Args:
        db: Database instance.
        table: Table name to clean.
        invalid_hostnames: List of invalid hostnames to clean.

    Returns:
        int: Number of rows updated.
    """
    if not invalid_hostnames:
        logger.info(f"No invalid hostnames found in {table}")
        return 0

    total_updated = 0
    for hostname in invalid_hostnames:
        query = f"""
            UPDATE {table}
            SET host_name = NULL
            WHERE host_name = ?
        """
        result = db.execute(query, (hostname,))
        if result:
            total_updated += result

    logger.info(f"Cleaned {total_updated} rows with invalid hostnames in {table}")
    return total_updated


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Clean invalid hostnames from historical data"
    )
    parser.add_argument("--backup", action="store_true", help="Create backup before cleaning")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be cleaned without modifying data",
    )
    args = parser.parse_args()

    db = Database()

    # Tables to clean (host_name is present in these tables)
    tables = ["daily_messages", "daily_usage", "usage_summary"]

    logger.info("Starting invalid hostname cleanup...")
    logger.info(f"Tables to check: {tables}")

    # Find all invalid hostnames
    all_invalid_hostnames = set()
    for table in tables:
        invalid = find_invalid_hostnames(db, table)
        if invalid:
            logger.info(f"Found {len(invalid)} invalid hostnames in {table}: {invalid[:10]}...")
            all_invalid_hostnames.update(invalid)

    if not all_invalid_hostnames:
        logger.info("No invalid hostnames found in any table. Exiting.")
        return

    logger.info(f"Total unique invalid hostnames: {len(all_invalid_hostnames)}")
    logger.info(f"Invalid hostnames: {sorted(all_invalid_hostnames)}")

    # Dry run mode - just show what would be cleaned
    if args.dry_run:
        logger.info("DRY RUN - No data will be modified")
        for table in tables:
            for hostname in all_invalid_hostnames:
                query = f"SELECT COUNT(*) as count FROM {table} WHERE host_name = ?"
                result = db.fetch_one(query, (hostname,))
                if result and result["count"] > 0:
                    logger.info(
                        f"Would clean {result['count']} rows in {table} with hostname '{hostname}'"
                    )
        logger.info("Dry run complete. Run without --dry-run to actually clean data.")
        return

    # Create backups if requested
    if args.backup:
        logger.info("Creating backups...")
        for table in tables:
            backup_table(db, table)

    # Clean invalid hostnames
    logger.info("Cleaning invalid hostnames...")
    total_cleaned = 0
    for table in tables:
        cleaned = clean_invalid_hostnames(db, table, list(all_invalid_hostnames))
        total_cleaned += cleaned

    logger.info(f"Cleanup complete. Total rows cleaned: {total_cleaned}")

    # Refresh summary after cleaning
    logger.info("Refreshing usage summary...")
    from app.services.summary_service import SummaryService

    summary_service = SummaryService(db=db)
    summary_service.refresh_summary()
    logger.info("Summary refreshed successfully")


if __name__ == "__main__":
    main()