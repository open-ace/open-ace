"""Unit tests for SchedulerStatusReader.

Issue #2820: Tests for cross-process scheduler status reading.
"""

import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.services.scheduler_status_reader import (
    SchedulerStatusReader,
    clear_cache,
    get_scheduler_status,
    get_status_reader,
)


class TestSchedulerStatusReaderSingleton:
    """Test singleton behavior."""

    def setup_method(self):
        SchedulerStatusReader._instance = None

    def test_singleton_returns_same_instance(self):
        r1 = SchedulerStatusReader()
        r2 = SchedulerStatusReader()
        assert r1 is r2

    def test_thread_safe_singleton(self):
        results = []

        def create_instance():
            results.append(SchedulerStatusReader())

        threads = [threading.Thread(target=create_instance) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(r is results[0] for r in results)


class TestSchedulerStatusReaderCache:
    """Test caching mechanism."""

    def setup_method(self):
        SchedulerStatusReader._instance = None

    def test_cache_hit_within_ttl(self):
        """Test that cache is used within TTL."""
        reader = SchedulerStatusReader()

        with patch.object(reader, "_query_database") as mock_query:
            mock_query.return_value = {
                "running": True,
                "worker_id": "test-worker",
                "heartbeat": "2026-01-01T00:00:00",
                "heartbeat_age_seconds": 10,
                "last_run": "2026-01-01T00:00:00",
                "next_run": "2026-01-01T00:05:00",
                "health_status": "healthy",
                "error": None,
            }

            # First call
            result1 = reader.get_status("test_job", 300)
            assert result1["cache_hit"] is False
            assert mock_query.call_count == 1

            # Second call within TTL
            result2 = reader.get_status("test_job", 300)
            assert result2["cache_hit"] is True
            assert mock_query.call_count == 1  # No additional query

    def test_cache_miss_after_ttl(self):
        """Test that cache is refreshed after TTL."""
        reader = SchedulerStatusReader()
        reader._cache = {}  # Clear cache

        with patch.object(reader, "_query_database") as mock_query:
            mock_query.return_value = {
                "running": True,
                "worker_id": "test-worker",
                "heartbeat": "2026-01-01T00:00:00",
                "heartbeat_age_seconds": 10,
                "last_run": "2026-01-01T00:00:00",
                "next_run": "2026-01-01T00:05:00",
                "health_status": "healthy",
                "error": None,
            }

            # First call
            result1 = reader.get_status("test_job", 300)
            assert result1["cache_hit"] is False

            # Simulate TTL expiration
            with reader._cache_lock:
                if "test_job" in reader._cache:
                    cached_data, cached_at = reader._cache["test_job"]
                    reader._cache["test_job"] = (cached_data, cached_at - 10)

            # Second call after TTL
            result2 = reader.get_status("test_job", 300)
            assert result2["cache_hit"] is False
            assert mock_query.call_count == 2

    def test_cache_clear(self):
        """Test cache clearing."""
        reader = SchedulerStatusReader()

        with patch.object(reader, "_query_database") as mock_query:
            mock_query.return_value = {
                "running": True,
                "worker_id": "test-worker",
                "heartbeat": "2026-01-01T00:00:00",
                "heartbeat_age_seconds": 10,
                "last_run": "2026-01-01T00:00:00",
                "next_run": "2026-01-01T00:05:00",
                "health_status": "healthy",
                "error": None,
            }

            # First call - cache miss
            reader.get_status("test_job", 300)
            assert mock_query.call_count == 1

            # Clear cache
            reader.clear_cache("test_job")

            # Second call - cache miss again
            reader.get_status("test_job", 300)
            assert mock_query.call_count == 2


class TestSchedulerStatusReaderHealthStatus:
    """Test health status determination."""

    def setup_method(self):
        SchedulerStatusReader._instance = None

    def test_healthy_status(self):
        """Test healthy status when heartbeat is fresh."""
        reader = SchedulerStatusReader()

        # Heartbeat age < threshold_healthy
        health_status = reader._get_health_status(
            heartbeat_age_seconds=10,
            is_expired=False,
            threshold_healthy=360,
            threshold_stopped=720,
        )
        assert health_status == "healthy"

    def test_stale_status(self):
        """Test stale status when heartbeat is old but not too old."""
        reader = SchedulerStatusReader()

        # Heartbeat age >= threshold_healthy but < threshold_stopped
        health_status = reader._get_health_status(
            heartbeat_age_seconds=500,
            is_expired=False,
            threshold_healthy=360,
            threshold_stopped=720,
        )
        assert health_status == "stale"

    def test_stopped_status_expired(self):
        """Test stopped status when leadership is expired."""
        reader = SchedulerStatusReader()

        # Leadership expired
        health_status = reader._get_health_status(
            heartbeat_age_seconds=10,
            is_expired=True,
            threshold_healthy=360,
            threshold_stopped=720,
        )
        assert health_status == "stopped"

    def test_stopped_status_heartbeat_too_old(self):
        """Test stopped status when heartbeat is too old."""
        reader = SchedulerStatusReader()

        # Heartbeat age >= threshold_stopped
        health_status = reader._get_health_status(
            heartbeat_age_seconds=800,
            is_expired=False,
            threshold_healthy=360,
            threshold_stopped=720,
        )
        assert health_status == "stopped"

    def test_stopped_status_no_heartbeat(self):
        """Test stopped status when no heartbeat."""
        reader = SchedulerStatusReader()

        # No heartbeat
        health_status = reader._get_health_status(
            heartbeat_age_seconds=None,
            is_expired=False,
            threshold_healthy=360,
            threshold_stopped=720,
        )
        assert health_status == "stopped"


class TestSchedulerStatusReaderNextRun:
    """Test next_run calculation."""

    def setup_method(self):
        SchedulerStatusReader._instance = None

    def test_next_run_when_running(self):
        """Test next_run calculation when scheduler is running."""
        reader = SchedulerStatusReader()

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        last_run = now - timedelta(minutes=5)

        next_run = reader._calculate_next_run(
            last_run_at=last_run,
            heartbeat_at=now,
            running=True,
            interval_seconds=300,
        )

        # Should be now + interval
        assert next_run is not None
        expected = now + timedelta(seconds=300)
        # Allow 1 second tolerance
        assert abs((next_run - expected).total_seconds()) < 1

    def test_next_run_when_idle(self):
        """Test next_run calculation when scheduler is idle."""
        reader = SchedulerStatusReader()

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        last_run = now - timedelta(minutes=2)

        next_run = reader._calculate_next_run(
            last_run_at=last_run,
            heartbeat_at=None,
            running=False,
            interval_seconds=300,
        )

        # Should be last_run + interval
        assert next_run is not None
        expected = last_run + timedelta(seconds=300)
        assert abs((next_run - expected).total_seconds()) < 1

    def test_next_run_no_history(self):
        """Test next_run when no run history."""
        reader = SchedulerStatusReader()

        next_run = reader._calculate_next_run(
            last_run_at=None,
            heartbeat_at=None,
            running=False,
            interval_seconds=300,
        )

        # Should be None
        assert next_run is None


class TestSchedulerStatusReaderDegradation:
    """Test database failure degradation."""

    def setup_method(self):
        SchedulerStatusReader._instance = None

    def test_database_error_returns_unknown(self):
        """Test that database error returns unknown status."""
        reader = SchedulerStatusReader()
        reader._cache = {}  # Clear cache

        with patch.object(reader, "_query_database") as mock_query:
            mock_query.side_effect = Exception("Database connection failed")

            result = reader.get_status("test_job", 300)

            assert result["running"] == "unknown"
            assert result["error"] == "database_unavailable"
            assert result["health_status"] == "unknown"


class TestConvenienceFunctions:
    """Test convenience functions."""

    def test_get_status_reader(self):
        """Test get_status_reader returns singleton."""
        SchedulerStatusReader._instance = None

        r1 = get_status_reader()
        r2 = get_status_reader()
        assert r1 is r2

    def test_get_scheduler_status(self):
        """Test get_scheduler_status convenience function."""
        SchedulerStatusReader._instance = None

        with patch.object(SchedulerStatusReader, "get_status") as mock_get:
            mock_get.return_value = {"running": True}

            result = get_scheduler_status("test", 300)
            assert result["running"] is True
            mock_get.assert_called_once_with("test", 300)

    def test_clear_cache(self):
        """Test clear_cache convenience function."""
        SchedulerStatusReader._instance = None

        with patch.object(SchedulerStatusReader, "clear_cache") as mock_clear:
            clear_cache("test_job")
            mock_clear.assert_called_once_with("test_job")


class TestSchedulerStatusReaderRunsFallback:
    """Test scheduler_runs fallback when no leader row exists.

    Issue #3146: After release_leadership() deletes the scheduler_leaders
    row, the status reader should fall back to scheduler_runs for
    durable run history instead of reporting "stopped".
    """

    def setup_method(self):
        SchedulerStatusReader._instance = None

    def test_fallback_to_runs_returns_idle(self):
        """When leader row absent but recent run exists, return idle."""
        reader = SchedulerStatusReader()
        reader._cache = {}

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        recent_run = now - timedelta(seconds=30)

        mock_db = MagicMock()
        mock_db.fetch_one.side_effect = [
            None,  # scheduler_leaders query returns no row
            {  # scheduler_runs fallback query
                "leader_id": "worker-abc-123",
                "started_at": recent_run,
                "ended_at": recent_run,
                "status": "completed",
            },
        ]

        with patch("app.repositories.database.Database", return_value=mock_db):
            result = reader.get_status("data_fetch", 300)

        assert result["health_status"] == "idle"
        assert result["running"] is True
        assert result["worker_id"] == "worker-abc-123"
        assert result["last_run"] is not None
        assert result["next_run"] is not None
        assert result["heartbeat"] is None
        assert result["error"] is None

    def test_fallback_to_runs_returns_stale(self):
        """When last run is old but not too old, return stale."""
        reader = SchedulerStatusReader()
        reader._cache = {}

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        # threshold_healthy for interval=300 is 360, threshold_stopped is 720
        old_run = now - timedelta(seconds=500)

        mock_db = MagicMock()
        mock_db.fetch_one.side_effect = [
            None,  # scheduler_leaders query returns no row
            {  # scheduler_runs fallback query
                "leader_id": "worker-abc-123",
                "started_at": old_run,
                "ended_at": old_run,
                "status": "completed",
            },
        ]

        with patch("app.repositories.database.Database", return_value=mock_db):
            result = reader.get_status("data_fetch", 300)

        assert result["health_status"] == "stale"
        assert result["running"] is True
        assert result["worker_id"] == "worker-abc-123"

    def test_fallback_to_runs_returns_stopped_when_old(self):
        """When last run is very old, return stopped."""
        reader = SchedulerStatusReader()
        reader._cache = {}

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        # threshold_stopped for interval=300 is 720
        very_old_run = now - timedelta(seconds=800)

        mock_db = MagicMock()
        mock_db.fetch_one.side_effect = [
            None,  # scheduler_leaders query returns no row
            {  # scheduler_runs fallback query
                "leader_id": "worker-abc-123",
                "started_at": very_old_run,
                "ended_at": very_old_run,
                "status": "completed",
            },
        ]

        with patch("app.repositories.database.Database", return_value=mock_db):
            result = reader.get_status("data_fetch", 300)

        assert result["health_status"] == "stopped"
        assert result["running"] is False
        assert result["worker_id"] == "worker-abc-123"

    def test_fallback_no_runs_returns_stopped(self):
        """When no leader row and no run history, return stopped."""
        reader = SchedulerStatusReader()
        reader._cache = {}

        mock_db = MagicMock()
        mock_db.fetch_one.side_effect = [
            None,  # scheduler_leaders query returns no row
            None,  # scheduler_runs fallback query also returns nothing
        ]

        with patch("app.repositories.database.Database", return_value=mock_db):
            result = reader.get_status("data_fetch", 300)

        assert result["health_status"] == "stopped"
        assert result["running"] is False
        assert result["worker_id"] is None
        assert result["last_run"] is None
        assert result["next_run"] is None

    def test_fallback_next_run_calculated_from_last_run(self):
        """next_run should be last_run + interval."""
        reader = SchedulerStatusReader()
        reader._cache = {}

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        recent_run = now - timedelta(seconds=60)

        mock_db = MagicMock()
        mock_db.fetch_one.side_effect = [
            None,  # scheduler_leaders query returns no row
            {  # scheduler_runs fallback query
                "leader_id": "worker-abc-123",
                "started_at": recent_run,
                "ended_at": recent_run,
                "status": "completed",
            },
        ]

        with patch("app.repositories.database.Database", return_value=mock_db):
            result = reader.get_status("data_fetch", 300)

        assert result["next_run"] is not None
        next_run_dt = datetime.fromisoformat(result["next_run"])
        expected = recent_run + timedelta(seconds=300)
        assert abs((next_run_dt - expected).total_seconds()) < 1

    def test_fallback_uses_ended_at_when_available(self):
        """Fallback should prefer ended_at over started_at for last_run."""
        reader = SchedulerStatusReader()
        reader._cache = {}

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        started = now - timedelta(seconds=120)
        ended = now - timedelta(seconds=30)

        mock_db = MagicMock()
        mock_db.fetch_one.side_effect = [
            None,  # scheduler_leaders query returns no row
            {  # scheduler_runs fallback query
                "leader_id": "worker-abc-123",
                "started_at": started,
                "ended_at": ended,
                "status": "completed",
            },
        ]

        with patch("app.repositories.database.Database", return_value=mock_db):
            result = reader.get_status("data_fetch", 300)

        # last_run should be ended_at, not started_at
        last_run_dt = datetime.fromisoformat(result["last_run"])
        assert abs((last_run_dt - ended).total_seconds()) < 1

    def test_leader_row_takes_precedence_over_runs(self):
        """When leader row exists, scheduler_runs is NOT queried."""
        reader = SchedulerStatusReader()
        reader._cache = {}

        now = datetime.now(timezone.utc).replace(tzinfo=None)

        mock_db = MagicMock()
        mock_db.fetch_one.return_value = {
            "job_name": "data_fetch",
            "leader_id": "active-worker",
            "owner_info": "host:123",
            "acquired_at": now,
            "expires_at": now + timedelta(seconds=1800),
            "heartbeat_at": now,
            "last_run_at": now,
            "run_count": 5,
            "skip_count": 0,
            "fail_count": 0,
        }

        with patch("app.repositories.database.Database", return_value=mock_db):
            result = reader.get_status("data_fetch", 300)

        # Should return from leader row, not runs fallback
        assert result["health_status"] == "healthy"
        assert result["worker_id"] == "active-worker"
        # fetch_one should be called only once (for scheduler_leaders)
        assert mock_db.fetch_one.call_count == 1

    def test_fallback_heartbeat_age_is_last_run_age(self):
        """heartbeat_age_seconds should reflect last run age in fallback."""
        reader = SchedulerStatusReader()
        reader._cache = {}

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        recent_run = now - timedelta(seconds=45)

        mock_db = MagicMock()
        mock_db.fetch_one.side_effect = [
            None,
            {
                "leader_id": "worker-abc-123",
                "started_at": recent_run,
                "ended_at": recent_run,
                "status": "completed",
            },
        ]

        with patch("app.repositories.database.Database", return_value=mock_db):
            result = reader.get_status("data_fetch", 300)

        assert result["heartbeat_age_seconds"] is not None
        # Should be approximately 45 seconds
        assert 40 < result["heartbeat_age_seconds"] < 55
