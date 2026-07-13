"""Unit tests for DataFetchScheduler."""

import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.services.data_fetch_scheduler import DataFetchScheduler, scheduler


class TestDataFetchSchedulerSingleton:
    """Test singleton behavior."""

    def setup_method(self):
        DataFetchScheduler._instance = None

    def test_singleton_returns_same_instance(self):
        s1 = DataFetchScheduler()
        s2 = DataFetchScheduler()
        assert s1 is s2

    def test_thread_safe_singleton(self):
        results = []

        def create_instance():
            results.append(DataFetchScheduler())

        threads = [threading.Thread(target=create_instance) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(r is results[0] for r in results)


class TestDataFetchSchedulerConfigure:
    """Test configuration."""

    def setup_method(self):
        DataFetchScheduler._instance = None

    def test_configure_interval(self):
        s = DataFetchScheduler()
        s.configure(interval=600)
        assert s._interval == 600

    def test_configure_interval_minimum(self):
        s = DataFetchScheduler()
        s.configure(interval=30)
        assert s._interval == 60  # Minimum is 60

    def test_configure_enabled(self):
        s = DataFetchScheduler()
        s.configure(enabled=False)
        assert s._enabled is False

    def test_configure_none_values(self):
        s = DataFetchScheduler()
        original = s._interval
        s.configure(interval=None, enabled=None)
        assert s._interval == original

    def test_default_interval(self):
        s = DataFetchScheduler()
        assert s._interval == 300

    def test_default_enabled(self):
        s = DataFetchScheduler()
        assert s._enabled is True


class TestDataFetchSchedulerStartStop:
    """Test start/stop lifecycle."""

    def setup_method(self):
        DataFetchScheduler._instance = None

    def test_start_when_disabled(self):
        s = DataFetchScheduler()
        s.configure(enabled=False)
        s.start()
        assert s._running is False

    def test_start_creates_daemon_thread(self):
        s = DataFetchScheduler()
        s.configure(interval=999999)
        s.start()
        try:
            assert s._running is True
            assert s._thread is not None
            assert s._thread.daemon is True
        finally:
            s.stop()

    def test_start_twice_no_new_thread(self):
        s = DataFetchScheduler()
        s.configure(interval=999999)
        s.start()
        try:
            old_thread = s._thread
            s.start()  # Should warn and not create new thread
            assert s._thread is old_thread
        finally:
            s.stop()

    def test_stop_sets_running_false(self):
        s = DataFetchScheduler()
        s.configure(interval=999999)
        s.start()
        s.stop()
        assert s._running is False

    def test_stop_without_start(self):
        s = DataFetchScheduler()
        s.stop()  # Should not raise
        assert s._running is False

    def test_is_running_when_started(self):
        s = DataFetchScheduler()
        s.configure(interval=999999)
        s.start()
        try:
            assert s.is_running() is True
        finally:
            s.stop()

    def test_is_running_when_stopped(self):
        s = DataFetchScheduler()
        s.configure(interval=999999)
        s.start()
        s.stop()
        assert s.is_running() is False

    def test_is_running_never_started(self):
        s = DataFetchScheduler()
        assert s.is_running() is False


class TestDataFetchSchedulerStatus:
    """Test get_status method."""

    def setup_method(self):
        DataFetchScheduler._instance = None

    def test_status_initial(self):
        s = DataFetchScheduler()
        status = s.get_status()
        assert status["running"] is False
        assert status["enabled"] is True
        assert status["interval"] == 300
        assert status["last_run"] is None
        assert status["next_run"] is None

    def test_status_after_configure(self):
        s = DataFetchScheduler()
        s.configure(interval=600, enabled=False)
        status = s.get_status()
        assert status["interval"] == 600
        assert status["enabled"] is False

    def test_status_with_last_run(self):
        s = DataFetchScheduler()
        now = datetime.now()
        s._last_run = now
        status = s.get_status()
        assert status["last_run"] == now.isoformat()

    def test_status_with_next_run(self):
        s = DataFetchScheduler()
        s._next_run = datetime.now().timestamp() + 300
        status = s.get_status()
        assert status["next_run"] is not None

    def test_status_with_invalid_next_run(self):
        s = DataFetchScheduler()
        s._next_run = "not_a_number"
        status = s.get_status()
        assert status["next_run"] is None


class TestDataFetchSchedulerRunFetch:
    """Test _run_fetch method."""

    def setup_method(self):
        DataFetchScheduler._instance = None

    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._check_quotas")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._refresh_usage_summary")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._aggregate_user_stats")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._refresh_materialized_views")
    @patch("app.routes.fetch.run_fetch_scripts")
    def test_run_fetch_success(self, mock_fetch, mock_mv, mock_agg, mock_summary, mock_quotas):
        s = DataFetchScheduler()
        s._run_fetch()
        mock_fetch.assert_called_once()
        assert s._last_run is not None

    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._check_quotas")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._refresh_usage_summary")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._aggregate_user_stats")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._refresh_materialized_views")
    @patch("app.routes.fetch.run_fetch_scripts")
    def test_run_fetch_error_continues(
        self, mock_fetch, mock_mv, mock_agg, mock_summary, mock_quotas
    ):
        mock_fetch.side_effect = Exception("Fetch error")
        s = DataFetchScheduler()
        s._run_fetch()
        # Should still call other steps
        mock_mv.assert_called_once()
        mock_agg.assert_called_once()

    @patch("app.repositories.database.is_postgresql", return_value=False)
    def test_refresh_materialized_views_skips_non_postgres(self, mock_pg):
        s = DataFetchScheduler()
        s._refresh_materialized_views()
        # Should return early without doing anything

    @patch("app.repositories.database.is_postgresql", return_value=True)
    @patch("app.repositories.database.Database")
    def test_refresh_materialized_views_postgres(self, mock_db_cls, mock_pg):
        mock_db = MagicMock()
        mock_db.fetch_one.return_value = {"exists": True}
        mock_db_cls.return_value = mock_db

        s = DataFetchScheduler()
        s._refresh_materialized_views()
        mock_db.execute.assert_called_once_with("REFRESH MATERIALIZED VIEW session_stats")

    @patch("app.repositories.database.is_postgresql", return_value=True)
    @patch("app.repositories.database.Database")
    def test_refresh_materialized_views_no_mv(self, mock_db_cls, mock_pg):
        mock_db = MagicMock()
        mock_db.fetch_one.return_value = {"exists": False}
        mock_db_cls.return_value = mock_db

        s = DataFetchScheduler()
        s._refresh_materialized_views()
        # Should not try to refresh if MV doesn't exist
        mock_db.execute.assert_not_called()

    @patch("app.repositories.database.is_postgresql", return_value=True)
    @patch("app.repositories.database.Database")
    def test_refresh_materialized_views_error(self, mock_db_cls, mock_pg):
        mock_db = MagicMock()
        mock_db.fetch_one.side_effect = Exception("MV error")
        mock_db_cls.return_value = mock_db

        s = DataFetchScheduler()
        s._refresh_materialized_views()  # Should not raise

    @patch("app.services.user_stats_aggregator.aggregate_user_stats_background")
    def test_aggregate_user_stats_success(self, mock_agg):
        s = DataFetchScheduler()
        s._aggregate_user_stats()
        mock_agg.assert_called_once()

    @patch("app.services.user_stats_aggregator.aggregate_user_stats_background")
    def test_aggregate_user_stats_error(self, mock_agg):
        mock_agg.side_effect = Exception("Agg error")
        s = DataFetchScheduler()
        s._aggregate_user_stats()  # Should not raise

    @patch("app.services.summary_service.SummaryService")
    def test_refresh_usage_summary_success(self, mock_svc_cls):
        mock_svc = MagicMock()
        mock_svc.refresh_summary.return_value = True
        mock_svc_cls.return_value = mock_svc

        s = DataFetchScheduler()
        s._refresh_usage_summary()
        mock_svc.refresh_summary.assert_called_once()

    @patch("app.services.summary_service.SummaryService")
    def test_refresh_usage_summary_error(self, mock_svc_cls):
        mock_svc_cls.side_effect = Exception("Summary error")
        s = DataFetchScheduler()
        s._refresh_usage_summary()  # Should not raise


