"""Open ACE - Deregistration Compensation Worker.

Issue #2596: Background worker that retries failed session termination batches
during machine deregistration.

This worker:
- Scans the deregister_failures table for pending records
- Retries terminating sessions with exponential backoff
- Logs and alerts on repeated failures
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

import gevent

from app.repositories.database import Database, _param

logger = logging.getLogger(__name__)

# Configuration (Issue #2596)
COMPENSATION_CHECK_INTERVAL = int(
    os.getenv("DEREGISTER_COMPENSATION_INTERVAL_SEC", "300")
)  # 5 minutes
COMPENSATION_MAX_RETRIES = int(
    os.getenv("DEREGISTER_COMPENSATION_MAX_RETRIES", "3")
)
COMPENSATION_BACKOFF_BASE = int(
    os.getenv("DEREGISTER_COMPENSATION_BACKOFF_BASE", "60")
)  # 1 minute base, doubles each retry


class DeregisterCompensationWorker:
    """Background worker for compensating failed session terminations.

    Issue #2596: Handles retry logic for batches that failed during
    machine deregistration.
    """

    def __init__(self, db: Database):
        self.db = db
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the compensation worker."""
        if self._running:
            return

        self._running = True

        def run_loop():
            logger.info("Deregister compensation worker started")
            while self._running:
                try:
                    self._process_pending_failures()
                except Exception as e:
                    logger.error("Compensation worker error: %s", e)

                # Sleep before next check
                gevent.sleep(COMPENSATION_CHECK_INTERVAL)

        # Run in a greenlet for gevent compatibility
        gevent.spawn(run_loop)
        logger.info(
            "Deregister compensation worker scheduled (interval=%ds)",
            COMPENSATION_CHECK_INTERVAL,
        )

    def stop(self) -> None:
        """Stop the compensation worker."""
        self._running = False
        logger.info("Deregister compensation worker stopped")

    def _process_pending_failures(self) -> None:
        """Process all pending failure records."""
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                # Get all pending failures ordered by creation time
                cursor.execute("""
                    SELECT id, machine_id, batch_index, session_ids, error_message,
                           retry_count, created_at
                    FROM deregister_failures
                    WHERE status = 'pending' OR status = 'retrying'
                    ORDER BY created_at ASC
                    LIMIT 100
                    """)
                failures = cursor.fetchall()

            if not failures:
                return

            logger.info("Processing %d pending failure records", len(failures))

            for failure in failures:
                self._process_single_failure(failure)

        except Exception as e:
            logger.error("Failed to process pending failures: %s", e)

    def _process_single_failure(self, failure: dict[str, Any]) -> None:
        """Process a single failure record.

        Args:
            failure: Row from deregister_failures table.
        """
        failure_id = failure["id"]
        machine_id = failure["machine_id"]
        batch_index = failure["batch_index"]
        session_ids_json = failure["session_ids"]
        retry_count = failure["retry_count"] or 0
        created_at = failure["created_at"]

        # Parse session IDs
        try:
            session_ids = (
                json.loads(session_ids_json)
                if isinstance(session_ids_json, str)
                else session_ids_json
            )
        except json.JSONDecodeError:
            logger.error("Invalid session_ids JSON for failure %d", failure_id)
            self._mark_failure_resolved(failure_id, "Invalid session_ids JSON")
            return

        # Check if max retries exceeded
        if retry_count >= COMPENSATION_MAX_RETRIES:
            logger.warning(
                "Failure %d exceeded max retries (%d), marking as failed",
                failure_id,
                COMPENSATION_MAX_RETRIES,
            )
            self._mark_failure_failed(failure_id)
            return

        # Check backoff timing
        if created_at:
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at)
            if created_at.tzinfo is not None:
                created_at = created_at.replace(tzinfo=None)

            # Exponential backoff: 1min, 5min, 15min
            backoff_seconds = COMPENSATION_BACKOFF_BASE * (2**retry_count)
            next_retry_time = created_at + timedelta(seconds=backoff_seconds)
            now = datetime.now(timezone.utc).replace(tzinfo=None)

            if now < next_retry_time:
                # Not yet time to retry
                return

        # Attempt to retry
        logger.info(
            "Retrying failure %d (machine=%s, batch=%d, attempt=%d)",
            failure_id,
            machine_id[:8],
            batch_index,
            retry_count + 1,
        )

        success = self._retry_terminate_sessions(session_ids)

        if success:
            self._mark_failure_resolved(failure_id, "Sessions terminated successfully")
            logger.info("Failure %d resolved successfully", failure_id)
        else:
            self._increment_retry_count(failure_id, retry_count)

    def _retry_terminate_sessions(self, session_ids: list[str]) -> bool:
        """Retry terminating a batch of sessions.

        Returns:
            True if successful, False otherwise.
        """
        if not session_ids:
            return True

        try:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            with self.db.connection() as conn:
                cursor = conn.cursor()
                placeholders = ", ".join([_param()] * len(session_ids))
                cursor.execute(
                    f"""
                    UPDATE agent_sessions
                    SET status = 'stopped', updated_at = {_param()}
                    WHERE session_id IN ({placeholders})
                    AND status NOT IN ('completed', 'stopped', 'error')
                    """,
                    [now] + session_ids,
                )
                conn.commit()
            return True
        except Exception as e:
            logger.error("Failed to retry session termination: %s", e)
            return False

    def _mark_failure_resolved(self, failure_id: int, message: str) -> None:
        """Mark a failure as resolved."""
        try:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    f"""
                    UPDATE deregister_failures
                    SET status = 'resolved', error_message = {_param()}, updated_at = {_param()}
                    WHERE id = {_param()}
                    """,
                    (message, now, failure_id),
                )
                conn.commit()
        except Exception as e:
            logger.error("Failed to mark failure %d as resolved: %s", failure_id, e)

    def _mark_failure_failed(self, failure_id: int) -> None:
        """Mark a failure as permanently failed after max retries."""
        try:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    f"""
                    UPDATE deregister_failures
                    SET status = 'failed', updated_at = {_param()}
                    WHERE id = {_param()}
                    """,
                    (now, failure_id),
                )
                conn.commit()

            logger.error(
                "Failure %d marked as permanently failed - requires manual intervention",
                failure_id,
            )

            # Issue #2596: Send alert for manual intervention
            self._send_failure_alert(failure_id)

        except Exception as e:
            logger.error("Failed to mark failure %d as failed: %s", failure_id, e)

    def _send_failure_alert(self, failure_id: int) -> None:
        """Send alert for permanently failed session termination.

        Issue #2596: Notify administrators when compensation worker
        fails after max retries.
        """
        try:
            from app.modules.governance.alert_notifier import (
                AlertSeverity,
                create_system_alert,
            )

            create_system_alert(
                title=f"Session termination permanently failed (failure_id={failure_id})",
                message=(
                    "Deregister compensation worker failed after max retries. "
                    f"Manual intervention required. Check deregister_failures table (id={failure_id}) for details."
                ),
                severity=AlertSeverity.CRITICAL.value,
            )
            logger.info("Sent alert for permanently failed session termination (failure_id=%d)", failure_id)
        except Exception as e:
            logger.error("Failed to send failure alert for failure_id %d: %s", failure_id, e)

    def _increment_retry_count(self, failure_id: int, current_count: int) -> None:
        """Increment retry count for a failure."""
        try:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    f"""
                    UPDATE deregister_failures
                    SET retry_count = {_param()}, status = 'retrying', updated_at = {_param()}
                    WHERE id = {_param()}
                    """,
                    (current_count + 1, now, failure_id),
                )
                conn.commit()
        except Exception as e:
            logger.error("Failed to increment retry count for failure %d: %s", failure_id, e)


# Singleton instance
_compensation_worker: DeregisterCompensationWorker | None = None


def start_deregister_compensation_worker(db: Database) -> DeregisterCompensationWorker:
    """Start the deregister compensation worker.

    Issue #2596: Should be called during application startup.
    """
    global _compensation_worker
    if _compensation_worker is None:
        _compensation_worker = DeregisterCompensationWorker(db)
        _compensation_worker.start()
    return _compensation_worker


def get_deregister_compensation_worker() -> DeregisterCompensationWorker | None:
    """Get the compensation worker instance."""
    return _compensation_worker
