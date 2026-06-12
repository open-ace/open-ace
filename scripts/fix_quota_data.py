"""
Quota Data Fix Script

This script checks and fixes abnormal quota values in the database.
Run this script to:
1. Check for abnormal quota values (exceeding database limits)
2. Report affected users
3. Optionally fix the data by setting to default or max values

Usage:
    python scripts/fix_quota_data.py --check          # Check only, no modification
    python scripts/fix_quota_data.py --fix-default    # Fix by setting to default values
    python scripts/fix_quota_data.py --fix-max        # Fix by setting to max values
    python scripts/fix_quota_data.py --fix-unlimited  # Fix by setting to unlimited (NULL)

IMPORTANT: Backup database before running --fix-* options!
"""

import argparse
import logging
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.repositories.database import Database
from app.repositories.user_repo import UserRepository
from app.schemas.quota import MAX_REQUEST_QUOTA, MAX_TOKEN_QUOTA

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# Default quota values (in M units for token, actual count for request)
DEFAULT_DAILY_TOKEN_QUOTA = 100  # 100M tokens
DEFAULT_MONTHLY_TOKEN_QUOTA = 3000  # 3000M tokens (3B)
DEFAULT_DAILY_REQUEST_QUOTA = 1000
DEFAULT_MONTHLY_REQUEST_QUOTA = 30000


def check_abnormal_quotas(db: Database) -> dict:
    """
    Check for abnormal quota values in the database.

    Returns:
        Dictionary with statistics of abnormal values
    """
    logger.info("Checking for abnormal quota values...")

    stats = {
        "total_users": 0,
        "abnormal_daily_token": 0,
        "abnormal_monthly_token": 0,
        "abnormal_daily_request": 0,
        "abnormal_monthly_request": 0,
        "affected_users": [],
    }

    # Query all users
    query = """
        SELECT id, username, email,
               daily_token_quota, monthly_token_quota,
               daily_request_quota, monthly_request_quota
        FROM users
    """
    results = db.fetch_all(query)

    stats["total_users"] = len(results)

    for row in results:
        user_id = row["id"]
        username = row["username"]
        email = row["email"]
        daily_token = row["daily_token_quota"]
        monthly_token = row["monthly_token_quota"]
        daily_request = row["daily_request_quota"]
        monthly_request = row["monthly_request_quota"]

        abnormal_fields = []

        # Check daily token quota
        if daily_token is not None:
            if daily_token < 0:
                abnormal_fields.append("daily_token_quota (negative)")
                stats["abnormal_daily_token"] += 1
            elif daily_token > MAX_TOKEN_QUOTA:
                abnormal_fields.append(f"daily_token_quota ({daily_token}M > {MAX_TOKEN_QUOTA}M)")
                stats["abnormal_daily_token"] += 1

        # Check monthly token quota
        if monthly_token is not None:
            if monthly_token < 0:
                abnormal_fields.append("monthly_token_quota (negative)")
                stats["abnormal_monthly_token"] += 1
            elif monthly_token > MAX_TOKEN_QUOTA:
                abnormal_fields.append(
                    f"monthly_token_quota ({monthly_token}M > {MAX_TOKEN_QUOTA}M)"
                )
                stats["abnormal_monthly_token"] += 1

        # Check daily request quota
        if daily_request is not None:
            if daily_request < 0:
                abnormal_fields.append("daily_request_quota (negative)")
                stats["abnormal_daily_request"] += 1
            elif daily_request > MAX_REQUEST_QUOTA:
                abnormal_fields.append(
                    f"daily_request_quota ({daily_request} > {MAX_REQUEST_QUOTA})"
                )
                stats["abnormal_daily_request"] += 1

        # Check monthly request quota
        if monthly_request is not None:
            if monthly_request < 0:
                abnormal_fields.append("monthly_request_quota (negative)")
                stats["abnormal_monthly_request"] += 1
            elif monthly_request > MAX_REQUEST_QUOTA:
                abnormal_fields.append(
                    f"monthly_request_quota ({monthly_request} > {MAX_REQUEST_QUOTA})"
                )
                stats["abnormal_monthly_request"] += 1

        if abnormal_fields:
            stats["affected_users"].append(
                {
                    "id": user_id,
                    "username": username,
                    "email": email,
                    "abnormal_fields": abnormal_fields,
                }
            )

    return stats


