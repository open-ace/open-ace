"""Pending revoke token cleanup scheduler.

Issue #2499: Periodically clean up expired pending_revoke tokens.

This scheduler runs every 60 seconds and force-revokes tokens that have
exceeded their revoke_after timeout, ensuring old tokens don't remain
valid indefinitely.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.services.distributed_scheduler import DistributedScheduler
from app.repositories.database import adapt_boolean_value, is_postgresql

logger = logging.getLogger(__name__)


class PendingRevokeCleanupScheduler(DistributedScheduler):
    """Scheduler to clean up expired pending_revoke tokens.

    Issue #2499: Implements the timeout cleanup mechanism for token rotation.

    This ensures that pending_revoke tokens are force-revoked after their
    timeout window expires, preventing old tokens from remaining valid
    indefinitely.
    """

    def __init__(self, db):
        """Initialize the scheduler.

        Args:
            db: Database instance.
        """
        super().__init__(
            job_name="pending_revoke_token_cleanup",
            db=db,
            strategy="advisory",  # Short task, use advisory lock
            lock_timeout=300,  # 5 minutes max execution time
        )
        self.db = db

    def _run_job(self) -> None:
        """Execute the cleanup job.

        This method is called by the parent class when the lock is acquired.
        """
        logger.info("Starting pending_revoke token cleanup")

        try:
            self._cleanup_expired_tokens()
        except Exception as e:
            logger.error("Failed to cleanup expired pending_revoke tokens: %s", e)
            raise

    def _cleanup_expired_tokens(self) -> None:
        """Find and revoke expired pending_revoke tokens."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        with self.db.connection() as conn:
            cursor = conn.cursor()

            # Find expired tokens
            now_expr = "NOW()" if is_postgresql() else "datetime('now')"
            cursor.execute(
                f"""
                SELECT id, machine_id, rotation_id, revoke_after
                FROM agent_tokens
                WHERE pending_revoke = {_param()}
                  AND is_revoked = {_param()}
                  AND revoke_after < {now_expr}
                """,
                (adapt_boolean_value(True), adapt_boolean_value(False)),
            )
            expired_tokens = cursor.fetchall()

            if not expired_tokens:
                logger.debug("No expired pending_revoke tokens found")
                return

            # Revoke expired tokens
            token_ids = [row["id"] for row in expired_tokens]

            # Build parameterized IN clause
            placeholders = ", ".join([_param() for _ in range(len(token_ids))])
            cursor.execute(
                f"""
                UPDATE agent_tokens
                SET is_revoked = {_param()}, revoked_at = {_param()}, pending_revoke = {_param()}
                WHERE id IN ({placeholders})
                """,
                [
                    adapt_boolean_value(True),
                    now.isoformat(),
                    adapt_boolean_value(False),
                ] + token_ids,
            )

            affected = cursor.rowcount
            conn.commit()

            # Log audit events for each revoked token
            for row in expired_tokens:
                self._log_force_revoked_audit(row)

            logger.info(
                "Force-revoked %d expired pending_revoke tokens",
                affected,
            )

    def _log_force_revoked_audit(self, token_row) -> None:
        """Log audit event for force-revoked token.

        Args:
            token_row: Database row containing token info.
        """
        try:
            from app.modules.governance.audit_logger import AuditAction, audit_logger

            audit_logger.log_action(
                AuditAction.AGENT_TOKEN_FORCE_REVOKED,
                severity="warning",
                resource_type="agent_token",
                resource_id=str(token_row["id"]),
                details={
                    "machine_id": token_row["machine_id"],
                    "rotation_id": token_row["rotation_id"],
                    "revoke_after": token_row["revoke_after"],
                },
            )
        except Exception as e:
            logger.warning("Failed to log audit event for force-revoked token: %s", e)


def _param():
    """Get parameter placeholder for current database."""
    return "%s" if is_postgresql() else "?"


# Convenience function to start the scheduler
def start_pending_revoke_cleanup(db):
    """Start the pending revoke cleanup scheduler.

    Args:
        db: Database instance.

    Returns:
        PendingRevokeCleanupScheduler instance.
    """
    scheduler = PendingRevokeCleanupScheduler(db)

    def run_loop():
        import time
        while True:
            try:
                scheduler.run_with_lock(scheduler._run_job)
            except Exception as e:
                logger.error("Pending revoke cleanup error: %s", e)
            time.sleep(60)  # Run every 60 seconds

    import threading
    thread = threading.Thread(target=run_loop, daemon=True)
    thread.start()
    logger.info("Pending revoke cleanup scheduler started")

    return scheduler
