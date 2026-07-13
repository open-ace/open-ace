"""
Open ACE - Scheduler Health Monitor

Monitors the health of background schedulers and creates alerts when they fail.

Features:
- Periodic health checks (every minute)
- Automatic alert creation when scheduler stops
- Status reporting for all schedulers
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Configuration
HEALTH_CHECK_INTERVAL_SEC = int(os.environ.get("SCHEDULER_HEALTH_CHECK_INTERVAL_SEC", "60"))
SCHEDULER_STOP_THRESHOLD_SEC = int(os.environ.get("SCHEDULER_STOP_THRESHOLD_SEC", "300"))  # 5 minutes
HEALTH_MONITOR_ENABLED = os.environ.get("SCHEDULER_HEALTH_MONITOR_ENABLED", "true").lower() == "true"


class SchedulerHealthMonitor:
    """
    Background monitor for scheduler health.

    Checks if schedulers are running and creates alerts when they stop.
    """

    _instance: SchedulerHealthMonitor | None = None
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
        self._interval = HEALTH_CHECK_INTERVAL_SEC
        self._enabled = HEALTH_MONITOR_ENABLED
        self._running = False
        self._last_check = None
        self._scheduler_statuses = {}
        self._alert_created_for = set()  # Track which schedulers we've alerted for
        self._initialized = True
        logger.info(f"SchedulerHealthMonitor initialized")

    def configure(
        self,
        interval_sec: int | None = None,
        enabled: bool | None = None,
    ):
        """Configure the monitor.

        Args:
            interval_sec: Check interval in seconds.
            enabled: Whether the monitor is enabled.
        """
        if interval_sec is not None:
            self._interval = max(10, interval_sec)
            logger.info(f"Scheduler health check interval set to {self._interval} seconds")

        if enabled is not None:
            self._enabled = enabled

    def start(self):
        """Start the monitor."""
        if self._running:
            logger.warning("SchedulerHealthMonitor is already running")
            return

        if not self._enabled:
            logger.info("SchedulerHealthMonitor is disabled, not starting")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._running = True
        logger.info("SchedulerHealthMonitor started")

    def stop(self):
        """Stop the monitor."""
        if self._thread is None:
            return

        self._stop_event.set()
        self._thread.join(timeout=5)
        self._running = False
        logger.info("SchedulerHealthMonitor stopped")

    def is_running(self) -> bool:
        """Check if the monitor is running."""
        return self._running and self._thread is not None and self._thread.is_alive()

    def get_status(self) -> dict:
        """Get monitor status."""
        return {
            "running": self._running,
            "enabled": self._enabled,
            "interval_seconds": self._interval,
            "last_check": self._last_check.isoformat() if self._last_check else None,
            "schedulers": self._scheduler_statuses,
        }

    def _run_loop(self):
        """Main monitor loop."""
        while not self._stop_event.is_set():
            self._check_schedulers()
            self._stop_event.wait(timeout=self._interval)

    def _check_schedulers(self):
        """Check health of all schedulers."""
        self._last_check = datetime.now(timezone.utc).replace(tzinfo=None)

        # Check quota enforcement scheduler
        try:
            from app.services.quota_enforcement_scheduler import enforcement_scheduler

            status = enforcement_scheduler.get_status()
            self._scheduler_statuses["quota_enforcement"] = status
            self._check_scheduler_health("quota_enforcement", status)

        except Exception as e:
            logger.error(f"Error checking quota enforcement scheduler: {e}")
            self._scheduler_statuses["quota_enforcement"] = {
                "error": str(e),
                "running": False,
            }

        # Check data fetch scheduler
        try:
            from app.services.data_fetch_scheduler import scheduler

            status = scheduler.get_status()
            self._scheduler_statuses["data_fetch"] = status
            self._check_scheduler_health("data_fetch", status)

        except Exception as e:
            logger.error(f"Error checking data fetch scheduler: {e}")
            self._scheduler_statuses["data_fetch"] = {
                "error": str(e),
                "running": False,
            }

        # Check alert compensation worker
        try:
            from app.services.alert_compensation_worker import compensation_worker

            status = compensation_worker.get_status()
            self._scheduler_statuses["alert_compensation"] = status
            # Don't create alerts for compensation worker - it's optional

        except Exception as e:
            logger.error(f"Error checking alert compensation worker: {e}")
            self._scheduler_statuses["alert_compensation"] = {
                "error": str(e),
            }

    def _check_scheduler_health(self, name: str, status: dict):
        """Check individual scheduler health and create alert if needed.

        Args:
            name: Scheduler name.
            status: Scheduler status dict.
        """
        is_healthy = self._is_scheduler_healthy(status)

        if not is_healthy:
            if name not in self._alert_created_for:
                self._create_scheduler_alert(name, status)
                self._alert_created_for.add(name)
        else:
            # Clear alert flag when scheduler is healthy again
            self._alert_created_for.discard(name)

    def _is_scheduler_healthy(self, status: dict) -> bool:
        """Determine if a scheduler is healthy.

        Args:
            status: Scheduler status dict.

        Returns:
            True if healthy, False otherwise.
        """
        # Check if running
        if not status.get("running", False):
            return False

        # Check heartbeat freshness if available
        heartbeat_ok = status.get("heartbeat_ok")
        if heartbeat_ok is not None:
            return bool(heartbeat_ok)

        # If no heartbeat info, just check running flag
        return True

    def _create_scheduler_alert(self, name: str, status: dict):
        """Create a system alert for a stopped scheduler.

        Args:
            name: Scheduler name.
            status: Scheduler status dict.
        """
        try:
            from app.modules.governance.alert_notifier import create_system_alert

            create_system_alert(
                title=f"Scheduler Stopped: {name}",
                message=f"The {name} scheduler has stopped running. "
                        f"Please check the system logs. Status: {status}",
                severity="critical",
            )
            logger.warning(f"Created alert for stopped scheduler: {name}")

        except Exception as e:
            logger.error(f"Failed to create scheduler alert: {e}")

    def get_all_scheduler_statuses(self) -> dict:
        """Get statuses of all monitored schedulers.

        Returns:
            Dict with scheduler name -> status mapping.
        """
        # Update statuses if needed
        if not self._scheduler_statuses:
            self._check_schedulers()

        return self._scheduler_statuses.copy()


# Global instance
health_monitor = SchedulerHealthMonitor()


def init_scheduler_health_monitor():
    """Initialize and start the scheduler health monitor."""
    if health_monitor._enabled:
        health_monitor.start()
        logger.info("Scheduler health monitor started")
    else:
        logger.info("Scheduler health monitor is disabled")


def get_scheduler_status() -> dict:
    """Get status of all schedulers.

    Returns:
        Dict with combined scheduler status info.
    """
    return health_monitor.get_all_scheduler_statuses()