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

    @patch("app.services.leader_election.LeaderElectionClient")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._check_quotas")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._refresh_usage_summary")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._aggregate_user_stats")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._refresh_materialized_views")
    @patch("app.routes.fetch.run_fetch_scripts")
    def test_run_fetch_success(
        self, mock_fetch, mock_mv, mock_agg, mock_summary, mock_quotas, mock_leader_client
    ):
        # Mock leader election to always succeed
        mock_client_instance = mock_leader_client.return_value
        mock_client_instance.try_acquire_leadership.return_value = True
        mock_fetch.return_value = {"qwen": {"success": True}}

        s = DataFetchScheduler()
        s._run_fetch()
        mock_fetch.assert_called_once()
        assert s._last_run is not None
        assert s._last_result_summary is not None
        assert s._last_result_summary["status"] == "completed"

    @patch("app.services.leader_election.LeaderElectionClient")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._check_quotas")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._refresh_usage_summary")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._aggregate_user_stats")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._refresh_materialized_views")
    @patch("app.routes.fetch.run_fetch_scripts")
    def test_run_fetch_error_continues(
        self,
        mock_fetch,
        mock_mv,
        mock_agg,
        mock_summary,
        mock_quotas,
        mock_leader_client,
    ):
        # Mock leader election to always succeed
        mock_client_instance = mock_leader_client.return_value
        mock_client_instance.try_acquire_leadership.return_value = True

        mock_fetch.side_effect = Exception("Fetch error")
        s = DataFetchScheduler()
        s._run_fetch()
        # Should still call other steps
        mock_mv.assert_called_once()
        mock_agg.assert_called_once()

    @patch("app.repositories.database.is_postgresql", return_value=False)
    @patch("app.repositories.database.Database")
    def test_refresh_materialized_views_skips_non_postgres(self, mock_db_cls, mock_pg):
        s = DataFetchScheduler()
        s._refresh_materialized_views()
        # Non-postgres: should return early without touching the DB at all
        mock_db_cls.assert_not_called()

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
        # Error during existence check must abort before attempting REFRESH
        mock_db.execute.assert_not_called()

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
        # Aggregation was attempted (and its failure swallowed)
        mock_agg.assert_called_once()

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
        # SummaryService instantiation was attempted (and its failure swallowed)
        mock_svc_cls.assert_called_once()

    @patch("app.repositories.daily_stats_repo.DailyStatsRepository")
    def test_refresh_daily_stats_success(self, mock_repo_cls):
        mock_repo = MagicMock()
        mock_repo.refresh_stats.return_value = True
        mock_repo_cls.return_value = mock_repo

        s = DataFetchScheduler()
        s._refresh_daily_stats()
        mock_repo.refresh_stats.assert_called_once()

    @patch("app.repositories.daily_stats_repo.DailyStatsRepository")
    def test_refresh_daily_stats_error(self, mock_repo_cls):
        mock_repo_cls.side_effect = Exception("Stats refresh error")
        s = DataFetchScheduler()
        s._refresh_daily_stats()  # Should not raise
        # Repository instantiation was attempted (and its failure swallowed)
        mock_repo_cls.assert_called_once()

    # ---- Issue #2375: fetch result propagation tests ----

    def _make_run_fetch_mocks(self, mock_fetch_return_value):
        """Helper to set up mocks for _run_fetch() tests.
        
        Mocks leader election (succeeds), run_fetch_scripts (returns given value),
        and all post-fetch cleanup steps.
        """
        mocks = {
            "leader_client": MagicMock(),
            "fetch": MagicMock(),
            "mv": MagicMock(),
            "agg": MagicMock(),
            "summary": MagicMock(),
            "quotas": MagicMock(),
            "daily_stats": MagicMock(),
            "feishu": MagicMock(),
            "dingtalk": MagicMock(),
        }
        mocks["leader_client"].return_value.try_acquire_leadership.return_value = True
        mocks["fetch"].return_value = mock_fetch_return_value
        return mocks

    @patch("app.repositories.daily_stats_repo.DailyStatsRepository")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._maybe_sync_dingtalk_org")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._maybe_sync_feishu_org")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._check_quotas")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._refresh_usage_summary")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._refresh_daily_stats")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._aggregate_user_stats")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._refresh_materialized_views")
    @patch("app.routes.fetch.run_fetch_scripts")
    @patch("app.services.leader_election.LeaderElectionClient")
    def test_run_fetch_all_failed(
        self, mock_leader, mock_fetch, mock_mv, mock_agg, mock_ds,
        mock_summary, mock_quotas, mock_feishu, mock_dingtalk, mock_ds_repo,
    ):
        """When all scripts fail, status should be 'failed'."""
        mock_leader.return_value.try_acquire_leadership.return_value = True
        mock_fetch.return_value = {
            "qwen": {"success": False, "error": "sudo: password required"},
            "claude": {"success": False, "error": "sudo: password required"},
            "openclaw": {"success": False, "error": "sudo: password required"},
            "codex": {"success": False, "error": "sudo: password required"},
            "zcode": {"success": False, "error": "sudo: password required"},
        }

        s = DataFetchScheduler()
        s._run_fetch()

        mock_leader.return_value.record_run.assert_called_once()
        call_args = mock_leader.return_value.record_run.call_args[0]
        assert call_args[0] == "failed"  # status
        assert "All fetch scripts failed" in call_args[2]  # error_message
        assert s._last_result_summary["status"] == "failed"
        assert s._last_result_summary["tools_failed"] == 5

    @patch("app.repositories.daily_stats_repo.DailyStatsRepository")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._maybe_sync_dingtalk_org")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._maybe_sync_feishu_org")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._check_quotas")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._refresh_usage_summary")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._refresh_daily_stats")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._aggregate_user_stats")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._refresh_materialized_views")
    @patch("app.routes.fetch.run_fetch_scripts")
    @patch("app.services.leader_election.LeaderElectionClient")
    def test_run_fetch_partial_failure(
        self, mock_leader, mock_fetch, mock_mv, mock_agg, mock_ds,
        mock_summary, mock_quotas, mock_feishu, mock_dingtalk, mock_ds_repo,
    ):
        """When some scripts fail, status=completed with warning."""
        mock_leader.return_value.try_acquire_leadership.return_value = True
        mock_fetch.return_value = {
            "qwen": {"success": True},
            "claude": {"success": True},
            "openclaw": {"success": True},
            "codex": {"success": False, "error": "timeout"},
            "zcode": {"success": False, "error": "disk full"},
        }

        s = DataFetchScheduler()
        s._run_fetch()

        mock_leader.return_value.record_run.assert_called_once()
        call_args = mock_leader.return_value.record_run.call_args[0]
        assert call_args[0] == "completed"  # partial failure is still "completed"
        assert "Partial failure" in call_args[2]
        assert "codex" in call_args[2]
        assert "zcode" in call_args[2]
        assert s._last_result_summary["status"] == "partial"
        assert s._last_result_summary["tools_failed"] == 2

    @patch("app.repositories.daily_stats_repo.DailyStatsRepository")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._maybe_sync_dingtalk_org")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._maybe_sync_feishu_org")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._check_quotas")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._refresh_usage_summary")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._refresh_daily_stats")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._aggregate_user_stats")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._refresh_materialized_views")
    @patch("app.routes.fetch.run_fetch_scripts")
    @patch("app.services.leader_election.LeaderElectionClient")
    def test_run_fetch_no_scripts(
        self, mock_leader, mock_fetch, mock_mv, mock_agg, mock_ds,
        mock_summary, mock_quotas, mock_feishu, mock_dingtalk, mock_ds_repo,
    ):
        """Empty results (no scripts) should be 'completed', not 'failed'."""
        mock_leader.return_value.try_acquire_leadership.return_value = True
        mock_fetch.return_value = {}

        s = DataFetchScheduler()
        s._run_fetch()

        mock_leader.return_value.record_run.assert_called_once()
        call_args = mock_leader.return_value.record_run.call_args[0]
        assert call_args[0] == "completed"
        assert s._last_result_summary["status"] == "completed"
        assert s._last_result_summary["tools"] == 0

    @patch("app.repositories.daily_stats_repo.DailyStatsRepository")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._maybe_sync_dingtalk_org")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._maybe_sync_feishu_org")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._check_quotas")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._refresh_usage_summary")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._refresh_daily_stats")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._aggregate_user_stats")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._refresh_materialized_views")
    @patch("app.routes.fetch.run_fetch_scripts")
    @patch("app.services.leader_election.LeaderElectionClient")
    def test_run_fetch_skipped(
        self, mock_leader, mock_fetch, mock_mv, mock_agg, mock_ds,
        mock_summary, mock_quotas, mock_feishu, mock_dingtalk, mock_ds_repo,
    ):
        """Concurrent fetch skip should be 'skipped'."""
        mock_leader.return_value.try_acquire_leadership.return_value = True
        mock_fetch.return_value = {"_skipped": True}

        s = DataFetchScheduler()
        s._run_fetch()

        mock_leader.return_value.record_run.assert_called_once()
        call_args = mock_leader.return_value.record_run.call_args[0]
        assert call_args[0] == "skipped"
        assert s._last_result_summary["status"] == "skipped"

    @patch("app.repositories.daily_stats_repo.DailyStatsRepository")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._maybe_sync_dingtalk_org")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._maybe_sync_feishu_org")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._check_quotas")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._refresh_usage_summary")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._refresh_daily_stats")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._aggregate_user_stats")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._refresh_materialized_views")
    @patch("app.routes.fetch.run_fetch_scripts")
    @patch("app.services.leader_election.LeaderElectionClient")
    def test_run_fetch_none_result(
        self, mock_leader, mock_fetch, mock_mv, mock_agg, mock_ds,
        mock_summary, mock_quotas, mock_feishu, mock_dingtalk, mock_ds_repo,
    ):
        """None result (unexpected error) should be 'failed'."""
        mock_leader.return_value.try_acquire_leadership.return_value = True
        mock_fetch.return_value = None

        s = DataFetchScheduler()
        s._run_fetch()

        mock_leader.return_value.record_run.assert_called_once()
        call_args = mock_leader.return_value.record_run.call_args[0]
        assert call_args[0] == "failed"
        assert "unexpected error" in call_args[2].lower()
        assert s._last_result_summary["status"] == "failed"

    @patch("app.repositories.daily_stats_repo.DailyStatsRepository")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._maybe_sync_dingtalk_org")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._maybe_sync_feishu_org")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._check_quotas")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._refresh_usage_summary")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._refresh_daily_stats")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._aggregate_user_stats")
    @patch("app.services.data_fetch_scheduler.DataFetchScheduler._refresh_materialized_views")
    @patch("app.routes.fetch.run_fetch_scripts")
    @patch("app.services.leader_election.LeaderElectionClient")
    def test_run_fetch_all_success(
        self, mock_leader, mock_fetch, mock_mv, mock_agg, mock_ds,
        mock_summary, mock_quotas, mock_feishu, mock_dingtalk, mock_ds_repo,
    ):
        """All scripts success should be 'completed'."""
        mock_leader.return_value.try_acquire_leadership.return_value = True
        mock_fetch.return_value = {
            "qwen": {"success": True},
            "claude": {"success": True},
            "openclaw": {"success": True},
            "codex": {"success": True},
            "zcode": {"success": True},
        }

        s = DataFetchScheduler()
        s._run_fetch()

        mock_leader.return_value.record_run.assert_called_once()
        call_args = mock_leader.return_value.record_run.call_args[0]
        assert call_args[0] == "completed"
        assert call_args[2] is None  # No error message
        assert s._last_result_summary["status"] == "completed"
        assert s._last_result_summary["tools_failed"] == 0


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
        with patch.object(s, "_enforce_user_quota") as mock_enforce:
            s._check_quotas()  # Should not raise
            # DB error aborts the query loop before any enforcement happens
            mock_enforce.assert_not_called()


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

        with patch(
            "app.modules.governance.alert_transaction_manager.create_quota_alert_transactional"
        ) as mock_alert:
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
    def test_enforce_defers_autonomous_workflow_sessions(self, mock_sm_cls, mock_alert):
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
        workflow_session = MagicMock(
            session_id="workflow-session",
            session_type="workflow",
            context={"workflow_id": "wf-123"},
        )
        regular_session = MagicMock(
            session_id="regular-session",
            session_type="agent",
            context={},
        )
        mock_sm = MagicMock()
        mock_sm.get_active_sessions.return_value = [workflow_session, regular_session]
        mock_sm_cls.return_value = mock_sm

        s._enforce_user_quota(row, today, "daily")

        mock_sm.complete_session.assert_called_once_with("regular-session")

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
        # Session listing failed mid-enforcement: no session should be terminated
        mock_sm.complete_session.assert_not_called()

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
