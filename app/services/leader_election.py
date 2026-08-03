"""
Open ACE - Distributed Leader Election

Provides leader election mechanisms for distributed scheduler coordination.
Supports two strategies:

Strategy A (Advisory Lock): For short tasks (< 5 minutes)
- Uses PostgreSQL session-level advisory locks
- Lock held within transaction scope
- Automatically released on commit/rollback/connection close

Strategy B (Heartbeat): For long tasks (> 5 minutes)
- Uses scheduler_leaders table with periodic heartbeat updates
- Supports leader expiration and failover
- Requires manual release or expiration timeout

Issue #2187
"""

from __future__ import annotations

import hashlib
import logging
import os
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from app.repositories.database import Database

logger = logging.getLogger(__name__)

# Configuration constants
HEARTBEAT_INTERVAL_SECONDS = int(os.environ.get("SCHEDULER_HEARTBEAT_INTERVAL", "10"))
HEARTBEAT_TIMEOUT_SECONDS = int(os.environ.get("SCHEDULER_HEARTBEAT_TIMEOUT", "60"))
LOCK_TIMEOUT_SECONDS = int(os.environ.get("SCHEDULER_LOCK_TIMEOUT", "1800"))
ELECTION_INTERVAL_SECONDS = int(os.environ.get("SCHEDULER_ELECTION_INTERVAL", "15"))


@dataclass
class LeaderInfo:
    """Information about current leader."""

    job_name: str
    leader_id: str
    owner_info: Optional[str]
    acquired_at: datetime
    expires_at: datetime
    heartbeat_at: datetime
    last_run_at: Optional[datetime]
    run_count: int
    skip_count: int
    fail_count: int


def generate_leader_id() -> str:
    """Generate unique leader identifier.

    Format: {hostname}-{pid}-{uuid8}
    """
    hostname = socket.gethostname()
    pid = os.getpid()
    uuid_str = str(uuid.uuid4())[:8]
    return f"{hostname}-{pid}-{uuid_str}"


def get_owner_info() -> str:
    """Get owner information for debugging."""
    return f"{socket.gethostname()}:{os.getpid()}"


def job_name_to_lock_key(job_name: str) -> int:
    """Convert job_name to advisory lock key (64-bit integer).

    Uses SHA-256 hash and takes first 8 bytes to ensure consistent mapping.
    """
    hash_bytes = hashlib.sha256(job_name.encode()).digest()[:8]
    return int.from_bytes(hash_bytes, byteorder="big", signed=True)


