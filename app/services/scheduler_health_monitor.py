"""
Open ACE - Scheduler Health Monitor

Monitors the health of background schedulers and creates alerts when they fail.

Features:
- Periodic health checks (every minute)
- Automatic alert creation when scheduler stops
- Status reporting for all schedulers
- Execution health monitoring (Issue #3144)

Supports multiple implementation backends:
- threading: Default Python threading (may not work with gevent)
- gevent: Greenlet-based scheduling for gevent environments
- apscheduler: APScheduler-based scheduling (recommended for stability)
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Configuration
HEALTH_CHECK_INTERVAL_SEC = int(os.environ.get("SCHEDULER_HEALTH_CHECK_INTERVAL_SEC", "60"))
SCHEDULER_STOP_THRESHOLD_SEC = int(
    os.environ.get("SCHEDULER_STOP_THRESHOLD_SEC", "300")
)  # 5 minutes
HEALTH_MONITOR_ENABLED = (
    os.environ.get("SCHEDULER_HEALTH_MONITOR_ENABLED", "true").lower() == "true"
)

# Execution health thresholds (Issue #3144)
SCHEDULER_FAILURE_THRESHOLD = int(os.environ.get("SCHEDULER_FAILURE_THRESHOLD", "3"))
SCHEDULER_NO_SUCCESS_THRESHOLD_SEC = int(
    os.environ.get("SCHEDULER_NO_SUCCESS_THRESHOLD_SEC", "3600")  # 1 hour
)

# Scheduler implementation backend (Issue #1481)
SCHEDULER_IMPLEMENTATION = os.environ.get("SCHEDULER_IMPLEMENTATION", "threading").lower()

# Try to import APScheduler
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger

    APSCHEDULER_AVAILABLE = True
except ImportError:
    APSCHEDULER_AVAILABLE = False
    logger.warning("APScheduler not available, falling back to threading")


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
        self._implementation = SCHEDULER_IMPLEMENTATION
        self._scheduler = None  # APScheduler instance
        self._initialized = True
        logger.info(f"SchedulerHealthMonitor initialized (implementation: {self._implementation})")

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

        if self._implementation == "apscheduler" and APSCHEDULER_AVAILABLE:
            self._start_apscheduler()
        elif self._implementation == "gevent":
            self._start_gevent()
        else:
            self._start_threading()

        self._running = True
        logger.info(f"SchedulerHealthMonitor started (implementation: {self._implementation})")

    def _start_threading(self):
        """Start using threading backend."""
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _start_gevent(self):
        """Start using gevent greenlets."""
        try:
            import gevent
            import gevent.event

            self._gevent_stop_event = gevent.event.Event()

            def gevent_loop():
                while not self._gevent_stop_event.is_set():
                    self._check_schedulers()
                    gevent.sleep(self._interval)

            self._greenlet = gevent.spawn(gevent_loop)
            logger.info("Started gevent-based health monitor")

        except ImportError:
            logger.warning("gevent not available, falling back to threading")
            self._start_threading()

    def _start_apscheduler(self):
        """Start using APScheduler backend."""
        self._scheduler = BackgroundScheduler()
        self._scheduler.add_job(
            self._check_schedulers,
            IntervalTrigger(seconds=self._interval),
            id="scheduler_health_check",
            name="Scheduler Health Check",
            replace_existing=True,
        )
        self._scheduler.start()
        logger.info("Started APScheduler-based health monitor")

    def stop(self):
        """Stop the monitor."""
        if self._implementation == "apscheduler" and self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
        elif self._implementation == "gevent" and hasattr(self, "_greenlet"):
            self._gevent_stop_event.set()
            self._greenlet.kill()
        elif self._thread is not None:
            self._stop_event.set()
            self._thread.join(timeout=5)

        self._running = False
        logger.info("SchedulerHealthMonitor stopped")

    def is_running(self) -> bool:
        """Check if the monitor is running."""
        if self._implementation == "apscheduler":
            return bool(self._running and self._scheduler and self._scheduler.running)
        elif self._implementation == "gevent":
            return self._running and hasattr(self, "_greenlet") and not self._greenlet.dead
        return self._running and self._thread is not None and self._thread.is_alive()

    def get_status(self) -> dict:
        """Get monitor status."""
        return {
            "running": self._running,
            "enabled": self._enabled,
            "implementation": self._implementation,
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
        """Check health of all schedulers with distributed lock (Issue #2187)."""
        import time

        from app.repositories.database import Database
        from app.services.leader_election import LeaderElectionClient

        # Acquire distributed lock
        db_local = Database()
        lock_client = LeaderElectionClient(
            "scheduler_health_monitor", db_local, strategy="heartbeat", lock_timeout=1800
        )

        if not lock_client.try_acquire_leadership():
            logger.debug("Health monitor skipped - not leader")
            lock_client.record_run("skipped")
            return

        start_time = time.time()
        check_status = "completed"
        check_error = None

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

        # Record execution and release lock
        duration_ms = int((time.time() - start_time) * 1000)
        lock_client.record_run(check_status, duration_ms, check_error)
        lock_client.release_leadership()

    def _check_scheduler_health(self, name: str, status: dict):
        """Check individual scheduler health and create alert if needed.

        Issue #2820: Uses health status classification (healthy/stale/stopped/unknown).
        Issue #3146: "idle" status treated like "healthy" — worker alive between runs.
        Issue #3144: Also checks execution health (failed runs, no success).

        Args:
            name: Scheduler name.
            status: Scheduler status dict.
        """
        # Check liveness health (heartbeat/running status)
        health_status = self._get_scheduler_health_status(status)

        if health_status == "stopped":
            # Generate critical alert for stopped scheduler
            if name not in self._alert_created_for:
                self._create_scheduler_alert(name, status, severity="critical")
                self._alert_created_for.add(name)

        elif health_status == "stale":
            # Generate warning alert for stale scheduler
            stale_key = f"{name}:stale"
            if stale_key not in self._alert_created_for:
                self._create_scheduler_alert(name, status, severity="warning", is_stale=True)
                self._alert_created_for.add(stale_key)
            # Clear the stopped alert flag if it exists
            self._alert_created_for.discard(name)

        elif health_status in ("healthy", "idle"):
            # Clear alert flags when scheduler is healthy or idle
            self._alert_created_for.discard(name)
            self._alert_created_for.discard(f"{name}:stale")

        # Unknown status: don't generate alerts (can't determine health)

        # Check execution health (Issue #3144)
        # Only check execution health if the worker is alive (not stopped)
        if health_status in ("healthy", "idle", "stale"):
            self._check_execution_health(name, status)

    def _get_scheduler_health_status(self, status: dict) -> str:
        """Determine scheduler health status.

        Issue #2820: Returns health status classification.

        Args:
            status: Scheduler status dict.

        Returns:
            "healthy" | "stale" | "stopped" | "unknown"
        """
        # Check if health_status is already computed
        if "health_status" in status:
            return str(status["health_status"])

        # Check if running
        running = status.get("running")
        if running == "unknown":
            return "unknown"
        if not running:
            return "stopped"

        # Check heartbeat freshness if available
        heartbeat_ok = status.get("heartbeat_ok")
        if heartbeat_ok is not None:
            return "healthy" if heartbeat_ok else "stale"

        # If no heartbeat info, check based on running flag
        return "healthy" if running else "stopped"

    def _is_scheduler_healthy(self, status: dict) -> bool:
        """Determine if a scheduler is healthy.

        Args:
            status: Scheduler status dict.

        Returns:
            True if healthy, False otherwise.
        """
        health_status = self._get_scheduler_health_status(status)
        return health_status in ("healthy", "stale")  # stale is still "healthy" in basic check

    def _check_execution_health(self, name: str, status: dict):
        """Check execution health and create alert if needed.

        Issue #3144: Checks for consecutive failures and no-success condition.

        Args:
            name: Scheduler name.
            status: Scheduler status dict with execution_health fields.
        """
        execution_health = status.get("execution_health", "unknown")
        consecutive_failures = status.get("consecutive_failures", 0)
        last_success_at = status.get("last_success_at")

        # Alert keys for execution health
        failing_key = f"{name}:failing"
        no_success_key = f"{name}:no_success"

        # Check for failing state (consecutive failures >= threshold)
        if execution_health == "failing":
            if failing_key not in self._alert_created_for:
                self._create_execution_alert(
                    name, status, alert_type="failing", consecutive_failures=consecutive_failures
                )
                self._alert_created_for.add(failing_key)
        else:
            # Clear failing alert flag when recovered
            self._alert_created_for.discard(failing_key)

        # Check for no-success condition (no successful run for too long)
        if last_success_at:
            try:
                from datetime import datetime as dt

                last_success_dt = dt.fromisoformat(last_success_at.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                time_since_success = (now - last_success_dt).total_seconds()

                if time_since_success > SCHEDULER_NO_SUCCESS_THRESHOLD_SEC:
                    if no_success_key not in self._alert_created_for:
                        self._create_execution_alert(
                            name,
                            status,
                            alert_type="no_success",
                            time_since_success=int(time_since_success),
                        )
                        self._alert_created_for.add(no_success_key)
                else:
                    # Clear no-success alert flag when there's a recent success
                    self._alert_created_for.discard(no_success_key)
            except (ValueError, TypeError):
                logger.warning(f"Invalid last_success_at format for {name}: {last_success_at}")
        elif consecutive_failures > 0 and execution_health in ("degraded", "failing"):
            # No success timestamp but there are failures - this means no successful run ever
            # Only alert if there are actual failures
            if no_success_key not in self._alert_created_for:
                self._create_execution_alert(
                    name, status, alert_type="no_success", time_since_success=None
                )
                self._alert_created_for.add(no_success_key)

        # Clear no-success alert when execution is healthy with a success timestamp
        if execution_health == "healthy" and last_success_at:
            self._alert_created_for.discard(no_success_key)

    def _create_execution_alert(
        self,
        name: str,
        status: dict,
        alert_type: str = "failing",
        consecutive_failures: int | None = None,
        time_since_success: int | None = None,
    ):
        """Create a system alert for execution health issues.

        Issue #3144: Creates alerts for consecutive failures or no-success condition.

        Args:
            name: Scheduler name.
            status: Scheduler status dict.
            alert_type: "failing" for consecutive failures, "no_success" for no success.
            consecutive_failures: Number of consecutive failures.
            time_since_success: Seconds since last successful run.
        """
        try:
            from app.modules.governance.alert_notifier import create_system_alert

            error_summary = status.get("error_summary")

            if alert_type == "failing":
                title = f"Scheduler Execution Failing: {name}"
                message = (
                    f"The {name} scheduler has failed {consecutive_failures} consecutive times. "
                    f"Last error: {error_summary or 'unknown'}. "
                    f"Please check the scheduler logs."
                )
                severity = "warning"
            else:  # no_success
                title = f"Scheduler No Success: {name}"
                if time_since_success:
                    hours = time_since_success // 3600
                    minutes = (time_since_success % 3600) // 60
                    message = (
                        f"The {name} scheduler has not had a successful run "
                        f"for {hours}h {minutes}m. "
                        f"Last error: {error_summary or 'unknown'}. "
                        f"Please check the scheduler logs."
                    )
                else:
                    message = (
                        f"The {name} scheduler has never had a successful run. "
                        f"Last error: {error_summary or 'unknown'}. "
                        f"Please check the scheduler logs."
                    )
                severity = "warning"

            create_system_alert(
                title=title,
                message=message,
                severity=severity,
            )
            logger.warning(
                f"Created {severity} execution alert for scheduler {name} "
                f"(type={alert_type}, failures={consecutive_failures})"
            )

        except Exception as e:
            logger.error(f"Failed to create execution alert: {e}")

    def _create_scheduler_alert(
        self, name: str, status: dict, severity: str = "critical", is_stale: bool = False
    ):
        """Create a system alert for a scheduler issue.

        Args:
            name: Scheduler name.
            status: Scheduler status dict.
            severity: Alert severity ("critical" or "warning").
            is_stale: Whether this is a stale (suspected) issue.
        """
        try:
            from app.modules.governance.alert_notifier import create_system_alert

            if is_stale:
                title = f"Scheduler Stale: {name}"
                message = (
                    f"The {name} scheduler heartbeat is stale. "
                    f"Possible causes: worker stopped, network partition, or high load. "
                    f"Status: {status}"
                )
            else:
                title = f"Scheduler Stopped: {name}"
                message = (
                    f"The {name} scheduler has stopped running. "
                    f"Please check the system logs. Status: {status}"
                )

            create_system_alert(
                title=title,
                message=message,
                severity=severity,
            )
            logger.warning(f"Created {severity} alert for scheduler {name} (stale={is_stale})")

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