class TestDataFetchSchedulerCheckQuotas:
    """Test _check_quotas method."""

    def setup_method(self):
        DataFetchScheduler._instance = None

    @patch("app.repositories.database.adapt_sql", side_effect=lambda x: x)
    @patch("app.repositories.database.adapt_boolean_condition", return_value="u.is_active = 1")
    @patch("app.repositories.database.Database")
    def test_check_quotas_no_exceeded(self, mock_db_cls, mock_adapt_bool, mock_adapt_sql):
        mock_db = MagicMock()
        mock_db.fetch_all.return_value = []
        mock_db_cls.return_value = mock_db

        s = DataFetchScheduler()
        s._check_quotas()
        assert mock_db.fetch_all.call_count == 2  # daily + monthly

    @patch("app.repositories.database.adapt_sql", side_effect=lambda x: x)
    @patch("app.repositories.database.adapt_boolean_condition", return_value="u.is_active = 1")
    @patch("app.repositories.database.Database")
    def test_check_quotas_daily_exceeded(self, mock_db_cls, mock_adapt_bool, mock_adapt_sql):
        today = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
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
            [daily_row],  # daily
            [],  # monthly
        ]
        mock_db_cls.return_value = mock_db

        s = DataFetchScheduler()
        with patch.object(s, "_enforce_user_quota") as mock_enforce:
            s._check_quotas()
            mock_enforce.assert_called_once_with(daily_row, today, "daily")

    @patch("app.repositories.database.adapt_sql", side_effect=lambda x: x)
    @patch("app.repositories.database.adapt_boolean_condition", return_value="u.is_active = 1")
    @patch("app.repositories.database.Database")
    def test_check_quotas_error(self, mock_db_cls, mock_adapt_bool, mock_adapt_sql):
        mock_db = MagicMock()
        mock_db.fetch_all.side_effect = Exception("DB error")
        mock_db_cls.return_value = mock_db

        s = DataFetchScheduler()
        s._check_quotas()  # Should not raise


