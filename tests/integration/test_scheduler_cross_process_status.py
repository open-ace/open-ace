"""Integration tests for scheduler cross-process status synchronization.

Issue #2820: Tests for cross-process scheduler status visibility.
"""

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.services.data_fetch_scheduler import DataFetchScheduler
from app.services.scheduler_status_reader import SchedulerStatusReader


class TestDataFetchSchedulerCrossProcess:
    """Test DataFetchScheduler cross-process status reading."""

    def setup_method(self):
        DataFetchScheduler._instance = None
        SchedulerStatusReader._instance = None

    def teardown_method(self):
        # Reset singleton
        if DataFetchScheduler._instance:
            try:
                scheduler = DataFetchScheduler._instance
                if scheduler._running:
                    scheduler.stop()
            except Exception:
                pass
        DataFetchScheduler._instance = None
        SchedulerStatusReader._instance = None

    def test_get_status_returns_local_when_running(self):
        """Test that get_status returns local status when scheduler is running."""
        scheduler = DataFetchScheduler()
        scheduler.configure(interval=999999, enabled=True)

        # Mock the is_running() method to return True
        with patch.object(scheduler, 'is_running', return_value=True):
            scheduler._running = True  # Set running flag

            status = scheduler.get_status()

            # Should return local status
            assert status["running"] is True
            assert status["enabled"] is True
            assert status["interval"] == 999999
            assert status["cache_hit"] is None  # Not from cache

            scheduler._running = False

    def test_get_status_reads_shared_when_not_running(self):
        """Test that get_status reads from shared storage when not running."""
        scheduler = DataFetchScheduler()
        scheduler.configure(interval=300, enabled=True)
        scheduler._running = False  # Not running locally

        with patch(
            "app.services.scheduler_status_reader.get_scheduler_status"
        ) as mock_shared:
            mock_shared.return_value = {
                "running": True,
                "worker_id": "remote-worker-123",
                "heartbeat": "2026-01-01T00:00:00",
                "heartbeat_age_seconds": 10,
                "last_run": "2026-01-01T00:00:00",
                "next_run": "2026-01-01T00:05:00",
                "health_status": "healthy",
                "cache_age_seconds": 0,
                "cache_hit": False,
                "error": None,
            }

            status = scheduler.get_status()

            # Should read from shared storage
            assert status["running"] is True
            assert status["worker_id"] == "remote-worker-123"
            assert status["health_status"] == "healthy"

            mock_shared.assert_called_once_with("data_fetch", 300)

    def test_get_status_handles_database_error(self):
        """Test that get_status handles database errors gracefully."""
        scheduler = DataFetchScheduler()
        scheduler.configure(interval=300, enabled=True)
        scheduler._running = False

        with patch(
            "app.services.scheduler_status_reader.get_scheduler_status"
        ) as mock_shared:
            mock_shared.side_effect = Exception("Database connection failed")

            status = scheduler.get_status()

            # Should return unknown status
            assert status["running"] == "unknown"
            assert status["error"] == "database_unavailable"
            assert status["health_status"] == "unknown"

    def test_get_status_merges_local_config(self):
        """Test that get_status merges local configuration with shared status."""
        scheduler = DataFetchScheduler()
        scheduler.configure(interval=600, enabled=True)
        scheduler._running = False

        with patch(
            "app.services.scheduler_status_reader.get_scheduler_status"
        ) as mock_shared:
            mock_shared.return_value = {
                "running": True,
                "worker_id": "remote-worker",
                "heartbeat": "2026-01-01T00:00:00",
                "heartbeat_age_seconds": 10,
                "last_run": "2026-01-01T00:00:00",
                "next_run": "2026-01-01T00:05:00",
                "health_status": "healthy",
                "cache_age_seconds": 0,
                "cache_hit": False,
                "error": None,
            }

            status = scheduler.get_status()

            # Should merge local config
            assert status["enabled"] is True
            assert status["interval"] == 600
            # And shared status
            assert status["running"] is True
            assert status["worker_id"] == "remote-worker"

    def test_clear_cache_on_start(self):
        """Test that cache is cleared when scheduler starts."""
        scheduler = DataFetchScheduler()
        scheduler.configure(interval=999999, enabled=True)

        with patch(
            "app.services.scheduler_status_reader.clear_cache"
        ) as mock_clear:
            scheduler._running = False
            scheduler.start()

            # Should clear cache
            mock_clear.assert_called_once_with("data_fetch")

            # Cleanup
            scheduler.stop()

    def test_clear_cache_on_stop(self):
        """Test that cache is cleared when scheduler stops."""
        scheduler = DataFetchScheduler()
        scheduler.configure(interval=999999, enabled=True)
        scheduler._running = True

        with patch(
            "app.services.scheduler_status_reader.clear_cache"
        ) as mock_clear:
            scheduler.stop()

            # Should clear cache
            mock_clear.assert_called_once_with("data_fetch")


