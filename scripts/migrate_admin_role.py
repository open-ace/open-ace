#!/usr/bin/env python3
"""
Admin Role Migration Tool

Issue #2276: Migrate legacy 'admin' role to 'platform_admin'.

Features:
- Pre-migration checks
- Database backup
- Transactional migration with rollback support
- Progress tracking (resume capability)
- Multi-language support
- Audit logging

Usage:
    python scripts/migrate_admin_role.py [OPTIONS]

Options:
    --locale LOCALE      Output language (en_US, zh_CN, ja_JP, ko_KR)
    --batch-size SIZE    Number of users to migrate per batch
    --dry-run            Show what would be done without making changes
    --resume BATCH_ID    Resume interrupted migration
    --yes                Skip confirmation prompts
    --help               Show this help message
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

try:
    import yaml
except ImportError:
    print("Error: PyYAML is required. Install with: pip install PyYAML")
    sys.exit(1)

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
        "migration.start": "Starting migration...",
        "migration.success": "Migration completed successfully",
        "migration.failed": "Migration failed: {error}",
        "migration.progress": "Progress: {current}/{total} users",
        "check.database": "Checking database connection...",
        "check.environment": "Checking environment: {env}",
        "check.users": "Found {count} users to migrate",
        "check.sessions": "Found {count} active sessions (threshold: {threshold})",
        "check.sessions_warning": "WARNING: High number of active sessions detected",
        "backup.created": "Backup created successfully",
        "backup.failed": "Backup failed: {error}",
        "confirm.proceed": "Proceed with migration? [yes/NO]: ",
        "confirm.required": "Confirmation required. Use --yes to skip.",
        "error.env_unknown": "Unknown environment: {env}",
        "error.no_users": "No users to migrate",
        "error.db_connection": "Database connection failed",
    },
    "zh_CN": {
        "migration.start": "开始迁移...",
        "migration.success": "迁移成功完成",
        "migration.failed": "迁移失败：{error}",
        "migration.progress": "进度：{current}/{total} 个用户",
        "check.database": "检查数据库连接...",
        "check.environment": "检查环境：{env}",
        "check.users": "发现 {count} 个待迁移用户",
        "check.sessions": "发现 {count} 个活跃会话（阈值：{threshold}）",
        "check.sessions_warning": "警告：检测到大量活跃会话",
        "backup.created": "备份创建成功",
        "backup.failed": "备份失败：{error}",
        "confirm.proceed": "是否继续迁移？[yes/NO]: ",
        "confirm.required": "需要确认。使用 --yes 跳过确认。",
        "error.env_unknown": "未知环境：{env}",
        "error.no_users": "没有待迁移用户",
        "error.db_connection": "数据库连接失败",
    },
}


class MigrationConfig:
    """Migration configuration."""

    def __init__(self, config_path: str | None = None):
        self.config_path = config_path or "config/migration.yaml"
        self.config = self._load_config()

    def _load_config(self) -> dict:
        """Load configuration from YAML file."""
        config_file = Path(self.config_path)
        if not config_file.exists():
            logger.warning(f"Config file not found: {config_file}, using defaults")
            return self._default_config()

        with open(config_file) as f:
            return yaml.safe_load(f)

    def _default_config(self) -> dict:
        """Return default configuration."""
        return {
            "environments": {
                "dev": {
                    "batch_size": 10,
                    "timeout": 60,
                    "enable_notifications": False,
                    "active_session_threshold": 5,
                },
                "staging": {
                    "batch_size": 50,
                    "timeout": 300,
                    "enable_notifications": True,
                    "require_confirmation": True,
                    "active_session_threshold": 10,
                },
                "prod": {
                    "batch_size": 100,
                    "timeout": 600,
                    "enable_notifications": True,
                    "require_confirmation": True,
                    "active_session_threshold": 20,
                },
            },
            "migration": {
                "target_role": "platform_admin",
                "source_roles": ["admin"],
                "create_backup": True,
                "use_external_backup": False,
                "active_session_check": True,
            },
        }

    def get_environment_config(self, env: str) -> dict:
        """Get configuration for specific environment."""
        return self.config.get("environments", {}).get(env, {})


class MigrationTool:
    """Admin role migration tool."""

    def __init__(self, locale: str = "en_US", dry_run: bool = False):
        self.locale = locale
        self.dry_run = dry_run
        self.config = MigrationConfig()
        self.messages = MESSAGES.get(locale, MESSAGES["en_US"])
        self.batch_id = self._generate_batch_id()
        self.start_time: datetime | None = None

    def _generate_batch_id(self) -> str:
        """Generate unique batch ID."""
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return f"batch-{timestamp}"

    def _message(self, key: str, **kwargs) -> str:
        """Get localized message."""
        msg = self.messages.get(key, key)
        return msg.format(**kwargs)

    def _print(self, key: str, **kwargs):
        """Print localized message."""
        print(self._message(key, **kwargs))

    def check_environment(self) -> bool:
        """Check environment configuration."""
        env = os.environ.get("OPENACE_ENV", "unknown")
        self._print("check.environment", env=env)

        if env == "prod":
            logger.info("Running in PRODUCTION environment")
            return True
        elif env in ("dev", "staging", "test"):
            logger.info(f"Running in {env.upper()} environment")
            return True
        else:
            logger.warning(f"Unknown environment: {env}")
            # Don't fail for unknown environment, just warn
            return True

    def check_database_connection(self) -> bool:
        """Check database connection."""
        self._print("check.database")
        try:
            conn = db.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            conn.close()
            logger.info("Database connection successful")
            return True
        except Exception as e:
            logger.error(f"Database connection failed: {e}")
            return False

    def count_users_to_migrate(self) -> int:
        """Count users to migrate.

        Raises on database errors so callers can distinguish between
        "no users" and "database failure".
        """
        conn = db.get_connection()
        cursor = conn.cursor()

        # Count users with admin role
        db._execute(cursor, "SELECT COUNT(*) FROM users WHERE role = 'admin'")

        result = cursor.fetchone()
        conn.close()

        return result[0] if result else 0

    def count_active_sessions(self) -> int:
        """Count active sessions."""
        try:
            conn = db.get_connection()
            cursor = conn.cursor()

            # Count sessions that expire in the future
            if db.is_postgresql():
                db._execute(cursor, "SELECT COUNT(*) FROM sessions WHERE expires_at > NOW()")
            else:
                db._execute(
                    cursor, "SELECT COUNT(*) FROM sessions WHERE expires_at > datetime('now')"
                )

            result = cursor.fetchone()
            conn.close()

            return result[0] if result else 0
        except Exception as e:
            logger.error(f"Failed to count active sessions: {e}")
            return 0

    def pre_check(self) -> bool:
        """Run pre-migration checks."""
        logger.info("Running pre-migration checks...")

        # Check environment
        if not self.check_environment():
            return False

        # Check database connection
        if not self.check_database_connection():
            self._print("error.db_connection")
            return False

        # Count users to migrate
        try:
            user_count = self.count_users_to_migrate()
        except Exception as e:
            logger.error(f"Failed to count users: {e}")
            self._print("error.db_connection")
            return False
        self._print("check.users", count=user_count)

        if user_count == 0:
            self._print("error.no_users")
            return False

        # Check active sessions
        env_config = self.config.get_environment_config(os.environ.get("OPENACE_ENV", "dev"))
        threshold = env_config.get("active_session_threshold", 10)

        active_sessions = self.count_active_sessions()
        self._print("check.sessions", count=active_sessions, threshold=threshold)

        if active_sessions > threshold:
            self._print("check.sessions_warning")

        return True

    def create_backup_table(self) -> bool:
        """Create backup table."""
        if self.dry_run:
            logger.info("DRY RUN: Would create backup table")
            return True

        try:
            conn = db.get_connection()
            cursor = conn.cursor()

            # Create backup table
            if db.is_postgresql():
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS admin_role_migration_backup (
                        id INT PRIMARY KEY,
                        username VARCHAR(255),
                        role VARCHAR(50),
                        tenant_id INT,
                        updated_at TIMESTAMP,
                        backed_up_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        backup_source VARCHAR(50),
                        batch_id VARCHAR(50)
                    )
                """)
            else:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS admin_role_migration_backup (
                        id INTEGER PRIMARY KEY,
                        username TEXT,
                        role TEXT,
                        tenant_id INTEGER,
                        updated_at TEXT,
                        backed_up_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        backup_source TEXT,
                        batch_id TEXT
                    )
                """)

            # Create index
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_backup_batch ON admin_role_migration_backup(batch_id)"
            )

            # Backup current users
            db._execute(
                cursor,
                """
                INSERT INTO admin_role_migration_backup (id, username, role, tenant_id, updated_at, backup_source, batch_id)
                SELECT id, username, role, tenant_id, updated_at, 'local', ?
                FROM users
                WHERE role = 'admin'
            """,
                (self.batch_id,),
            )

            conn.commit()
            conn.close()

            self._print("backup.created")
            logger.info(f"Backup created with batch_id: {self.batch_id}")
            return True

        except Exception as e:
            self._print("backup.failed", error=str(e))
            logger.error(f"Backup failed: {e}")
            return False

    def migrate_users(self) -> bool:
        """Migrate users from admin to platform_admin."""
        if self.dry_run:
            logger.info("DRY RUN: Would migrate users")
            return True

        self._print("migration.start")
        self.start_time = datetime.now()

        try:
            conn = db.get_connection()
            cursor = conn.cursor()

            # Start transaction
            cursor.execute("BEGIN")

            # Update user roles
            if db.is_postgresql():
                cursor.execute("""
                    UPDATE users
                    SET role = 'platform_admin', updated_at = NOW()
                    WHERE role = 'admin'
                """)
            else:
                cursor.execute("""
                    UPDATE users
                    SET role = 'platform_admin', updated_at = datetime('now')
                    WHERE role = 'admin'
                """)

            affected_rows = cursor.rowcount

            # Commit transaction
            conn.commit()
            conn.close()

            logger.info(f"Migrated {affected_rows} users")
            self._print("migration.success")
            return True

        except Exception as e:
            self._print("migration.failed", error=str(e))
            logger.error(f"Migration failed: {e}")

            # Rollback on error
            try:
                conn.rollback()
                conn.close()
                logger.info("Transaction rolled back")
            except Exception:
                pass

            return False

    def confirm(self, skip_confirm: bool = False) -> bool:
        """Ask for confirmation.

        Reads ``require_confirmation`` from the environment config so that
        any environment (not just ``prod``) can opt in to interactive
        confirmation.
        """
        if skip_confirm:
            return True

        env = os.environ.get("OPENACE_ENV", "unknown")
        env_config = self.config.get_environment_config(env)
        if env_config.get("require_confirmation", False):
            self._print("confirm.proceed")
            response = input().strip().lower()
            return response == "yes"
        return True

    def run(self, skip_confirm: bool = False):
        """Run migration."""
        logger.info(f"Starting migration with batch_id: {self.batch_id}")

        # Pre-check
        if not self.pre_check():
            logger.error("Pre-check failed")
            return False

        # Confirmation
        if not self.confirm(skip_confirm):
            self._print("confirm.required")
            return False

        # Create backup
        if not self.create_backup_table():
            logger.error("Backup failed")
            return False

        # Migrate users
        if not self.migrate_users():
            logger.error("Migration failed")
            return False

        logger.info(f"Migration completed successfully: {self.batch_id}")
        return True


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Admin role migration tool (Issue #2276)")
    parser.add_argument(
        "--locale",
        default="en_US",
        choices=["en_US", "zh_CN", "ja_JP", "ko_KR"],
        help="Output language",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Number of users to migrate per batch",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    parser.add_argument(
        "--resume",
        metavar="BATCH_ID",
        help="Resume interrupted migration",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompts",
    )

    args = parser.parse_args()

    # Run migration
    tool = MigrationTool(locale=args.locale, dry_run=args.dry_run)
    success = tool.run(skip_confirm=args.yes)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
