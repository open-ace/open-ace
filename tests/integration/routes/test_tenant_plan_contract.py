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
    expected_plans = {"free", "standard", "premium", "enterprise"}
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
        "daily_token_limit",
        "monthly_token_limit",
        "daily_request_limit",
        "monthly_request_limit",
        "max_users",
        "max_sessions_per_user",
    ]

    for plan_name, quota_config in TenantService.PLAN_QUOTAS.items():
        quota_dict = quota_config.to_dict()
        for field in required_fields:
            assert field in quota_dict, (
                f"Plan '{plan_name}' is missing required field '{field}'. "
                f"Available fields: {list(quota_dict.keys())}"
            )


def test_free_plan_exists():
    """
    验证 free 套餐存在且有合理的默认值。

    Issue #3137: free 套餐应该有合理的配额限制，
    作为基础套餐，其限制应该低于 standard 套餐。
    """
    assert "free" in TenantService.PLAN_QUOTAS, "free plan should exist in PLAN_QUOTAS"

    free_quota = TenantService.PLAN_QUOTAS["free"].to_dict()
    standard_quota = TenantService.PLAN_QUOTAS["standard"].to_dict()

    # free 套餐的各项限制应该低于或等于 standard 套餐
    assert (
        free_quota["daily_token_limit"] <= standard_quota["daily_token_limit"]
    ), "free plan daily_token_limit should be <= standard"
    assert (
        free_quota["monthly_token_limit"] <= standard_quota["monthly_token_limit"]
    ), "free plan monthly_token_limit should be <= standard"
    assert (
        free_quota["daily_request_limit"] <= standard_quota["daily_request_limit"]
    ), "free plan daily_request_limit should be <= standard"
    assert (
        free_quota["monthly_request_limit"] <= standard_quota["monthly_request_limit"]
    ), "free plan monthly_request_limit should be <= standard"
    assert (
        free_quota["max_users"] <= standard_quota["max_users"]
    ), "free plan max_users should be <= standard"