class LeaderElectionClient:
    """Client for distributed leader election.

    Supports two strategies:
    - Advisory Lock (strategy="advisory"): For short tasks, lock held in transaction
    - Heartbeat (strategy="heartbeat"): For long tasks, periodic heartbeat updates
    """

    def __init__(
        self,
        job_name: str,
        db: Database,
        strategy: str = "auto",
        heartbeat_interval: int = HEARTBEAT_INTERVAL_SECONDS,
        heartbeat_timeout: int = HEARTBEAT_TIMEOUT_SECONDS,
        lock_timeout: int = LOCK_TIMEOUT_SECONDS,
    ):
        """Initialize leader election client.

        Args:
            job_name: Unique name for this job
            db: Database instance
            strategy: "advisory", "heartbeat", or "auto" (chooses based on timeout)
            heartbeat_interval: Seconds between heartbeat updates
            heartbeat_timeout: Seconds before heartbeat considered stale
            lock_timeout: Seconds before lock expires (fallback)
        """
        self.job_name = job_name
        self.db = db
        self.leader_id = generate_leader_id()
        self.owner_info = get_owner_info()

        # Determine strategy
        if strategy == "auto":
            # Auto-select based on lock timeout
            self.strategy = "heartbeat" if lock_timeout > 300 else "advisory"
        else:
            self.strategy = strategy

        self.heartbeat_interval = heartbeat_interval
        self.heartbeat_timeout = heartbeat_timeout
        self.lock_timeout = lock_timeout

        # State tracking
        self._is_leader = False
        self._lock_key = job_name_to_lock_key(job_name)
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._stop_heartbeat = threading.Event()
        self._lock = threading.Lock()

        # Metrics tracking
        self._run_count = 0
        self._skip_count = 0
        self._fail_count = 0

        logger.info(
            f"LeaderElectionClient initialized: job={job_name}, "
            f"leader_id={self.leader_id}, strategy={self.strategy}"
        )

    def try_acquire_leadership(self, timeout: Optional[int] = None) -> bool:
        """Attempt to acquire leadership.

        Args:
            timeout: Lock timeout in seconds (defaults to self.lock_timeout)

        Returns:
            True if leadership acquired, False otherwise
        """
        timeout = timeout or self.lock_timeout

        if self.strategy == "advisory":
            return self._try_acquire_advisory_lock()
        else:
            return self._try_acquire_heartbeat_lock(timeout)

    def _try_acquire_advisory_lock(self) -> bool:
        """Try to acquire advisory lock (Strategy A).

        Uses PostgreSQL session-level advisory lock.
        Lock is held for the duration of the transaction.
        """
        if not self.db.is_postgresql:
            # SQLite doesn't support advisory locks, fall back to heartbeat
            logger.warning(
                f"Advisory lock not supported on SQLite, using heartbeat for {self.job_name}"
            )
            self.strategy = "heartbeat"
            return self._try_acquire_heartbeat_lock(self.lock_timeout)

        try:
            # Check if lock is available
            result = self.db.fetch_one(
                "SELECT pg_try_advisory_xact_lock(?) AS acquired",
                (self._lock_key,),
            )

            if result and result.get("acquired"):
                self._is_leader = True
                logger.info(f"Advisory lock acquired: job={self.job_name}, key={self._lock_key}")
                return True
            else:
                self._skip_count += 1
                logger.debug(
                    f"Advisory lock not acquired (held by another): job={self.job_name}"
                )
                return False

        except Exception as e:
            logger.error(f"Failed to acquire advisory lock: {e}")
            self._fail_count += 1
            return False

    def _try_acquire_heartbeat_lock(self, timeout: int) -> bool:
        """Try to acquire leadership via heartbeat mechanism (Strategy B).

        Attempts to insert a new leader record or update an expired one.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        expires_at = datetime.now(timezone.utc).replace(tzinfo=None).replace(
            second=0, microsecond=0
        ) + __import__("datetime").timedelta(seconds=timeout)

        try:
            # Try to insert new leader
            self.db.execute(
                """
                INSERT INTO scheduler_leaders
                    (job_name, leader_id, owner_info, acquired_at, expires_at, heartbeat_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (job_name) DO UPDATE SET
                    leader_id = EXCLUDED.leader_id,
                    owner_info = EXCLUDED.owner_info,
                    acquired_at = EXCLUDED.acquired_at,
                    expires_at = EXCLUDED.expires_at,
                    heartbeat_at = EXCLUDED.heartbeat_at,
                    run_count = scheduler_leaders.run_count,
                    skip_count = scheduler_leaders.skip_count,
                    fail_count = scheduler_leaders.fail_count
                WHERE scheduler_leaders.expires_at < ?
                """,
                (
                    self.job_name,
                    self.leader_id,
                    self.owner_info,
                    now,
                    expires_at,
                    now,
                    now,  # WHERE clause parameter
                ),
            )

            # Check if we're now the leader
            result = self.db.fetch_one(
                "SELECT leader_id FROM scheduler_leaders WHERE job_name = ?",
                (self.job_name,),
            )

            if result and result.get("leader_id") == self.leader_id:
                self._is_leader = True
                self._start_heartbeat_thread()
                logger.info(f"Heartbeat lock acquired: job={self.job_name}")
                return True
            else:
                self._skip_count += 1
                logger.debug(
                    f"Heartbeat lock not acquired (held by another): job={self.job_name}"
                )
                return False

        except Exception as e:
            logger.error(f"Failed to acquire heartbeat lock: {e}")
            self._fail_count += 1
            return False

    def renew_heartbeat(self) -> bool:
        """Renew heartbeat (only for heartbeat strategy).

        Returns:
            True if still leader, False otherwise
        """
        if self.strategy != "heartbeat" or not self._is_leader:
            return False

        now = datetime.now(timezone.utc).replace(tzinfo=None)

        try:
            self.db.execute(
                """
                UPDATE scheduler_leaders
                SET heartbeat_at = ?
                WHERE job_name = ? AND leader_id = ?
                """,
                (now, self.job_name, self.leader_id),
            )

            # Verify we're still the leader
            result = self.db.fetch_one(
                "SELECT leader_id FROM scheduler_leaders WHERE job_name = ?",
                (self.job_name,),
            )

            if result and result.get("leader_id") == self.leader_id:
                return True
            else:
                self._is_leader = False
                logger.warning(f"Heartbeat renewal failed: job={self.job_name}, lost leadership")
                return False

        except Exception as e:
            logger.error(f"Heartbeat renewal failed: {e}")
            self._is_leader = False
            return False

    def release_leadership(self) -> None:
        """Release leadership."""
        with self._lock:
            if not self._is_leader:
                return

            self._stop_heartbeat_thread()

            if self.strategy == "heartbeat":
                try:
                    self.db.execute(
                        "DELETE FROM scheduler_leaders WHERE job_name = ? AND leader_id = ?",
                        (self.job_name, self.leader_id),
                    )
                    logger.info(f"Leadership released: job={self.job_name}")
                except Exception as e:
                    logger.error(f"Failed to release leadership: {e}")
            else:
                # Advisory lock is released automatically on transaction commit
                logger.info(f"Advisory lock will be released on transaction commit: job={self.job_name}")

            self._is_leader = False

    def is_leader(self) -> bool:
        """Check if currently the leader."""
        return self._is_leader

    def get_leader_info(self) -> Optional[LeaderInfo]:
        """Get current leader information.

        Returns:
            LeaderInfo if leader exists, None otherwise
        """
        try:
            result = self.db.fetch_one(
                """
                SELECT job_name, leader_id, owner_info, acquired_at, expires_at,
                       heartbeat_at, last_run_at, run_count, skip_count, fail_count
                FROM scheduler_leaders
                WHERE job_name = ?
                """,
                (self.job_name,),
            )

            if not result:
                return None

            return LeaderInfo(
                job_name=result["job_name"],
                leader_id=result["leader_id"],
                owner_info=result.get("owner_info"),
                acquired_at=result["acquired_at"],
                expires_at=result["expires_at"],
                heartbeat_at=result["heartbeat_at"],
                last_run_at=result.get("last_run_at"),
                run_count=result["run_count"],
                skip_count=result["skip_count"],
                fail_count=result["fail_count"],
            )

        except Exception as e:
            logger.error(f"Failed to get leader info: {e}")
            return None

    def record_run(self, status: str, duration_ms: Optional[int] = None,
                    error_message: Optional[str] = None) -> None:
        """Record a run execution.

        Args:
            status: 'completed', 'failed', or 'skipped'
            duration_ms: Execution duration in milliseconds
            error_message: Error message if failed
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        try:
            # Update counters in scheduler_leaders
            if status == "completed":
                self.db.execute(
                    """
                    UPDATE scheduler_leaders
                    SET run_count = run_count + 1, last_run_at = ?
                    WHERE job_name = ? AND leader_id = ?
                    """,
                    (now, self.job_name, self.leader_id),
                )
                self._run_count += 1
            elif status == "skipped":
                self.db.execute(
                    """
                    UPDATE scheduler_leaders
                    SET skip_count = skip_count + 1
                    WHERE job_name = ? AND leader_id = ?
                    """,
                    (self.job_name, self.leader_id),
                )
                self._skip_count += 1
            elif status == "failed":
                self.db.execute(
                    """
                    UPDATE scheduler_leaders
                    SET fail_count = fail_count + 1
                    WHERE job_name = ? AND leader_id = ?
                    """,
                    (self.job_name, self.leader_id),
                )
                self._fail_count += 1

            # Record in scheduler_runs
            self.db.execute(
                """
                INSERT INTO scheduler_runs
                    (job_name, leader_id, started_at, ended_at, status, duration_ms, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (self.job_name, self.leader_id, now, now, status, duration_ms, error_message),
            )

        except Exception as e:
            logger.error(f"Failed to record run: {e}")

    def _start_heartbeat_thread(self) -> None:
        """Start background heartbeat thread."""
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            return

        self._stop_heartbeat.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name=f"heartbeat-{self.job_name}",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def _stop_heartbeat_thread(self) -> None:
        """Stop heartbeat thread."""
        if not self._heartbeat_thread:
            return

        self._stop_heartbeat.set()
        self._heartbeat_thread.join(timeout=5)
        self._heartbeat_thread = None

    def _heartbeat_loop(self) -> None:
        """Background loop to renew heartbeat."""
        while not self._stop_heartbeat.is_set():
            try:
                if not self.renew_heartbeat():
                    logger.warning(f"Heartbeat lost, stopping: job={self.job_name}")
                    break
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")

            self._stop_heartbeat.wait(self.heartbeat_interval)

    def get_metrics(self) -> dict[str, Any]:
        """Get metrics for this client."""
        return {
            "job_name": self.job_name,
            "leader_id": self.leader_id,
            "is_leader": self._is_leader,
            "strategy": self.strategy,
            "run_count": self._run_count,
            "skip_count": self._skip_count,
            "fail_count": self._fail_count,
        }


def check_scheduler_tables_exist(db: Database) -> bool:
    """Check if scheduler tables exist.

    Args:
        db: Database instance

    Returns:
        True if tables exist, False otherwise
    """
    try:
        if db.is_postgresql:
            result = db.fetch_one(
                """
                SELECT COUNT(*) as count
                FROM information_schema.tables
                WHERE table_name IN ('scheduler_leaders', 'scheduler_runs')
                """
            )
            return result and result.get("count", 0) == 2
        else:
            # SQLite
            result = db.fetch_one(
                """
                SELECT COUNT(*) as count
                FROM sqlite_master
                WHERE type='table' AND name IN ('scheduler_leaders', 'scheduler_runs')
                """
            )
            return result and result.get("count", 0) == 2
    except Exception as e:
        logger.error(f"Failed to check scheduler tables: {e}")
        return False