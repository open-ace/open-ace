"""
Tenant Plan Contract Test - Issue #3137

确保后端套餐集合与前端类型定义保持一致。
"""

import pytest

from app.services.tenant_service import TenantService


def test_backend_plan_quotas_match_expected_plans():
    """
    验证后端 PLAN_QUOTAS 包含预期的所有套餐。

    前端 TenantPlan 类型定义：
    'free' | 'standard' | 'premium' | 'enterprise'
    """
    expected_plans = {'free', 'standard', 'premium', 'enterprise'}
    backend_plans = set(TenantService.PLAN_QUOTAS.keys())

    assert backend_plans == expected_plans, (
        f"Backend plans {backend_plans} do not match expected plans {expected_plans}. "
        f"Missing: {expected_plans - backend_plans}, Extra: {backend_plans - expected_plans}"
    )


def test_backend_plan_quotas_not_empty():
    """
    验证后端 PLAN_QUOTAS 不为空。
    """
    assert len(TenantService.PLAN_QUOTAS) > 0, "PLAN_QUOTAS should not be empty"


def test_each_plan_has_quota_config():
    """
    验证每个套餐都有完整的配额配置。
    """
    required_fields = [
        'daily_token_limit',
        'monthly_token_limit',
        'daily_request_limit',
        'monthly_request_limit',
        'max_users',
        'max_sessions_per_user',
    ]

    for plan_name, quota_config in TenantService.PLAN_QUOTAS.items():
        quota_dict = quota_config.to_dict()
        for field in required_fields:
            assert field in quota_dict, (
                f"Plan '{plan_name}' missing required field '{field}' in quota config"
            )
            assert isinstance(quota_dict[field], int), (
                f"Plan '{plan_name}' field '{field}' should be int, got {type(quota_dict[field])}"
            )
            assert quota_dict[field] > 0, (
                f"Plan '{plan_name}' field '{field}' should be positive, got {quota_dict[field]}"
            )


def test_free_plan_exists_and_has_lowest_limits():
    """
    验证 free 套餐存在且具有最低的配额限制。
    """
    assert 'free' in TenantService.PLAN_QUOTAS, "free plan should exist in PLAN_QUOTAS"

    free_quota = TenantService.PLAN_QUOTAS['free'].to_dict()
    standard_quota = TenantService.PLAN_QUOTAS['standard'].to_dict()

    # Free plan should have lower limits than standard
    assert free_quota['daily_token_limit'] < standard_quota['daily_token_limit'], (
        f"Free plan daily_token_limit ({free_quota['daily_token_limit']}) "
        f"should be lower than standard ({standard_quota['daily_token_limit']})"
    )
    assert free_quota['max_users'] < standard_quota['max_users'], (
        f"Free plan max_users ({free_quota['max_users']}) "
        f"should be lower than standard ({standard_quota['max_users']})"
    )


def test_plan_ordering_by_value():
    """
    验证套餐按价值排序（free -> standard -> premium -> enterprise）。
    Enterprise 应该有最高的配额限制。
    """
    plans = list(TenantService.PLAN_QUOTAS.keys())
    expected_order = ['free', 'standard', 'premium', 'enterprise']

    assert plans == expected_order, (
        f"Plans should be ordered by value: {expected_order}, got {plans}"
    )

    # Enterprise should have highest limits
    enterprise_quota = TenantService.PLAN_QUOTAS['enterprise'].to_dict()
    for plan_name, quota_config in TenantService.PLAN_QUOTAS.items():
        if plan_name != 'enterprise':
            quota_dict = quota_config.to_dict()
            assert enterprise_quota['monthly_token_limit'] >= quota_dict['monthly_token_limit'], (
                f"Enterprise plan should have highest monthly_token_limit"
            )