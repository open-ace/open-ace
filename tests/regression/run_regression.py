#!/usr/bin/env python3
"""
回归测试运行器

运行所有回归测试并生成报告。
使用方法：
    python tests/regression/run_regression.py

测试文件命名规范：
    test_模式_一级菜单_二级菜单.py
    例如：test_manage_governance_audit.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from datetime import datetime


# 测试模块配置
TEST_MODULES = [
    # 登录和导航
    ("登录功能", "test_login"),
    ("导航功能", "test_navigation"),
    # Manage 模式 - Overview
    ("Manage - Overview - Dashboard", "test_manage_overview_dashboard"),
    # Manage 模式 - Analysis
    ("Manage - Analysis - Trend", "test_manage_analysis_trend"),
    ("Manage - Analysis - Anomaly", "test_manage_analysis_anomaly"),
    ("Manage - Analysis - ROI", "test_manage_analysis_roi"),
    ("Manage - Analysis - Conversation History", "test_manage_analysis_conversation_history"),
    ("Manage - Analysis - Messages", "test_manage_analysis_messages"),
    # Manage 模式 - Governance
    ("Manage - Governance - Audit", "test_manage_governance_audit"),
    ("Manage - Governance - Quota", "test_manage_governance_quota"),
    ("Manage - Governance - Compliance", "test_manage_governance_compliance"),
    ("Manage - Governance - Security", "test_manage_governance_security"),
    # Manage 模式 - Users
    ("Manage - Users - Management", "test_manage_users_management"),
    ("Manage - Users - Tenants", "test_manage_users_tenants"),
    # Manage 模式 - Settings
    ("Manage - Settings - SSO", "test_manage_settings_sso"),
    # Work 模式
    ("Work - Workspace", "test_work_workspace"),
    ("Work - Sessions", "test_work_sessions"),
    ("Work - Prompts", "test_work_prompts"),
]


def run_all_regression_tests():
    """运行所有回归测试"""
    print("\n" + "=" * 70)
    print("Open ACE 回归测试套件")
    print(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    all_results = []

    for module_name, module_file in TEST_MODULES:
        try:
            module_path = os.path.join(os.path.dirname(__file__), module_file + ".py")
            if os.path.exists(module_path):
                # 动态导入模块
                import importlib.util

                spec = importlib.util.spec_from_file_location(module_file, module_path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                # 运行测试
                if hasattr(module, "run_all_tests"):
                    results = module.run_all_tests()
                    all_results.extend(results)
            else:
                print(f"警告: 测试模块 {module_file} 不存在")
                all_results.append((module_name, "SKIP", "Module not found"))
        except Exception as e:
            print(f"错误: 运行 {module_name} 测试时出错: {e}")
            all_results.append((module_name, "ERROR", str(e)))

    # 生成总结报告
    print("\n" + "=" * 70)
    print("回归测试总结")
    print("=" * 70)

    passed = sum(1 for r in all_results if r[1] == "PASS")
    failed = sum(1 for r in all_results if r[1] == "FAIL")
    errors = sum(1 for r in all_results if r[1] == "ERROR")
    skipped = sum(1 for r in all_results if r[1] == "SKIP")
    total = len(all_results)

    print(f"\n总计: {total} 个测试")
    print(f"  ✓ 通过: {passed}")
    print(f"  ✗ 失败: {failed}")
    print(f"  ! 错误: {errors}")
    print(f"  - 跳过: {skipped}")

    if failed > 0 or errors > 0:
        print("\n失败的测试:")
        for name, status, error in all_results:
            if status in ("FAIL", "ERROR"):
                print(f"  - {name}: {error}")

    print("\n" + "-" * 70)
    success_rate = (passed / total * 100) if total > 0 else 0
    print(f"成功率: {success_rate:.1f}%")
    print("-" * 70)

    return all_results


if __name__ == "__main__":
    results = run_all_regression_tests()

    # 如果有失败，返回非零退出码
    failed = sum(1 for r in results if r[1] in ("FAIL", "ERROR"))
    sys.exit(0 if failed == 0 else 1)
