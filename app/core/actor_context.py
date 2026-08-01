"""
Actor Context - 操作者上下文封装

封装操作者信息，解决 Service 层参数列表过长问题。

Issue #2179: 租户管理员权限模型
"""

from dataclasses import dataclass


@dataclass
class ActorContext:
    """操作者上下文

    封装操作者的身份信息，用于 Service 层权限验证。
    """

    user_id: int
    role: str
    tenant_id: int | None = None

    def is_platform_admin(self) -> bool:
        """是否为平台管理员

        Returns:
            bool: True 如果是平台管理员
        """
        return self.role == "platform_admin"

    def is_tenant_admin(self) -> bool:
        """是否为租户管理员

        租户管理员必须有 tenant_id

        Returns:
            bool: True 如果是租户管理员且有 tenant_id
        """
        return self.role == "tenant_admin" and self.tenant_id is not None

    def can_access_tenant(self, target_tenant_id: int | None) -> bool:
        """是否有权访问指定租户

        权限规则：
        - platform_admin：可访问任意租户
        - tenant_admin：仅可访问自己的租户
        - 其他：无权访问

        Args:
            target_tenant_id: 目标租户ID

        Returns:
            bool: True 如果有权访问
        """
        if self.is_platform_admin():
            return True

        if self.is_tenant_admin():
            return self.tenant_id == target_tenant_id

        return False

    def validate(self) -> list[str]:
        """验证数据一致性，返回错误列表

        检查角色与 tenant_id 的一致性

        Returns:
            list[str]: 错误消息列表
        """
        errors = []

        # 租户管理员必须有 tenant_id
        if self.role == "tenant_admin" and self.tenant_id is None:
            errors.append("租户管理员必须有 tenant_id")

        # tenant_id 为空字符串时应规范化为 None
        if self.tenant_id == "":
            errors.append("tenant_id 不应为空字符串，请使用 NULL")

        return errors

    def to_dict(self) -> dict:
        """转换为字典

        Returns:
            dict: Actor 上下文字典
        """
        return {
            "user_id": self.user_id,
            "role": self.role,
            "tenant_id": self.tenant_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ActorContext":
        """从字典创建

        Args:
            data: 包含 actor 信息的字典

        Returns:
            ActorContext: Actor 上下文实例
        """
        return cls(
            user_id=data.get("user_id"),
            role=data.get("role"),
            tenant_id=data.get("tenant_id"),
        )

    @classmethod
    def from_flask_g(cls) -> "ActorContext":
        """从 Flask g 对象创建

        Returns:
            ActorContext: Actor 上下文实例

        Raises:
            ValueError: 如果缺少必需的字段
        """
        from flask import g

        user = getattr(g, 'user', {})
        if not user:
            raise ValueError("Flask g.user 未设置")

        user_id = user.get('id') or g.get('user_id')
        role = user.get('role') or g.get('user_role')
        tenant_id = user.get('tenant_id') or g.get('tenant_id')

        if user_id is None:
            raise ValueError("缺少 user_id")
        if role is None:
            raise ValueError("缺少 role")

        return cls(
            user_id=user_id,
            role=role,
            tenant_id=tenant_id,
        )
