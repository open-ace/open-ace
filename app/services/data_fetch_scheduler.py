#!/usr/bin/env python3
"""
Open ACE - Data Fetch Scheduler

Background scheduler for automatic data fetching at configurable intervals.
"""

import logging
import threading
import time
from datetime import datetime

logger = logging.getLogger(__name__)


class DataFetchScheduler:
    """
    Background scheduler for automatic data fetching.

    Runs data fetch scripts at configurable intervals.
    """

    _instance = None
    _lock = threading.Lock()

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
        self._interval = 300  # Default: 5 minutes
        self._enabled = True
        self._running = False
        self._last_run = None
        self._next_run = None
        self._initialized = True
        logger.info("DataFetchScheduler initialized")

    def configure(self, interval: int = None, enabled: bool = None):
        """
        Configure the scheduler.

        Args:
            interval: Fetch interval in seconds.
            enabled: Whether auto fetch is enabled.
        """
        if interval is not None:
            self._interval = max(60, interval)  # Minimum 1 minute
            logger.info(f"Data fetch interval set to {self._interval} seconds")

        if enabled is not None:
            self._enabled = enabled
            logger.info(f"Data fetch enabled: {self._enabled}")

    def start(self):
        """Start the scheduler."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("DataFetchScheduler is already running")
            return

        if not self._enabled:
            logger.info("DataFetchScheduler is disabled, not starting")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._running = True
        logger.info(f"DataFetchScheduler started with interval {self._interval} seconds")

    def stop(self):
        """Stop the scheduler."""
        if self._thread is None:
            return

        self._stop_event.set()
        self._thread.join(timeout=5)
        self._running = False
        logger.info("DataFetchScheduler stopped")

    def is_running(self) -> bool:
        """Check if the scheduler is running."""
        return self._running and self._thread is not None and self._thread.is_alive()

    def get_status(self) -> dict:
        """Get scheduler status."""
        from datetime import datetime

        next_run_str = None
        if self._next_run:
            try:
                next_run_dt = datetime.fromtimestamp(self._next_run)
                next_run_str = next_run_dt.isoformat()
            except (TypeError, ValueError):
                pass

        return {
            'running': self._running,
            'enabled': self._enabled,
            'interval': self._interval,
            'last_run': self._last_run.isoformat() if self._last_run else None,
            'next_run': next_run_str,
        }

    def _run_loop(self):
        """Main scheduler loop."""
        # Calculate next run time
        self._next_run = datetime.now().timestamp() + self._interval

        while not self._stop_event.is_set():
            # Wait for the interval or stop event
            if self._stop_event.wait(timeout=self._interval):
                break

            # Run fetch
            self._run_fetch()

            # Update next run time
            self._next_run = datetime.now().timestamp() + self._interval

    def _run_fetch(self):
        """Run the data fetch scripts."""
        from app.routes.fetch import run_fetch_scripts

        logger.info("Starting scheduled data fetch...")
        self._last_run = datetime.now()

        try:
            run_fetch_scripts()
            logger.info("Scheduled data fetch completed")
        except Exception as e:
            logger.exception(f"Error in scheduled data fetch: {e}")


# Global scheduler instance
scheduler = DataFetchScheduler()


def init_scheduler():
    """Initialize and start the data fetch scheduler."""
    import sys
    import os

    # Add scripts/shared to path for config import
    scripts_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'scripts', 'shared')
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)

    from config import get_data_fetch_interval, is_data_fetch_enabled

    interval = get_data_fetch_interval()
    enabled = is_data_fetch_enabled()

    scheduler.configure(interval=interval, enabled=enabled)

    if enabled:
        scheduler.start()
        logger.info(f"Data fetch scheduler started with interval {interval} seconds")
    else:
        logger.info("Data fetch scheduler is disabled")