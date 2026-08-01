"""
Test TenantService actor parameter validation

Issue #2179: 验证 Service 层权限检查
"""

import pytest

from app.core.actor_context import ActorContext
from app.services.tenant_service import TenantService


class TestTenantServiceActorValidation:
    """Test that Service layer properly validates actor permissions."""

    def test_create_tenant_requires_platform_admin(self):
        """Test that create_tenant requires platform_admin role."""
        service = TenantService()

        # Non-platform-admin should raise PermissionError
        actor = ActorContext(user_id=2, role="tenant_admin", tenant_id=1)

        with pytest.raises(PermissionError) as exc_info:
            service.create_tenant(
                name="Test Tenant",
                actor=actor
            )

        assert "platform_admin" in str(exc_info.value)

    def test_update_tenant_validates_tenant_access(self):
        """Test that update_tenant validates actor can access tenant."""
        service = TenantService()

        # tenant_admin trying to update different tenant
        actor = ActorContext(user_id=2, role="tenant_admin", tenant_id=1)

        with pytest.raises(PermissionError) as exc_info:
            service.update_tenant(
                tenant_id=999,  # Different tenant
                updates={"name": "Hacked"},
                actor=actor
            )

        assert "无权修改租户" in str(exc_info.value)

    def test_update_quota_validates_tenant_access(self):
        """Test that update_quota validates actor can access tenant."""
        service = TenantService()

        # tenant_admin trying to update quota for different tenant
        actor = ActorContext(user_id=2, role="tenant_admin", tenant_id=1)

        with pytest.raises(PermissionError) as exc_info:
            service.update_quota(
                tenant_id=999,  # Different tenant
                quota_updates={"daily_token_limit": 1000000},
                actor=actor
            )

        assert "无权修改租户" in str(exc_info.value)

    def test_suspend_tenant_requires_platform_admin(self):
        """Test that suspend_tenant requires platform_admin role."""
        service = TenantService()

        # tenant_admin should not be able to suspend
        actor = ActorContext(user_id=2, role="tenant_admin", tenant_id=1)

        with pytest.raises(PermissionError) as exc_info:
            service.suspend_tenant(
                tenant_id=1,
                reason="Test",
                actor=actor
            )

        assert "platform_admin" in str(exc_info.value)

    def test_activate_tenant_requires_platform_admin(self):
        """Test that activate_tenant requires platform_admin role."""
        service = TenantService()

        # tenant_admin should not be able to activate
        actor = ActorContext(user_id=2, role="tenant_admin", tenant_id=1)

        with pytest.raises(PermissionError) as exc_info:
            service.activate_tenant(
                tenant_id=1,
                actor=actor
            )

        assert "platform_admin" in str(exc_info.value)

    def test_delete_tenant_requires_platform_admin(self):
        """Test that delete_tenant requires platform_admin role."""
        service = TenantService()

        # tenant_admin should not be able to delete
        actor = ActorContext(user_id=2, role="tenant_admin", tenant_id=1)

        with pytest.raises(PermissionError) as exc_info:
            service.delete_tenant(
                tenant_id=1,
                actor=actor
            )

        assert "platform_admin" in str(exc_info.value)

    def test_platform_admin_can_create_tenant(self):
        """Test that platform_admin can create tenants (no permission error)."""
        service = TenantService()

        # platform_admin should be able to create - no PermissionError
        actor = ActorContext(user_id=1, role="platform_admin", tenant_id=None)

        # Mock the repository to avoid DB requirement
        from unittest.mock import Mock
        service.tenant_repo = Mock()
        service.tenant_repo.get_by_slug.return_value = None  # Slug doesn't exist
        service.tenant_repo.create.return_value = 1  # Mock tenant ID

        # This should not raise PermissionError
        try:
            service.create_tenant(
                name="Test Tenant",
                actor=actor
            )
            # Should have attempted to create
            assert service.tenant_repo.create.called
        except PermissionError:
            pytest.fail("platform_admin should be able to create tenant")

    def test_tenant_admin_can_update_own_tenant(self):
        """Test that tenant_admin can update their own tenant (no exception)."""
        service = TenantService()

        # tenant_admin updating own tenant
        actor = ActorContext(user_id=2, role="tenant_admin", tenant_id=1)

        # Should not raise PermissionError
        try:
            result = service.update_tenant(
                tenant_id=1,  # Own tenant
                updates={"name": "Updated"},
                actor=actor
            )
            # Result will be False because tenant doesn't exist, but no PermissionError
            assert result is False or result is True  # Just checking no exception
        except PermissionError:
            pytest.fail("tenant_admin should be able to update own tenant")

    def test_update_settings_validates_tenant_access(self):
        """Test that update_settings validates actor can access tenant."""
        service = TenantService()

        # tenant_admin trying to update settings for different tenant
        actor = ActorContext(user_id=2, role="tenant_admin", tenant_id=1)

        with pytest.raises(PermissionError) as exc_info:
            service.update_settings(
                tenant_id=999,  # Different tenant
                settings_updates={"content_filter_enabled": True},
                actor=actor
            )

        assert "无权修改租户" in str(exc_info.value)

    def test_none_actor_bypasses_validation(self):
        """Test that None actor bypasses validation (backward compatibility)."""
        service = TenantService()

        # None actor should not raise PermissionError
        # This ensures backward compatibility
        try:
            result = service.update_tenant(
                tenant_id=999,
                updates={"name": "Test"},
                actor=None
            )
            # Will return False because tenant doesn't exist, but no PermissionError
            assert result is False or result is True
        except PermissionError:
            pytest.fail("None actor should bypass validation for backward compatibility")
