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
        self._initialized = True
        logger.info(f"AlertCompensationWorker initialized (interval: {COMPENSATION_INTERVAL_MIN} min)")

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
        logger.info(f"AlertCompensationWorker started")

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
            },
        }

    def _run_loop(self):
        """Main worker loop."""
        while not self._stop_event.is_set():
            self._process_failures()
            self._stop_event.wait(timeout=self._interval)

    def _process_failures(self):
        """Process failed alert creations."""
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
            logger.error(f"Error processing alert failures: {e}")

    def process_now(self) -> dict:
        """Process failures immediately (for manual trigger).

        Returns:
            Dict with processing results.
        """
        result = {
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