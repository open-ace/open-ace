"""
Tests for Leader Election Client (Issue #2187)

Unit tests for distributed leader election mechanisms.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

import pytest

from app.repositories.database import Database
from app.services.leader_election import (
    LeaderElectionClient,
    LeaderInfo,
    check_scheduler_tables_exist,
    generate_leader_id,
    get_owner_info,
)


@pytest.fixture
def db():
    """Create test database instance with scheduler tables."""
    database = Database()

    # Create scheduler tables if they don't exist (for unit tests)
    # This mimics the migration but is safe to run multiple times
    conn = database.get_connection()
    cursor = conn.cursor()

    # Create scheduler_leaders table
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

    # Create scheduler_runs table
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

    # Create indexes
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_scheduler_leaders_expires ON scheduler_leaders (expires_at)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_scheduler_leaders_heartbeat ON scheduler_leaders (heartbeat_at)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_scheduler_runs_job_time ON scheduler_runs (job_name, started_at DESC)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_scheduler_runs_status ON scheduler_runs (status)"
    )

    conn.commit()
    conn.close()

    yield database

    # Cleanup: clear tables after each test
    try:
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM scheduler_runs")
        cursor.execute("DELETE FROM scheduler_leaders")
        conn.commit()
        conn.close()
    except Exception:  # allow-swallow: sqlite fixture cleanup (best-effort teardown)
        pass


class TestLeaderElectionHelpers:
    """Tests for helper functions."""

    def test_generate_leader_id_unique(self):
        """Test that leader IDs are unique."""
        id1 = generate_leader_id()
        id2 = generate_leader_id()

        assert id1 != id2
        assert "-" in id1
        assert len(id1) > 10

    def test_get_owner_info(self):
        """Test owner info format."""
        owner = get_owner_info()
        assert ":" in owner
        assert len(owner) > 0


class TestSchedulerTablesExist:
    """Tests for scheduler tables check."""

    def test_tables_exist_after_migration(self, db):
        """Test that scheduler tables exist after migration."""
        assert check_scheduler_tables_exist(db) is True


class TestLeaderElectionClient:
    """Tests for LeaderElectionClient."""

    def test_initialization(self, db):
        """Test client initialization."""
        client = LeaderElectionClient("test_job", db, strategy="heartbeat")

        assert client.job_name == "test_job"
        assert client.strategy == "heartbeat"
        assert client.leader_id
        assert not client.is_leader()

    def test_acquire_heartbeat_leadership(self, db):
        """Test acquiring leadership with heartbeat strategy."""
        client = LeaderElectionClient("test_job_heartbeat", db, strategy="heartbeat")

        # Should acquire leadership
        acquired = client.try_acquire_leadership(timeout=60)
        assert acquired is True
        assert client.is_leader()

        # Clean up
        client.release_leadership()

    def test_multiple_clients_competition(self, db):
        """Test that only one client can acquire leadership."""
        job_name = "test_competition"

        client1 = LeaderElectionClient(job_name, db, strategy="heartbeat", lock_timeout=60)
        client2 = LeaderElectionClient(job_name, db, strategy="heartbeat", lock_timeout=60)

        # First client should acquire
        acquired1 = client1.try_acquire_leadership(timeout=60)

        # Second client should fail
        acquired2 = client2.try_acquire_leadership(timeout=60)

        # Exactly one should succeed
        assert acquired1 != acquired2

        # Clean up
        if acquired1:
            client1.release_leadership()
        else:
            client2.release_leadership()

    def test_release_leadership(self, db):
        """Test releasing leadership."""
        client = LeaderElectionClient("test_release", db, strategy="heartbeat")

        # Acquire
        acquired = client.try_acquire_leadership(timeout=60)
        assert acquired is True
        assert client.is_leader()

        # Release
        client.release_leadership()
        assert not client.is_leader()

        # Another client should now be able to acquire
        client2 = LeaderElectionClient("test_release", db, strategy="heartbeat")
        acquired2 = client2.try_acquire_leadership(timeout=60)
        assert acquired2 is True

        client2.release_leadership()

    def test_get_leader_info(self, db):
        """Test getting leader info."""
        client = LeaderElectionClient("test_info", db, strategy="heartbeat")

        # Before acquiring
        info = client.get_leader_info()
        assert info is None

        # Acquire
        client.try_acquire_leadership(timeout=60)

        # After acquiring
        info = client.get_leader_info()
        assert info is not None
        assert isinstance(info, LeaderInfo)
        assert info.job_name == "test_info"
        assert info.leader_id == client.leader_id

        client.release_leadership()

    def test_record_run_completed(self, db):
        """Test recording a completed run."""
        client = LeaderElectionClient("test_run_completed", db, strategy="heartbeat")
        client.try_acquire_leadership(timeout=60)

        # Record run
        client.record_run("completed", duration_ms=1000)

        # Verify counters
        metrics = client.get_metrics()
        assert metrics["run_count"] == 1

        client.release_leadership()

    def test_record_run_skipped(self, db):
        """Test recording a skipped run."""
        client = LeaderElectionClient("test_run_skipped", db, strategy="heartbeat")

        # Record skip (without acquiring leadership)
        client.record_run("skipped")

        # Verify counters
        metrics = client.get_metrics()
        assert metrics["skip_count"] == 1

    def test_record_run_failed(self, db):
        """Test recording a failed run."""
        client = LeaderElectionClient("test_run_failed", db, strategy="heartbeat")
        client.try_acquire_leadership(timeout=60)

        # Record failure
        client.record_run("failed", error_message="Test error")

        # Verify counters
        metrics = client.get_metrics()
        assert metrics["fail_count"] == 1

        client.release_leadership()

    def test_auto_strategy_always_selects_heartbeat(self, db):
        """auto resolves to heartbeat regardless of timeout (advisory is deprecated)."""
        client_short = LeaderElectionClient("short_job", db, strategy="auto", lock_timeout=60)
        assert client_short.strategy == "heartbeat"

        client_long = LeaderElectionClient("long_job", db, strategy="auto", lock_timeout=3600)
        assert client_long.strategy == "heartbeat"


class TestLeaderElectionConcurrency:
    """Concurrency tests for leader election."""

    def test_concurrent_acquisition(self, db):
        """Test concurrent acquisition attempts."""
        job_name = "test_concurrent"
        results = []
        lock = threading.Lock()

        def try_acquire():
            client = LeaderElectionClient(job_name, db, strategy="heartbeat", lock_timeout=60)
            acquired = client.try_acquire_leadership(timeout=60)
            with lock:
                results.append(acquired)
            if acquired:
                time.sleep(0.1)  # Hold leadership briefly
                client.release_leadership()

        # Start multiple threads
        threads = [threading.Thread(target=try_acquire) for _ in range(5)]

        for t in threads:
            t.start()

        for t in threads:
            t.join(timeout=10)

        # At least one should have acquired leadership
        assert any(results)
        # But not all should have succeeded at the same time
        # (This test is probabilistic, but in practice only one should succeed at a time)


class TestAdvisoryDeprecation:
    """The deprecated advisory strategy transparently uses heartbeat.

    Advisory locks used to be a PostgreSQL-only path; the heartbeat fallback
    runs on every backend, so these tests are no longer skipped on SQLite.
    """

    def test_advisory_falls_back_to_heartbeat(self, db):
        """strategy='advisory' acquires via heartbeat and flips its strategy."""
        client = LeaderElectionClient("test_advisory_fallback", db, strategy="advisory")

        acquired = client.try_acquire_leadership()
        try:
            assert acquired is True
            assert client.is_leader()
            # The advisory request is redirected to the correct heartbeat path.
            assert client.strategy == "heartbeat"
        finally:
            client.release_leadership()

    def test_advisory_provides_real_mutual_exclusion(self, db):
        """Two 'advisory' clients: exactly one wins (real exclusion via heartbeat).

        Regression for the advisory-lock lifetime bug: the old path released its
        transaction-scoped lock before the critical section, so both clients
        could "acquire". The heartbeat fallback gives genuine mutual exclusion.
        """
        job_name = "test_advisory_exclusion"
        client1 = LeaderElectionClient(job_name, db, strategy="advisory", lock_timeout=60)
        client2 = LeaderElectionClient(job_name, db, strategy="advisory", lock_timeout=60)

        acquired1 = client1.try_acquire_leadership()
        acquired2 = client2.try_acquire_leadership()
        try:
            assert acquired1 is True
            assert acquired2 is False
        finally:
            client1.release_leadership()
            client2.release_leadership()


class TestRecordRunPartial:
    """Test record_run with partial status. Issue #2822."""

    def test_record_run_partial(self, db):
        """Partial status should increment run_count (not fail_count)."""
        client = LeaderElectionClient("test_partial", db, strategy="heartbeat", lock_timeout=60)

        try:
            acquired = client.try_acquire_leadership()
            assert acquired is True

            # Get initial metrics
            initial_metrics = client.get_metrics()
            initial_run_count = initial_metrics["run_count"]

            # Record partial run
            client.record_run("partial", 1000, '{"type": "partial_failure"}')

            # Verify run_count incremented
            final_metrics = client.get_metrics()
            assert final_metrics["run_count"] == initial_run_count + 1

        finally:
            client.release_leadership()

    def test_record_run_invalid_status(self, db):
        """Invalid status should not update counters (but still inserts)."""
        client = LeaderElectionClient("test_invalid", db, strategy="heartbeat", lock_timeout=60)

        try:
            acquired = client.try_acquire_leadership()
            assert acquired is True

            # Get initial metrics
            initial_metrics = client.get_metrics()

            # Record with invalid status (no matching elif branch)
            client.record_run("invalid_status", 1000, "test")

            # Verify counters unchanged
            final_metrics = client.get_metrics()
            assert final_metrics["run_count"] == initial_metrics["run_count"]
            assert final_metrics["fail_count"] == initial_metrics["fail_count"]
            assert final_metrics["skip_count"] == initial_metrics["skip_count"]

        finally:
            client.release_leadership()

    def test_record_run_concurrent(self, db):
        """Concurrent record_run calls should correctly update counters."""
        import threading
        import time

        job_name = "test_concurrent_record"
        results = []
        lock = threading.Lock()

        def record_partial():
            client = LeaderElectionClient(job_name, db, strategy="heartbeat", lock_timeout=60)
            try:
                acquired = client.try_acquire_leadership()
                if acquired:
                    for _ in range(5):
                        client.record_run("partial", 100, "test")
                        time.sleep(0.01)
                    with lock:
                        results.append(client.get_metrics()["run_count"])
            finally:
                client.release_leadership()

        # Only one thread will acquire leadership and record
        threads = [threading.Thread(target=record_partial) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # Verify at least one thread recorded runs
        if results:
            # Should have recorded 5 partial runs
            assert any(r >= 5 for r in results)