class TestSchedulerStatusReaderIntegration:
    """Integration tests for SchedulerStatusReader."""

    def setup_method(self):
        SchedulerStatusReader._instance = None

    def teardown_method(self):
        SchedulerStatusReader._instance = None

    def test_get_status_returns_not_running_when_no_leader(self):
        """Test that get_status returns stopped when no leader exists."""
        reader = SchedulerStatusReader()
        reader._cache = {}

        with patch.object(reader, "_query_database") as mock_query:
            mock_query.return_value = {
                "running": False,
                "worker_id": None,
                "heartbeat": None,
                "heartbeat_age_seconds": None,
                "last_run": None,
                "next_run": None,
                "health_status": "stopped",
                "error": None,
            }

            status = reader.get_status("data_fetch", 300)

            assert status["running"] is False
            assert status["health_status"] == "stopped"
            assert status["worker_id"] is None

    def test_get_status_returns_running_with_valid_leader(self):
        """Test that get_status returns healthy with valid leader."""
        reader = SchedulerStatusReader()
        reader._cache = {}

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        now_str = now.isoformat()

        with patch.object(reader, "_query_database") as mock_query:
            mock_query.return_value = {
                "running": True,
                "worker_id": "test-worker-123",
                "heartbeat": now_str,
                "heartbeat_age_seconds": 10,
                "last_run": now_str,
                "next_run": (now + timedelta(seconds=300)).isoformat(),
                "health_status": "healthy",
                "error": None,
            }

            status = reader.get_status("data_fetch", 300)

            assert status["running"] is True
            assert status["health_status"] == "healthy"
            assert status["worker_id"] == "test-worker-123"
            assert status["heartbeat"] is not None

    def test_get_status_returns_stale_with_old_heartbeat(self):
        """Test that get_status returns stale with old heartbeat."""
        reader = SchedulerStatusReader()
        reader._cache = {}

        old_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            seconds=400
        )
        old_time_str = old_time.isoformat()

        with patch.object(reader, "_query_database") as mock_query:
            mock_query.return_value = {
                "running": True,
                "worker_id": "test-worker-123",
                "heartbeat": old_time_str,
                "heartbeat_age_seconds": 400,
                "last_run": old_time_str,
                "next_run": None,
                "health_status": "stale",
                "error": None,
            }

            status = reader.get_status("data_fetch", 300)

            assert status["running"] is True
            assert status["health_status"] == "stale"
            assert status["heartbeat_age_seconds"] == 400


class TestHealthMonitorIntegration:
    """Integration tests for scheduler health monitor."""

    def setup_method(self):
        from app.services.scheduler_health_monitor import SchedulerHealthMonitor

        SchedulerHealthMonitor._instance = None

    def teardown_method(self):
        from app.services.scheduler_health_monitor import SchedulerHealthMonitor

        SchedulerHealthMonitor._instance = None

    def test_health_status_classification(self):
        """Test that health monitor uses health status classification."""
        from app.services.scheduler_health_monitor import SchedulerHealthMonitor

        monitor = SchedulerHealthMonitor()

        # Test healthy status
        status = {"health_status": "healthy", "running": True}
        assert monitor._get_scheduler_health_status(status) == "healthy"

        # Test stale status
        status = {"health_status": "stale", "running": True}
        assert monitor._get_scheduler_health_status(status) == "stale"

        # Test stopped status
        status = {"health_status": "stopped", "running": False}
        assert monitor._get_scheduler_health_status(status) == "stopped"

        # Test unknown status
        status = {"health_status": "unknown", "running": "unknown"}
        assert monitor._get_scheduler_health_status(status) == "unknown"

    def test_health_monitor_determines_status_without_health_status_field(self):
        """Test that health monitor can determine status without health_status field."""
        from app.services.scheduler_health_monitor import SchedulerHealthMonitor

        monitor = SchedulerHealthMonitor()

        # Test with running and heartbeat_ok
        status = {"running": True, "heartbeat_ok": True}
        assert monitor._get_scheduler_health_status(status) == "healthy"

        # Test with running but heartbeat not ok
        status = {"running": True, "heartbeat_ok": False}
        assert monitor._get_scheduler_health_status(status) == "stale"

        # Test with not running
        status = {"running": False}
        assert monitor._get_scheduler_health_status(status) == "stopped"

        # Test with unknown running
        status = {"running": "unknown"}
        assert monitor._get_scheduler_health_status(status) == "unknown"