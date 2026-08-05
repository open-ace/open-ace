#!/usr/bin/env python3
"""
Admin Role Migration Rollback Tool

Issue #2276: Rollback migration from platform_admin back to admin.

Usage:
    python scripts/migrate_admin_role_rollback.py [OPTIONS]

Options:
    --batch-id BATCH_ID  Batch ID to rollback
    --user-ids IDS       Comma-separated user IDs to rollback
    --locale LOCALE      Output language (en_US, zh_CN, ja_JP, ko_KR)
    --yes                Skip confirmation prompts
    --help               Show this help message
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from scripts.shared import db

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# Internationalization messages
MESSAGES = {
    "en_US": {
        "rollback.start": "Starting rollback...",
        "rollback.success": "Rollback completed successfully",
        "rollback.failed": "Rollback failed: {error}",
        "rollback.progress": "Progress: {current}/{total} users",
        "check.backup": "Checking backup table...",
        "check.backup_found": "Found {count} users in backup",
        "check.backup_missing": "No backup found for batch_id: {batch_id}",
        "confirm.proceed": "Proceed with rollback? [yes/NO]: ",
        "confirm.required": "Confirmation required. Use --yes to skip.",
    },
    "zh_CN": {
        "rollback.start": "开始回滚...",
        "rollback.success": "回滚成功完成",
        "rollback.failed": "回滚失败：{error}",
        "rollback.progress": "进度：{current}/{total} 个用户",
        "check.backup": "检查备份表...",
        "check.backup_found": "发现 {count} 个备份用户",
        "check.backup_missing": "未找到批次 ID 的备份：{batch_id}",
        "confirm.proceed": "是否继续回滚？[yes/NO]: ",
        "confirm.required": "需要确认。使用 --yes 跳过确认。",
    },
}


class RollbackTool:
    """Admin role migration rollback tool."""

    def __init__(self, batch_id: str = None, locale: str = "en_US"):
        self.batch_id = batch_id
        self.locale = locale
        self.messages = MESSAGES.get(locale, MESSAGES["en_US"])

    def _message(self, key: str, **kwargs) -> str:
        """Get localized message."""
        msg = self.messages.get(key, key)
        return msg.format(**kwargs)

    def _print(self, key: str, **kwargs):
        """Print localized message."""
        print(self._message(key, **kwargs))

    def find_backup_users(self) -> list:
        """Find users in backup table."""
        try:
            conn = db.get_connection()
            cursor = conn.cursor()

            if self.batch_id:
                cursor.execute(
                    "SELECT id, username, role, tenant_id FROM admin_role_migration_backup WHERE batch_id = ?",
                    (self.batch_id,),
                )
            else:
                # Get most recent backup
                cursor.execute("""
                    SELECT id, username, role, tenant_id
                    FROM admin_role_migration_backup
                    WHERE backed_up_at = (
                        SELECT MAX(backed_up_at) FROM admin_role_migration_backup
                    )
                """)

            users = cursor.fetchall()
            conn.close()

            return users
        except Exception as e:
            logger.error(f"Failed to find backup users: {e}")
            return []

    def rollback_users(self, user_ids: list = None) -> bool:
        """Rollback user roles."""
        self._print("rollback.start")

        try:
            conn = db.get_connection()
            cursor = conn.cursor()

            # Start transaction
            cursor.execute("BEGIN")

            if user_ids:
                # Rollback specific users
                for user_id in user_ids:
                    # Get backup data
                    cursor.execute(
                        "SELECT role FROM admin_role_migration_backup WHERE id = ?",
                        (user_id,),
                    )
                    backup = cursor.fetchone()

                    if backup:
                        original_role = backup[0]

                        # Restore role
                        if db.is_postgresql():
                            cursor.execute(
                                "UPDATE users SET role = ?, updated_at = NOW() WHERE id = ?",
                                (original_role, user_id),
                            )
                        else:
                            cursor.execute(
                                "UPDATE users SET role = ?, updated_at = datetime('now') WHERE id = ?",
                                (original_role, user_id),
                            )
            else:
                # Rollback all users in batch
                if self.batch_id:
                    # Restore from backup
                    if db.is_postgresql():
                        cursor.execute("""
                            UPDATE users
                            SET role = b.role, updated_at = NOW()
                            FROM admin_role_migration_backup b
                            WHERE users.id = b.id AND b.batch_id = ?
                        """, (self.batch_id,))
                    else:
                        # SQLite doesn't support FROM clause in UPDATE
                        # Use a subquery instead
                        cursor.execute("""
                            UPDATE users
                            SET role = (
                                SELECT role FROM admin_role_migration_backup
                                WHERE id = users.id AND batch_id = ?
                            ),
                            updated_at = datetime('now')
                            WHERE id IN (
                                SELECT id FROM admin_role_migration_backup WHERE batch_id = ?
                            )
                        """, (self.batch_id, self.batch_id))

            affected_rows = cursor.rowcount

            # Commit transaction
            conn.commit()
            conn.close()

            logger.info(f"Rolled back {affected_rows} users")
            self._print("rollback.success")
            return True

        except Exception as e:
            self._print("rollback.failed", error=str(e))
            logger.error(f"Rollback failed: {e}")

            # Rollback on error
            try:
                conn.rollback()
                conn.close()
                logger.info("Transaction rolled back")
            except Exception:
                pass

            return False

    def confirm(self, skip_confirm: bool = False) -> bool:
        """Ask for confirmation."""
        if skip_confirm:
            return True

        self._print("confirm.proceed")
        response = input().strip().lower()
        return response == "yes"

    def run(self, skip_confirm: bool = False, user_ids: list = None):
        """Run rollback."""
        logger.info("Starting rollback...")

        # Check backup
        self._print("check.backup")
        backup_users = self.find_backup_users()

        if not backup_users:
            if self.batch_id:
                self._print("check.backup_missing", batch_id=self.batch_id)
            else:
                logger.error("No backup found")
            return False

        self._print("check.backup_found", count=len(backup_users))

        # Confirmation
        if not self.confirm(skip_confirm):
            self._print("confirm.required")
            return False

        # Rollback users
        return self.rollback_users(user_ids)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Admin role migration rollback tool (Issue #2276)"
    )
    parser.add_argument(
        "--batch-id",
        help="Batch ID to rollback",
    )
    parser.add_argument(
        "--user-ids",
        help="Comma-separated user IDs to rollback",
    )
    parser.add_argument(
        "--locale",
        default="en_US",
        choices=["en_US", "zh_CN", "ja_JP", "ko_KR"],
        help="Output language",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompts",
    )

    args = parser.parse_args()

    # Parse user IDs
    user_ids = None
    if args.user_ids:
        user_ids = [int(id.strip()) for id in args.user_ids.split(",")]

    # Run rollback
    tool = RollbackTool(batch_id=args.batch_id, locale=args.locale)
    success = tool.run(skip_confirm=args.yes, user_ids=user_ids)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()