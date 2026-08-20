"""Tests for DistributedScheduler.run_with_lock (Issue #2187).

Regression coverage for the advisory-lock deprecation: run_with_lock must
release the lease (delete the leader row and stop the heartbeat thread) after
each run, even when the scheduler was constructed with the deprecated
``strategy="advisory"`` (which now falls back to heartbeat). Gating the release
on ``self.strategy == "heartbeat"`` would leak a perpetual heartbeat thread and
never-deleted leader row for advisory/auto schedulers.
"""

from __future__ import annotations

import pytest

from app.repositories.database import Database
from app.services.distributed_scheduler import DistributedScheduler


@pytest.fixture
def db():
    """Database instance with the scheduler tables created (SQLite-safe)."""
    database = Database()

    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scheduler_leaders (
            job_name TEXT PRIMARY KEY,
            leader_id TEXT NOT NULL,
            owner_info TEXT,
            acquired_at TIMESTAMP NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            heartbeat_at TIMESTAMP NOT NULL,
            last_run_at TIMESTAMP,
            run_count INTEGER DEFAULT 0,
            skip_count INTEGER DEFAULT 0,
            fail_count INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scheduler_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_name TEXT NOT NULL,
            leader_id TEXT NOT NULL,
            started_at TIMESTAMP NOT NULL,
            ended_at TIMESTAMP,
            status TEXT NOT NULL,
            duration_ms INTEGER,
            error_message TEXT,
            metrics TEXT
        )
    """)
    conn.commit()
    conn.close()

    yield database

    try:
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM scheduler_runs")
        cursor.execute("DELETE FROM scheduler_leaders")
        conn.commit()
        conn.close()
    except Exception:  # allow-swallow: sqlite fixture cleanup (best-effort teardown)
        pass


def _assert_released(scheduler: DistributedScheduler, db: Database, job_name: str) -> None:
    """The lease is fully released: not leader, no row, no heartbeat thread."""
    assert scheduler.is_leader() is False
    client = scheduler._leader_client
    assert client is not None
    assert client._is_leader is False
    assert client._heartbeat_thread is None
    row = db.fetch_one(
        "SELECT leader_id FROM scheduler_leaders WHERE job_name = ?",
        (job_name,),
    )
    assert row is None


@pytest.mark.parametrize("strategy", ["heartbeat", "advisory", "auto"])
def test_run_with_lock_releases_leadership(db, strategy):
    """run_with_lock runs the job and releases the lease for every strategy.

    Regression: with the pre-fix ``if self.strategy == "heartbeat"`` release
    gate, an "advisory"/"auto" scheduler ran the job but never released,
    leaking the heartbeat thread and leader row.
    """
    job_name = f"drwl_{strategy}"
    scheduler = DistributedScheduler(job_name, db, strategy=strategy, lock_timeout=60)

    ran: list[int] = []
    result = scheduler.run_with_lock(lambda: ran.append(1))

    assert result is True
    assert ran == [1]
    _assert_released(scheduler, db, job_name)


def test_run_with_lock_releases_after_job_failure(db):
    """A failing job still releases the lease (release is in the finally block)."""
    job_name = "drwl_failing"
    scheduler = DistributedScheduler(job_name, db, strategy="advisory", lock_timeout=60)

    def boom() -> None:
        raise RuntimeError("boom")

    # run_with_lock swallows the job exception and returns True (it acquired).
    result = scheduler.run_with_lock(boom)

    assert result is True
    assert scheduler._metrics["fail_count"] == 1
    _assert_released(scheduler, db, job_name)


def test_run_with_lock_second_cycle_reacquires(db):
    """The cached client can re-acquire on a later cycle after release.

    Mirrors the pending_revoke_cleanup loop (run_with_lock every 60s): a leaked
    lease from cycle 1 would not by itself break cycle 2, but a leaked heartbeat
    thread would accumulate. After the fix each cycle acquires and releases.
    """
    job_name = "drwl_cycles"
    scheduler = DistributedScheduler(job_name, db, strategy="advisory", lock_timeout=60)

    for _ in range(2):
        assert scheduler.run_with_lock(lambda: None) is True
        _assert_released(scheduler, db, job_name)
