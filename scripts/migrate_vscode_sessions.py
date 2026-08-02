#!/usr/bin/env python3
"""
Migration script for VSCode sessions (Issue #2183).

Migrates legacy VSCode sessions by adding missing metadata:
- tenant_id
- owner_user_id
- created_at
- expires_at

Usage:
    python scripts/migrate_vscode_sessions.py [--dry-run] [--verbose]
"""

import argparse
import logging
import sys
import time

# Add project root to path
sys.path.insert(0, ".")

from app.modules.workspace.remote_agent_manager import get_remote_agent_manager
from app.modules.workspace.vscode_store import vscode_info_store

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def migrate_legacy_sessions(dry_run: bool = False, verbose: bool = False) -> tuple[int, int]:
    """Migrate legacy VSCode sessions.

    Args:
        dry_run: If True, only report what would be migrated without making changes.
        verbose: If True, log detailed information for each session.

    Returns:
        Tuple of (migrated_count, failed_count)
    """
    agent_mgr = get_remote_agent_manager()
    migrated_count = 0
    failed_count = 0
    now = time.time()

    # Scan all sessions in the store
    for (machine_id, vscode_id), info in vscode_info_store._store.items():
        # Check if session needs migration
        needs_migration = False
        missing_fields = []

        if "tenant_id" not in info:
            needs_migration = True
            missing_fields.append("tenant_id")

        if "owner_user_id" not in info:
            needs_migration = True
            missing_fields.append("owner_user_id")

        if "created_at" not in info:
            needs_migration = True
            missing_fields.append("created_at")

        if "expires_at" not in info:
            needs_migration = True
            missing_fields.append("expires_at")

        if not needs_migration:
            if verbose:
                logger.debug(f"Session {vscode_id[:8]} already has all required fields")
            continue

        if verbose:
            logger.info(
                f"Session {vscode_id[:8]} needs migration: missing {', '.join(missing_fields)}"
            )

        # Query machine information
        machine = agent_mgr.get_machine(machine_id)
        if not machine:
            logger.error(
                f"Cannot migrate session {vscode_id[:8]}: machine {machine_id[:8]} not found"
            )
            failed_count += 1
            continue

        # Prepare migration data
        migration_data = {}

        if "tenant_id" not in info:
            tenant_id = machine.get("tenant_id", 1)
            migration_data["tenant_id"] = tenant_id

        if "owner_user_id" not in info:
            owner_user_id = machine.get("created_by")
            migration_data["owner_user_id"] = owner_user_id

        if "created_at" not in info:
            # Use _updated_at as approximation for created_at
            created_at = info.get("_updated_at", now)
            migration_data["created_at"] = created_at

        if "expires_at" not in info:
            # Set expiration to 1 hour from now
            expires_at = now + 3600
            migration_data["expires_at"] = expires_at

        if dry_run:
            logger.info(f"[DRY RUN] Would migrate session {vscode_id[:8]}: {migration_data}")
            migrated_count += 1
        else:
            # Apply migration
            info.update(migration_data)
            logger.info(
                f"Migrated session {vscode_id[:8]}: tenant={migration_data.get('tenant_id')}, "
                f"owner={migration_data.get('owner_user_id')}"
            )
            migrated_count += 1

    return migrated_count, failed_count


def main():
    parser = argparse.ArgumentParser(description="Migrate legacy VSCode sessions (Issue #2183)")
    parser.add_argument("--dry-run", action="store_true", help="Only report what would be migrated")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    logger.info("Starting VSCode session migration...")
    if args.dry_run:
        logger.info("DRY RUN MODE: No changes will be made")

    migrated_count, failed_count = migrate_legacy_sessions(
        dry_run=args.dry_run, verbose=args.verbose
    )

    logger.info(f"Migration complete: {migrated_count} migrated, {failed_count} failed")

    if failed_count > 0:
        logger.warning("Some sessions could not be migrated due to missing machine info")
        logger.warning(
            "These sessions will need to be restarted after the 30-day transition period"
        )

    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
