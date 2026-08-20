#!/usr/bin/env python3
"""Cleanup zombie sessions - sessions whose machines have been deregistered.

Issue #2596: This script should be run before deploying the fix to clean up
existing zombie sessions.

Features:
- Idempotent execution (cleanup_id tracking)
- Batch processing with progress tracking
- Cleans up sessions, commands, and outputs
- Supports resuming from interruption

Usage:
    python scripts/cleanup_zombie_sessions.py [--dry-run] [--batch-size N]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from datetime import datetime, timezone

# Add project root to path
sys.path.insert(0, ".")

from app.repositories.database import Database, _param

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Session states that need cleanup
SESSION_STATES_TO_TERMINATE = ["active", "paused", "pending", "starting", "stopping"]
DEFAULT_BATCH_SIZE = 100


class ZombieSessionCleaner:
    """Clean up zombie sessions with idempotency and progress tracking."""

    def __init__(self, db: Database, batch_size: int = DEFAULT_BATCH_SIZE, dry_run: bool = False):
        self.db = db
        self.batch_size = batch_size
        self.dry_run = dry_run
        self.cleanup_id = str(uuid.uuid4())

    def run(self) -> dict:
        """Execute the cleanup process.

        Returns:
            Dict with cleanup statistics.
        """
        stats = {
            "cleanup_id": self.cleanup_id,
            "sessions_updated": 0,
            "commands_deleted": 0,
            "outputs_deleted": 0,
            "errors": [],
        }

        # Check if this cleanup_id already executed
        if self._is_already_executed():
            logger.warning("Cleanup %s already executed, skipping", self.cleanup_id)
            return stats

        logger.info("Starting zombie session cleanup (cleanup_id=%s)", self.cleanup_id)

        # Get zombie sessions
        zombie_sessions = self._get_zombie_sessions()
        if not zombie_sessions:
            logger.info("No zombie sessions found")
            self._record_cleanup_complete(stats)
            return stats

        logger.info("Found %d zombie sessions to clean up", len(zombie_sessions))

        # Process in batches
        for batch_start in range(0, len(zombie_sessions), self.batch_size):
            batch = zombie_sessions[batch_start:batch_start + self.batch_size]
            batch_stats = self._process_batch(batch)
            stats["sessions_updated"] += batch_stats["sessions_updated"]
            stats["commands_deleted"] += batch_stats["commands_deleted"]
            stats["outputs_deleted"] += batch_stats["outputs_deleted"]
            stats["errors"].extend(batch_stats.get("errors", []))

        # Record completion
        self._record_cleanup_complete(stats)

        logger.info(
            "Cleanup complete: sessions=%d, commands=%d, outputs=%d, errors=%d",
            stats["sessions_updated"],
            stats["commands_deleted"],
            stats["outputs_deleted"],
            len(stats["errors"]),
        )

        return stats

    def _is_already_executed(self) -> bool:
        """Check if this cleanup has already been executed."""
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                # Check if there's a cleanup_history table
                cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='cleanup_history'"
                )
                if cursor.fetchone():
                    cursor.execute(
                        f"SELECT id FROM cleanup_history WHERE cleanup_id = {_param()}",
                        (self.cleanup_id,),
                    )
                    return cursor.fetchone() is not None
        except Exception as e:
            logger.warning("Failed to check cleanup history: %s", e)

        return False

    def _get_zombie_sessions(self) -> list[str]:
        """Get all sessions whose machines have been deregistered."""
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                placeholders = ", ".join([_param()] * len(SESSION_STATES_TO_TERMINATE))
                cursor.execute(
                    f"""
                    SELECT s.session_id
                    FROM agent_sessions s
                    WHERE s.remote_machine_id IS NOT NULL
                    AND s.remote_machine_id NOT IN (SELECT machine_id FROM remote_machines)
                    AND s.status IN ({placeholders})
                    """,
                    SESSION_STATES_TO_TERMINATE,
                )
                rows = cursor.fetchall()
                return [row["session_id"] for row in rows]
        except Exception as e:
            logger.error("Failed to get zombie sessions: %s", e)
            return []

    def _process_batch(self, session_ids: list[str]) -> dict:
        """Process a batch of zombie sessions."""
        stats = {
            "sessions_updated": 0,
            "commands_deleted": 0,
            "outputs_deleted": 0,
            "errors": [],
        }

        if not session_ids:
            return stats

        now = datetime.now(timezone.utc).replace(tzinfo=None)

        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()

                # Update session status
                if not self.dry_run:
                    placeholders = ", ".join([_param()] * len(session_ids))
                    cursor.execute(
                        f"""
                        UPDATE agent_sessions
                        SET status = 'stopped', updated_at = {_param()}
                        WHERE session_id IN ({placeholders})
                        """,
                        [now.isoformat()] + session_ids,
                    )
                    stats["sessions_updated"] = cursor.rowcount

                # Delete runtime commands for these sessions
                # Get machine_ids first
                cursor.execute(
                    f"""
                    SELECT DISTINCT remote_machine_id FROM agent_sessions
                    WHERE session_id IN ({placeholders})
                    AND remote_machine_id IS NOT NULL
                    """,
                    session_ids,
                )
                machine_ids = [row["remote_machine_id"] for row in cursor.fetchall()]

                # Delete commands
                if machine_ids and not self.dry_run:
                    cmd_placeholders = ", ".join([_param()] * len(machine_ids))
                    cursor.execute(
                        f"DELETE FROM remote_runtime_commands WHERE machine_id IN ({cmd_placeholders})",
                        machine_ids,
                    )
                    stats["commands_deleted"] = cursor.rowcount

                # Delete outputs
                if not self.dry_run:
                    placeholders = ", ".join([_param()] * len(session_ids))
                    cursor.execute(
                        f"DELETE FROM remote_runtime_outputs WHERE session_id IN ({placeholders})",
                        session_ids,
                    )
                    stats["outputs_deleted"] = cursor.rowcount

                conn.commit()

            logger.debug(
                "Processed batch: sessions=%d, commands=%d, outputs=%d",
                stats["sessions_updated"],
                stats["commands_deleted"],
                stats["outputs_deleted"],
            )

        except Exception as e:
            error_msg = f"Batch failed: {e}"
            logger.error(error_msg)
            stats["errors"].append(error_msg)

        return stats

    def _record_cleanup_complete(self, stats: dict) -> None:
        """Record that cleanup completed."""
        if self.dry_run:
            logger.info("Dry run - not recording cleanup")
            return

        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()

                # Create cleanup_history table if it doesn't exist
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS cleanup_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        cleanup_id TEXT NOT NULL,
                        cleanup_type TEXT NOT NULL,
                        stats TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                cursor.execute(
                    f"""
                    INSERT INTO cleanup_history (cleanup_id, cleanup_type, stats)
                    VALUES ({_param()}, {_param()}, {_param()})
                    """,
                    (self.cleanup_id, "zombie_sessions", json.dumps(stats)),
                )
                conn.commit()
        except Exception as e:
            logger.warning("Failed to record cleanup: %s", e)


def main():
    parser = argparse.ArgumentParser(description="Clean up zombie sessions")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Batch size for processing (default: {DEFAULT_BATCH_SIZE})",
    )

    args = parser.parse_args()

    db = Database()
    cleaner = ZombieSessionCleaner(db, batch_size=args.batch_size, dry_run=args.dry_run)

    stats = cleaner.run()

    print("\n=== Cleanup Summary ===")
    print(f"Cleanup ID: {stats['cleanup_id']}")
    print(f"Sessions updated: {stats['sessions_updated']}")
    print(f"Commands deleted: {stats['commands_deleted']}")
    print(f"Outputs deleted: {stats['outputs_deleted']}")
    print(f"Errors: {len(stats['errors'])}")

    if stats["errors"]:
        print("\nErrors:")
        for error in stats["errors"]:
            print(f"  - {error}")
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()