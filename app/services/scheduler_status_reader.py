"""
Open ACE - Scheduler Status Reader

Provides unified status reading for schedulers across processes.

Issue #2820: Enables Web API and health monitor to read scheduler status
from shared database storage (scheduler_leaders table) instead of process-local memory.
Issue #3144: Adds execution health monitoring based on scheduler_runs history.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Configuration
CACHE_TTL_SECONDS = int(os.environ.get("SCHEDULER_STATUS_CACHE_TTL", "5"))
QUERY_TIMEOUT_SECONDS = int(os.environ.get("SCHEDULER_STATUS_QUERY_TIMEOUT", "5"))

# Default health thresholds (can be overridden by environment variables)
DEFAULT_THRESHOLD_HEALTHY = int(os.environ.get("SCHEDULER_HEALTH_THRESHOLD_HEALTHY", "0"))
DEFAULT_THRESHOLD_STOPPED = int(os.environ.get("SCHEDULER_HEALTH_THRESHOLD_STOPPED", "0"))

# Execution health thresholds (Issue #3144)
DEFAULT_FAILURE_THRESHOLD = int(os.environ.get("SCHEDULER_FAILURE_THRESHOLD", "3"))
DEFAULT_NO_SUCCESS_THRESHOLD_SEC = int(
    os.environ.get("SCHEDULER_NO_SUCCESS_THRESHOLD_SEC", "3600")  # 1 hour
)


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
            - health_status: "healthy" | "stale" | "stopped" | "idle" | "unknown"
            - cache_age_seconds: float or None
            - cache_hit: bool or None
            - error: str or None (if database error)
            - execution_health: "healthy" | "degraded" | "failing" | "unknown" (Issue #3144)
            - latest_run_status: "completed" | "partial" | "failed" | "skipped" | None
            - latest_run_at: ISO timestamp or None
            - last_success_at: ISO timestamp or None
            - consecutive_failures: int
            - error_summary: str or None (sanitized error message)
        """
        # Set default thresholds based on interval
        # Type narrowing: after this block, thresholds are guaranteed to be int
        if threshold_healthy is None or threshold_healthy == 0:
            threshold_healthy = interval_seconds + 60
        if threshold_stopped is None or threshold_stopped == 0:
            threshold_stopped = threshold_healthy * 2

        # Assert types for mypy
        assert threshold_healthy is not None and threshold_stopped is not None

        # Check cache
        cache_key = job_name
        now = time.time()
        cache_age = None

        with self._cache_lock:
            if cache_key in self._cache:
                cached_data, cached_at = self._cache[cache_key]
                cache_age = now - cached_at
                if cache_age < CACHE_TTL_SECONDS:
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
                # Execution health fields (Issue #3144)
                "execution_health": "unknown",
                "latest_run_status": None,
                "latest_run_at": None,
                "last_success_at": None,
                "consecutive_failures": 0,
                "error_summary": None,
            }

    def _query_database(
        self,
        job_name: str,
        interval_seconds: int,
        threshold_healthy: int,
        threshold_stopped: int,
    ) -> dict[str, Any]:
        """Query scheduler_leaders table for status.

        When the leader row is absent (idle interval between runs),
        falls back to scheduler_runs for last-run and worker info.
        Issue #3146: Fall back to scheduler_runs when no leader row.
        Issue #3144: Include execution health from scheduler_runs.
        """
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
            # No active leader row — the scheduler is idle between runs
            # (release_leadership deletes the row after each execution).
            # Fall back to scheduler_runs for durable run history.
            # Issue #3146.
            return self._query_runs_fallback(
                db, job_name, interval_seconds, threshold_healthy, threshold_stopped
            )

        # Check if expired
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        expires_at = result.get("expires_at")
        heartbeat_at = result.get("heartbeat_at")
        last_run_at = result.get("last_run_at")
        leader_id = result.get("leader_id")

        is_expired = bool(expires_at and expires_at < now)

        # Calculate heartbeat age
        heartbeat_age_seconds = None
        if heartbeat_at:
            # Handle timezone-aware and naive datetime objects
            if heartbeat_at.tzinfo is not None:
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
        heartbeat_str = heartbeat_at.isoformat() if heartbeat_at else None

        last_run_str = last_run_at.isoformat() if last_run_at else None

        next_run_str = next_run.isoformat() if next_run else None

        # Query execution health (Issue #3144)
        # When leader row exists, we still need to check scheduler_runs for execution health
        execution_health = self._query_execution_health_from_runs(db, job_name, last_run_at, now)

        return {
            "running": running,
            "worker_id": leader_id,
            "heartbeat": heartbeat_str,
            "heartbeat_age_seconds": heartbeat_age_seconds,
            "last_run": last_run_str,
            "next_run": next_run_str,
            "health_status": health_status,
            "error": None,
            # Execution health fields (Issue #3144)
            "execution_health": execution_health["execution_health"],
            "latest_run_status": execution_health["latest_run_status"],
            "latest_run_at": last_run_str,
            "last_success_at": execution_health["last_success_at"],
            "consecutive_failures": execution_health["consecutive_failures"],
            "error_summary": execution_health["error_summary"],
        }

    def _query_execution_health_from_runs(
        self,
        db: Any,
        job_name: str,
        last_run_at: datetime | None,
        now: datetime,
    ) -> dict[str, Any]:
        """Query execution health from scheduler_runs when leader row exists.

        Issue #3144: Helper method to get execution health info.

        Returns:
            Dict with execution health fields.
        """
        # Query the most recent run and consecutive failures
        runs = db.fetch_all(
            """
            SELECT status, started_at, ended_at, error_message
            FROM scheduler_runs
            WHERE job_name = ?
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (job_name, DEFAULT_FAILURE_THRESHOLD + 1),
        )

        if not runs:
            return {
                "execution_health": "unknown",
                "latest_run_status": None,
                "last_success_at": None,
                "consecutive_failures": 0,
                "error_summary": None,
            }

        # Get latest run status
        latest_run = runs[0]
        latest_run_status = latest_run.get("status")
        error_message = latest_run.get("error_message")

        # Count consecutive failures from most recent
        consecutive_failures = 0
        last_success_at: datetime | None = None

        for run in runs:
            status = run.get("status")
            if status == "failed":
                consecutive_failures += 1
            elif status in ("completed", "partial"):
                ended_at = run.get("ended_at") or run.get("started_at")
                if ended_at:
                    last_success_at = ended_at
                break
            else:
                # skipped or other status - doesn't break consecutive failure chain
                pass

        # Determine execution health
        if latest_run_status in ("completed", "partial"):
            execution_health = "healthy"
        elif consecutive_failures >= DEFAULT_FAILURE_THRESHOLD:
            execution_health = "failing"
        elif consecutive_failures > 0:
            execution_health = "degraded"
        else:
            execution_health = "healthy"

        return {
            "execution_health": execution_health,
            "latest_run_status": latest_run_status,
            "last_success_at": last_success_at.isoformat() if last_success_at else None,
            "consecutive_failures": consecutive_failures,
            "error_summary": self._sanitize_error_message(error_message),
        }

    def _query_runs_fallback(
        self,
        db: Any,
        job_name: str,
        interval_seconds: int,
        threshold_healthy: int,
        threshold_stopped: int,
    ) -> dict[str, Any]:
        """Query scheduler_runs for last-run info when no leader row exists.

        Issue #3146: After release_leadership() deletes the scheduler_leaders
        row, the scheduler is idle between runs.  We read the most recent
        scheduler_runs record to determine last_run, worker_id, next_run,
        and compute health based on the age of that record.
        Issue #3144: Also includes execution health information.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        run = db.fetch_one(
            """
            SELECT leader_id, started_at, ended_at, status, error_message
            FROM scheduler_runs
            WHERE job_name = ?
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (job_name,),
        )

        if not run:
            # No run history at all — scheduler has never executed
            return {
                "running": False,
                "worker_id": None,
                "heartbeat": None,
                "heartbeat_age_seconds": None,
                "last_run": None,
                "next_run": None,
                "health_status": "stopped",
                "error": None,
                # Execution health fields (Issue #3144)
                "execution_health": "unknown",
                "latest_run_status": None,
                "latest_run_at": None,
                "last_success_at": None,
                "consecutive_failures": 0,
                "error_summary": None,
            }

        # Use ended_at if available, otherwise started_at
        last_run_at = run.get("ended_at") or run.get("started_at")
        worker_id = run.get("leader_id")
        latest_run_status = run.get("status")
        error_message = run.get("error_message")

        # Calculate age of last run
        last_run_age_seconds: float | None = None
        if last_run_at:
            if last_run_at.tzinfo is not None:
                last_run_age_seconds = (now - last_run_at.replace(tzinfo=None)).total_seconds()
            else:
                last_run_age_seconds = (now - last_run_at).total_seconds()

        # Determine health status based on last run age
        if last_run_age_seconds is None or last_run_age_seconds >= threshold_stopped:
            health_status = "stopped"
        elif last_run_age_seconds >= threshold_healthy:
            health_status = "stale"
        else:
            # Last run was recent — worker is alive but idle between runs
            health_status = "idle"

        # running is True for idle and stale (worker may still be alive);
        # False only for stopped
        running = health_status in ("idle", "stale")

        # Calculate next_run from last_run + interval
        next_run = None
        if last_run_at:
            if last_run_at.tzinfo:
                last_run_naive = last_run_at.replace(tzinfo=None)
            else:
                last_run_naive = last_run_at
            next_run = last_run_naive + timedelta(seconds=interval_seconds)

        # Format timestamps
        last_run_str = last_run_at.isoformat() if last_run_at else None
        next_run_str = next_run.isoformat() if next_run else None

        # Query execution health (Issue #3144)
        execution_health = self._query_execution_health(
            db, job_name, latest_run_status, last_run_at, now
        )

        return {
            "running": running,
            "worker_id": worker_id,
            "heartbeat": None,  # No active heartbeat when idle
            "heartbeat_age_seconds": last_run_age_seconds,
            "last_run": last_run_str,
            "next_run": next_run_str,
            "health_status": health_status,
            "error": None,
            # Execution health fields (Issue #3144)
            "execution_health": execution_health["execution_health"],
            "latest_run_status": latest_run_status,
            "latest_run_at": last_run_str,
            "last_success_at": execution_health["last_success_at"],
            "consecutive_failures": execution_health["consecutive_failures"],
            "error_summary": self._sanitize_error_message(error_message),
        }

    def _query_execution_health(
        self,
        db: Any,
        job_name: str,
        latest_run_status: str | None,
        latest_run_at: datetime | None,
        now: datetime,
    ) -> dict[str, Any]:
        """Query execution health from scheduler_runs.

        Issue #3144: Determines execution health based on run history.

        Returns:
            Dict with:
            - execution_health: "healthy" | "degraded" | "failing" | "unknown"
            - last_success_at: ISO timestamp or None
            - consecutive_failures: int
        """
        # Query consecutive failures and last success
        runs = db.fetch_all(
            """
            SELECT status, started_at, ended_at
            FROM scheduler_runs
            WHERE job_name = ?
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (job_name, DEFAULT_FAILURE_THRESHOLD + 1),
        )

        if not runs:
            return {
                "execution_health": "unknown",
                "last_success_at": None,
                "consecutive_failures": 0,
            }

        # Count consecutive failures from most recent
        consecutive_failures = 0
        last_success_at: datetime | None = None

        for run in runs:
            status = run.get("status")
            if status == "failed":
                consecutive_failures += 1
            elif status in ("completed", "partial"):
                ended_at = run.get("ended_at") or run.get("started_at")
                if ended_at:
                    last_success_at = ended_at
                break
            else:
                # skipped or other status - doesn't break consecutive failure chain
                pass

        # Determine execution health
        if latest_run_status in ("completed", "partial"):
            execution_health = "healthy"
        elif consecutive_failures >= DEFAULT_FAILURE_THRESHOLD:
            execution_health = "failing"
        elif consecutive_failures > 0:
            execution_health = "degraded"
        else:
            execution_health = "healthy"

        return {
            "execution_health": execution_health,
            "last_success_at": last_success_at.isoformat() if last_success_at else None,
            "consecutive_failures": consecutive_failures,
        }

    def _sanitize_error_message(self, error_message: str | None) -> str | None:
        """Sanitize error message to remove sensitive information.

        Issue #3144: Removes paths, IPs, usernames, tokens, and credentials.

        Args:
            error_message: Raw error message from scheduler_runs.

        Returns:
            Sanitized error message safe for API exposure.
        """
        if not error_message:
            return None

        sanitized = error_message

        # Remove file paths (e.g., /home/user/..., /var/lib/...)
        sanitized = re.sub(r"/[\w./\-]+", "<path>", sanitized)

        # Remove IP addresses
        sanitized = re.sub(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "<ip>", sanitized)

        # Remove potential tokens/credentials (key=value patterns with long values)
        sanitized = re.sub(
            r"(token|key|password|secret|credential)[=:]\s*\S+",
            r"\1=<redacted>",
            sanitized,
            flags=re.IGNORECASE,
        )

        # Remove usernames in paths
        sanitized = re.sub(r"/home/[^/]+/", "/home/<user>/", sanitized)

        return sanitized

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
            if last_run_at.tzinfo:
                last_run_naive = last_run_at.replace(tzinfo=None)
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
