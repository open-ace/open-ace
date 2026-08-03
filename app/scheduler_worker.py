#!/usr/bin/env python3
"""
Open ACE - Scheduler Worker Entry Point

Standalone worker process for running background schedulers.
Used in production deployments (Kubernetes, Docker Compose, systemd).

Issue #2187: Decouples scheduler from web application lifecycle.

Usage:
    python -m app.scheduler_worker

Environment Variables:
    SCHEDULER_MODE: Must be set to "scheduler"
    SCHEDULER_HEARTBEAT_INTERVAL: Heartbeat update interval (default: 10s)
    SCHEDULER_HEARTBEAT_TIMEOUT: Heartbeat timeout (default: 60s)
    SCHEDULER_LOCK_TIMEOUT: Lock expiration time (default: 1800s)
    SCHEDULER_METRICS_PORT: Prometheus metrics port (default: 9090)
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import sys
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Validate SCHEDULER_MODE
if os.environ.get("SCHEDULER_MODE") != "scheduler":
    logger.error("ERROR: SCHEDULER_MODE must be set to 'scheduler' to run scheduler worker")
    logger.error("Current value: %s", os.environ.get("SCHEDULER_MODE", "(not set)"))
    sys.exit(1)

# Set scheduler implementation to APScheduler (Issue #1481)
os.environ.setdefault("SCHEDULER_IMPLEMENTATION", "apscheduler")

# Apply gevent monkey patch early (before any other imports)
try:
    from gevent import monkey

    monkey.patch_all()
    logger.info("gevent monkey patch applied")
except ImportError:
    logger.warning("gevent not available, using standard threading")
except Exception as e:
    logger.error(f"Failed to apply gevent patch: {e}")
    sys.exit(1)

# Apply psycogreen patch for psycopg2 gevent compatibility (Issue #2187)
try:
    import psycogreen.gevent

    psycogreen.gevent.patch_psycopg()
    logger.info("psycogreen patch applied successfully")
except ImportError:
    logger.warning(
        "psycogreen not available - psycopg2 connections may block gevent. "
        "Install with: pip install psycogreen"
    )
except Exception as e:
    logger.error(f"Failed to apply psycogreen patch: {e}")
    sys.exit(1)

# Add scripts/shared to path for config import
scripts_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "shared")
if scripts_path not in sys.path:
    sys.path.insert(0, scripts_path)

# Now import application modules
try:
    from app.repositories.database import Database, is_postgresql
    from app.services.leader_election import check_scheduler_tables_exist
except ImportError as e:
    logger.error(f"Failed to import application modules: {e}")
    sys.exit(1)


class SchedulerWorker:
    """Main scheduler worker process.

    Manages all background schedulers with distributed leader election.
    """

    def __init__(self):
        self._stop_event = threading.Event()
        self._db: Database | None = None
        self._schedulers_started = False
        self._metrics_port = int(os.environ.get("SCHEDULER_METRICS_PORT", "9090"))

    def start(self) -> None:
        """Start scheduler worker."""
        logger.info("=" * 60)
        logger.info("Open ACE Scheduler Worker Starting...")
        logger.info("=" * 60)
        logger.info(f"Hostname: {socket.gethostname()}")
        logger.info(f"PID: {os.getpid()}")
        logger.info(f"Python: {sys.version}")
        logger.info(f"Database: {'PostgreSQL' if is_postgresql() else 'SQLite'}")

        # Check database schema version
        self._check_schema_version()

        # Check scheduler tables exist
        self._db = Database()
        if not check_scheduler_tables_exist(self._db):
            logger.error(
                "Scheduler tables (scheduler_leaders, scheduler_runs) do not exist. "
                "Run database migration first: alembic upgrade head"
            )
            sys.exit(1)
        logger.info("Scheduler tables verified")

        # Start Prometheus metrics server (optional)
        self._start_metrics_server()

        # Start all schedulers
        self._start_schedulers()

        # Register signal handlers
        self._register_signal_handlers()

        logger.info("Scheduler worker started successfully")
        self._log_scheduler_status()

        # Main loop - just wait for stop signal
        self._run_loop()

    def _check_schema_version(self) -> None:
        """Check database schema is compatible."""
        try:
            # Import check script
            import scripts.check_min_revision

            # Run check
            if not scripts.check_min_revision.check_min_revision():
                logger.error(
                    "Database schema version is too old. "
                    "Required: baseline_2026_06_23. "
                    "Run: alembic upgrade head"
                )
                sys.exit(1)
            logger.info("Database schema version check passed")
        except Exception as e:
            logger.error(f"Failed to check schema version: {e}")
            sys.exit(1)

    def _start_metrics_server(self) -> None:
        """Start Prometheus metrics HTTP server."""
        try:
            from prometheus_client import start_http_server

            start_http_server(self._metrics_port)
            logger.info(f"Prometheus metrics server started on port {self._metrics_port}")
        except ImportError:
            logger.warning(
                "prometheus_client not available - metrics server not started. "
                "Install with: pip install prometheus_client"
            )
        except Exception as e:
            logger.warning(f"Failed to start metrics server: {e}")

    def _start_schedulers(self) -> None:
        """Start all background schedulers."""
        logger.info("Starting background schedulers...")

        # Import scheduler modules
        # Note: These will use leader election internally (Issue #2187)

        # 1. Data Fetch Scheduler
        try:
            from app.services.data_fetch_scheduler import init_scheduler as init_data_fetch

            init_data_fetch()
            logger.info("Data fetch scheduler initialized")
        except Exception as e:
            logger.error(f"Failed to initialize data fetch scheduler: {e}")

        # 2. Quota Enforcement Scheduler
        try:
            from app.services.quota_enforcement_scheduler import (
                init_quota_enforcement,
            )

            init_quota_enforcement()
            logger.info("Quota enforcement scheduler initialized")
        except Exception as e:
            logger.error(f"Failed to initialize quota enforcement scheduler: {e}")

        # 3. Autonomous Scheduler
        try:
            from app.utils.config import is_autonomous_enabled

            if is_autonomous_enabled():
                from app.services.autonomous_scheduler import (
                    init_autonomous_scheduler,
                )

                init_autonomous_scheduler()
                logger.info("Autonomous scheduler initialized")
            else:
                logger.info("Autonomous scheduler disabled by configuration")
        except Exception as e:
            logger.error(f"Failed to initialize autonomous scheduler: {e}")

        # 4. Alert Compensation Worker
        try:
            from app.services.alert_compensation_worker import init_alert_compensation

            init_alert_compensation()
            logger.info("Alert compensation worker initialized")
        except Exception as e:
            logger.error(f"Failed to initialize alert compensation worker: {e}")

        # 5. Scheduler Health Monitor
        try:
            from app.services.scheduler_health_monitor import (
                init_scheduler_health_monitor,
            )

            init_scheduler_health_monitor()
            logger.info("Scheduler health monitor initialized")
        except Exception as e:
            logger.error(f"Failed to initialize scheduler health monitor: {e}")

        # 6. SSO Cleanup
        try:
            from app.modules.sso.manager import init_sso_cleanup

            init_sso_cleanup()
            logger.info("SSO cleanup initialized")
        except Exception as e:
            logger.error(f"Failed to initialize SSO cleanup: {e}")

        self._schedulers_started = True
        logger.info("All background schedulers started")

    def _register_signal_handlers(self) -> None:
        """Register graceful shutdown handlers."""

        def handle_shutdown(signum, frame):
            logger.info(f"Received signal {signum}, initiating graceful shutdown...")
            self._stop_event.set()

        signal.signal(signal.SIGTERM, handle_shutdown)
        signal.signal(signal.SIGINT, handle_shutdown)
        logger.info("Signal handlers registered (SIGTERM, SIGINT)")

    def _log_scheduler_status(self) -> None:
        """Log status of all schedulers."""
        logger.info("Scheduler status:")

        # Data Fetch Scheduler
        try:
            from app.services.data_fetch_scheduler import scheduler

            status = scheduler.get_status()
            logger.info(f"  Data Fetch: running={status.get('running')}, interval={status.get('interval')}s")
        except Exception:
            logger.warning("  Data Fetch: status unavailable")

        # Quota Enforcement Scheduler
        try:
            from app.services.quota_enforcement_scheduler import enforcement_scheduler

            status = enforcement_scheduler.get_status()
            logger.info(f"  Quota Enforcement: running={status.get('running')}, interval={status.get('interval')}s")
        except Exception:
            logger.warning("  Quota Enforcement: status unavailable")

        # Autonomous Scheduler
        try:
            from app.services.autonomous_scheduler import AutonomousScheduler

            inst = AutonomousScheduler.instance()
            logger.info(f"  Autonomous: running={inst._thread.is_alive() if inst._thread else False}")
        except Exception:
            logger.warning("  Autonomous: status unavailable")

        # Alert Compensation Worker
        try:
            from app.services.alert_compensation_worker import compensation_worker

            logger.info(f"  Alert Compensation: running={compensation_worker.is_running()}")
        except Exception:
            logger.warning("  Alert Compensation: status unavailable")

        # Scheduler Health Monitor
        try:
            from app.services.scheduler_health_monitor import health_monitor

            logger.info(f"  Health Monitor: running={health_monitor.is_running()}")
        except Exception:
            logger.warning("  Health Monitor: status unavailable")

    def _run_loop(self) -> None:
        """Main run loop."""
        logger.info("Entering main loop - waiting for shutdown signal")

        while not self._stop_event.is_set():
            # Periodic health check (every 60 seconds)
            self._stop_event.wait(60)

            if not self._stop_event.is_set():
                # Log health status
                self._log_health_status()

        # Graceful shutdown
        self._shutdown()

    def _log_health_status(self) -> None:
        """Log periodic health status."""
        try:
            # Check if schedulers are still running
            # This is a lightweight check without full status dump
            pass  # Intentionally minimal - detailed status logged on startup/shutdown
        except Exception as e:
            logger.warning(f"Health status check failed: {e}")

    def _shutdown(self) -> None:
        """Perform graceful shutdown."""
        logger.info("=" * 60)
        logger.info("Initiating graceful shutdown...")
        logger.info("=" * 60)

        shutdown_timeout = 30  # seconds

        # Stop autonomous scheduler first (it may have active workflows)
        try:
            from app.services.autonomous_scheduler import AutonomousScheduler

            inst = AutonomousScheduler.instance()
            logger.info("Stopping autonomous scheduler...")
            inst.stop()
            logger.info("Autonomous scheduler stopped")
        except Exception as e:
            logger.warning(f"Error stopping autonomous scheduler: {e}")

        # Stop other schedulers
        # Note: Most schedulers use daemon threads and will be terminated automatically
        # but we try to stop them gracefully

        # Data Fetch Scheduler
        try:
            from app.services.data_fetch_scheduler import scheduler

            if scheduler.is_running():
                logger.info("Stopping data fetch scheduler...")
                scheduler.stop()
                logger.info("Data fetch scheduler stopped")
        except Exception as e:
            logger.warning(f"Error stopping data fetch scheduler: {e}")

        # Quota Enforcement Scheduler
        try:
            from app.services.quota_enforcement_scheduler import enforcement_scheduler

            if enforcement_scheduler.is_running():
                logger.info("Stopping quota enforcement scheduler...")
                enforcement_scheduler.stop()
                logger.info("Quota enforcement scheduler stopped")
        except Exception as e:
            logger.warning(f"Error stopping quota enforcement scheduler: {e}")

        # Alert Compensation Worker
        try:
            from app.services.alert_compensation_worker import compensation_worker

            if compensation_worker.is_running():
                logger.info("Stopping alert compensation worker...")
                compensation_worker.stop()
                logger.info("Alert compensation worker stopped")
        except Exception as e:
            logger.warning(f"Error stopping alert compensation worker: {e}")

        # Scheduler Health Monitor
        try:
            from app.services.scheduler_health_monitor import health_monitor

            if health_monitor.is_running():
                logger.info("Stopping scheduler health monitor...")
                health_monitor.stop()
                logger.info("Scheduler health monitor stopped")
        except Exception as e:
            logger.warning(f"Error stopping scheduler health monitor: {e}")

        logger.info("Graceful shutdown complete")
        logger.info("=" * 60)


def main() -> None:
    """Main entry point."""
    worker = SchedulerWorker()
    worker.start()


if __name__ == "__main__":
    main()