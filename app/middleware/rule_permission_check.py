"""
Open ACE - Rule Permission Check Middleware

负责内容过滤规则操作的权限检查。
"""

import functools
import logging
from collections.abc import Callable

from flask import g, request

logger = logging.getLogger(__name__)


# 角色定义
RULE_ROLES = {
    "creator": "rule_creator",        # 规则创建者
    "approver": "rule_approver",      # 规则审批者
    "admin": "rule_admin",            # 规则管理员
    "system_admin": "system_admin",   # 系统管理员
}


def get_user_role() -> str | None:
    """
    获取当前用户的角色。

    Returns:
        用户角色或None
    """
    if not hasattr(g, "user") or g.user is None:
        return None

    # 检查是否是系统管理员
    if g.user.get("is_platform_admin") or g.user.get("role") == "admin":
        return RULE_ROLES["system_admin"]

    # 检查角色字段
    role = g.user.get("role", "")

    if role in ["rule_admin", "admin"]:
        return RULE_ROLES["admin"]
    elif role == "rule_approver":
        return RULE_ROLES["approver"]
    elif role == "rule_creator":
        return RULE_ROLES["creator"]

    # 默认为创建者
    return RULE_ROLES["creator"]


def check_permission(required_role: str) -> bool:
    """
    检查用户是否有指定角色的权限。

    Args:
        required_role: 需要的角色

    Returns:
        是否有权限
    """
    user_role = get_user_role()
    if user_role is None:
        return False

    # 权限层级：system_admin > admin > approver > creator
    role_hierarchy = [
        RULE_ROLES["creator"],
        RULE_ROLES["approver"],
        RULE_ROLES["admin"],
        RULE_ROLES["system_admin"],
    ]

    try:
        user_level = role_hierarchy.index(user_role)
        required_level = role_hierarchy.index(required_role)
        return user_level >= required_level
    except ValueError:
        return False


def require_role(required_role: str):
    """
    装饰器：要求用户具有指定角色。

    Args:
        required_role: 需要的角色

    Returns:
        装饰器函数
    """
    def decorator(f: Callable) -> Callable:
        @functools.wraps(f)
        def decorated_function(*args, **kwargs):
            if not check_permission(required_role):
                logger.warning(
                    f"Permission denied: user lacks role {required_role}, "
                    f"current role: {get_user_role()}"
                )
                return {
                    "error": "Permission denied",
                    "message": f"Requires {required_role} role"
                }, 403

            return f(*args, **kwargs)

        return decorated_function

    return decorator


def check_self_approval(rule_creator_id: int) -> bool:
    """
    检查是否存在自审自批问题。

    Args:
        rule_creator_id: 规则创建者ID

    Returns:
        True 如果允许（系统管理员或非自己创建），False 如果不允许
    """
    user_role = get_user_role()
    user_id = g.user.get("id") if hasattr(g, "user") and g.user else None

    # 系统管理员可以审批自己创建的规则（紧急审批）
    if user_role == RULE_ROLES["system_admin"]:
        return True

    # 其他角色不能审批自己创建的规则
    return not (user_id and rule_creator_id and int(user_id) == int(rule_creator_id))


def check_tenant_access(rule_tenant_id: int | None) -> bool:
    """
    检查租户访问权限。

    Args:
        rule_tenant_id: 规则所属租户ID

    Returns:
        是否有权限访问
    """
    # 获取当前用户的租户ID
    user_tenant_id = g.user.get("tenant_id") if hasattr(g, "user") and g.user else None

    # 如果规则没有租户ID（全局规则），所有人都可以访问
    if rule_tenant_id is None:
        return True

    # 如果用户没有租户ID，只能访问全局规则
    if user_tenant_id is None:
        return False

    # 租户ID必须匹配
    return int(user_tenant_id) == int(rule_tenant_id)


def require_tenant_access():
    """
    装饰器：要求租户访问权限。

    Returns:
        装饰器函数
    """
    def decorator(f: Callable) -> Callable:
        @functools.wraps(f)
        def decorated_function(*args, **kwargs):
            rule_id = kwargs.get("rule_id") or request.view_args.get("rule_id")

            if rule_id:
                # 查询规则所属租户
                from app.repositories.governance_repo import GovernanceRepository

                repo = GovernanceRepository()
                rule = repo.get_filter_rule(rule_id)

                if rule and not check_tenant_access(rule.get("tenant_id")):
                    logger.warning(
                        f"Cross-tenant access blocked: "
                        f"user_tenant={g.user.get('tenant_id')}, "
                        f"rule_tenant={rule.get('tenant_id')}"
                    )
                    return {"error": "Rule not found"}, 404

            return f(*args, **kwargs)

        return decorated_function

    return decorator


def can_approve_rule(rule: dict) -> tuple[bool, str | None]:
    """
    检查用户是否可以审批规则。

    Args:
        rule: 规则字典

    Returns:
        (是否可以审批, 错误消息)
    """
    # 必须是审批者或更高角色
    if not check_permission(RULE_ROLES["approver"]):
        return False, "Requires approver role or higher"

    # 检查自审自批
    rule_creator_id = rule.get("created_by")
    if not check_self_approval(rule_creator_id):
        return False, "Cannot approve your own rule"

    # 检查租户访问权限
    if not check_tenant_access(rule.get("tenant_id")):
        return False, "Cross-tenant access denied"

    return True, None


def can_edit_rule(rule: dict) -> tuple[bool, str | None]:
    """
    检查用户是否可以编辑规则。

    Args:
        rule: 规则字典

    Returns:
        (是否可以编辑, 错误消息)
    """
    user_role = get_user_role()
    user_id = g.user.get("id") if hasattr(g, "user") and g.user else None

    # 管理员和系统管理员可以编辑任何规则
    if check_permission(RULE_ROLES["admin"]):
        return True, None

    # 审批者可以编辑未审批的规则
    if user_role == RULE_ROLES["approver"]:
        if rule.get("approval_status") != "approved":
            return True, None

    # 创建者只能编辑自己创建的且未审批的规则
    if user_role == RULE_ROLES["creator"]:
        if str(user_id) == str(rule.get("created_by")):
            if rule.get("approval_status") in ["pending", "draft", "rejected"]:
                return True, None

    return False, "Permission denied"


def can_delete_rule(rule: dict) -> tuple[bool, str | None]:
    """
    检查用户是否可以删除规则。

    Args:
        rule: 规则字典

    Returns:
        (是否可以删除, 错误消息)
    """
    # 只有管理员和系统管理员可以删除规则
    if not check_permission(RULE_ROLES["admin"]):
        return False, "Requires admin role"

    # 检查租户访问权限
    if not check_tenant_access(rule.get("tenant_id")):
        return False, "Cross-tenant access denied"

    return True, None


def can_mark_test_rule(rule: dict) -> tuple[bool, str | None]:
    """
    检查用户是否可以标记测试规则。

    Args:
        rule: 规则字典

    Returns:
        (是否可以标记, 错误消息)
    """
    # 只有管理员和系统管理员可以标记测试规则
    if not check_permission(RULE_ROLES["admin"]):
        return False, "Requires admin role"

    # 检查租户访问权限
    if not check_tenant_access(rule.get("tenant_id")):
        return False, "Cross-tenant access denied"

    return True, None
