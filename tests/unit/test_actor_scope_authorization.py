"""
Unit tests for ActorScope and tenant authorization primitives.

Issue #2327: API Key 管理 Tenant 授权修复。
"""

import pytest
from app.auth.decorators import (
    ActorScope,
    resolve_authorized_target_tenant,
    require_actor_scope,
)


class TestActorScope:
    """测试 ActorScope 数据类的验证方法"""

    def test_valid_actor_scope(self):
        """测试有效的 ActorScope 构造"""
        scope = ActorScope(
            user_id=1,
            role="tenant_admin",
            actor_tenant_id=1,
            target_tenant_id=1,
            is_cross_tenant=False,
            request_id="test-request-id",
        )
        assert scope.user_id == 1
        assert scope.role == "tenant_admin"
        assert scope.target_tenant_id == 1
        assert scope.is_cross_tenant is False

    def test_validate_for_read_success(self):
        """测试读操作验证成功"""
        scope = ActorScope(
            user_id=1,
            role="tenant_admin",
            actor_tenant_id=1,
            target_tenant_id=1,
            is_cross_tenant=False,
            request_id=None,
        )
        # 应该不抛异常
        scope.validate_for_read()

    def test_validate_for_write_success(self):
        """测试写操作验证成功"""
        scope = ActorScope(
            user_id=1,
            role="platform_admin",
            actor_tenant_id=None,
            target_tenant_id=1,
            is_cross_tenant=True,
            request_id="test-id",
        )
        # 应该不抛异常
        scope.validate_for_write()

    def test_validate_invalid_user_id(self):
        """测试无效的 user_id"""
        scope = ActorScope(
            user_id=0,
            role="tenant_admin",
            actor_tenant_id=1,
            target_tenant_id=1,
            is_cross_tenant=False,
            request_id=None,
        )
        with pytest.raises(ValueError, match="Invalid user_id"):
            scope.validate_for_read()

    def test_validate_invalid_role(self):
        """测试无效的 role"""
        scope = ActorScope(
            user_id=1,
            role="user",
            actor_tenant_id=1,
            target_tenant_id=1,
            is_cross_tenant=False,
            request_id=None,
        )
        with pytest.raises(ValueError, match="Invalid role"):
            scope.validate_for_read()

    def test_validate_invalid_target_tenant_id(self):
        """测试无效的 target_tenant_id"""
        scope = ActorScope(
            user_id=1,
            role="tenant_admin",
            actor_tenant_id=1,
            target_tenant_id=0,
            is_cross_tenant=False,
            request_id=None,
        )
        with pytest.raises(ValueError, match="Invalid target_tenant_id"):
            scope.validate_for_read()

    def test_from_actor_and_target_success(self):
        """测试工厂方法成功构造"""
        actor = {
            "id": 1,
            "role": "tenant_admin",
            "tenant_id": 1,
        }
        scope = ActorScope.from_actor_and_target(actor, target_tenant_id=1)
        assert scope.user_id == 1
        assert scope.role == "tenant_admin"
        assert scope.actor_tenant_id == 1
        assert scope.target_tenant_id == 1
        assert scope.is_cross_tenant is False

    def test_from_actor_and_target_cross_tenant(self):
        """测试工厂方法识别跨租户操作"""
        actor = {
            "id": 1,
            "role": "platform_admin",
            "tenant_id": 1,
        }
        scope = ActorScope.from_actor_and_target(actor, target_tenant_id=2)
        assert scope.is_cross_tenant is True
        assert scope.target_tenant_id == 2

    def test_from_actor_and_target_invalid_user_id(self):
        """测试工厂方法验证无效 user_id"""
        actor = {
            "id": 0,
            "role": "tenant_admin",
            "tenant_id": 1,
        }
        with pytest.raises(ValueError, match="Invalid actor user_id"):
            ActorScope.from_actor_and_target(actor, target_tenant_id=1)

    def test_from_actor_and_target_missing_role(self):
        """测试工厂方法验证缺少 role"""
        actor = {
            "id": 1,
            "tenant_id": 1,
        }
        with pytest.raises(ValueError, match="Actor role is required"):
            ActorScope.from_actor_and_target(actor, target_tenant_id=1)

    def test_actor_scope_is_immutable(self):
        """测试 ActorScope 不可变"""
        scope = ActorScope(
            user_id=1,
            role="tenant_admin",
            actor_tenant_id=1,
            target_tenant_id=1,
            is_cross_tenant=False,
            request_id=None,
        )
        with pytest.raises(AttributeError):
            scope.user_id = 2


