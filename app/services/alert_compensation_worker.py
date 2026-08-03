"""
Open ACE - Alert Compensation Worker

Background worker that periodically retries failed alert creations.
Processes the alert_creation_failures queue and attempts to create alerts.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Configuration
COMPENSATION_INTERVAL_MIN = int(os.environ.get("ALERT_COMPENSATION_RETRY_INTERVAL_MIN", "5"))
COMPENSATION_MAX_RETRIES = int(os.environ.get("ALERT_COMPENSATION_MAX_RETRIES", "10"))
COMPENSATION_ENABLED = os.environ.get("ALERT_COMPENSATION_ENABLED", "true").lower() == "true"


class AlertCompensationWorker:
    """
    Background worker for retrying failed alert creations.

    Features:
    - Periodic scanning of failure queue
    - Configurable retry interval and max retries
    - Graceful start/stop
    - Status reporting
    """

    _instance: AlertCompensationWorker | None = None
    _lock = threading.Lock()
    _initialized: bool

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._thread = None
        self._stop_event = threading.Event()
        self._interval = COMPENSATION_INTERVAL_MIN * 60  # Convert to seconds
        self._enabled = COMPENSATION_ENABLED
        self._running = False
        self._last_run = None
        self._total_retried = 0
        self._total_success = 0
        self._total_failed = 0
        # Issue #1831: webhook deliveries retried by the delivery-state reaper.
        self._total_deliveries_retried = 0
        self._initialized = True
        logger.info(
            f"AlertCompensationWorker initialized (interval: {COMPENSATION_INTERVAL_MIN} min)"
        )

    def configure(
        self,
        interval_min: int | None = None,
        enabled: bool | None = None,
    ):
        """Configure the worker.

        Args:
            interval_min: Retry interval in minutes.
            enabled: Whether the worker is enabled.
        """
        if interval_min is not None:
            self._interval = max(1, interval_min) * 60
            logger.info(f"Alert compensation interval set to {interval_min} minutes")

        if enabled is not None:
            self._enabled = enabled

    def start(self):
        """Start the worker."""
        if self._running:
            logger.warning("AlertCompensationWorker is already running")
            return

        if not self._enabled:
            logger.info("AlertCompensationWorker is disabled, not starting")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._running = True
        logger.info("AlertCompensationWorker started")

    def stop(self):
        """Stop the worker."""
        if self._thread is None:
            return

        self._stop_event.set()
        self._thread.join(timeout=5)
        self._running = False
        logger.info("AlertCompensationWorker stopped")

    def is_running(self) -> bool:
        """Check if the worker is running."""
        return self._running and self._thread is not None and self._thread.is_alive()

    def get_status(self) -> dict:
        """Get worker status."""
        return {
            "running": self._running,
            "enabled": self._enabled,
            "interval_seconds": self._interval,
            "last_run": self._last_run.isoformat() if self._last_run else None,
            "stats": {
                "total_retried": self._total_retried,
                "total_success": self._total_success,
                "total_failed": self._total_failed,
                "total_deliveries_retried": self._total_deliveries_retried,
            },
        }

    def _run_loop(self):
        """Main worker loop."""
        while not self._stop_event.is_set():
            self._process_failures()
            # Issue #1831: advance webhook delivery retries each cycle too. A
            # failure here must never abort the compensation loop.
            self._process_due_deliveries()
            self._stop_event.wait(timeout=self._interval)

    def _process_failures(self):
        """Process failed alert creations with distributed lock (Issue #2187)."""
        import time
        from app.repositories.database import Database
        from app.services.leader_election import LeaderElectionClient

        # Acquire distributed lock
        db_local = Database()
        lock_client = LeaderElectionClient("alert_compensation", db_local, strategy="heartbeat", lock_timeout=1800)

        if not lock_client.try_acquire_leadership():
            logger.debug("Alert compensation skipped - not leader")
            lock_client.record_run("skipped")
            return

        start_time = time.time()
        process_status = "completed"
        process_error = None

        self._last_run = datetime.now(timezone.utc).replace(tzinfo=None)

        try:
            from app.modules.governance.alert_transaction_manager import get_transaction_manager

            manager = get_transaction_manager()
            failures = manager.get_pending_failures(limit=100)

            if not failures:
                logger.debug("No pending alert failures to process")
                return

            logger.info(f"Processing {len(failures)} pending alert failures")

            for failure in failures:
                try:
                    success = manager.retry_failure(failure)
                    self._total_retried += 1

                    if success:
                        self._total_success += 1
                        logger.info(f"Successfully retried alert failure {failure.id}")
                    else:
                        self._total_failed += 1
                        logger.warning(f"Failed to retry alert failure {failure.id}")

                except Exception as e:
                    self._total_failed += 1
                    logger.error(f"Error retrying alert failure {failure.id}: {e}")

        except Exception as e:
            process_status = "failed"
            process_error = str(e)
            logger.error(f"Error processing alert failures: {e}")

        # Record execution and release lock
        duration_ms = int((time.time() - start_time) * 1000)
        lock_client.record_run(process_status, duration_ms, process_error)
        lock_client.release_leadership()

    def _process_due_deliveries(self) -> int:
        """Retry due webhook deliveries via the alert notifier reaper (Issue #1831).

        Returns the number of deliveries attempted. Isolated from the failure
        queue so a reaper error can't break alert-creation compensation.
        """
        try:
            from app.modules.governance.alert_notifier import get_alert_notifier

            attempted = get_alert_notifier().process_due_deliveries()
            if attempted:
                self._total_deliveries_retried += attempted
                logger.info(f"Retried {attempted} due webhook deliveries")
            return attempted
        except Exception as e:
            logger.error(f"Error processing due webhook deliveries: {e}")
            return 0

    def process_now(self) -> dict:
        """Process failures immediately (for manual trigger).

        Returns:
            Dict with processing results.
        """
        result: dict[str, Any] = {
            "processed": 0,
            "success": 0,
            "failed": 0,
        }

        try:
            from app.modules.governance.alert_transaction_manager import get_transaction_manager

            manager = get_transaction_manager()
            failures = manager.get_pending_failures(limit=100)

            for failure in failures:
                try:
                    success = manager.retry_failure(failure)
                    result["processed"] += 1
                    if success:
                        result["success"] += 1
                    else:
                        result["failed"] += 1
                except Exception as e:
                    result["failed"] += 1
                    logger.error(f"Error retrying alert failure {failure.id}: {e}")

        except Exception as e:
            logger.error(f"Error in manual processing: {e}")
            result["error"] = str(e)

        return result


# Global instance
compensation_worker = AlertCompensationWorker()


def init_alert_compensation():
    """Initialize and start the alert compensation worker."""
    if compensation_worker._enabled:
        compensation_worker.start()
        logger.info("Alert compensation worker started")
    else:
        logger.info("Alert compensation worker is disabled")


def get_failure_queue_stats() -> dict:
    """Get statistics about the failure queue."""
    try:
        from app.modules.governance.alert_transaction_manager import get_transaction_manager

        manager = get_transaction_manager()
        return manager.get_failure_stats()
    except Exception as e:
        logger.error(f"Error getting failure stats: {e}")
        return {"error": str(e)}
