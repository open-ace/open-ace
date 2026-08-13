"""
Open ACE - Rule Loader Module

负责从数据库加载内容过滤规则，支持多租户过滤。
"""

import logging
from datetime import datetime, timezone
from typing import Any

from app.repositories.governance_repo import GovernanceRepository

logger = logging.getLogger(__name__)


class RuleLoader:
    """
    规则加载器，负责从数据库加载内容过滤规则。

    功能：
    - 从数据库加载规则
    - 支持多租户过滤
    - 支持审批状态过滤
    - 支持测试规则过滤
    - 支持有效期检查
    """

    def __init__(self, governance_repo: GovernanceRepository | None = None):
        """
        初始化规则加载器。

        Args:
            governance_repo: 治理数据仓库实例
        """
        self.governance_repo = governance_repo

    def load_rules(
        self,
        tenant_id: int | None = None,
        include_test: bool = False,
        approval_status: str | None = "approved",
    ) -> list[dict[str, Any]]:
        """
        从数据库加载规则。

        Args:
            tenant_id: 租户ID（用于多租户隔离）
            include_test: 是否包含测试规则
            approval_status: 审批状态过滤（默认只加载已审批的规则）

        Returns:
            规则列表
        """
        if self.governance_repo is None:
            logger.warning("Governance repository not initialized")
            return []

        try:
            # 加载所有规则
            all_rules = self.governance_repo.get_filter_rules()

            # 应用过滤条件
            filtered_rules = self._filter_rules(
                all_rules,
                tenant_id=tenant_id,
                include_test=include_test,
                approval_status=approval_status,
            )

            # 检查有效期
            valid_rules = self._check_validity(filtered_rules)

            # 按优先级排序
            sorted_rules = self._sort_by_priority(valid_rules)

            logger.debug(
                f"Loaded {len(sorted_rules)} rules "
                f"(tenant_id={tenant_id}, include_test={include_test}, "
                f"approval_status={approval_status})"
            )

            return sorted_rules

        except Exception as e:
            logger.error(f"Failed to load filter rules: {e}")
            return []

    def _filter_rules(
        self,
        rules: list[dict[str, Any]],
        tenant_id: int | None = None,
        include_test: bool = False,
        approval_status: str | None = "approved",
    ) -> list[dict[str, Any]]:
        """
        过滤规则。

        Args:
            rules: 原始规则列表
            tenant_id: 租户ID
            include_test: 是否包含测试规则
            approval_status: 审批状态

        Returns:
            过滤后的规则列表
        """
        filtered = []

        for rule in rules:
            # 1. 检查是否启用
            if not rule.get("is_enabled", True):
                continue

            # 2. 检查是否为测试规则
            if not include_test and rule.get("is_test", False):
                continue

            # 3. 检查审批状态
            if approval_status:
                if rule.get("approval_status") != approval_status:
                    continue

            # 4. 检查租户隔离
            if tenant_id is not None:
                rule_tenant_id = rule.get("tenant_id")
                # 如果规则有租户ID，必须匹配
                if rule_tenant_id is not None and rule_tenant_id != tenant_id:
                    continue
                # 如果规则没有租户ID（全局规则），对所有租户可见
                # 如果请求没有指定租户ID，只加载全局规则

            filtered.append(rule)

        return filtered

    def _check_validity(self, rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        检查规则的有效期。

        Args:
            rules: 规则列表

        Returns:
            有效的规则列表
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        valid_rules = []

        for rule in rules:
            valid_from = rule.get("valid_from")
            valid_until = rule.get("valid_until")

            # 如果没有设置有效期，规则始终有效
            if valid_from is None and valid_until is None:
                valid_rules.append(rule)
                continue

            # 检查生效时间
            if valid_from is not None:
                # 处理时区问题
                valid_from_dt = valid_from if isinstance(valid_from, datetime) else valid_from
                if now < valid_from_dt:
                    # 规则尚未生效
                    continue

            # 检查失效时间
            if valid_until is not None:
                valid_until_dt = valid_until if isinstance(valid_until, datetime) else valid_until
                if now > valid_until_dt:
                    # 规则已过期
                    continue

            valid_rules.append(rule)

        return valid_rules

    def _sort_by_priority(self, rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        按优先级排序规则（数值越小优先级越高）。

        Args:
            rules: 规则列表

        Returns:
            排序后的规则列表
        """
        return sorted(rules, key=lambda r: r.get("priority", 100))

    def get_rule_by_id(self, rule_id: int, tenant_id: int | None = None) -> dict[str, Any] | None:
        """
        根据ID获取单个规则。

        Args:
            rule_id: 规则ID
            tenant_id: 租户ID

        Returns:
            规则字典或None
        """
        if self.governance_repo is None:
            return None

        try:
            rule = self.governance_repo.get_filter_rule(rule_id)

            if rule is None:
                return None

            # 检查租户隔离
            if tenant_id is not None:
                rule_tenant_id = rule.get("tenant_id")
                if rule_tenant_id is not None and rule_tenant_id != tenant_id:
                    # 跨租户访问
                    logger.warning(
                        f"Cross-tenant access blocked: "
                        f"rule_tenant_id={rule_tenant_id}, request_tenant_id={tenant_id}"
                    )
                    return None

            return rule

        except Exception as e:
            logger.error(f"Failed to get rule {rule_id}: {e}")
            return None
