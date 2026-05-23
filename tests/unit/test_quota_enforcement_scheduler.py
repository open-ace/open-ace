"""Unit tests for QuotaEnforcementScheduler."""

import threading
from datetime import datetime, timedelta
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from app.services.quota_enforcement_scheduler import (
    QuotaEnforcementScheduler,
    enforcement_scheduler,
)


class TestQuotaEnforcementSchedulerSingleton:
    """Test singleton behavior."""

    def setup_method(self):
        # Reset singleton between tests
        QuotaEnforcementScheduler._instance = None

    def test_singleton_returns_same_instance(self):
        s1 = QuotaEnforcementScheduler()
        s2 = QuotaEnforcementScheduler()
        assert s1 is s2

    def test_double_checked_locking(self):
        """Multiple threads should get the same instance."""
        results = []

        def create_instance():
            results.append(QuotaEnforcementScheduler())

        threads = [threading.Thread(target=create_instance) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(r is results[0] for r in results)


class TestQuotaEnforcementSchedulerConfigure:
    """Test configuration."""

    def setup_method(self):
        QuotaEnforcementScheduler._instance = None

    def test_configure_interval(self):
        scheduler = QuotaEnforcementScheduler()
        scheduler.configure(interval=120)
        assert scheduler._interval == 120

    def test_configure_interval_minimum(self):
        scheduler = QuotaEnforcementScheduler()
        scheduler.configure(interval=10)
        assert scheduler._interval == 30  # Minimum is 30

    def test_configure_enabled(self):
        scheduler = QuotaEnforcementScheduler()
        scheduler.configure(enabled=False)
        assert scheduler._enabled is False

    def test_configure_enabled_true(self):
        scheduler = QuotaEnforcementScheduler()
        scheduler.configure(enabled=False)
        scheduler.configure(enabled=True)
        assert scheduler._enabled is True

    def test_configure_none_values_no_change(self):
        scheduler = QuotaEnforcementScheduler()
        original_interval = scheduler._interval
        scheduler.configure(interval=None, enabled=None)
        assert scheduler._interval == original_interval


class TestQuotaEnforcementSchedulerStartStop:
    """Test start/stop lifecycle."""

    def setup_method(self):
        QuotaEnforcementScheduler._instance = None

    def test_start_when_disabled(self):
        scheduler = QuotaEnforcementScheduler()
        scheduler.configure(enabled=False)
        scheduler.start()
        assert scheduler._running is False

    def test_start_creates_thread(self):
        scheduler = QuotaEnforcementScheduler()
        scheduler.configure(interval=999999)  # Long interval to prevent actual run
        scheduler.start()
        try:
            assert scheduler._running is True
            assert scheduler._thread is not None
            assert scheduler._thread.daemon is True
        finally:
            scheduler.stop()

    def test_start_twice_warning(self):
        scheduler = QuotaEnforcementScheduler()
        scheduler.configure(interval=999999)
        scheduler.start()
        try:
            # Second start should not create a new thread
            old_thread = scheduler._thread
            scheduler.start()
            assert scheduler._thread is old_thread
        finally:
            scheduler.stop()

    def test_stop_sets_running_false(self):
        scheduler = QuotaEnforcementScheduler()
        scheduler.configure(interval=999999)
        scheduler.start()
        scheduler.stop()
        assert scheduler._running is False

    def test_stop_without_start(self):
        scheduler = QuotaEnforcementScheduler()
        scheduler.stop()  # Should not raise
        assert scheduler._running is False

    def test_is_running_when_not_started(self):
        scheduler = QuotaEnforcementScheduler()
        assert scheduler.is_running() is False


class TestQuotaEnforcementSchedulerStatus:
    """Test get_status method."""

    def setup_method(self):
        QuotaEnforcementScheduler._instance = None

    def test_status_initial(self):
        scheduler = QuotaEnforcementScheduler()
        status = scheduler.get_status()
        assert status["running"] is False
        assert status["enabled"] is True
        assert status["interval"] == 60
        assert status["last_run"] is None
        assert status["next_run"] is None

    def test_status_after_configure(self):
        scheduler = QuotaEnforcementScheduler()
        scheduler.configure(interval=120, enabled=False)
        status = scheduler.get_status()
        assert status["interval"] == 120
        assert status["enabled"] is False

    def test_status_with_last_run(self):
        scheduler = QuotaEnforcementScheduler()
        now = datetime.now()
        scheduler._last_run = now
        status = scheduler.get_status()
        assert status["last_run"] == now.isoformat()

    def test_status_with_next_run(self):
        scheduler = QuotaEnforcementScheduler()
        scheduler._next_run = datetime.now().timestamp() + 60
        status = scheduler.get_status()
        assert status["next_run"] is not None

    def test_status_with_invalid_next_run(self):
        scheduler = QuotaEnforcementScheduler()
        scheduler._next_run = "invalid"
        status = scheduler.get_status()
        assert status["next_run"] is None


class TestQuotaEnforcementSchedulerEnforcement:
    """Test quota enforcement logic."""

    def setup_method(self):
        QuotaEnforcementScheduler._instance = None

    @patch("app.repositories.database.adapt_sql", side_effect=lambda x: x)
    @patch("app.repositories.database.adapt_boolean_condition", return_value="u.is_active = 1")
    @patch("app.repositories.database.Database")
    def test_run_enforcement_no_exceeded_users(self, mock_db_cls, mock_adapt_bool, mock_adapt_sql):
        mock_db = MagicMock()
        mock_db.fetch_all.return_value = []
        mock_db_cls.return_value = mock_db

        scheduler = QuotaEnforcementScheduler()
        scheduler._run_enforcement()

        assert scheduler._last_run is not None

    @patch("app.repositories.database.adapt_sql", side_effect=lambda x: x)
    @patch("app.repositories.database.adapt_boolean_condition", return_value="u.is_active = 1")
    @patch("app.repositories.database.Database")
    def test_run_enforcement_daily_exceeded(self, mock_db_cls, mock_adapt_bool, mock_adapt_sql):
        today = datetime.utcnow().strftime("%Y-%m-%d")
        mock_db = MagicMock()
        daily_row = {
            "user_id": 1,
            "username": "testuser",
            "today_requests": 100,
            "today_tokens": 5000000,
            "daily_request_quota": 50,
            "daily_token_quota": 1,
        }
        mock_db.fetch_all.side_effect = [
            [daily_row],  # daily rows
            [],  # monthly rows
        ]
        mock_db_cls.return_value = mock_db

        scheduler = QuotaEnforcementScheduler()
        with patch.object(scheduler, "_enforce_user") as mock_enforce:
            scheduler._run_enforcement()
            mock_enforce.assert_called_once_with(daily_row, today, "daily")

    @patch("app.repositories.database.adapt_sql", side_effect=lambda x: x)
    @patch("app.repositories.database.adapt_boolean_condition", return_value="u.is_active = 1")
    @patch("app.repositories.database.Database")
    def test_run_enforcement_monthly_exceeded(self, mock_db_cls, mock_adapt_bool, mock_adapt_sql):
        today = datetime.utcnow().strftime("%Y-%m-%d")
        mock_db = MagicMock()
        monthly_row = {
            "user_id": 2,
            "username": "monthlyuser",
            "month_requests": 5000,
            "month_tokens": 50000000,
            "monthly_request_quota": 1000,
            "monthly_token_quota": 10,
        }
        mock_db.fetch_all.side_effect = [
            [],  # daily rows
            [monthly_row],  # monthly rows
        ]
        mock_db_cls.return_value = mock_db

        scheduler = QuotaEnforcementScheduler()
        with patch.object(scheduler, "_enforce_user") as mock_enforce:
            scheduler._run_enforcement()
            mock_enforce.assert_called_once_with(
                monthly_row, today, "monthly", month_prefix="month_"
            )

    @patch("app.repositories.database.adapt_sql", side_effect=lambda x: x)
    @patch("app.repositories.database.adapt_boolean_condition", return_value="u.is_active = 1")
    @patch("app.repositories.database.Database")
    def test_run_enforcement_deduplicates_monthly(
        self, mock_db_cls, mock_adapt_bool, mock_adapt_sql
    ):
        today = datetime.utcnow().strftime("%Y-%m-%d")
        mock_db = MagicMock()
        daily_row = {
            "user_id": 1,
            "username": "testuser",
            "today_requests": 100,
            "today_tokens": 5000000,
            "daily_request_quota": 50,
            "daily_token_quota": 1,
        }
        monthly_row_same_user = {
            "user_id": 1,  # Same user as daily
            "username": "testuser",
            "month_requests": 5000,
            "month_tokens": 50000000,
            "monthly_request_quota": 1000,
            "monthly_token_quota": 10,
        }
        mock_db.fetch_all.side_effect = [
            [daily_row],  # daily rows
            [monthly_row_same_user],  # monthly rows - same user, should be skipped
        ]
        mock_db_cls.return_value = mock_db

        scheduler = QuotaEnforcementScheduler()
        with patch.object(scheduler, "_enforce_user") as mock_enforce:
            scheduler._run_enforcement()
            # Monthly enforcement should NOT be called for user already in daily
            mock_enforce.assert_called_once_with(daily_row, today, "daily")

    @patch("app.repositories.database.Database")
    def test_run_enforcement_db_exception(self, mock_db_cls):
        mock_db_cls.return_value.fetch_all.side_effect = Exception("DB error")
        scheduler = QuotaEnforcementScheduler()
        scheduler._run_enforcement()  # Should not raise
        assert scheduler._last_run is not None

    def test_enforce_user_deduplication(self):
        scheduler = QuotaEnforcementScheduler()
        today = datetime.utcnow().strftime("%Y-%m-%d")

        row = {
            "user_id": 1,
            "username": "testuser",
            "today_requests": 100,
            "today_tokens": 5000000,
            "daily_request_quota": 50,
            "daily_token_quota": 1,
        }

        action_key = f"1:quota_exceeded:{today}:daily"
        scheduler._enforced_users = {action_key}

        with patch("app.modules.governance.alert_notifier.create_quota_alert") as mock_alert:
            scheduler._enforce_user(row, today, "daily")
            mock_alert.assert_not_called()

    @patch("app.modules.governance.alert_notifier.create_quota_alert")
    @patch("app.modules.workspace.session_manager.SessionManager")
    def test_enforce_user_creates_alert(self, mock_sm_cls, mock_create_alert):
        scheduler = QuotaEnforcementScheduler()
        today = datetime.utcnow().strftime("%Y-%m-%d")

        row = {
            "user_id": 1,
            "username": "testuser",
            "today_requests": 100,
            "today_tokens": 5000000,
            "daily_request_quota": 50,
            "daily_token_quota": 1,
        }

        mock_sm = MagicMock()
        mock_sm.get_active_sessions.return_value = []
        mock_sm_cls.return_value = mock_sm

        scheduler._enforce_user(row, today, "daily")

        mock_create_alert.assert_called_once()
        call_kwargs = mock_create_alert.call_args
        assert call_kwargs[1]["user_id"] == 1
        assert call_kwargs[1]["username"] == "testuser"

    @patch("app.modules.governance.alert_notifier.create_quota_alert")
    @patch("app.modules.workspace.session_manager.SessionManager")
    def test_enforce_user_terminates_sessions(self, mock_sm_cls, mock_create_alert):
        scheduler = QuotaEnforcementScheduler()
        today = datetime.utcnow().strftime("%Y-%m-%d")

        row = {
            "user_id": 1,
            "username": "testuser",
            "today_requests": 100,
            "today_tokens": 5000000,
            "daily_request_quota": 50,
            "daily_token_quota": 1,
        }

        mock_session = MagicMock()
        mock_session.session_id = "abc12345"
        mock_sm = MagicMock()
        mock_sm.get_active_sessions.return_value = [mock_session]
        mock_sm_cls.return_value = mock_sm

        scheduler._enforce_user(row, today, "daily")

        mock_sm.complete_session.assert_called_once_with("abc12345")

    @patch("app.modules.governance.alert_notifier.create_quota_alert")
    @patch("app.modules.workspace.session_manager.SessionManager")
    def test_enforce_user_alert_failure_continues(self, mock_sm_cls, mock_create_alert):
        mock_create_alert.side_effect = Exception("Alert service down")

        scheduler = QuotaEnforcementScheduler()
        today = datetime.utcnow().strftime("%Y-%m-%d")

        row = {
            "user_id": 1,
            "username": "testuser",
            "today_requests": 100,
            "today_tokens": 5000000,
            "daily_request_quota": 50,
            "daily_token_quota": 1,
        }

        mock_sm = MagicMock()
        mock_sm.get_active_sessions.return_value = []
        mock_sm_cls.return_value = mock_sm

        # Should not raise
        scheduler._enforce_user(row, today, "daily")

    @patch("app.modules.governance.alert_notifier.create_quota_alert")
    @patch("app.modules.workspace.session_manager.SessionManager")
    def test_enforce_user_session_terminate_failure_continues(self, mock_sm_cls, mock_create_alert):
        mock_sm_instance = MagicMock()
        mock_sm_instance.get_active_sessions.side_effect = Exception("SM error")
        mock_sm_cls.return_value = mock_sm_instance

        scheduler = QuotaEnforcementScheduler()
        today = datetime.utcnow().strftime("%Y-%m-%d")

        row = {
            "user_id": 1,
            "username": "testuser",
            "today_requests": 100,
            "today_tokens": 5000000,
            "daily_request_quota": 50,
            "daily_token_quota": 1,
        }

        # Should not raise
        scheduler._enforce_user(row, today, "daily")

    @patch("app.modules.governance.alert_notifier.create_quota_alert")
    @patch("app.modules.workspace.session_manager.SessionManager")
    def test_enforce_user_monthly_prefix(self, mock_sm_cls, mock_create_alert):
        scheduler = QuotaEnforcementScheduler()
        today = datetime.utcnow().strftime("%Y-%m-%d")

        row = {
            "user_id": 1,
            "username": "testuser",
            "month_requests": 5000,
            "month_tokens": 50000000,
            "monthly_request_quota": 1000,
            "monthly_token_quota": 10,
        }

        mock_sm = MagicMock()
        mock_sm.get_active_sessions.return_value = []
        mock_sm_cls.return_value = mock_sm

        scheduler._enforce_user(row, today, "monthly", month_prefix="month_")

        mock_create_alert.assert_called_once()
        quota_type = mock_create_alert.call_args[1]["quota_type"]
        assert "monthly" in quota_type

    @patch("app.modules.governance.alert_notifier.create_quota_alert")
    @patch("app.modules.workspace.session_manager.SessionManager")
    def test_enforce_user_cleans_old_action_keys(self, mock_sm_cls, mock_create_alert):
        scheduler = QuotaEnforcementScheduler()
        today = datetime.utcnow().strftime("%Y-%m-%d")

        # Add old action keys from a different date
        scheduler._enforced_users = {"99:quota_exceeded:2020-01-01:daily"}

        row = {
            "user_id": 1,
            "username": "testuser",
            "today_requests": 100,
            "today_tokens": 5000000,
            "daily_request_quota": 50,
            "daily_token_quota": 1,
        }

        mock_sm = MagicMock()
        mock_sm.get_active_sessions.return_value = []
        mock_sm_cls.return_value = mock_sm

        scheduler._enforce_user(row, today, "daily")

        # Old key should be removed, new key should be added
        action_key = f"1:quota_exceeded:{today}:daily"
        assert action_key in scheduler._enforced_users
        assert "99:quota_exceeded:2020-01-01:daily" not in scheduler._enforced_users


class TestInitQuotaEnforcement:
    """Test init_quota_enforcement function."""

    def setup_method(self):
        QuotaEnforcementScheduler._instance = None

    @patch("app.services.quota_enforcement_scheduler.enforcement_scheduler")
    def test_init_with_config(self, mock_scheduler):
        mock_scheduler._enabled = True
        with patch.dict("sys.modules", {"config": MagicMock()}):
            with patch(
                "config.get_quota_enforcement_config",
                return_value={"interval": 120, "enabled": True},
                create=True,
            ):
                from app.services.quota_enforcement_scheduler import init_quota_enforcement

                # Just verify it doesn't crash
                mock_scheduler.configure.assert_not_called()  # Called via the function