def fix_quotas(db: Database, fix_strategy: str, affected_users: list) -> int:
    """
    Fix abnormal quota values using the specified strategy.

    Args:
        db: Database connection
        fix_strategy: 'default', 'max', or 'unlimited'
        affected_users: List of affected users from check_abnormal_quotas

    Returns:
        Number of users fixed
    """
    logger.info(f"Fixing quotas using strategy: {fix_strategy}")

    fixed_count = 0

    for user in affected_users:
        user_id = user["id"]
        username = user["username"]

        # Determine fix values based on strategy
        if fix_strategy == "default":
            daily_token = DEFAULT_DAILY_TOKEN_QUOTA
            monthly_token = DEFAULT_MONTHLY_TOKEN_QUOTA
            daily_request = DEFAULT_DAILY_REQUEST_QUOTA
            monthly_request = DEFAULT_MONTHLY_REQUEST_QUOTA
        elif fix_strategy == "max":
            daily_token = MAX_TOKEN_QUOTA
            monthly_token = MAX_TOKEN_QUOTA
            daily_request = MAX_REQUEST_QUOTA
            monthly_request = MAX_REQUEST_QUOTA
        elif fix_strategy == "unlimited":
            daily_token = None
            monthly_token = None
            daily_request = None
            monthly_request = None
        else:
            raise ValueError(f"Unknown fix strategy: {fix_strategy}")

        # Build update query based on abnormal fields
        updates = []
        params = []

        for field_desc in user["abnormal_fields"]:
            field_name = field_desc.split(" ")[0]

            if field_name == "daily_token_quota":
                updates.append("daily_token_quota = ?")
                params.append(daily_token)
            elif field_name == "monthly_token_quota":
                updates.append("monthly_token_quota = ?")
                params.append(monthly_token)
            elif field_name == "daily_request_quota":
                updates.append("daily_request_quota = ?")
                params.append(daily_request)
            elif field_name == "monthly_request_quota":
                updates.append("monthly_request_quota = ?")
                params.append(monthly_request)

        if updates:
            params.append(user_id)
            query = f"UPDATE users SET {', '.join(updates)} WHERE id = ?"

            try:
                db.execute(query, tuple(params))
                logger.info(f"Fixed quotas for user {username} (ID: {user_id})")
                fixed_count += 1
            except Exception as e:
                logger.error(f"Failed to fix quotas for user {username}: {e}")

    return fixed_count


def print_report(stats: dict):
    """Print check report."""
    logger.info("=" * 60)
    logger.info("QUOTA DATA CHECK REPORT")
    logger.info("=" * 60)
    logger.info(f"Total users checked: {stats['total_users']}")
    logger.info(f"Abnormal daily token quotas: {stats['abnormal_daily_token']}")
    logger.info(f"Abnormal monthly token quotas: {stats['abnormal_monthly_token']}")
    logger.info(f"Abnormal daily request quotas: {stats['abnormal_daily_request']}")
    logger.info(f"Abnormal monthly request quotas: {stats['abnormal_monthly_request']}")
    logger.info("=" * 60)

    if stats["affected_users"]:
        logger.info(f"Affected users ({len(stats['affected_users'])}):")
        for user in stats["affected_users"]:
            logger.info(f"  - {user['username']} ({user['email']})")
            for field in user["abnormal_fields"]:
                logger.info(f"    * {field}")
    else:
        logger.info("No abnormal quota values found. Database is healthy.")

    logger.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Check and fix abnormal quota values in database")
    parser.add_argument(
        "--check", action="store_true", help="Check for abnormal values without fixing"
    )
    parser.add_argument(
        "--fix-default",
        action="store_true",
        help="Fix abnormal values by setting to default quotas",
    )
    parser.add_argument(
        "--fix-max", action="store_true", help="Fix abnormal values by setting to max quotas"
    )
    parser.add_argument(
        "--fix-unlimited",
        action="store_true",
        help="Fix abnormal values by setting to unlimited (NULL)",
    )
    parser.add_argument(
        "--backup", action="store_true", help="Create database backup before fixing (recommended)"
    )

    args = parser.parse_args()

    # Check that at least one action is specified
    if not (args.check or args.fix_default or args.fix_max or args.fix_unlimited):
        parser.error("At least one action is required (--check or --fix-*)")

    # Initialize database
    db = Database()

    try:
        # Check for abnormal quotas
        stats = check_abnormal_quotas(db)
        print_report(stats)

        # Fix if requested
        if args.fix_default or args.fix_max or args.fix_unlimited:
            if not stats["affected_users"]:
                logger.info("No users need fixing. Exiting.")
                return

            # Determine fix strategy
            if args.fix_default:
                strategy = "default"
            elif args.fix_max:
                strategy = "max"
            elif args.fix_unlimited:
                strategy = "unlimited"

            # Warn user
            logger.warning(f"You are about to fix {len(stats['affected_users'])} users")
            logger.warning(f"Fix strategy: {strategy}")
            logger.warning("IMPORTANT: Ensure you have backed up the database!")

            if args.backup:
                logger.info("Backup option specified, but backup implementation is pending")
                # TODO: Implement backup functionality

            # Ask for confirmation
            confirm = input("Type 'yes' to continue: ")
            if confirm.lower() != "yes":
                logger.info("Aborted by user")
                return

            # Fix quotas
            fixed_count = fix_quotas(db, strategy, stats["affected_users"])
            logger.info(f"Fixed {fixed_count} users")

            # Re-check to verify
            logger.info("Re-checking after fix...")
            stats_after = check_abnormal_quotas(db)
            print_report(stats_after)

    except Exception as e:
        logger.error(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
