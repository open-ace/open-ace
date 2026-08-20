"""
Open ACE - Scheduler Status Reader

Provides unified status reading for schedulers across processes.

Issue #2820: Enables Web API and health monitor to read scheduler status
from shared database storage (scheduler_leaders table) instead of process-local memory.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Configuration
CACHE_TTL_SECONDS = int(os.environ.get("SCHEDULER_STATUS_CACHE_TTL", "5"))
QUERY_TIMEOUT_SECONDS = int(os.environ.get("SCHEDULER_STATUS_QUERY_TIMEOUT", "5"))

# Default health thresholds (can be overridden by environment variables)
DEFAULT_THRESHOLD_HEALTHY = int(os.environ.get("SCHEDULER_HEALTH_THRESHOLD_HEALTHY", "0"))
DEFAULT_THRESHOLD_STOPPED = int(os.environ.get("SCHEDULER_HEALTH_THRESHOLD_STOPPED", "0"))


class SchedulerStatusReader:
    """
    Unified scheduler status reader with caching and degradation.

    Reads scheduler status from scheduler_leaders table, providing:
    - Cross-process status visibility
    - Short TTL cache for performance
    - Database failure degradation
    - Health status classification
    """

    _instance: SchedulerStatusReader | None = None
    _lock = threading.Lock()
    _initialized: bool = False

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

        self._cache: dict[str, tuple[dict, float]] = {}
        self._cache_lock = threading.Lock()
        self._initialized = True

        logger.info("SchedulerStatusReader initialized")

    def get_status(
        self,
        job_name: str,
        interval_seconds: int,
        threshold_healthy: int | None = None,
        threshold_stopped: int | None = None,
    ) -> dict[str, Any]:
        """
        Get scheduler status from shared storage.

        Args:
            job_name: Scheduler job name (e.g., "data_fetch")
            interval_seconds: Expected scheduler interval in seconds
            threshold_healthy: Heartbeat age threshold for "healthy" status
            threshold_stopped: Heartbeat age threshold for "stopped" status

        Returns:
            Status dict with fields:
            - running: bool or "unknown"
            - worker_id: str or None
            - heartbeat: ISO timestamp or None
            - heartbeat_age_seconds: float or None
            - last_run: ISO timestamp or None
            - next_run: ISO timestamp or None
            - health_status: "healthy" | "stale" | "stopped" | "unknown"
            - cache_age_seconds: float or None
            - cache_hit: bool or None
            - error: str or None (if database error)
        """
        # Set default thresholds based on interval
        if threshold_healthy is None or threshold_healthy == 0:
            threshold_healthy = interval_seconds + 60
        if threshold_stopped is None or threshold_stopped == 0:
            threshold_stopped = threshold_healthy * 2

        # Check cache
        cache_key = job_name
        now = time.time()
        cache_hit = False
        cache_age = None

        with self._cache_lock:
            if cache_key in self._cache:
                cached_data, cached_at = self._cache[cache_key]
                cache_age = now - cached_at
                if cache_age < CACHE_TTL_SECONDS:
                    cache_hit = True
                    result = cached_data.copy()
                    result["cache_age_seconds"] = cache_age
                    result["cache_hit"] = True
                    return result

        # Query database
        try:
            result = self._query_database(
                job_name, interval_seconds, threshold_healthy, threshold_stopped
            )
            result["cache_age_seconds"] = 0.0
            result["cache_hit"] = False

            # Store in cache
            with self._cache_lock:
                self._cache[cache_key] = (result.copy(), now)

            return result

        except Exception as e:
            logger.error(f"Failed to query scheduler status for {job_name}: {e}")
            return {
                "running": "unknown",
                "worker_id": None,
                "heartbeat": None,
                "heartbeat_age_seconds": None,
                "last_run": None,
                "next_run": None,
                "health_status": "unknown",
                "cache_age_seconds": None,
                "cache_hit": None,
                "error": "database_unavailable",
            }

    def _query_database(
        self,
        job_name: str,
        interval_seconds: int,
        threshold_healthy: int,
        threshold_stopped: int,
    ) -> dict[str, Any]:
        """Query scheduler_leaders table for status."""
        from app.repositories.database import Database

        db = Database()

        # Query scheduler_leaders
        result = db.fetch_one(
            """
            SELECT job_name, leader_id, owner_info, acquired_at, expires_at,
                   heartbeat_at, last_run_at, run_count, skip_count, fail_count
            FROM scheduler_leaders
            WHERE job_name = ?
            """,
            (job_name,),
        )

        if not result:
            return {
                "running": False,
                "worker_id": None,
                "heartbeat": None,
                "heartbeat_age_seconds": None,
                "last_run": None,
                "next_run": None,
                "health_status": "stopped",
                "error": None,
            }

        # Check if expired
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        expires_at = result.get("expires_at")
        heartbeat_at = result.get("heartbeat_at")
        last_run_at = result.get("last_run_at")
        leader_id = result.get("leader_id")

        is_expired = expires_at and expires_at < now

        # Calculate heartbeat age
        heartbeat_age_seconds = None
        if heartbeat_at:
            # Handle timezone-aware and naive datetime objects
            if hasattr(heartbeat_at, "tzinfo") and heartbeat_at.tzinfo is not None:
                heartbeat_age_seconds = (now - heartbeat_at.replace(tzinfo=None)).total_seconds()
            else:
                heartbeat_age_seconds = (now - heartbeat_at).total_seconds()

        # Determine health status
        health_status = self._get_health_status(
            heartbeat_age_seconds, is_expired, threshold_healthy, threshold_stopped
        )

        # Determine running status
        running = health_status in ("healthy", "stale")

        # Calculate next_run
        next_run = self._calculate_next_run(last_run_at, heartbeat_at, running, interval_seconds)

        # Format timestamps
        heartbeat_str = None
        if heartbeat_at:
            heartbeat_str = (
                heartbeat_at.isoformat()
                if hasattr(heartbeat_at, "isoformat")
                else str(heartbeat_at)
            )

        last_run_str = None
        if last_run_at:
            last_run_str = (
                last_run_at.isoformat() if hasattr(last_run_at, "isoformat") else str(last_run_at)
            )

        next_run_str = None
        if next_run:
            next_run_str = next_run.isoformat() if hasattr(next_run, "isoformat") else str(next_run)

        return {
            "running": running,
            "worker_id": leader_id,
            "heartbeat": heartbeat_str,
            "heartbeat_age_seconds": heartbeat_age_seconds,
            "last_run": last_run_str,
            "next_run": next_run_str,
            "health_status": health_status,
            "error": None,
        }

    def _get_health_status(
        self,
        heartbeat_age_seconds: float | None,
        is_expired: bool,
        threshold_healthy: int,
        threshold_stopped: int,
    ) -> str:
        """
        Determine health status based on heartbeat age.

        Returns: "healthy" | "stale" | "stopped"
        """
        if is_expired:
            return "stopped"

        if heartbeat_age_seconds is None:
            return "stopped"

        if heartbeat_age_seconds >= threshold_stopped:
            return "stopped"

        if heartbeat_age_seconds >= threshold_healthy:
            return "stale"

        return "healthy"

    def _calculate_next_run(
        self,
        last_run_at: datetime | None,
        heartbeat_at: datetime | None,
        running: bool,
        interval_seconds: int,
    ) -> datetime | None:
        """
        Calculate expected next run time.

        Logic:
        - If running: now + interval (current execution ongoing)
        - If has last_run: last_run + interval
        - Otherwise: None (no scheduled runs yet)
        """
        from datetime import timedelta

        now = datetime.now(timezone.utc).replace(tzinfo=None)

        if running:
            # Currently running, next run after current execution
            return now + timedelta(seconds=interval_seconds)

        if last_run_at:
            # Based on last run time
            if hasattr(last_run_at, "replace"):
                last_run_naive = (
                    last_run_at.replace(tzinfo=None) if last_run_at.tzinfo else last_run_at
                )
            else:
                last_run_naive = last_run_at
            return last_run_naive + timedelta(seconds=interval_seconds)

        return None

    def clear_cache(self, job_name: str | None = None):
        """
        Clear cache for a specific job or all jobs.

        Args:
            job_name: Job name to clear, or None to clear all
        """
        with self._cache_lock:
            if job_name:
                if job_name in self._cache:
                    del self._cache[job_name]
                    logger.debug(f"Cache cleared for {job_name}")
            else:
                self._cache.clear()
                logger.debug("All cache cleared")


# Global instance
_status_reader: SchedulerStatusReader | None = None


def get_status_reader() -> SchedulerStatusReader:
    """Get the global status reader instance."""
    global _status_reader
    if _status_reader is None:
        _status_reader = SchedulerStatusReader()
    return _status_reader


def get_scheduler_status(job_name: str, interval_seconds: int, **kwargs) -> dict[str, Any]:
    """Convenience function to get scheduler status."""
    return get_status_reader().get_status(job_name, interval_seconds, **kwargs)


def clear_cache(job_name: str | None = None):
    """Convenience function to clear cache."""
    get_status_reader().clear_cache(job_name)
