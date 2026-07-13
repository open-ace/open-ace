"""
Configuration file backup and recovery utilities.

Provides automatic backup mechanism for config.json to prevent data loss
and support rollback/recovery operations.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# Number of backup versions to keep
MAX_BACKUP_VERSIONS = 5


def backup_config(config_path: str) -> bool:
    """Create a backup of the current configuration file.

    Implements rolling backup mechanism, keeping the last MAX_BACKUP_VERSIONS versions.
    Backup files are named: config.json.bak.1, config.json.bak.2, etc.

    Args:
        config_path: Path to the config.json file.

    Returns:
        True if backup was successful, False otherwise.
    """
    if not os.path.exists(config_path):
        logger.debug("Config file does not exist, no backup needed")
        return True

    try:
        # Shift existing backups: .bak.4 -> .bak.5, .bak.3 -> .bak.4, etc.
        for i in range(MAX_BACKUP_VERSIONS - 1, 0, -1):
            old_backup = f"{config_path}.bak.{i}"
            new_backup = f"{config_path}.bak.{i + 1}"
            if os.path.exists(old_backup):
                shutil.move(old_backup, new_backup)

        # Create new .bak.1 backup
        backup_path = f"{config_path}.bak.1"
        shutil.copy2(config_path, backup_path)

        logger.debug("Created config backup at %s", backup_path)
        return True

    except Exception as e:
        logger.error("Failed to create config backup: %s", e)
        return False


def restore_config(config_path: str, backup_index: int = 1) -> dict[str, Any] | None:
    """Restore configuration from a backup file.

    Args:
        config_path: Path to the config.json file.
        backup_index: Which backup to restore (1 = most recent).

    Returns:
        The restored configuration dict, or None if restore failed.
    """
    if backup_index < 1 or backup_index > MAX_BACKUP_VERSIONS:
        logger.error("Invalid backup index: %d", backup_index)
        return None

    backup_path = f"{config_path}.bak.{backup_index}"

    if not os.path.exists(backup_path):
        logger.error("Backup file does not exist: %s", backup_path)
        return None

    try:
        with open(backup_path) as f:
            config_data = json.load(f)

        # Validate the backup before restoring
        if not isinstance(config_data, dict):
            logger.error("Backup file is not a valid JSON object")
            return None

        config: dict[str, Any] = config_data

        # Create backup of current config before restore
        if os.path.exists(config_path):
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            temp_backup = f"{config_path}.pre_restore.{timestamp}"
            shutil.copy2(config_path, temp_backup)

        # Restore the backup
        shutil.copy2(backup_path, config_path)

        logger.info("Restored config from backup %s", backup_path)
        return config

    except json.JSONDecodeError as e:
        logger.error("Backup file is not valid JSON: %s", e)
        return None
    except Exception as e:
        logger.error("Failed to restore config from backup: %s", e)
        return None


def get_backup_versions(config_path: str) -> list[dict[str, Any]]:
    """Get list of available backup versions.

    Args:
        config_path: Path to the config.json file.

    Returns:
        List of backup version info dicts, sorted by recency (most recent first).
    """
    backups = []

    for i in range(1, MAX_BACKUP_VERSIONS + 1):
        backup_path = f"{config_path}.bak.{i}"
        if os.path.exists(backup_path):
            try:
                stat = os.stat(backup_path)
                backups.append(
                    {
                        "index": i,
                        "path": backup_path,
                        "timestamp": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        "size": stat.st_size,
                    }
                )
            except Exception as e:
                logger.warning("Failed to get backup info for %s: %s", backup_path, e)

    return backups


def check_and_recover_corrupted_config(config_path: str) -> dict[str, Any] | None:
    """Check if config file is corrupted and auto-recover from backup if needed.

    Args:
        config_path: Path to the config.json file.

    Returns:
        The recovered configuration dict, or None if recovery failed.
    """
    # Check if config file exists
    if not os.path.exists(config_path):
        logger.info("Config file does not exist, will be created")
        return {}

    # Try to read and parse the config
    try:
        with open(config_path) as f:
            config = json.load(f)
        if isinstance(config, dict):
            return config
        else:
            logger.error("Config file is not a valid JSON object")
    except json.JSONDecodeError as e:
        logger.error("Config file is corrupted (invalid JSON): %s", e)
    except Exception as e:
        logger.error("Failed to read config file: %s", e)

    # Config is corrupted, try to recover from backup
    logger.warning("Config file is corrupted, attempting to recover from backup")

    for backup_index in range(1, MAX_BACKUP_VERSIONS + 1):
        config = restore_config(config_path, backup_index)
        if config is not None:
            logger.info("Successfully recovered config from backup %d", backup_index)
            return config

    logger.error("Failed to recover config from all backups")
    return None


def cleanup_old_temp_files(config_path: str) -> None:
    """Clean up old temporary and backup files.

    Removes:
        - Temporary files (.tmp) older than 1 hour
        - Pre-restore backups older than 24 hours

    Args:
        config_path: Path to the config.json file.
    """
    config_dir = os.path.dirname(config_path)
    if not config_dir:
        config_dir = "."

    try:
        now = datetime.now().timestamp()

        for filename in os.listdir(config_dir):
            filepath = os.path.join(config_dir, filename)

            # Clean up .tmp files older than 1 hour
            if filename.endswith(".tmp"):
                if os.path.isfile(filepath):
                    age = now - os.path.getmtime(filepath)
                    if age > 3600:  # 1 hour
                        os.remove(filepath)
                        logger.debug("Removed old temp file: %s", filepath)

            # Clean up pre-restore backups older than 24 hours
            elif ".pre_restore." in filename:
                if os.path.isfile(filepath):
                    age = now - os.path.getmtime(filepath)
                    if age > 86400:  # 24 hours
                        os.remove(filepath)
                        logger.debug("Removed old pre-restore backup: %s", filepath)

    except Exception as e:
        logger.warning("Failed to cleanup old temp files: %s", e)
