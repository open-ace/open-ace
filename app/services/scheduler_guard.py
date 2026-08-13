"""
Open ACE - Scheduler Execution Guard

Provides unified, fail-closed lock management for background scheduler jobs.
Issue #2333: Ensures lock handles cover complete job execution windows.

Supports two strategies:
- session_lock: PostgreSQL session-level advisory lock, held on dedicated connection
- heartbeat: Database table-based lease with periodic renewal and fencing tokens

Transaction-level locks are intentionally NOT supported - they're released when
the acquisition context closes, providing no real mutual exclusion.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
import uuid
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any

from typing_extensions import Self

from app.repositories.database import Database, adapt_sql, is_postgresql

logger = logging.getLogger(__name__)

# Configuration
LOCK_ACQUISITION_TIMEOUT_SECONDS = int(os.environ.get("SCHEDULER_LOCK_TIMEOUT", "30"))
HEARTBEAT_INTERVAL_SECONDS = int(os.environ.get("SCHEDULER_HEARTBEAT_INTERVAL", "10"))
HEARTBEAT_TIMEOUT_SECONDS = int(os.environ.get("SCHEDULER_HEARTBEAT_TIMEOUT", "60"))

# SQLite warning tracking (single warning per session)
_sqlite_warning_logged = False


class LockAcquisitionError(Exception):
    """Raised when a lock cannot be acquired.

    This is a signal to skip job execution without side effects.
    The guard context manager catches this and records status='skipped'.
    """

    pass


def generate_leader_id() -> str:
    """Generate unique leader identifier.

    Format: {hostname}-{pid}-{uuid8}
    """
    hostname = socket.gethostname()
    pid = os.getpid()
    uuid_str = str(uuid.uuid4())[:8]
    return f"{hostname}-{pid}-{uuid_str}"


def job_name_to_lock_key(job_name: str) -> int:
    """Convert job_name to advisory lock key (64-bit integer).

    Uses SHA-256 hash and takes first 8 bytes to ensure consistent mapping.
    """
    import hashlib

    hash_bytes = hashlib.sha256(job_name.encode()).digest()[:8]
    return int.from_bytes(hash_bytes, byteorder="big", signed=True)


class SchedulerExecutionGuard:
    """Unified context manager for scheduler job locking.

    Ensures lock covers complete job execution window. Fail-closed:
    - Lock acquisition failure raises LockAcquisitionError (skip execution)
    - Heartbeat lease loss aborts job before external commits
    - Connection lifecycle safety with timeout and finally cleanup

    Usage:
        with SchedulerExecutionGuard("my_job", db, strategy="session_lock") as guard:
            # Job body runs only if lock acquired
            ...
            # For heartbeat strategy, check before external commits:
            if guard.check_lease_valid():
                perform_external_commit()

    Issue #2333
    """

    def __init__(
        self,
        job_name: str,
        db: Database,
        strategy: str = "session_lock",
        lock_timeout: int | None = None,
        heartbeat_interval: int | None = None,
        leader_id: str | None = None,
    ):
        """Initialize the guard.

        Args:
            job_name: Unique name for this job (used as lock key).
            db: Database instance.
            strategy: "session_lock" or "heartbeat".
            lock_timeout: Lock timeout in seconds (for heartbeat strategy).
            heartbeat_interval: Seconds between heartbeat updates.
            leader_id: Optional leader ID (generated if not provided).
        """
        if strategy not in ("session_lock", "heartbeat"):
            raise ValueError(f"Invalid strategy: {strategy}. Must be 'session_lock' or 'heartbeat'")

        self.job_name = job_name
        self.db = db
        self.strategy = strategy
        self.lock_key = job_name_to_lock_key(job_name)
        self.leader_id = leader_id or generate_leader_id()
        self.leader_host = f"{socket.gethostname()}:{os.getpid()}"

        # Heartbeat configuration
        self.lock_timeout = lock_timeout or HEARTBEAT_TIMEOUT_SECONDS
        self.heartbeat_interval = heartbeat_interval or HEARTBEAT_INTERVAL_SECONDS

        # Connection for session_lock strategy
        self._connection: Any = None

        # Heartbeat thread and state
        self._heartbeat_thread: threading.Thread | None = None
        self._stop_heartbeat = threading.Event()
        self._lease_lost_event = threading.Event()
        self._lease_check_lock = threading.Lock()

        # Fencing token (heartbeat strategy only)
        self._fencing_token: int | None = None

        # Timing
        self._lock_acquired_at: datetime | None = None
        self._lock_released_at: datetime | None = None
        self._lock_acquisition_timeout = LOCK_ACQUISITION_TIMEOUT_SECONDS

        # State tracking
        self._acquired = False

    def __enter__(self) -> Self:
        """Acquire lock and enter context."""
        if self.strategy == "session_lock":
            self._acquire_session_lock()
        else:
            self._acquire_heartbeat_lock()

        self._lock_acquired_at = datetime.now(timezone.utc).replace(tzinfo=None)
        self._acquired = True
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Release lock and record run."""
        try:
            self._release_lock()
        except Exception as e:
            logger.error(f"Failed to release lock for {self.job_name}: {e}")

        self._lock_released_at = datetime.now(timezone.utc).replace(tzinfo=None)

        # Record run if we acquired the lock
        if self._acquired:
            status = "completed" if exc_type is None else "failed"
            if exc_type is LockAcquisitionError:
                status = "skipped"
            self._record_run(status)

        self._acquired = False

    def _acquire_session_lock(self) -> None:
        """Acquire PostgreSQL session-level advisory lock.

        Holds a dedicated connection for the lock duration.
        Raises LockAcquisitionError if lock unavailable.
        """
        if not is_postgresql():
            # SQLite doesn't support advisory locks
            global _sqlite_warning_logged
            if not _sqlite_warning_logged:
                logger.warning(
                    f"SQLite does not support advisory locks. Job '{self.job_name}' "
                    "will run without distributed mutual exclusion. "
                    "This is safe only for single-process development."
                )
                _sqlite_warning_logged = True
            return

        # Get a connection that we'll hold for the lock duration
        self._connection = self.db.get_connection()

        try:
            cursor = self._connection.cursor()

            # Try to acquire the lock with timeout
            # pg_advisory_lock blocks, pg_try_advisory_lock returns immediately
            # We use try with a polling loop for timeout support
            start_time = time.monotonic()
            acquired = False

            while time.monotonic() - start_time < self._lock_acquisition_timeout:
                cursor.execute("SELECT pg_try_advisory_lock(%s) AS acquired", (self.lock_key,))
                result = cursor.fetchone()
                if result and (result[0] if isinstance(result, tuple) else result.get("acquired")):
                    acquired = True
                    break
                # Wait a bit before retrying
                time.sleep(0.1)

            if not acquired:
                self._safe_close_connection()
                raise LockAcquisitionError(
                    f"Could not acquire session lock for '{self.job_name}' within {self._lock_acquisition_timeout}s"
                )

            logger.info(
                f"Session lock acquired: job={self.job_name}, key={self.lock_key}, leader={self.leader_id}"
            )

        except LockAcquisitionError:
            raise
        except Exception as e:
            self._safe_close_connection()
            raise LockAcquisitionError(
                f"Failed to acquire session lock for '{self.job_name}': {e}"
            ) from e

    def _acquire_heartbeat_lock(self) -> None:
        """Acquire heartbeat-based lease with fencing token.

        Inserts/updates scheduler_leaders table with fencing token.
        Starts heartbeat thread for renewal.
        Raises LockAcquisitionError if lease unavailable.
        """
        if not is_postgresql():
            # SQLite fallback - no real distributed lock
            global _sqlite_warning_logged
            if not _sqlite_warning_logged:
                logger.warning(
                    f"SQLite heartbeat lock for '{self.job_name}' is single-process only. "
                    "No distributed mutual exclusion is provided."
                )
                _sqlite_warning_logged = True
            # Still proceed for SQLite development
            self._fencing_token = int(time.time() * 1000) % (2**63)
            return

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        expires_at = datetime.now(timezone.utc).replace(tzinfo=None).replace(
            second=0, microsecond=0
        ) + __import__("datetime").timedelta(seconds=self.lock_timeout)

        try:
            # Get fencing token from sequence
            # Use a temporary connection - must be closed explicitly
            connection = self.db.get_connection()
            try:
                cursor = connection.cursor()
                cursor.execute("SELECT nextval('fencing_token_seq')")
                result = cursor.fetchone()
                self._fencing_token = (
                    result[0] if isinstance(result, tuple) else result.get("nextval")
                )
            finally:
                connection.close()

            # Try to acquire/update lease
            self.db.execute(
                adapt_sql(
                    """
                    INSERT INTO scheduler_leaders
                        (job_name, leader_id, owner_info, acquired_at, expires_at, heartbeat_at,
                         fencing_token, lock_strategy)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (job_name) DO UPDATE SET
                        leader_id = EXCLUDED.leader_id,
                        owner_info = EXCLUDED.owner_info,
                        acquired_at = EXCLUDED.acquired_at,
                        expires_at = EXCLUDED.expires_at,
                        heartbeat_at = EXCLUDED.heartbeat_at,
                        fencing_token = EXCLUDED.fencing_token,
                        lock_strategy = EXCLUDED.lock_strategy,
                        run_count = scheduler_leaders.run_count,
                        skip_count = scheduler_leaders.skip_count,
                        fail_count = scheduler_leaders.fail_count
                    WHERE scheduler_leaders.expires_at < ?
                    """
                ),
                (
                    self.job_name,
                    self.leader_id,
                    self.leader_host,
                    now,
                    expires_at,
                    now,
                    self._fencing_token,
                    "heartbeat",
                    now,  # WHERE clause parameter
                ),
            )

            # Verify we're the leader
            result = self.db.fetch_one(
                "SELECT leader_id, fencing_token FROM scheduler_leaders WHERE job_name = ?",
                (self.job_name,),
            )

            if not result or result.get("leader_id") != self.leader_id:
                self._fencing_token = None
                raise LockAcquisitionError(
                    f"Could not acquire heartbeat lease for '{self.job_name}' - held by another worker"
                )

            # Use the actual fencing token from the database
            self._fencing_token = result.get("fencing_token")

            # Start heartbeat thread
            self._stop_heartbeat.clear()
            self._lease_lost_event.clear()
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                name=f"heartbeat-{self.job_name}",
                daemon=True,
            )
            self._heartbeat_thread.start()

            logger.info(
                f"Heartbeat lease acquired: job={self.job_name}, leader={self.leader_id}, "
                f"fencing_token={self._fencing_token}"
            )

        except LockAcquisitionError:
            raise
        except Exception as e:
            self._fencing_token = None
            raise LockAcquisitionError(
                f"Failed to acquire heartbeat lease for '{self.job_name}': {e}"
            ) from e

    def _heartbeat_loop(self) -> None:
        """Background thread that renews heartbeat lease."""
        while not self._stop_heartbeat.is_set():
            try:
                self._renew_heartbeat()
            except Exception as e:
                logger.error(f"Heartbeat renewal failed for {self.job_name}: {e}")
                self._lease_lost_event.set()

            # Wait for next heartbeat interval or stop signal
            self._stop_heartbeat.wait(self.heartbeat_interval)

    def _renew_heartbeat(self) -> bool:
        """Renew heartbeat lease.

        Returns:
            True if renewal successful, False otherwise.
        """
        if not is_postgresql():
            return True  # SQLite - no real lease

        now = datetime.now(timezone.utc).replace(tzinfo=None)

        try:
            self.db.execute(
                adapt_sql(
                    """
                    UPDATE scheduler_leaders
                    SET heartbeat_at = ?
                    WHERE job_name = ? AND leader_id = ?
                    """
                ),
                (now, self.job_name, self.leader_id),
            )

            # Verify we're still the leader
            result = self.db.fetch_one(
                "SELECT leader_id FROM scheduler_leaders WHERE job_name = ?",
                (self.job_name,),
            )

            if not result or result.get("leader_id") != self.leader_id:
                logger.warning(f"Heartbeat renewal detected lease loss for {self.job_name}")
                return False

            return True

        except Exception as e:
            logger.error(f"Heartbeat renewal error for {self.job_name}: {e}")
            return False

    def check_lease_valid(self) -> bool:
        """Check if lease is still valid (heartbeat strategy only).

        Call this before external commits to prevent stale leader writes.

        Returns:
            True if lease is valid, False if lost.
        """
        if self.strategy != "heartbeat":
            return True

        return not self._lease_lost_event.is_set()

    def get_fencing_token(self) -> int | None:
        """Get the current fencing token (heartbeat strategy only).

        Use this for external commit validation to prevent stale leader writes.

        Returns:
            Fencing token or None if not using heartbeat strategy.
        """
        return self._fencing_token

    def validate_fencing_token(self, token: int) -> bool:
        """Validate a fencing token against the current leader.

        Args:
            token: Token to validate.

        Returns:
            True if token matches current leader, False otherwise.
        """
        if self.strategy != "heartbeat":
            return True

        try:
            result = self.db.fetch_one(
                "SELECT fencing_token FROM scheduler_leaders WHERE job_name = ?",
                (self.job_name,),
            )

            if not result:
                return False

            current_token = result.get("fencing_token")
            return current_token == token

        except Exception:
            return False

    def _release_lock(self) -> None:
        """Release the lock."""
        if self.strategy == "session_lock":
            self._release_session_lock()
        else:
            self._release_heartbeat_lock()

    def _release_session_lock(self) -> None:
        """Release PostgreSQL session-level advisory lock."""
        if self._connection is None:
            return

        try:
            if is_postgresql():
                cursor = self._connection.cursor()
                cursor.execute("SELECT pg_advisory_unlock(%s)", (self.lock_key,))
                self._connection.commit()
                logger.info(f"Session lock released: job={self.job_name}, key={self.lock_key}")
        except Exception as e:
            logger.error(f"Failed to release session lock for {self.job_name}: {e}")
        finally:
            self._safe_close_connection()

    def _release_heartbeat_lock(self) -> None:
        """Release heartbeat lease."""
        # Stop heartbeat thread
        self._stop_heartbeat.set()
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=2)

        if not is_postgresql():
            return

        try:
            self.db.execute(
                "DELETE FROM scheduler_leaders WHERE job_name = ? AND leader_id = ?",
                (self.job_name, self.leader_id),
            )
            logger.info(f"Heartbeat lease released: job={self.job_name}, leader={self.leader_id}")
        except Exception as e:
            logger.error(f"Failed to release heartbeat lease for {self.job_name}: {e}")

    def _safe_close_connection(self) -> None:
        """Safely close connection, handling errors."""
        if self._connection is None:
            return

        try:
            with suppress(Exception):
                self._connection.close()
        except Exception:
            pass
        finally:
            self._connection = None

    def _record_run(self, status: str, error_message: str | None = None) -> None:
        """Record run in scheduler_runs table.

        Args:
            status: Run status ('completed', 'failed', 'skipped', 'lost_leadership').
            error_message: Optional error message.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        duration_ms = None
        if self._lock_acquired_at and self._lock_released_at:
            duration_ms = int(
                (self._lock_released_at - self._lock_acquired_at).total_seconds() * 1000
            )

        try:
            self.db.execute(
                adapt_sql(
                    """
                    INSERT INTO scheduler_runs
                        (job_name, leader_id, started_at, ended_at, status, duration_ms,
                         error_message, lock_strategy, fencing_token, lock_acquired_at,
                         lock_released_at, leader_host)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """
                ),
                (
                    self.job_name,
                    self.leader_id,
                    self._lock_acquired_at or now,
                    self._lock_released_at or now,
                    status,
                    duration_ms,
                    error_message,
                    self.strategy,
                    self._fencing_token,
                    self._lock_acquired_at,
                    self._lock_released_at,
                    self.leader_host,
                ),
            )

            # Update counters in scheduler_leaders
            if status == "completed":
                self.db.execute(
                    adapt_sql(
                        """
                        UPDATE scheduler_leaders
                        SET run_count = run_count + 1, last_run_at = ?
                        WHERE job_name = ? AND leader_id = ?
                        """
                    ),
                    (now, self.job_name, self.leader_id),
                )
            elif status == "skipped":
                self.db.execute(
                    adapt_sql(
                        """
                        UPDATE scheduler_leaders
                        SET skip_count = skip_count + 1
                        WHERE job_name = ?
                        """
                    ),
                    (self.job_name,),
                )
            elif status in ("failed", "lost_leadership"):
                self.db.execute(
                    adapt_sql(
                        """
                        UPDATE scheduler_leaders
                        SET fail_count = fail_count + 1
                        WHERE job_name = ? AND leader_id = ?
                        """
                    ),
                    (self.job_name, self.leader_id),
                )

        except Exception as e:
            logger.error(f"Failed to record run for {self.job_name}: {e}")

    @property
    def acquired(self) -> bool:
        """Whether lock was successfully acquired."""
        return self._acquired

    @property
    def lock_acquired_at(self) -> datetime | None:
        """When lock was acquired."""
        return self._lock_acquired_at

    @property
    def lock_released_at(self) -> datetime | None:
        """When lock was released."""
        return self._lock_released_at


def check_scheduler_process_guard(job_name: str) -> bool:
    """Check if current process is allowed to run scheduler jobs.

    Logs warning if called in web worker process.

    Args:
        job_name: Name of the scheduler job being initialized.

    Returns:
        True if process is scheduler mode, False otherwise.
    """
    scheduler_mode = os.environ.get("SCHEDULER_MODE", "web")
    if scheduler_mode == "scheduler":
        return True

    logger.warning(
        f"Scheduler job '{job_name}' initialized in web worker process. "
        f"SCHEDULER_MODE={scheduler_mode}. Jobs should only run in scheduler process. "
        "This is a no-op in web workers."
    )
    return False
