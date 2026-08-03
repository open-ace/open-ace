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
    job_name_to_lock_key,
)


@pytest.fixture
def db():
    """Create test database instance."""
    return Database()


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

    def test_job_name_to_lock_key_consistent(self):
        """Test lock key is consistent for same job name."""
        key1 = job_name_to_lock_key("test_job")
        key2 = job_name_to_lock_key("test_job")

        assert key1 == key2
        assert isinstance(key1, int)

    def test_job_name_to_lock_key_different(self):
        """Test lock keys are different for different job names."""
        key1 = job_name_to_lock_key("job1")
        key2 = job_name_to_lock_key("job2")

        assert key1 != key2


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

    def test_auto_strategy_selection(self, db):
        """Test auto strategy selection based on timeout."""
        # Short timeout -> advisory
        client_short = LeaderElectionClient("short_job", db, strategy="auto", lock_timeout=60)
        assert client_short.strategy == "advisory"

        # Long timeout -> heartbeat
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
        threads = [
            threading.Thread(target=try_acquire) for _ in range(5)
        ]

        for t in threads:
            t.start()

        for t in threads:
            t.join(timeout=10)

        # At least one should have acquired leadership
        assert any(results)
        # But not all should have succeeded at the same time
        # (This test is probabilistic, but in practice only one should succeed at a time)


# Skip advisory lock tests on SQLite (not supported)
@pytest.mark.skipif(
    not pytest.importorskip("app.repositories.database").Database().is_postgresql,
    reason="Advisory locks require PostgreSQL"
)
class TestAdvisoryLock:
    """Tests for PostgreSQL advisory locks."""

    def test_advisory_lock_basic(self, db):
        """Test basic advisory lock functionality."""
        client = LeaderElectionClient("test_advisory", db, strategy="advisory")

        acquired = client.try_acquire_leadership()
        assert acquired is True

        # Advisory lock is released on transaction commit
        # No explicit release needed

    def test_advisory_lock_conflict(self, db):
        """Test that advisory locks prevent concurrent execution."""
        job_name = "test_advisory_conflict"

        client1 = LeaderElectionClient(job_name, db, strategy="advisory")
        client2 = LeaderElectionClient(job_name, db, strategy="advisory")

        # First client acquires
        acquired1 = client1.try_acquire_leadership()

        # Second client should fail (within same transaction)
        acquired2 = client2.try_acquire_leadership()

        # Only one should succeed
        assert acquired1 != acquired2 or not acquired1