"""
Unit tests for User model extensions

Issue #2179: 租户管理员权限模型
"""

import pytest

from app.models.user import User


class TestUserModelExtensions:
    """Test User model permission methods"""

    def test_is_platform_admin_true(self):
        """Test platform admin identification"""
        user = User(id=1, role="platform_admin", tenant_id=None)
        assert user.is_platform_admin() is True

    def test_is_platform_admin_false_admin(self):
        """Test platform admin check for legacy admin role"""
        user = User(id=1, role="admin", tenant_id=None)
        # Legacy admin is NOT platform admin
        assert user.is_platform_admin() is False

    def test_is_platform_admin_false_tenant_admin(self):
        """Test platform admin check for tenant admin"""
        user = User(id=1, role="tenant_admin", tenant_id=1)
        assert user.is_platform_admin() is False

    def test_is_tenant_admin_true(self):
        """Test tenant admin identification"""
        user = User(id=1, role="tenant_admin", tenant_id=1)
        assert user.is_tenant_admin() is True

    def test_is_tenant_admin_false_no_tenant_id(self):
        """Test tenant admin without tenant_id"""
        user = User(id=1, role="tenant_admin", tenant_id=None)
        assert user.is_tenant_admin() is False

    def test_is_tenant_admin_false_platform_admin(self):
        """Test tenant admin check for platform admin"""
        user = User(id=1, role="platform_admin", tenant_id=None)
        assert user.is_tenant_admin() is False

    def test_is_admin_platform_admin(self):
        """Test is_admin includes platform_admin"""
        user = User(id=1, role="platform_admin", tenant_id=None)
        assert user.is_admin() is True

    def test_is_admin_tenant_admin(self):
        """Test is_admin includes tenant_admin"""
        user = User(id=1, role="tenant_admin", tenant_id=1)
        assert user.is_admin() is True

    def test_is_admin_legacy_admin(self):
        """Test is_admin includes legacy admin"""
        user = User(id=1, role="admin", tenant_id=None)
        assert user.is_admin() is True

    def test_is_admin_regular_user(self):
        """Test is_admin for regular user"""
        user = User(id=1, role="user", tenant_id=1)
        assert user.is_admin() is False

    def test_can_access_tenant_platform_admin_any(self):
        """Test platform admin can access any tenant"""
        user = User(id=1, role="platform_admin", tenant_id=None)
        assert user.can_access_tenant(1) is True
        assert user.can_access_tenant(999) is True
        assert user.can_access_tenant(None) is True

    def test_can_access_tenant_tenant_admin_own(self):
        """Test tenant admin can access own tenant"""
        user = User(id=1, role="tenant_admin", tenant_id=1)
        assert user.can_access_tenant(1) is True

    def test_can_access_tenant_tenant_admin_other(self):
        """Test tenant admin cannot access other tenants"""
        user = User(id=1, role="tenant_admin", tenant_id=1)
        assert user.can_access_tenant(2) is False
        assert user.can_access_tenant(None) is False

    def test_can_access_tenant_regular_user(self):
        """Test regular user cannot access tenants"""
        user = User(id=1, role="user", tenant_id=1)
        assert user.can_access_tenant(1) is False
        assert user.can_access_tenant(2) is False

    def test_validate_role_tenant_consistency_tenant_admin_without_tenant_id(self):
        """Test validation fails for tenant admin without tenant_id"""
        user = User(id=1, role="tenant_admin", tenant_id=None)
        errors = user.validate_role_tenant_consistency()
        assert len(errors) == 1
        assert "租户管理员必须有 tenant_id" in errors[0]

    def test_validate_role_tenant_consistency_tenant_admin_with_tenant_id(self):
        """Test validation passes for tenant admin with tenant_id"""
        user = User(id=1, role="tenant_admin", tenant_id=1)
        errors = user.validate_role_tenant_consistency()
        assert len(errors) == 0

    def test_validate_role_tenant_consistency_empty_string_tenant_id(self):
        """Test validation fails for empty string tenant_id"""
        user = User(id=1, role="user", tenant_id="")
        errors = user.validate_role_tenant_consistency()
        assert len(errors) == 1
        assert "tenant_id 不应为空字符串" in errors[0]

    def test_validate_role_tenant_consistency_platform_admin_without_tenant_id(self):
        """Test validation passes for platform admin without tenant_id"""
        user = User(id=1, role="platform_admin", tenant_id=None)
        errors = user.validate_role_tenant_consistency()
        assert len(errors) == 0

    def test_validate_role_tenant_consistency_regular_user(self):
        """Test validation passes for regular user with tenant_id"""
        user = User(id=1, role="user", tenant_id=1)
        errors = user.validate_role_tenant_consistency()
        assert len(errors) == 0
