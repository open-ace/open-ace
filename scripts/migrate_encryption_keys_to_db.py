"""
Open ACE - Encryption Keys Migration Script

将环境变量中的加密密钥元数据同步到数据库

Usage:
    # 预览模式（dry-run）
    python scripts/migrate_encryption_keys_to_db.py --dry-run

    # 执行迁移
    python scripts/migrate_encryption_keys_to_db.py --execute
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Add project root to path
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def compute_fingerprint(key_value: str) -> str:
    """计算密钥指纹（SHA-256 前 16 字符）"""
    import hashlib

    derived_key = hashlib.sha256(key_value.encode()).digest()
    fingerprint = hashlib.sha256(derived_key).hexdigest()[:16]
    return f"sha256:{fingerprint}"


def get_keys_from_env() -> dict:
    """从环境变量加载密钥"""
    from app.utils.encryption_key_registry import EncryptionKeyRegistry, KeyStatus

    keys_json = os.environ.get("OPENACE_ENCRYPTION_KEYS")

    if keys_json:
        try:
            config = json.loads(keys_json)
            keys_list = config.get("keys", [])
            primary_key_id = config.get("primary_key_id")

            result = {}
            for key_info in keys_list:
                key_id = key_info.get("id")
                key_value = key_info.get("value")
                status_str = key_info.get("status", "deprecated")

                if key_id is not None and key_value:
                    fingerprint = compute_fingerprint(key_value)
                    is_primary = key_id == primary_key_id
                    result[key_id] = {
                        "fingerprint": fingerprint,
                        "status": "active" if is_primary else status_str,
                    }

            return result
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Failed to parse OPENACE_ENCRYPTION_KEYS: {e}")
            return {}
    else:
        # 单密钥格式
        key_value = os.environ.get("OPENACE_ENCRYPTION_KEY")
        if key_value:
            fingerprint = compute_fingerprint(key_value)
            return {0: {"fingerprint": fingerprint, "status": "active"}}

    return {}


def sync_keys_to_db(keys: dict, db, dry_run: bool = True) -> dict:
    """同步密钥元数据到数据库

    Args:
        keys: 从环境变量加载的密钥字典
        db: 数据库实例
        dry_run: 是否为预览模式

    Returns:
        同步结果统计
    """
    stats = {
        "total_keys": len(keys),
        "inserted": 0,
        "skipped": 0,
        "errors": 0,
    }

    if not keys:
        logger.info("No keys found in environment variables")
        return stats

    # 查询现有记录
    existing_keys = db.fetch_all("SELECT key_id, key_fingerprint FROM encryption_keys")
    existing_fingerprints = {row["key_fingerprint"] for row in existing_keys}

    for key_id, key_data in keys.items():
        fingerprint = key_data["fingerprint"]
        status = key_data["status"]

        if fingerprint in existing_fingerprints:
            logger.info(f"Key {key_id} already exists in database, skipping")
            stats["skipped"] += 1
            continue

        # 插入新记录
        try:
            if not dry_run:
                db.execute(
                    """
                    INSERT INTO encryption_keys (key_fingerprint, status, config_version, created_at)
                    VALUES (?, ?, 1, datetime('now'))
                    """,
                    (fingerprint, status),
                )
                logger.info(f"Inserted key {key_id} with fingerprint {fingerprint}")
            else:
                logger.info(f"[DRY-RUN] Would insert key {key_id} with fingerprint {fingerprint}")

            stats["inserted"] += 1
        except Exception as e:
            logger.error(f"Failed to insert key {key_id}: {e}")
            stats["errors"] += 1

    return stats


def main():
    parser = argparse.ArgumentParser(description="Migrate encryption keys to database")
    parser.add_argument("--dry-run", action="store_true", help="Preview mode, no actual changes")
    parser.add_argument("--execute", action="store_true", help="Execute migration")
    parser.add_argument(
        "--db-url", default=None, help="Database URL (defaults to environment variable)"
    )

    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        logger.error("Must specify either --dry-run or --execute")
        sys.exit(1)

    dry_run = args.dry_run

    if dry_run:
        logger.info("Running in DRY-RUN mode (no changes will be made)")
    else:
        logger.info("Running in EXECUTE mode")

    # 加载密钥
    keys = get_keys_from_env()

    if not keys:
        logger.error("No encryption keys found in environment variables")
        logger.error("Please set OPENACE_ENCRYPTION_KEY or OPENACE_ENCRYPTION_KEYS")
        sys.exit(1)

    logger.info(f"Found {len(keys)} key(s) in environment variables")

    # 连接数据库
    from app.repositories.database import Database

    db_url = args.db_url or os.environ.get("DATABASE_URL") or "sqlite:///openace.db"
    logger.info(f"Using database: {db_url.split('@')[-1] if '@' in db_url else db_url}")

    db = Database(db_url=db_url)

    # 同步密钥
    stats = sync_keys_to_db(keys, db, dry_run)

    # 输出统计
    logger.info("=" * 60)
    logger.info("Migration Summary:")
    logger.info(f"  Total keys: {stats['total_keys']}")
    logger.info(f"  Inserted: {stats['inserted']}")
    logger.info(f"  Skipped: {stats['skipped']}")
    logger.info(f"  Errors: {stats['errors']}")
    logger.info("=" * 60)

    if stats["errors"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
