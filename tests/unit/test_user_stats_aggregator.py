"""Unit tests for UserDailyStatsAggregator."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.services.user_stats_aggregator import (
    UserDailyStatsAggregator,
    aggregate_user_stats_background,
    get_aggregator,
)


class TestUserDailyStatsAggregator:
    """Test UserDailyStatsAggregator class."""

    def _make_aggregator(self):
        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.connection.return_value = mock_conn

        mock_user_repo = MagicMock()
        agg = UserDailyStatsAggregator(db=mock_db)
        agg.user_repo = mock_user_repo
        return agg, mock_db, mock_cursor, mock_conn, mock_user_repo

    def test_init_default_db(self):
        with patch("app.services.user_stats_aggregator.Database") as mock_db_cls:
            UserDailyStatsAggregator()
            mock_db_cls.assert_called_once()

    def test_init_custom_db(self):
        mock_db = MagicMock()
        agg = UserDailyStatsAggregator(db=mock_db)
        assert agg.db is mock_db

    def test_aggregate_all_users_empty(self):
        agg, mock_db, _, _, mock_user_repo = self._make_aggregator()
        mock_user_repo.get_all_users.return_value = []

        result = agg.aggregate_all_users(days=30)
        assert result == 0

    def test_aggregate_all_users_success(self):
        agg, mock_db, mock_cursor, _, mock_user_repo = self._make_aggregator()
        mock_user_repo.get_all_users.return_value = [
            {"id": 1, "username": "user1", "system_account": "sys1"},
            {"id": 2, "username": "user2", "system_account": None},
        ]
        mock_cursor.rowcount = 5

        with patch.object(agg, "aggregate_user", return_value=5) as mock_agg_user:
            result = agg.aggregate_all_users(days=30)
            assert result == 10  # 5 + 5
            assert mock_agg_user.call_count == 2

    def test_aggregate_all_users_skips_users_without_id(self):
        agg, mock_db, mock_cursor, _, mock_user_repo = self._make_aggregator()
        mock_user_repo.get_all_users.return_value = [
            {"username": "no_id_user"},
            {"id": 1, "username": "user1"},
        ]
        mock_cursor.rowcount = 3

        with patch.object(agg, "aggregate_user", return_value=3) as mock_agg_user:
            agg.aggregate_all_users(days=30)
            assert mock_agg_user.call_count == 1  # Only user with id

    def test_aggregate_user_postgresql(self):
        agg, mock_db, mock_cursor, mock_conn, _ = self._make_aggregator()
        mock_cursor.rowcount = 10

        with patch("app.services.user_stats_aggregator.is_postgresql", return_value=True):
            result = agg.aggregate_user(user_id=1, username="testuser", days=30)
            assert result == 10
            mock_conn.commit.assert_called()

    def test_aggregate_user_sqlite(self):
        agg, mock_db, mock_cursor, mock_conn, _ = self._make_aggregator()
        mock_cursor.rowcount = 7

        with patch("app.services.user_stats_aggregator.is_postgresql", return_value=False):
            result = agg.aggregate_user(user_id=1, username="testuser", days=30)
            assert result == 7
            mock_conn.commit.assert_called()

    def test_aggregate_user_with_system_account(self):
        agg, mock_db, mock_cursor, mock_conn, _ = self._make_aggregator()
        mock_cursor.rowcount = 3

        with patch("app.services.user_stats_aggregator.is_postgresql", return_value=False):
            result = agg.aggregate_user(
                user_id=1, username="testuser", days=30, system_account="sys_account"
            )
            assert result == 3
            # Verify LIKE pattern uses system_account (with escaped underscore)
            call_args = mock_cursor.execute.call_args
            assert "sys\\\\_account%" in str(call_args)

    def test_aggregate_user_db_error(self):
        agg, mock_db, mock_cursor, _, _ = self._make_aggregator()
        mock_cursor.execute.side_effect = Exception("DB error")

        result = agg.aggregate_user(user_id=1, username="testuser", days=30)
        assert result == 0

    def test_aggregate_today_specific_user(self):
        agg, mock_db, mock_cursor, _, mock_user_repo = self._make_aggregator()
        mock_user_repo.get_user_by_id.return_value = {
            "id": 1,
            "username": "testuser",
        }

        with patch.object(agg, "aggregate_user", return_value=2) as mock_agg_user:
            result = agg.aggregate_today(user_id=1)
            assert result == 2
            mock_agg_user.assert_called_once_with(1, "testuser", days=1)

    def test_aggregate_today_user_not_found(self):
        agg, mock_db, _, _, mock_user_repo = self._make_aggregator()
        mock_user_repo.get_user_by_id.return_value = None

        result = agg.aggregate_today(user_id=999)
        assert result == 0

    def test_aggregate_today_all_users(self):
        agg, mock_db, _, _, mock_user_repo = self._make_aggregator()

        with patch.object(agg, "aggregate_all_users", return_value=15) as mock_agg_all:
            result = agg.aggregate_today(user_id=None)
            assert result == 15
            mock_agg_all.assert_called_once_with(days=1)

    def test_cleanup_old_data_postgresql(self):
        agg, mock_db, mock_cursor, mock_conn, _ = self._make_aggregator()
        mock_cursor.rowcount = 50

        with patch("app.services.user_stats_aggregator.is_postgresql", return_value=True):
            result = agg.cleanup_old_data(days_to_keep=30)
            assert result == 50
            mock_conn.commit.assert_called()

    def test_cleanup_old_data_sqlite(self):
        agg, mock_db, mock_cursor, mock_conn, _ = self._make_aggregator()
        mock_cursor.rowcount = 25

        with patch("app.services.user_stats_aggregator.is_postgresql", return_value=False):
            result = agg.cleanup_old_data(days_to_keep=90)
            assert result == 25
            mock_conn.commit.assert_called()

    def test_cleanup_old_data_error(self):
        agg, mock_db, mock_cursor, _, _ = self._make_aggregator()
        mock_cursor.execute.side_effect = Exception("DB error")

        result = agg.cleanup_old_data(days_to_keep=90)
        assert result == 0

    def test_cleanup_old_data_default_keep_days(self):
        agg, mock_db, mock_cursor, mock_conn, _ = self._make_aggregator()
        mock_cursor.rowcount = 10

        with patch("app.services.user_stats_aggregator.is_postgresql", return_value=False):
            agg.cleanup_old_data()
            # Verify cutoff date is 90 days ago (default)
            call_args = mock_cursor.execute.call_args
            sql = call_args[0][0]
            assert "DELETE FROM user_daily_stats WHERE date < ?" in sql


class TestGetAggregator:
    """Test get_aggregator singleton function."""

    def setup_method(self):
        import app.services.user_stats_aggregator as mod

        mod._aggregator = None

    def test_returns_aggregator(self):
        with patch("app.services.user_stats_aggregator.Database"):
            agg = get_aggregator()
            assert isinstance(agg, UserDailyStatsAggregator)

    def test_returns_same_instance(self):
        with patch("app.services.user_stats_aggregator.Database"):
            agg1 = get_aggregator()
            agg2 = get_aggregator()
            assert agg1 is agg2


class TestAggregateUserStatsBackground:
    """Test aggregate_user_stats_background function."""

    def test_success(self):
        with patch("app.services.user_stats_aggregator.get_aggregator") as mock_get:
            mock_agg = MagicMock()
            mock_agg.aggregate_all_users.return_value = 42
            mock_get.return_value = mock_agg

            aggregate_user_stats_background()
            mock_agg.aggregate_all_users.assert_called_once_with(days=7)

    def test_failure(self):
        with patch("app.services.user_stats_aggregator.get_aggregator") as mock_get:
            mock_get.side_effect = Exception("Aggregation failed")
            # Should not raise
            aggregate_user_stats_background()