class TestResolveAuthorizedTargetTenant:
    """测试 resolve_authorized_target_tenant 函数"""

    def test_tenant_admin_matching_tenant(self):
        """测试 tenant_admin 匹配租户"""
        actor = {
            "id": 1,
            "role": "tenant_admin",
            "tenant_id": 1,
        }
        target_tenant_id, error = resolve_authorized_target_tenant(actor, requested_tenant_id=1)
        assert target_tenant_id == 1
        assert error is None

    def test_tenant_admin_no_requested_tenant(self):
        """测试 tenant_admin 不提供 tenant_id 时使用 actor tenant"""
        actor = {
            "id": 1,
            "role": "tenant_admin",
            "tenant_id": 1,
        }
        target_tenant_id, error = resolve_authorized_target_tenant(actor, requested_tenant_id=None)
        assert target_tenant_id == 1
        assert error is None

    def test_tenant_admin_cross_tenant_denied(self):
        """测试 tenant_admin 跨租户访问被拒绝"""
        actor = {
            "id": 1,
            "role": "tenant_admin",
            "tenant_id": 1,
        }
        target_tenant_id, error = resolve_authorized_target_tenant(actor, requested_tenant_id=2)
        assert target_tenant_id is None
        assert "denied" in error.lower()

    def test_tenant_admin_without_tenant_id(self):
        """测试 tenant_admin 没有 tenant_id"""
        actor = {
            "id": 1,
            "role": "tenant_admin",
            "tenant_id": None,
        }
        target_tenant_id, error = resolve_authorized_target_tenant(actor, requested_tenant_id=None)
        assert target_tenant_id is None
        assert "must have tenant_id" in error.lower()

    def test_platform_admin_explicit_tenant(self):
        """测试 platform_admin 显式指定 tenant_id"""
        actor = {
            "id": 1,
            "role": "platform_admin",
            "tenant_id": None,
        }
        target_tenant_id, error = resolve_authorized_target_tenant(actor, requested_tenant_id=1)
        assert target_tenant_id == 1
        assert error is None

    def test_platform_admin_missing_tenant(self):
        """测试 platform_admin 缺少 tenant_id"""
        actor = {
            "id": 1,
            "role": "platform_admin",
            "tenant_id": None,
        }
        target_tenant_id, error = resolve_authorized_target_tenant(actor, requested_tenant_id=None)
        assert target_tenant_id is None
        assert "required" in error.lower()

    def test_platform_admin_invalid_tenant_id_negative(self):
        """测试 platform_admin 无效 tenant_id（负数）"""
        actor = {
            "id": 1,
            "role": "platform_admin",
            "tenant_id": None,
        }
        target_tenant_id, error = resolve_authorized_target_tenant(actor, requested_tenant_id=-1)
        assert target_tenant_id is None
        assert "Invalid" in error

    def test_platform_admin_invalid_tenant_id_zero(self):
        """测试 platform_admin 无效 tenant_id（0）"""
        actor = {
            "id": 1,
            "role": "platform_admin",
            "tenant_id": None,
        }
        target_tenant_id, error = resolve_authorized_target_tenant(actor, requested_tenant_id=0)
        assert target_tenant_id is None
        assert "Invalid" in error

    def test_legacy_admin_explicit_tenant(self):
        """测试 legacy admin 显式指定 tenant_id"""
        actor = {
            "id": 1,
            "role": "admin",
            "tenant_id": None,
        }
        target_tenant_id, error = resolve_authorized_target_tenant(actor, requested_tenant_id=1)
        assert target_tenant_id == 1
        assert error is None

    def test_legacy_admin_missing_tenant(self):
        """测试 legacy admin 缺少 tenant_id"""
        actor = {
            "id": 1,
            "role": "admin",
            "tenant_id": None,
        }
        target_tenant_id, error = resolve_authorized_target_tenant(actor, requested_tenant_id=None)
        assert target_tenant_id is None
        assert "required" in error.lower()

    def test_non_admin_role_denied(self):
        """测试非管理员角色被拒绝"""
        actor = {
            "id": 1,
            "role": "user",
            "tenant_id": 1,
        }
        target_tenant_id, error = resolve_authorized_target_tenant(actor, requested_tenant_id=1)
        assert target_tenant_id is None
        assert "Admin access required" in error


class TestRequireActorScope:
    """测试 @require_actor_scope 装饰器"""

    def test_decorator_with_valid_scope(self):
        """测试装饰器验证有效的 ActorScope"""
        from app.auth.decorators import require_actor_scope

        @require_actor_scope()
        def mock_service_method(self, scope):
            return "success"

        scope = ActorScope(
            user_id=1,
            role="tenant_admin",
            actor_tenant_id=1,
            target_tenant_id=1,
            is_cross_tenant=False,
            request_id=None,
        )

        result = mock_service_method(None, scope)
        assert result == "success"

    def test_decorator_with_invalid_type(self):
        """测试装饰器拒绝非 ActorScope 类型"""
        from app.auth.decorators import require_actor_scope

        @require_actor_scope()
        def mock_service_method(self, scope):
            return "success"

        with pytest.raises(TypeError, match="must receive ActorScope"):
            mock_service_method(None, "not_a_scope")

    def test_decorator_with_invalid_scope(self):
        """测试装饰器拒绝无效的 ActorScope"""
        from app.auth.decorators import require_actor_scope

        @require_actor_scope()
        def mock_service_method(self, scope):
            return "success"

        scope = ActorScope(
            user_id=0,  # Invalid
            role="tenant_admin",
            actor_tenant_id=1,
            target_tenant_id=1,
            is_cross_tenant=False,
            request_id=None,
        )

        with pytest.raises(ValueError, match="Invalid user_id"):
            mock_service_method(None, scope)

    def test_decorator_require_write_false(self):
        """测试装饰器只要求读权限"""
        from app.auth.decorators import require_actor_scope

        @require_actor_scope(require_write=False)
        def mock_read_method(self, scope):
            return "success"

        scope = ActorScope(
            user_id=1,
            role="tenant_admin",
            actor_tenant_id=1,
            target_tenant_id=1,
            is_cross_tenant=False,
            request_id=None,
        )

        result = mock_read_method(None, scope)
        assert result == "success"