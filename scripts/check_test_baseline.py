#!/usr/bin/env python3
"""
检查测试 baseline

Issue #2189: 三层阈值机制检查

功能：
1. 读取 .github/test_baseline.json
2. 对比实际收集数量与阈值
3. 三层判断逻辑：
   - 低于 hard_minimum：失败 CI
   - 低于 warning_threshold：发出警告
   - 正常范围：通过
4. 输出检查结果和详细报告

用法：
    python scripts/check_test_baseline.py --category default_tests --actual-count 7254
    python scripts/check_test_baseline.py --all-categories
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASELINE_FILE = PROJECT_ROOT / ".github" / "test_baseline.json"


def load_baseline() -> dict[str, Any]:
    """加载 baseline 配置"""
    if not BASELINE_FILE.exists():
        # 返回默认配置
        return {
            "default_tests": {
                "hard_minimum": 500,
                "baseline": 3956,
                "warning_threshold": 3500,
                "last_updated": "2026-08-03",
                "update_mode": "auto",
                "auto_update_limit": 10,
            },
            "critical_e2e": {
                "hard_minimum": 3,
                "baseline": 5,
                "warning_threshold": 4,
                "last_updated": "2026-08-03",
                "update_mode": "manual",
            },
            "full_e2e": {
                "hard_minimum": 50,
                "baseline": 74,
                "warning_threshold": 66,
                "last_updated": "2026-08-03",
                "update_mode": "manual",
            },
            "issue_tests": {
                "hard_minimum": 20,
                "baseline": 27,
                "warning_threshold": 24,
                "last_updated": "2026-08-03",
                "update_mode": "manual",
            },
        }

    content = BASELINE_FILE.read_text(encoding="utf-8")
    return json.loads(content)


def check_category(
    category: str,
    actual_count: int,
    baseline_data: dict[str, Any],
    verbose: bool = False,
) -> tuple[str, str]:
    """
    检查单个测试类别的 baseline

    返回: (状态, 消息)
    状态: "pass", "warning", "fail"
    """
    config = baseline_data.get(category, {})
    if not config:
        return "warning", f"Category '{category}' not found in baseline configuration"

    hard_minimum = config.get("hard_minimum", 1)
    baseline = config.get("baseline", actual_count)
    warning_threshold = config.get("warning_threshold", hard_minimum)
    last_updated = config.get("last_updated", "unknown")

    if verbose:
        print(f"\n检查 {category}:")
        print(f"  实际数量: {actual_count}")
        print(f"  硬性底线: {hard_minimum}")
        print(f"  基准值: {baseline}")
        print(f"  警告阈值: {warning_threshold}")
        print(f"  最后更新: {last_updated}")

    # 三层判断逻辑
    if actual_count < hard_minimum:
        return (
            "fail",
            f"{category}: {actual_count} < hard_minimum {hard_minimum} (CRITICAL FAILURE)",
        )
    elif actual_count < warning_threshold:
        return (
            "warning",
            f"{category}: {actual_count} < warning_threshold {warning_threshold} (WARNING)",
        )
    elif actual_count < baseline:
        # 低于 baseline 但高于 warning_threshold
        decrease_pct = ((baseline - actual_count) / baseline) * 100
        return (
            "warning",
            f"{category}: {actual_count} < baseline {baseline} (decreased by {decrease_pct:.1f}%)",
        )
    else:
        return "pass", f"{category}: {actual_count} >= baseline {baseline} (PASS)"


def main(argv: list[str] | None = None) -> int:
    """主函数"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--category",
        "-c",
        help="测试类别名称",
    )
    parser.add_argument(
        "--actual-count",
        "-n",
        type=int,
        help="实际测试数量",
    )
    parser.add_argument(
        "--all-categories",
        action="store_true",
        help="检查所有类别（使用默认值）",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="输出详细信息",
    )
    parser.add_argument(
        "--baseline-file",
        default=str(BASELINE_FILE),
        help="Baseline 配置文件路径",
    )
    args = parser.parse_args(argv)

    print("=" * 70)
    print("检查测试 baseline")
    print("=" * 70)

    # 加载 baseline
    baseline_data = load_baseline()

    if args.all_categories:
        # 检查所有类别（使用默认值）
        # Issue #2189: 使用实际测试数量作为默认值
        categories = [
            ("default_tests", 3956),
            ("critical_e2e", 5),
            ("full_e2e", 74),
            ("issue_tests", 27),
        ]

        all_results = []
        for category, default_count in categories:
            actual_count = default_count  # 使用默认值
            status, message = check_category(category, actual_count, baseline_data, args.verbose)
            all_results.append((status, message))

            if status == "fail":
                print(f"\n✗ {message}")
            elif status == "warning":
                print(f"\n⚠ {message}")
            else:
                print(f"\n✓ {message}")

        # 汇总结果
        print("\n" + "=" * 70)
        print("汇总结果")
        print("=" * 70)

        fail_count = sum(1 for status, _ in all_results if status == "fail")
        warning_count = sum(1 for status, _ in all_results if status == "warning")
        pass_count = sum(1 for status, _ in all_results if status == "pass")

        print(f"\n通过: {pass_count}, 警告: {warning_count}, 失败: {fail_count}")

        if fail_count > 0:
            return 1
        else:
            return 0

    elif args.category and args.actual_count is not None:
        # 检查单个类别
        status, message = check_category(
            args.category, args.actual_count, baseline_data, args.verbose
        )

        if status == "fail":
            print(f"\n✗ {message}")
            return 1
        elif status == "warning":
            print(f"\n⚠ {message}")
            return 0
        else:
            print(f"\n✓ {message}")
            return 0

    else:
        parser.error("需要 --category 和 --actual-count，或使用 --all-categories")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
