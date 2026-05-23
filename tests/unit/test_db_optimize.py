"""Unit tests for DatabaseOptimizer module."""

from unittest.mock import MagicMock, call, patch

import pytest

from app.utils.db_optimize import (
    RECOMMENDED_INDEXES,
    DatabaseOptimizer,
    create_all_indexes,
    optimize_database,
)


class TestRecommendedIndexes:
    """Test RECOMMENDED_INDEXES constant."""

    def test_has_daily_usage_indexes(self):
        assert "daily_usage" in RECOMMENDED_INDEXES
        assert len(RECOMMENDED_INDEXES["daily_usage"]) > 0

    def test_has_daily_messages_indexes(self):
        assert "daily_messages" in RECOMMENDED_INDEXES
        assert len(RECOMMENDED_INDEXES["daily_messages"]) > 0

    def test_has_users_indexes(self):
        assert "users" in RECOMMENDED_INDEXES
        assert len(RECOMMENDED_INDEXES["users"]) > 0

    def test_has_sessions_indexes(self):
        assert "sessions" in RECOMMENDED_INDEXES

    def test_has_audit_logs_indexes(self):
        assert "audit_logs" in RECOMMENDED_INDEXES

    def test_has_quota_usage_indexes(self):
        assert "quota_usage" in RECOMMENDED_INDEXES

    def test_has_quota_alerts_indexes(self):
        assert "quota_alerts" in RECOMMENDED_INDEXES

    def test_has_tenants_indexes(self):
        assert "tenants" in RECOMMENDED_INDEXES

    def test_has_tenant_usage_indexes(self):
        assert "tenant_usage" in RECOMMENDED_INDEXES

    def test_has_content_filter_rules_indexes(self):
        assert "content_filter_rules" in RECOMMENDED_INDEXES

    def test_index_format_is_correct(self):
        for table, indexes in RECOMMENDED_INDEXES.items():
            for idx_name, columns in indexes:
                assert isinstance(idx_name, str), f"Index name must be string: {idx_name}"
                assert isinstance(columns, list), f"Columns must be list: {columns}"
                assert len(columns) > 0, f"Columns must not be empty for {idx_name}"
                for col in columns:
                    assert isinstance(col, str), f"Column must be string: {col}"


