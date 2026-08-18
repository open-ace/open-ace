"""Unit tests for tenant settings audit logging.

Issue #2790: 租户设置修改审计日志
"""

import pytest

from app.core.actor_context import ActorContext
from app.models.tenant import Tenant, TenantSettings
from app.services.tenant_service import UpdateSettingsResult


class TestUpdateSettingsResult:
    """Test UpdateSettingsResult dataclass."""

    def test_success_result(self):
        """Test successful result construction."""
        result = UpdateSettingsResult(
            success=True,
            tenant_id=1,
            changed_fields=["content_filter_enabled"],
            old_values={"content_filter_enabled": True},
            new_values={"content_filter_enabled": False},
        )
        assert result.success is True
        assert result.tenant_id == 1
        assert result.changed_fields == ["content_filter_enabled"]
        assert result.error is None

    def test_failure_result(self):
        """Test failure result construction."""
        result = UpdateSettingsResult(
            success=False,
            tenant_id=1,
            error="Permission denied",
            error_type="permission",
        )
        assert result.success is False
        assert result.error == "Permission denied"
        assert result.error_type == "permission"

    def test_bool_conversion_true(self):
        """Test __bool__ returns True for success."""
        result = UpdateSettingsResult(success=True, tenant_id=1)
        assert bool(result) is True
        assert result  # truthy

    def test_bool_conversion_false(self):
        """Test __bool__ returns False for failure."""
        result = UpdateSettingsResult(success=False, tenant_id=1, error="Error")
        assert bool(result) is False
        assert not result  # falsy


class TestSanitizeValueForAudit:
    """Test _sanitize_value_for_audit method."""

    def test_sanitize_none_value(self):
        """Test None value passes through."""
        from app.services.tenant_service import TenantService

        service = TenantService()
        assert service._sanitize_value_for_audit("any_field", None) is None

    def test_sanitize_branding_logo_url_truncation(self):
        """Test branding_logo_url truncation to 200 chars."""
        from app.services.tenant_service import TenantService

        service = TenantService()
        long_url = "https://example.com/" + "a" * 300
        result = service._sanitize_value_for_audit("branding_logo_url", long_url)
        assert len(result) == 200
        assert result.startswith("https://example.com/")

    def test_sanitize_branding_logo_url_short(self):
        """Test short branding_logo_url passes through."""
        from app.services.tenant_service import TenantService

        service = TenantService()
        short_url = "https://example.com/logo.png"
        result = service._sanitize_value_for_audit("branding_logo_url", short_url)
        assert result == short_url

    def test_sanitize_roi_assumptions(self):
        """Test roi_assumptions only records keys."""
        from app.services.tenant_service import TenantService

        service = TenantService()
        value = {"labor_cost_per_hour": 100, "hours_saved_per_day": 2}
        result = service._sanitize_value_for_audit("roi_assumptions", value)
        assert "changed_keys" in result
        assert set(result["changed_keys"]) == {"labor_cost_per_hour", "hours_saved_per_day"}
        # Values should not be recorded
        assert "labor_cost_per_hour" not in result
        assert 100 not in result

    def test_sanitize_allowed_tools(self):
        """Test allowed_tools records total and tools."""
        from app.services.tenant_service import TenantService

        service = TenantService()
        value = ["claude", "qwen", "openclaw"]
        result = service._sanitize_value_for_audit("allowed_tools", value)
        assert result["total"] == 3
        assert result["tools"] == value

    def test_sanitize_boolean_value(self):
        """Test boolean value passes through."""
        from app.services.tenant_service import TenantService

        service = TenantService()
        assert service._sanitize_value_for_audit("content_filter_enabled", True) is True
        assert service._sanitize_value_for_audit("content_filter_enabled", False) is False

    def test_sanitize_string_value(self):
        """Test short string value passes through."""
        from app.services.tenant_service import TenantService

        service = TenantService()
        result = service._sanitize_value_for_audit("sso_provider", "okta")
        assert result == "okta"


