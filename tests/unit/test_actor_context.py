"""
Unit tests for ActorContext

Issue #2179: 租户管理员权限模型
"""

import pytest

from app.core.actor_context import ActorContext


class TestActorContext:
    """Test ActorContext class"""

    def test_is_platform_admin_true(self):
        """Test platform admin identification"""
        actor = ActorContext(user_id=1, role="platform_admin", tenant_id=None)
        assert actor.is_platform_admin() is True

    def test_is_platform_admin_false_tenant_admin(self):
        """Test non-platform admin (tenant admin)"""
        actor = ActorContext(user_id=1, role="tenant_admin", tenant_id=1)
        assert actor.is_platform_admin() is False

    def test_is_platform_admin_false_regular_user(self):
        """Test non-platform admin (regular user)"""
        actor = ActorContext(user_id=1, role="user", tenant_id=1)
        assert actor.is_platform_admin() is False

    def test_is_tenant_admin_true(self):
        """Test tenant admin identification"""
        actor = ActorContext(user_id=1, role="tenant_admin", tenant_id=1)
        assert actor.is_tenant_admin() is True

    def test_is_tenant_admin_false_no_tenant_id(self):
        """Test tenant admin without tenant_id"""
        actor = ActorContext(user_id=1, role="tenant_admin", tenant_id=None)
        assert actor.is_tenant_admin() is False

    def test_is_tenant_admin_false_platform_admin(self):
        """Test tenant admin check for platform admin"""
        actor = ActorContext(user_id=1, role="platform_admin", tenant_id=None)
        assert actor.is_tenant_admin() is False

    def test_can_access_tenant_platform_admin_any(self):
        """Test platform admin can access any tenant"""
        actor = ActorContext(user_id=1, role="platform_admin", tenant_id=None)
        assert actor.can_access_tenant(1) is True
        assert actor.can_access_tenant(999) is True
        assert actor.can_access_tenant(None) is True

    def test_can_access_tenant_tenant_admin_own(self):
        """Test tenant admin can access own tenant"""
        actor = ActorContext(user_id=1, role="tenant_admin", tenant_id=1)
        assert actor.can_access_tenant(1) is True

    def test_can_access_tenant_tenant_admin_other(self):
        """Test tenant admin cannot access other tenants"""
        actor = ActorContext(user_id=1, role="tenant_admin", tenant_id=1)
        assert actor.can_access_tenant(2) is False

    def test_can_access_tenant_regular_user(self):
        """Test regular user cannot access tenants"""
        actor = ActorContext(user_id=1, role="user", tenant_id=1)
        assert actor.can_access_tenant(1) is False
        assert actor.can_access_tenant(2) is False

    def test_validate_tenant_admin_without_tenant_id(self):
        """Test validation fails for tenant admin without tenant_id"""
        actor = ActorContext(user_id=1, role="tenant_admin", tenant_id=None)
        errors = actor.validate()
        assert len(errors) == 1
        assert "租户管理员必须有 tenant_id" in errors[0]

    def test_validate_tenant_admin_with_tenant_id(self):
        """Test validation passes for tenant admin with tenant_id"""
        actor = ActorContext(user_id=1, role="tenant_admin", tenant_id=1)
        errors = actor.validate()
        assert len(errors) == 0

    def test_validate_empty_string_tenant_id(self):
        """Test validation fails for empty string tenant_id"""
        actor = ActorContext(user_id=1, role="user", tenant_id="")
        errors = actor.validate()
        assert len(errors) == 1
        assert "tenant_id 不应为空字符串" in errors[0]

    def test_validate_platform_admin_without_tenant_id(self):
        """Test validation passes for platform admin without tenant_id"""
        actor = ActorContext(user_id=1, role="platform_admin", tenant_id=None)
        errors = actor.validate()
        assert len(errors) == 0

    def test_to_dict(self):
        """Test conversion to dictionary"""
        actor = ActorContext(user_id=1, role="tenant_admin", tenant_id=5)
        data = actor.to_dict()
        assert data["user_id"] == 1
        assert data["role"] == "tenant_admin"
        assert data["tenant_id"] == 5

    def test_from_dict(self):
        """Test creation from dictionary"""
        data = {"user_id": 1, "role": "platform_admin", "tenant_id": None}
        actor = ActorContext.from_dict(data)
        assert actor.user_id == 1
        assert actor.role == "platform_admin"
        assert actor.tenant_id is None