class TestDatabaseOptimizer:
    """Test DatabaseOptimizer class."""

    def _make_optimizer(self):
        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.connection.return_value = mock_conn
        opt = DatabaseOptimizer(db=mock_db)
        return opt, mock_db, mock_cursor, mock_conn

    def test_init_default_db(self):
        with patch("app.utils.db_optimize.Database") as mock_db_cls:
            DatabaseOptimizer()
            mock_db_cls.assert_called_once()

    def test_init_custom_db(self):
        mock_db = MagicMock()
        opt = DatabaseOptimizer(db=mock_db)
        assert opt.db is mock_db

    def test_create_indexes_all_tables(self):
        opt, mock_db, mock_cursor, _ = self._make_optimizer()
        result = opt.create_indexes()

        assert "created" in result
        assert "skipped" in result
        assert "errors" in result
        assert len(result["created"]) > 0

        # Verify CREATE INDEX statements were executed
        assert mock_cursor.execute.call_count > 0

    def test_create_indexes_specific_table(self):
        opt, mock_db, mock_cursor, _ = self._make_optimizer()
        result = opt.create_indexes(tables=["users"])

        assert len(result["created"]) == len(RECOMMENDED_INDEXES["users"])
        for entry in result["created"]:
            assert entry["table"] == "users"

    def test_create_indexes_unknown_table(self):
        opt, mock_db, mock_cursor, _ = self._make_optimizer()
        result = opt.create_indexes(tables=["nonexistent_table"])

        assert len(result["skipped"]) == 1
        assert result["skipped"][0]["table"] == "nonexistent_table"
        assert result["skipped"][0]["reason"] == "No recommended indexes"

    def test_create_indexes_handles_error(self):
        opt, mock_db, mock_cursor, _ = self._make_optimizer()
        mock_cursor.execute.side_effect = Exception("SQL error")
        result = opt.create_indexes(tables=["users"])

        assert len(result["errors"]) > 0
        assert result["errors"][0]["error"] == "SQL error"

    def test_create_indexes_mixed_results(self):
        opt, mock_db, mock_cursor, _ = self._make_optimizer()

        call_count = [0]

        def side_effect_fn(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("First index fails")
            return None  # simulate success for remaining indexes

        mock_cursor.execute.side_effect = side_effect_fn
        result = opt.create_indexes(tables=["users"])

        assert len(result["errors"]) >= 1
        assert len(result["created"]) >= 1

    def test_create_indexes_commits(self):
        opt, mock_db, mock_cursor, mock_conn = self._make_optimizer()
        opt.create_indexes(tables=["users"])
        mock_conn.commit.assert_called_once()

    def test_drop_indexes_all(self):
        opt, mock_db, mock_cursor, _ = self._make_optimizer()
        result = opt.drop_indexes()

        assert "dropped" in result
        assert "errors" in result
        assert len(result["dropped"]) > 0

    def test_drop_indexes_specific_table(self):
        opt, mock_db, mock_cursor, _ = self._make_optimizer()
        result = opt.drop_indexes(tables=["users"])

        assert len(result["dropped"]) == len(RECOMMENDED_INDEXES["users"])

    def test_drop_indexes_unknown_table(self):
        opt, mock_db, mock_cursor, _ = self._make_optimizer()
        result = opt.drop_indexes(tables=["nonexistent"])
        assert len(result["dropped"]) == 0

    def test_drop_indexes_handles_error(self):
        opt, mock_db, mock_cursor, _ = self._make_optimizer()
        mock_cursor.execute.side_effect = Exception("Drop error")
        result = opt.drop_indexes(tables=["users"])

        assert len(result["errors"]) > 0

    def test_analyze_table_with_data(self):
        opt, mock_db, mock_cursor, _ = self._make_optimizer()
        mock_cursor.fetchone.return_value = (100,)
        mock_cursor.fetchall.return_value = []

        result = opt.analyze_table("users")
        assert result["table"] == "users"
        assert result["row_count"] == 100
        assert "indexes" in result
        assert "recommendations" in result

    def test_analyze_table_with_recommendations(self):
        opt, mock_db, mock_cursor, _ = self._make_optimizer()
        mock_cursor.fetchone.return_value = (50,)
        # No existing indexes
        mock_cursor.fetchall.return_value = []

        result = opt.analyze_table("users")
        assert len(result["recommendations"]) == len(RECOMMENDED_INDEXES["users"])

    def test_analyze_table_with_existing_indexes(self):
        opt, mock_db, mock_cursor, _ = self._make_optimizer()
        mock_cursor.fetchone.return_value = (50,)

        # Simulate existing indexes
        idx_name = RECOMMENDED_INDEXES["users"][0][0]
        mock_cursor.fetchall.side_effect = [
            [(1, idx_name, 0)],  # index_list result
            [(1, "id", "username")],  # index_info result
        ]

        result = opt.analyze_table("users")
        assert len(result["indexes"]) == 1
        assert result["indexes"][0]["name"] == idx_name

    def test_analyze_table_unknown_table(self):
        opt, mock_db, mock_cursor, _ = self._make_optimizer()
        mock_cursor.fetchone.return_value = (0,)
        mock_cursor.fetchall.return_value = []

        result = opt.analyze_table("nonexistent_table")
        assert result["table"] == "nonexistent_table"
        assert result["recommendations"] == []

    def test_analyze_table_row_count_error(self):
        opt, mock_db, mock_cursor, _ = self._make_optimizer()
        mock_cursor.execute.side_effect = Exception("Table does not exist")

        result = opt.analyze_table("bad_table")
        assert result["row_count"] == "error"

    def test_get_table_stats(self):
        opt, mock_db, mock_cursor, _ = self._make_optimizer()

        # sqlite_master returns table names
        mock_cursor.fetchall.side_effect = [
            [("users",), ("sessions",)],  # table names
        ]
        mock_cursor.fetchone.side_effect = [
            (100,),  # COUNT(*) for users
            (50,),  # page_count
            (4096,),  # page_size
        ]

        result = opt.get_table_stats()
        assert isinstance(result, list)
        assert len(result) == 2

    def test_vacuum_success(self):
        opt, mock_db, mock_cursor, _ = self._make_optimizer()
        result = opt.vacuum()
        assert result is True
        mock_cursor.execute.assert_called_with("VACUUM")

    def test_vacuum_failure(self):
        opt, mock_db, mock_cursor, _ = self._make_optimizer()
        mock_cursor.execute.side_effect = Exception("VACUUM error")
        result = opt.vacuum()
        assert result is False

    def test_analyze_success(self):
        opt, mock_db, mock_cursor, _ = self._make_optimizer()
        result = opt.analyze()
        assert result is True
        mock_cursor.execute.assert_called_with("ANALYZE")

    def test_analyze_failure(self):
        opt, mock_db, mock_cursor, _ = self._make_optimizer()
        mock_cursor.execute.side_effect = Exception("ANALYZE error")
        result = opt.analyze()
        assert result is False

    def test_optimize(self):
        opt, mock_db, mock_cursor, _ = self._make_optimizer()
        mock_cursor.fetchone.side_effect = [
            (100,),  # for table stats row count
            (50,),  # page_count
            (4096,),  # page_size
        ]
        mock_cursor.fetchall.side_effect = [
            [("users",)],  # table list
        ]

        result = opt.optimize()
        assert "indexes" in result
        assert "analyze" in result
        assert "vacuum" in result
        assert "stats" in result

    def test_get_query_plan(self):
        opt, mock_db, mock_cursor, _ = self._make_optimizer()
        mock_cursor.fetchall.return_value = [
            ("TABLE users",),
        ]

        result = opt.get_query_plan("SELECT * FROM users")
        assert isinstance(result, list)
        assert len(result) == 1

    def test_get_query_plan_with_params(self):
        opt, mock_db, mock_cursor, _ = self._make_optimizer()
        mock_cursor.fetchall.return_value = []

        result = opt.get_query_plan("SELECT * FROM users WHERE id = ?", (1,))
        assert isinstance(result, list)


class TestModuleFunctions:
    """Test module-level convenience functions."""

    def test_optimize_database(self):
        with patch("app.utils.db_optimize.DatabaseOptimizer") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.optimize.return_value = {"indexes": {}, "analyze": True}
            mock_cls.return_value = mock_instance

            result = optimize_database()
            mock_cls.assert_called_once()
            mock_instance.optimize.assert_called_once()
            assert "indexes" in result

    def test_create_all_indexes(self):
        with patch("app.utils.db_optimize.DatabaseOptimizer") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.create_indexes.return_value = {"created": [], "errors": []}
            mock_cls.return_value = mock_instance

            create_all_indexes()
            mock_cls.assert_called_once()
            mock_instance.create_indexes.assert_called_once()
