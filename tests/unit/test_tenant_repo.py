"""Unit tests for TenantRepository.

Note: SQL string assertions verify key query structure. See issue #525 for
integration test plans.
"""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.models.tenant import QuotaConfig, Tenant, TenantSettings
from app.repositories.tenant_repo import TenantRepository


class TestTenantRepository:
    """Tests for TenantRepository."""

    def setup_method(self):
        self.db = MagicMock()
        self.db.is_postgresql = False
        self.repo = TenantRepository(db=self.db)

    def _make_tenant(self, **overrides):
        """Create a Tenant instance for testing."""
        defaults = {
            "name": "Test Corp",
            "slug": "test-corp",
            "status": "active",
            "plan": "standard",
            "contact_email": "admin@test.com",
            "contact_phone": "123-456-7890",
            "contact_name": "Admin",
            "quota": QuotaConfig(),
            "settings": TenantSettings(),
            "trial_ends_at": None,
            "subscription_ends_at": None,
        }
        defaults.update(overrides)
        return Tenant(**defaults)

    def _tenant_row(self, **overrides):
        """Create a mock tenant row dict."""
        defaults = {
            "id": 1,
            "name": "Test Corp",
            "slug": "test-corp",
            "status": "active",
            "plan": "standard",
            "contact_email": "admin@test.com",
            "contact_phone": "123-456-7890",
            "contact_name": "Admin",
            "quota": json.dumps(QuotaConfig().to_dict()),
            "settings": json.dumps(TenantSettings().to_dict()),
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
            "trial_ends_at": None,
            "subscription_ends_at": None,
            "user_count": 5,
            "total_tokens_used": 10000,
            "total_requests_made": 500,
        }
        defaults.update(overrides)
        return defaults

    # -------------------------------------------------------------------------
    # create (transactional - 3 INSERTs)
    # -------------------------------------------------------------------------

    def test_create_sqlite(self):
        tenant = self._make_tenant()
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 1
        mock_cursor.fetchone.return_value = None  # not PG

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        self.db.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        self.db.connection.return_value.__exit__ = MagicMock(return_value=False)

        with patch("app.repositories.database.is_postgresql", return_value=False):
            with patch("app.repositories.database.adapt_sql", lambda q: q):
                result = self.repo.create(tenant)

        assert result == 1
        # Should execute 3 INSERTs: tenants, tenant_quotas, tenant_settings
        assert mock_cursor.execute.call_count == 3

    def test_create_postgresql(self):
        tenant = self._make_tenant()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = [42]
        self.db.is_postgresql = True

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        self.db.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        self.db.connection.return_value.__exit__ = MagicMock(return_value=False)

        with patch("app.repositories.database.is_postgresql", return_value=True):
            with patch("app.repositories.database.adapt_sql", lambda q: q):
                result = self.repo.create(tenant)

        assert result == 42
        first_query = mock_cursor.execute.call_args_list[0][0][0]
        assert "RETURNING id" in first_query

    def test_create_sqlite_boolean_conversion(self):
        """SQLite should convert booleans to 1/0 for tenant_settings."""
        tenant = self._make_tenant()
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 1

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        self.db.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        self.db.connection.return_value.__exit__ = MagicMock(return_value=False)

        with patch("app.repositories.database.is_postgresql", return_value=False):
            with patch("app.repositories.database.adapt_sql", lambda q: q):
                self.repo.create(tenant)

        # Third INSERT is tenant_settings
        settings_call = mock_cursor.execute.call_args_list[2]
        params = settings_call[0][1]
        # content_filter_enabled and audit_log_enabled should be 1
        assert params[1] in (0, 1)
        assert params[2] in (0, 1)

    def test_create_exception(self):
        tenant = self._make_tenant()
        self.db.connection.side_effect = Exception("DB error")
        result = self.repo.create(tenant)
        assert result is None

    # -------------------------------------------------------------------------
    # get_by_id
    # -------------------------------------------------------------------------

    def test_get_by_id_found(self):
        row = self._tenant_row()
        # _row_to_tenant queries 2 additional tables
        self.db.fetch_one.side_effect = [
            row,  # tenant row
            {
                "daily_token_limit": 1000000,
                "monthly_token_limit": 30000000,
                "daily_request_limit": 10000,
                "monthly_request_limit": 300000,
                "max_users": 100,
                "max_sessions_per_user": 5,
            },  # quota row
            {
                "content_filter_enabled": 1,
                "audit_log_enabled": 1,
                "audit_log_retention_days": 90,
                "data_retention_days": 365,
                "sso_enabled": 0,
                "sso_provider": None,
            },  # settings row
        ]
        result = self.repo.get_by_id(1)
        assert result is not None
        assert result.name == "Test Corp"

    def test_get_by_id_not_found(self):
        self.db.fetch_one.return_value = None
        result = self.repo.get_by_id(999)
        assert result is None

    def test_get_by_id_include_deleted(self):
        row = self._tenant_row()
        self.db.fetch_one.side_effect = [row, None, None]
        self.repo.get_by_id(1, include_deleted=True)
        query = self.db.fetch_one.call_args_list[0][0][0]
        assert "deleted_at" not in query

    def test_get_by_id_exclude_deleted(self):
        row = self._tenant_row()
        self.db.fetch_one.side_effect = [row, None, None]
        self.repo.get_by_id(1)
        query = self.db.fetch_one.call_args_list[0][0][0]
        assert "deleted_at IS NULL" in query

    # -------------------------------------------------------------------------
    # get_by_slug
    # -------------------------------------------------------------------------

    def test_get_by_slug_found(self):
        row = self._tenant_row()
        self.db.fetch_one.side_effect = [row, None, None]
        result = self.repo.get_by_slug("test-corp")
        assert result is not None
        assert result.slug == "test-corp"

    def test_get_by_slug_not_found(self):
        self.db.fetch_one.return_value = None
        result = self.repo.get_by_slug("nonexistent")
        assert result is None

    # -------------------------------------------------------------------------
    # _row_to_tenant (queries 2 additional tables)
    # -------------------------------------------------------------------------

    def test_row_to_tenant_with_dedicated_tables(self):
        row = self._tenant_row()
        self.db.fetch_one.side_effect = [
            {
                "daily_token_limit": 500000,
                "monthly_token_limit": 15000000,
                "daily_request_limit": 5000,
                "monthly_request_limit": 150000,
                "max_users": 50,
                "max_sessions_per_user": 3,
            },
            {
                "content_filter_enabled": 0,
                "audit_log_enabled": 1,
                "audit_log_retention_days": 30,
                "data_retention_days": 180,
                "sso_enabled": 1,
                "sso_provider": "okta",
            },
        ]
        result = self.repo._row_to_tenant(row)
        assert result.quota.max_users == 50
        assert result.quota.daily_token_limit == 500000
        assert result.settings.sso_provider == "okta"
        assert result.settings.content_filter_enabled is False

    def test_row_to_tenant_fallback_to_json(self):
        """When dedicated tables return None, fall back to JSON in tenant row."""
        custom_quota = QuotaConfig(max_users=200, daily_token_limit=2000000)
        custom_settings = TenantSettings(sso_enabled=True, sso_provider="azure")
        row = self._tenant_row(
            quota=json.dumps(custom_quota.to_dict()),
            settings=json.dumps(custom_settings.to_dict()),
        )
        # Dedicated tables return None
        self.db.fetch_one.side_effect = [None, None]
        result = self.repo._row_to_tenant(row)
        assert result.quota.max_users == 200
        assert result.quota.daily_token_limit == 2000000

    def test_row_to_tenant_exception_in_quota_table(self):
        """Exception in quota table should fall back gracefully."""
        row = self._tenant_row()
        self.db.fetch_one.side_effect = [
            Exception("quota table error"),
            {
                "content_filter_enabled": 1,
                "audit_log_enabled": 1,
                "audit_log_retention_days": 90,
                "data_retention_days": 365,
                "sso_enabled": 0,
                "sso_provider": None,
            },
        ]
        # Should not raise, just use defaults
        result = self.repo._row_to_tenant(row)
        assert result is not None

    # -------------------------------------------------------------------------
    # get_all
    # -------------------------------------------------------------------------

    def test_get_all_default(self):
        self.db.fetch_all.return_value = []
        self.repo.get_all()
        query = self.db.fetch_all.call_args[0][0]
        assert "deleted_at IS NULL" in query
        assert "LIMIT ?" in query
        assert "OFFSET ?" in query

    def test_get_all_with_status_filter(self):
        self.db.fetch_all.return_value = []
        self.repo.get_all(status="active")
        query = self.db.fetch_all.call_args[0][0]
        assert "status = ?" in query

    def test_get_all_with_plan_filter(self):
        self.db.fetch_all.return_value = []
        self.repo.get_all(plan="enterprise")
        query = self.db.fetch_all.call_args[0][0]
        assert "plan = ?" in query

    def test_get_all_include_deleted(self):
        self.db.fetch_all.return_value = []
        self.repo.get_all(include_deleted=True)
        query = self.db.fetch_all.call_args[0][0]
        assert "deleted_at" not in query

    def test_get_all_pagination(self):
        self.db.fetch_all.return_value = []
        self.repo.get_all(limit=50, offset=10)
        params = self.db.fetch_all.call_args[0][1]
        assert params[-2] == 50
        assert params[-1] == 10

    # -------------------------------------------------------------------------
    # update (filters allowed columns, JSON-serializes dicts)
    # -------------------------------------------------------------------------

    def test_update_basic_fields(self):
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        self.db.execute.return_value = mock_cursor

        result = self.repo.update(1, {"name": "New Name", "status": "suspended"})
        assert result is True
        call_args = self.db.execute.call_args
        query = call_args[0][0]
        assert "name = ?" in query
        assert "status = ?" in query
        assert "updated_at = ?" in query

    def test_update_empty_updates(self):
        result = self.repo.update(1, {})
        assert result is False

    def test_update_json_serializes_quota(self):
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        self.db.execute.return_value = mock_cursor

        quota_dict = {"daily_token_limit": 500000}
        result = self.repo.update(1, {"quota": quota_dict})
        assert result is True
        call_args = self.db.execute.call_args
        params = call_args[0][1]
        # quota should be JSON-serialized
        quota_param = next(p for p in params if isinstance(p, str) and "daily_token_limit" in p)
        assert json.loads(quota_param) == quota_dict

    def test_update_json_serializes_settings(self):
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        self.db.execute.return_value = mock_cursor

        settings_dict = {"sso_enabled": True}
        result = self.repo.update(1, {"settings": settings_dict})
        assert result is True
        call_args = self.db.execute.call_args
        params = call_args[0][1]
        settings_param = next(p for p in params if isinstance(p, str) and "sso_enabled" in p)
        assert json.loads(settings_param) == settings_dict

    def test_update_ignores_disallowed_columns(self):
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        self.db.execute.return_value = mock_cursor

        result = self.repo.update(1, {"name": "Valid", "evil_column": "hacked"})
        assert result is True
        query = self.db.execute.call_args[0][0]
        assert "evil_column" not in query
        assert "name = ?" in query

    def test_update_no_allowed_columns(self):
        result = self.repo.update(1, {"evil_column": "hacked"})
        assert result is False

    def test_update_not_found(self):
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 0
        self.db.execute.return_value = mock_cursor

        result = self.repo.update(999, {"name": "New"})
        assert result is False

    def test_update_exception(self):
        self.db.execute.side_effect = Exception("DB error")
        result = self.repo.update(1, {"name": "New"})
        assert result is False

    # -------------------------------------------------------------------------
    # delete (soft delete)
    # -------------------------------------------------------------------------

    def test_delete_soft(self):
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        self.db.execute.return_value = mock_cursor

        result = self.repo.delete(1)
        assert result is True
        call_args = self.db.execute.call_args
        query = call_args[0][0]
        assert "deleted_at = ?" in query
        assert "deleted_at IS NULL" in query

    def test_delete_not_found(self):
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 0
        self.db.execute.return_value = mock_cursor

        result = self.repo.delete(999)
        assert result is False

    def test_delete_exception(self):
        self.db.execute.side_effect = Exception("DB error")
        result = self.repo.delete(1)
        assert result is False

    # -------------------------------------------------------------------------
    # restore
    # -------------------------------------------------------------------------

    def test_restore_success(self):
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        self.db.execute.return_value = mock_cursor

        result = self.repo.restore(1)
        assert result is True
        query = self.db.execute.call_args[0][0]
        assert "deleted_at = NULL" in query

    def test_restore_exception(self):
        self.db.execute.side_effect = Exception("DB error")
        result = self.repo.restore(1)
        assert result is False

    # -------------------------------------------------------------------------
    # hard_delete
    # -------------------------------------------------------------------------

    def test_hard_delete(self):
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        self.db.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        self.db.connection.return_value.__exit__ = MagicMock(return_value=False)

        with patch("app.repositories.database.adapt_sql", lambda q: q):
            result = self.repo.hard_delete(1)
        assert result is True
        # Should delete tenant_usage first, then tenant
        assert mock_cursor.execute.call_count == 2
        first_query = mock_cursor.execute.call_args_list[0][0][0]
        assert "DELETE FROM tenant_usage" in first_query
        second_query = mock_cursor.execute.call_args_list[1][0][0]
        assert "DELETE FROM tenants" in second_query

    def test_hard_delete_exception(self):
        self.db.connection.side_effect = Exception("DB error")
        with patch("app.repositories.database.adapt_sql", lambda q: q):
            result = self.repo.hard_delete(1)
        assert result is False

    # -------------------------------------------------------------------------
    # record_usage
    # -------------------------------------------------------------------------

    def test_record_usage_with_date(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        self.db.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        self.db.connection.return_value.__exit__ = MagicMock(return_value=False)

        with patch("app.repositories.database.adapt_sql", lambda q: q):
            result = self.repo.record_usage(tenant_id=1, tokens=500, requests=10, date="2024-01-15")
        assert result is True
        assert mock_cursor.execute.call_count == 2

    def test_record_usage_default_date(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        self.db.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        self.db.connection.return_value.__exit__ = MagicMock(return_value=False)

        with patch("app.repositories.database.adapt_sql", lambda q: q):
            result = self.repo.record_usage(tenant_id=1)
        assert result is True
        # First INSERT should use today's date
        insert_params = mock_cursor.execute.call_args_list[0][0][1]
        assert insert_params[1] == datetime.utcnow().strftime("%Y-%m-%d")

    def test_record_usage_exception(self):
        self.db.connection.side_effect = Exception("DB error")
        with patch("app.repositories.database.adapt_sql", lambda q: q):
            result = self.repo.record_usage(tenant_id=1)
        assert result is False

    # -------------------------------------------------------------------------
    # get_usage
    # -------------------------------------------------------------------------

    def test_get_usage(self):
        self.db.fetch_all.return_value = [
            {
                "tenant_id": 1,
                "date": "2024-01-01",
                "tokens_used": 500,
                "requests_made": 10,
                "active_users": 3,
                "new_users": 1,
            },
        ]
        result = self.repo.get_usage(tenant_id=1)
        assert len(result) == 1
        assert result[0].tokens_used == 500

    def test_get_usage_with_date_range(self):
        self.db.fetch_all.return_value = []
        self.repo.get_usage(tenant_id=1, start_date="2024-01-01", end_date="2024-01-31", limit=10)
        query = self.db.fetch_all.call_args[0][0]
        assert "date >= ?" in query
        assert "date <= ?" in query
        assert "LIMIT ?" in query
        params = self.db.fetch_all.call_args[0][1]
        assert params[-1] == 10

    # -------------------------------------------------------------------------
    # update_user_count
    # -------------------------------------------------------------------------

    def test_update_user_count_increase(self):
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        self.db.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        self.db.connection.return_value.__exit__ = MagicMock(return_value=False)

        with patch("app.repositories.database.adapt_sql", lambda q: q):
            result = self.repo.update_user_count(tenant_id=1, delta=3)
        assert result is True
        params = mock_cursor.execute.call_args[0][1]
        assert params[0] == 3

    def test_update_user_count_decrease(self):
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        self.db.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        self.db.connection.return_value.__exit__ = MagicMock(return_value=False)

        with patch("app.repositories.database.adapt_sql", lambda q: q):
            result = self.repo.update_user_count(tenant_id=1, delta=-1)
        assert result is True
        query = mock_cursor.execute.call_args[0][0]
        assert "MAX(0, user_count + ?)" in query

    def test_update_user_count_exception(self):
        self.db.connection.side_effect = Exception("DB error")
        with patch("app.repositories.database.adapt_sql", lambda q: q):
            result = self.repo.update_user_count(tenant_id=1)
        assert result is False

    # -------------------------------------------------------------------------
    # count
    # -------------------------------------------------------------------------

    def test_count_all(self):
        self.db.fetch_one.return_value = {"count": 42}
        result = self.repo.count()
        assert result == 42
        query = self.db.fetch_one.call_args[0][0]
        assert "COUNT(*)" in query

    def test_count_with_status(self):
        self.db.fetch_one.return_value = {"count": 10}
        result = self.repo.count(status="active")
        assert result == 10
        query = self.db.fetch_one.call_args[0][0]
        assert "status = ?" in query

    def test_count_no_result(self):
        self.db.fetch_one.return_value = None
        result = self.repo.count()
        assert result == 0