class TestDataFetchSchedulerEnforceUserQuota:
    """Test _enforce_user_quota method."""

    def setup_method(self):
        DataFetchScheduler._instance = None

    @patch("app.modules.governance.alert_transaction_manager.create_quota_alert_transactional")
    @patch("app.modules.workspace.session_manager.SessionManager")
    def test_enforce_daily_quota(self, mock_sm_cls, mock_alert):
        s = DataFetchScheduler()
        today = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")

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

        s._enforce_user_quota(row, today, "daily")
        mock_alert.assert_called_once()

    @patch("app.modules.governance.alert_transaction_manager.create_quota_alert_transactional")
    @patch("app.modules.workspace.session_manager.SessionManager")
    def test_enforce_monthly_quota(self, mock_sm_cls, mock_alert):
        s = DataFetchScheduler()
        today = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")

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

        s._enforce_user_quota(row, today, "monthly", month_prefix="month_")
        quota_type = mock_alert.call_args[1]["quota_type"]
        assert "monthly" in quota_type

    def test_enforce_deduplication(self):
        s = DataFetchScheduler()
        today = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
        action_key = f"1:quota_exceeded:{today}:daily"
        s._enforced_users = {action_key}

        row = {
            "user_id": 1,
            "username": "testuser",
            "today_requests": 100,
            "today_tokens": 5000000,
            "daily_request_quota": 50,
            "daily_token_quota": 1,
        }

        with patch("app.modules.governance.alert_transaction_manager.create_quota_alert_transactional") as mock_alert:
            s._enforce_user_quota(row, today, "daily")
            mock_alert.assert_not_called()

    @patch("app.modules.governance.alert_transaction_manager.create_quota_alert_transactional")
    @patch("app.modules.workspace.session_manager.SessionManager")
    def test_enforce_terminates_sessions(self, mock_sm_cls, mock_alert):
        s = DataFetchScheduler()
        today = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")

        row = {
            "user_id": 1,
            "username": "testuser",
            "today_requests": 100,
            "today_tokens": 5000000,
            "daily_request_quota": 50,
            "daily_token_quota": 1,
        }

        mock_session = MagicMock()
        mock_session.session_id = "session123"
        mock_sm = MagicMock()
        mock_sm.get_active_sessions.return_value = [mock_session]
        mock_sm_cls.return_value = mock_sm

        s._enforce_user_quota(row, today, "daily")
        mock_sm.complete_session.assert_called_once_with("session123")

    @patch("app.modules.governance.alert_transaction_manager.create_quota_alert_transactional")
    @patch("app.modules.workspace.session_manager.SessionManager")
    def test_enforce_session_failure_continues(self, mock_sm_cls, mock_alert):
        s = DataFetchScheduler()
        today = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")

        row = {
            "user_id": 1,
            "username": "testuser",
            "today_requests": 100,
            "today_tokens": 5000000,
            "daily_request_quota": 50,
            "daily_token_quota": 1,
        }

        mock_sm = MagicMock()
        mock_sm.get_active_sessions.side_effect = Exception("SM error")
        mock_sm_cls.return_value = mock_sm

        # Should not raise
        s._enforce_user_quota(row, today, "daily")

    @patch("app.modules.governance.alert_transaction_manager.create_quota_alert_transactional")
    @patch("app.modules.workspace.session_manager.SessionManager")
    def test_enforce_cleans_old_action_keys(self, mock_sm_cls, mock_alert):
        s = DataFetchScheduler()
        today = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")

        s._enforced_users = {"99:quota_exceeded:2020-01-01:daily"}

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

        s._enforce_user_quota(row, today, "daily")

        action_key = f"1:quota_exceeded:{today}:daily"
        assert action_key in s._enforced_users
        assert "99:quota_exceeded:2020-01-01:daily" not in s._enforced_users

    @patch("app.modules.governance.alert_transaction_manager.create_quota_alert_transactional")
    @patch("app.modules.workspace.session_manager.SessionManager")
    def test_enforce_initializes_enforced_users(self, mock_sm_cls, mock_alert):
        s = DataFetchScheduler()
        today = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")

        # Remove _enforced_users attribute to test initialization
        if hasattr(s, "_enforced_users"):
            del s._enforced_users

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

        s._enforce_user_quota(row, today, "daily")
        assert hasattr(s, "_enforced_users")


class TestGlobalSchedulerInstance:
    """Test the global scheduler instance."""

    def test_global_instance_exists(self):
        assert scheduler is not None
        assert isinstance(scheduler, DataFetchScheduler)