class TestTenantServiceUpdateSettings:
    """Test TenantService.update_settings with audit support."""

    def _make_service(self, tenant_repo=None):
        """Create TenantService with optional mocked repo."""
        from app.services.tenant_service import TenantService

        return TenantService(tenant_repo=tenant_repo)

    def test_update_settings_permission_denied(self):
        """Test permission denied returns correct error structure."""
        from unittest.mock import MagicMock

        tenant_repo = MagicMock()
        service = self._make_service(tenant_repo)

        # Actor without permission
        actor = ActorContext(user_id=1, role="tenant_admin", tenant_id=1)

        result = service.update_settings(
            tenant_id=999,  # Different tenant
            settings_updates={"content_filter_enabled": False},
            actor=actor,
        )

        assert result.success is False
        assert result.error_type == "permission"
        assert "无权" in result.error

    def test_update_settings_validation_invalid_match_mode(self):
        """Test validation error for invalid sensitive_keyword_match_mode."""
        from unittest.mock import MagicMock

        from app.models.tenant import TenantSettings

        tenant_repo = MagicMock()
        tenant = Tenant(id=1, settings=TenantSettings())
        tenant_repo.get_by_id.return_value = tenant

        service = self._make_service(tenant_repo)
        actor = ActorContext(user_id=1, role="tenant_admin", tenant_id=1)

        result = service.update_settings(
            tenant_id=1,
            settings_updates={"sensitive_keyword_match_mode": "invalid_mode"},
            actor=actor,
        )

        assert result.success is False
        assert result.error_type == "validation"
        assert "sensitive_keyword_match_mode" in result.error

    def test_update_settings_validation_no_valid_fields(self):
        """Test validation error for no valid fields."""
        from unittest.mock import MagicMock

        tenant_repo = MagicMock()
        tenant = Tenant(id=1, settings=TenantSettings())
        tenant_repo.get_by_id.return_value = tenant

        service = self._make_service(tenant_repo)
        actor = ActorContext(user_id=1, role="tenant_admin", tenant_id=1)

        result = service.update_settings(
            tenant_id=1,
            settings_updates={"invalid_field": "value"},
            actor=actor,
        )

        assert result.success is False
        assert result.error_type == "validation"
        assert "No valid fields" in result.error

    def test_update_settings_field_whitelist_filters_invalid(self):
        """Test field whitelist filters invalid fields."""
        from unittest.mock import MagicMock

        tenant_repo = MagicMock()
        tenant = Tenant(id=1, settings=TenantSettings(content_filter_enabled=True))
        tenant_repo.get_by_id.return_value = tenant
        tenant_repo.update.return_value = True

        service = self._make_service(tenant_repo)
        actor = ActorContext(user_id=1, role="tenant_admin", tenant_id=1)

        result = service.update_settings(
            tenant_id=1,
            settings_updates={
                "content_filter_enabled": False,
                "invalid_field": "should_be_ignored",
            },
            actor=actor,
        )

        # Should succeed, invalid field ignored
        assert result.success is True
        # Only valid field in changed_fields
        assert result.changed_fields == ["content_filter_enabled"]
        # Invalid field should not appear in old/new values
        assert "invalid_field" not in result.old_values
        assert "invalid_field" not in result.new_values

    def test_update_settings_returns_diff(self):
        """Test successful update returns diff."""
        from unittest.mock import MagicMock

        tenant_repo = MagicMock()
        tenant = Tenant(id=1, settings=TenantSettings(content_filter_enabled=True))
        tenant_repo.get_by_id.return_value = tenant
        tenant_repo.update.return_value = True

        service = self._make_service(tenant_repo)
        actor = ActorContext(user_id=1, role="tenant_admin", tenant_id=1)

        result = service.update_settings(
            tenant_id=1,
            settings_updates={"content_filter_enabled": False},
            actor=actor,
        )

        assert result.success is True
        assert result.tenant_id == 1
        assert result.changed_fields == ["content_filter_enabled"]
        assert result.old_values == {"content_filter_enabled": True}
        assert result.new_values == {"content_filter_enabled": False}

    def test_update_settings_no_changes(self):
        """Test update with no actual changes."""
        from unittest.mock import MagicMock

        tenant_repo = MagicMock()
        tenant = Tenant(id=1, settings=TenantSettings(content_filter_enabled=True))
        tenant_repo.get_by_id.return_value = tenant
        tenant_repo.update.return_value = True

        service = self._make_service(tenant_repo)
        actor = ActorContext(user_id=1, role="tenant_admin", tenant_id=1)

        result = service.update_settings(
            tenant_id=1,
            settings_updates={"content_filter_enabled": True},  # Same value
            actor=actor,
        )

        # Success but no changes recorded
        assert result.success is True
        assert result.changed_fields == []
        assert result.old_values == {}
        assert result.new_values == {}

    def test_update_settings_tenant_not_found(self):
        """Test tenant not found error."""
        from unittest.mock import MagicMock

        tenant_repo = MagicMock()
        tenant_repo.get_by_id.return_value = None

        service = self._make_service(tenant_repo)
        actor = ActorContext(user_id=1, role="platform_admin", tenant_id=None)

        result = service.update_settings(
            tenant_id=999,
            settings_updates={"content_filter_enabled": False},
            actor=actor,
        )

        assert result.success is False
        assert result.error_type == "not_found"

    def test_update_settings_platform_admin_cross_tenant(self):
        """Test platform admin can modify any tenant."""
        from unittest.mock import MagicMock

        tenant_repo = MagicMock()
        tenant = Tenant(id=1, settings=TenantSettings(content_filter_enabled=True))
        tenant_repo.get_by_id.return_value = tenant
        tenant_repo.update.return_value = True

        service = self._make_service(tenant_repo)
        # Platform admin with no tenant_id
        actor = ActorContext(user_id=1, role="platform_admin", tenant_id=None)

        result = service.update_settings(
            tenant_id=1,
            settings_updates={"content_filter_enabled": False},
            actor=actor,
        )

        assert result.success is True
        assert result.tenant_id == 1