"""
Open ACE - Distributed Scheduler Base Class

Provides a base class for schedulers that need distributed coordination.
Simplifies integration of leader election into existing schedulers.

Issue #2187
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from app.repositories.database import Database
    from app.services.leader_election import LeaderElectionClient

logger = logging.getLogger(__name__)


class DistributedScheduler:
    """Base class for distributed schedulers with leader election.

    Subclasses should:
    1. Call super().__init__(job_name, db, strategy) in __init__
    2. Override _run_job() method with actual job logic
    3. Call run_with_lock() instead of running job directly

    Example:
        class MyScheduler(DistributedScheduler):
            def __init__(self, db: Database):
                super().__init__("my_scheduler", db, strategy="advisory")

            def run(self):
                self.run_with_lock(self._run_job)

            def _run_job(self):
                # Actual job logic here
                pass
    """

    def __init__(
        self,
        job_name: str,
        db: Database,
        strategy: str = "auto",
        lock_timeout: int = 1800,
        heartbeat_interval: int = 10,
        heartbeat_timeout: int = 60,
    ):
        """Initialize distributed scheduler.

        Args:
            job_name: Unique name for this scheduler
            db: Database instance
            strategy: "advisory", "heartbeat", or "auto"
            lock_timeout: Lock expiration timeout (seconds)
            heartbeat_interval: Heartbeat update interval (seconds)
            heartbeat_timeout: Heartbeat stale timeout (seconds)
        """
        self.job_name = job_name
        self.db = db
        self.strategy = strategy
        self.lock_timeout = lock_timeout
        self.heartbeat_interval = heartbeat_interval
        self.heartbeat_timeout = heartbeat_timeout

        self._leader_client: Optional[LeaderElectionClient] = None
        self._metrics: dict[str, int] = {
            "run_count": 0,
            "skip_count": 0,
            "fail_count": 0,
        }

        logger.info(
            f"DistributedScheduler initialized: job={job_name}, strategy={strategy}"
        )

    def _get_leader_client(self) -> LeaderElectionClient:
        """Get or create leader election client."""
        if self._leader_client is None:
            from app.services.leader_election import LeaderElectionClient

            self._leader_client = LeaderElectionClient(
                job_name=self.job_name,
                db=self.db,
                strategy=self.strategy,
                lock_timeout=self.lock_timeout,
                heartbeat_interval=self.heartbeat_interval,
                heartbeat_timeout=self.heartbeat_timeout,
            )
        return self._leader_client

    def run_with_lock(self, job_func: Callable[[], None]) -> bool:
        """Run job with distributed lock.

        Args:
            job_func: Function to execute if lock acquired

        Returns:
            True if job executed, False if skipped (no lock)
        """
        start_time = time.time()
        client = self._get_leader_client()

        # Try to acquire leadership
        if not client.try_acquire_leadership(self.lock_timeout):
            logger.debug(f"Job skipped (lock not acquired): {self.job_name}")
            self._metrics["skip_count"] += 1
            client.record_run("skipped")
            return False

        # We have leadership, run the job
        status = "completed"
        error_message = None

        try:
            logger.info(f"Job started: {self.job_name}")
            job_func()
            self._metrics["run_count"] += 1
            logger.info(f"Job completed: {self.job_name}")

        except Exception as e:
            status = "failed"
            error_message = str(e)
            self._metrics["fail_count"] += 1
            logger.error(f"Job failed: {self.job_name}, error: {e}", exc_info=True)

        finally:
            # Calculate duration
            duration_ms = int((time.time() - start_time) * 1000)

            # Record execution
            client.record_run(status, duration_ms, error_message)

            # Release leadership (for heartbeat strategy)
            if self.strategy == "heartbeat":
                client.release_leadership()

        return True

    def get_metrics(self) -> dict[str, Any]:
        """Get scheduler metrics."""
        metrics = self._metrics.copy()

        if self._leader_client:
            metrics.update(self._leader_client.get_metrics())

        return metrics

    def is_leader(self) -> bool:
        """Check if currently the leader."""
        if self._leader_client:
            return self._leader_client.is_leader()
        return False


def run_job_with_metrics(job_name: str, db: Database, job_func: Callable[[], None]) -> bool:
    """Convenience function to run a job with metrics.

    Args:
        job_name: Name of the job
        db: Database instance
        job_func: Job function to execute

    Returns:
        True if job executed, False if skipped
    """
    start_time = time.time()
    from app.services.leader_election import LeaderElectionClient, job_name_to_lock_key

    client = LeaderElectionClient(job_name, db, strategy="auto")

    if not client.try_acquire_leadership():
        logger.info(f"Job skipped: {job_name}")
        client.record_run("skipped")
        return False

    status = "completed"
    error_message = None

    try:
        job_func()
    except Exception as e:
        status = "failed"
        error_message = str(e)
        logger.error(f"Job {job_name} failed: {e}", exc_info=True)

    duration_ms = int((time.time() - start_time) * 1000)
    client.record_run(status, duration_ms, error_message)

    return True