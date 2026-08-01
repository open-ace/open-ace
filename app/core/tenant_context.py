"""
Tenant Context Management - Fail-Closed Mechanism

Provides centralized tenant context access with explicit validation.
Eliminates silent fallback to tenant_id=1 patterns.

Issue #2179: 租户管理员权限模型
"""

from typing import Optional


class TenantContextError(Exception):
    """租户上下文缺失异常"""

    def __init__(self, message: str = "租户上下文缺失"):
        self.message = message
        super().__init__(self.message)


class TenantContext:
    """租户上下文管理（Fail-Closed）

    禁止静默回退到任何默认值。
    所有租户相关操作必须显式获取 tenant_id。
    """

    @staticmethod
    def get_required_tenant_id() -> int:
        """
        获取当前租户ID，缺失时抛出异常

        禁止静默回退到任何默认值

        Raises:
            TenantContextError: 租户上下文缺失

        Returns:
            int: 当前租户ID
        """
        from flask import g

        tenant_id = getattr(g, 'tenant_id', None)

        if tenant_id is None:
            # 明确禁止的模式
            # ❌ return 1  # 禁止静默回退
            # ❌ return g.user.get('tenant_id', 1)  # 禁止静默回退

            raise TenantContextError(
                "租户上下文缺失。"
                "请确保：\n"
                "1. 请求已通过权限装饰器验证\n"
                "2. 用户已正确分配 tenant_id\n"
                "3. 非平台管理员操作必须有租户归属"
            )

        return tenant_id

    @staticmethod
    def get_optional_tenant_id() -> Optional[int]:
        """
        获取当前租户ID（可选）

        用于平台管理员操作等允许无租户上下文的场景

        Returns:
            Optional[int]: 租户ID或None
        """
        from flask import g
        return getattr(g, 'tenant_id', None)

    @staticmethod
    def set_tenant_id(tenant_id: Optional[int]) -> None:
        """
        设置当前请求的租户ID

        Args:
            tenant_id: 租户ID，可以为None（平台管理员）
        """
        from flask import g
        g.tenant_id = tenant_id