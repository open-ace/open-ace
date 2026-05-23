"""Unit tests for TenantService."""

from unittest.mock import MagicMock

import pytest

from app.services.tenant_service import TenantService


class TestTenantService:
    """Test TenantService business logic."""

    def _make_service(self):
        mock_tenant_repo = MagicMock()
        mock_user_repo = MagicMock()
        svc = TenantService(tenant_repo=mock_tenant_repo, user_repo=mock_user_repo)
        return svc, mock_tenant_repo, mock_user_repo

    def test_create_tenant(self):
        svc, mock_repo, _ = self._make_service()
        mock_tenant = MagicMock()
        mock_tenant.id = 1
        mock_tenant.name = "Test Co"
        mock_tenant.slug = "test-co"
        mock_repo.get_by_slug.return_value = None
        mock_repo.create.return_value = mock_tenant
        result = svc.create_tenant(name="Test Co")
        assert result is not None
        assert result.name == "Test Co"

    def test_create_tenant_duplicate_slug(self):
        svc, mock_repo, _ = self._make_service()
        mock_repo.get_by_slug.return_value = MagicMock()
        # Pass slug explicitly to avoid _generate_slug infinite loop with truthy mock
        result = svc.create_tenant(name="Test", slug="test-co")
        assert result is None

    def test_get_tenant(self):
        svc, mock_repo, _ = self._make_service()
        mock_repo.get_by_id.return_value = MagicMock(id=1)
        result = svc.get_tenant(1)
        assert result is not None
        mock_repo.get_by_id.assert_called_with(1)

    def test_get_tenant_by_slug(self):
        svc, mock_repo, _ = self._make_service()
        mock_repo.get_by_slug.return_value = MagicMock(slug="test-co")
        result = svc.get_tenant_by_slug("test-co")
        assert result is not None

    def test_list_tenants(self):
        svc, mock_repo, _ = self._make_service()
        mock_repo.get_all.return_value = [MagicMock(), MagicMock()]
        result = svc.list_tenants()
        assert len(result) == 2

    def test_update_tenant(self):
        svc, mock_repo, _ = self._make_service()
        mock_repo.update.return_value = True
        result = svc.update_tenant(1, {"name": "Updated"})
        assert result is True

    def test_suspend_tenant(self):
        svc, mock_repo, _ = self._make_service()
        mock_repo.update.return_value = True
        result = svc.suspend_tenant(1, reason="Violation")
        assert result is True
        call_args = mock_repo.update.call_args
        assert call_args[0][1]["status"] == "suspended"

    def test_activate_tenant(self):
        svc, mock_repo, _ = self._make_service()
        mock_repo.update.return_value = True
        result = svc.activate_tenant(1)
        assert result is True

    def test_delete_tenant_soft(self):
        svc, mock_repo, _ = self._make_service()
        mock_repo.delete.return_value = True
        result = svc.delete_tenant(1)
        assert result is True

    def test_delete_tenant_hard(self):
        svc, mock_repo, _ = self._make_service()
        mock_repo.hard_delete.return_value = True
        result = svc.delete_tenant(1, hard=True)
        assert result is True
        mock_repo.hard_delete.assert_called_with(1)

    def test_generate_slug_basic(self):
        svc, mock_repo, _ = self._make_service()
        mock_repo.get_by_slug.return_value = None
        assert svc._generate_slug("Hello World!") == "hello-world"
        assert svc._generate_slug("Test@#$Company") == "test-company"

    def test_generate_slug_length_limit(self):
        svc, mock_repo, _ = self._make_service()
        mock_repo.get_by_slug.return_value = None
        assert len(svc._generate_slug("A" * 100)) <= 50

    def test_record_usage(self):
        svc, mock_repo, _ = self._make_service()
        mock_repo.record_usage.return_value = True
        result = svc.record_usage(1, tokens=100, requests=5)
        assert result is True

    def test_check_quota_allowed(self):
        svc, mock_repo, _ = self._make_service()
        mock_tenant = MagicMock()
        mock_tenant.is_active.return_value = True
        mock_tenant.quota.daily_token_limit = 1000000
        mock_tenant.quota.daily_request_limit = 10000
        mock_tenant.to_dict.return_value = {"status": "active"}
        mock_repo.get_by_id.return_value = mock_tenant
        mock_repo.get_usage.return_value = []  # No usage today
        result = svc.check_quota(1, tokens=100)
        assert result["allowed"] is True

    def test_check_quota_over_limit(self):
        svc, mock_repo, _ = self._make_service()
        mock_tenant = MagicMock()
        mock_tenant.is_active.return_value = True
        mock_tenant.quota.daily_token_limit = 1000
        mock_tenant.quota.daily_request_limit = 10
        mock_tenant.to_dict.return_value = {"status": "active"}
        mock_repo.get_by_id.return_value = mock_tenant
        # Simulate usage that puts us near the limit
        mock_usage = MagicMock()
        mock_usage.tokens_used = 999
        mock_usage.requests_made = 1
        mock_repo.get_usage.return_value = [mock_usage]
        result = svc.check_quota(1, tokens=100)
        assert result["allowed"] is False

    def test_can_add_user(self):
        svc, mock_repo, _ = self._make_service()
        mock_tenant = MagicMock()
        mock_tenant.can_add_users.return_value = True
        mock_repo.get_by_id.return_value = mock_tenant
        assert svc.can_add_user(1) is True

    def test_can_add_user_at_limit(self):
        svc, mock_repo, _ = self._make_service()
        mock_tenant = MagicMock()
        mock_tenant.can_add_users.return_value = False
        mock_repo.get_by_id.return_value = mock_tenant
        assert svc.can_add_user(1) is False

    def test_get_plan_quotas(self):
        svc, _, _ = self._make_service()
        quotas = svc.get_plan_quotas()
        assert "free" in quotas
        assert "standard" in quotas
        assert "premium" in quotas
        assert "enterprise" in quotas

    def test_update_quota(self):
        svc, mock_repo, _ = self._make_service()
        mock_tenant = MagicMock()
        mock_tenant.quota.to_dict.return_value = {"daily_token_limit": 1000000}
        mock_repo.get_by_id.return_value = mock_tenant
        mock_repo.update.return_value = True
        result = svc.update_quota(1, {"daily_token_limit": 50000})
        assert result is True

    def test_update_settings(self):
        svc, mock_repo, _ = self._make_service()
        mock_tenant = MagicMock()
        mock_tenant.settings.to_dict.return_value = {"theme": "light"}
        mock_repo.get_by_id.return_value = mock_tenant
        mock_repo.update.return_value = True
        result = svc.update_settings(1, {"theme": "dark"})
        assert result is True

    def test_increment_user_count(self):
        svc, mock_repo, _ = self._make_service()
        mock_repo.update_user_count.return_value = True
        result = svc.increment_user_count(1)
        assert result is True

    def test_decrement_user_count(self):
        svc, mock_repo, _ = self._make_service()
        mock_repo.update_user_count.return_value = True
        result = svc.decrement_user_count(1)
        assert result is True

    def test_get_tenant_stats(self):
        svc, mock_repo, _ = self._make_service()
        mock_tenant = MagicMock()
        mock_tenant.quota.daily_token_limit = 1000000
        mock_tenant.quota.daily_request_limit = 1000
        mock_repo.get_by_id.return_value = mock_tenant
        mock_repo.get_usage.return_value = []
        result = svc.get_tenant_stats(1)
        assert result is not None
