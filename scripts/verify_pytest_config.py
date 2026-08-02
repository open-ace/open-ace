#!/usr/bin/env python3
"""
验证 pytest 配置一致性

Issue #2189: 配置文件迁移支持

功能：
1. 读取 pytest.ini 和 pyproject.toml 配置
2. 比较关键配置项是否一致
3. 输出一致性报告
4. 不一致时发出警告

用法：
    python scripts/verify_pytest_config.py [--strict]
"""

from __future__ import annotations

import argparse
import configparser
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTEST_INI = PROJECT_ROOT / "pytest.ini"
PYPROJECT_TOML = PROJECT_ROOT / "pyproject.toml"


def parse_pytest_ini() -> dict[str, Any]:
    """解析 pytest.ini 配置"""
    if not PYTEST_INI.exists():
        return {}

    config = configparser.ConfigParser()
    config.read(PYTEST_INI)

    result = {}
    if "pytest" in config:
        for key, value in config["pytest"].items():
            result[key] = value.strip()

    return result


def parse_pyproject_toml() -> dict[str, Any]:
    """解析 pyproject.toml 配置"""
    if not PYPROJECT_TOML.exists():
        return {}

    # 简单解析 TOML（Python 3.11+ 内置 tomllib）
    try:
        import tomllib
    except ImportError:
        # Python 3.10 回退到第三方库
        try:
            import tomli as tomllib
        except ImportError:
            print("警告: 需要安装 tomli 库: pip install tomli")
            return {}

    try:
        content = PYPROJECT_TOML.read_text(encoding="utf-8")
        data = tomllib.loads(content)
        return data.get("tool", {}).get("pytest", {}).get("ini_options", {})
    except Exception as e:
        print(f"警告: 无法解析 pyproject.toml: {e}")
        return {}


def compare_configs(strict: bool = False) -> tuple[bool, list[str]]:
    """
    比较配置一致性

    返回: (是否一致, 消息列表)
    """
    pytest_ini_config = parse_pytest_ini()
    pyproject_toml_config = parse_pyproject_toml()

    messages = []
    all_consistent = True

    # 关键配置项
    key_configs = [
        "python_files",
        "python_classes",
        "python_functions",
        "testpaths",
    ]

    print("\n配置比较:")
    print("-" * 70)

    for key in key_configs:
        ini_value = pytest_ini_config.get(key, "")
        toml_value = pyproject_toml_config.get(key, "")

        # 处理列表类型（pyproject.toml 可能是列表）
        if isinstance(toml_value, list):
            toml_value = " ".join(toml_value)

        # 处理 pytest.ini 的空格分隔
        ini_parts = set(ini_value.split())
        toml_parts = set(toml_value.split())

        if ini_parts == toml_parts:
            print(f"✓ {key}: {ini_value}")
        else:
            print(f"✗ {key}: pytest.ini={ini_value}, pyproject.toml={toml_value}")
            messages.append(
                f"配置项 '{key}' 不一致: pytest.ini={ini_value}, pyproject.toml={toml_value}"
            )
            all_consistent = False

    return all_consistent, messages


def main(argv: list[str] | None = None) -> int:
    """主函数"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="严格模式：配置不一致时返回错误码",
    )
    args = parser.parse_args(argv)

    print("=" * 70)
    print("验证 pytest 配置一致性")
    print("=" * 70)

    # 检查文件存在性
    print(f"\npytest.ini 存在: {PYTEST_INI.exists()}")
    print(f"pyproject.toml 存在: {PYPROJECT_TOML.exists()}")

    if not PYTEST_INI.exists() and not PYPROJECT_TOML.exists():
        print("\n错误: 没有找到任何 pytest 配置文件")
        return 1

    # 比较配置
    all_consistent, messages = compare_configs(args.strict)

    # 输出结果
    print("\n" + "=" * 70)
    if all_consistent:
        print("✓ 配置一致性检查通过")
        return 0
    else:
        print("✗ 配置一致性检查失败")
        if messages:
            print("\n问题列表:")
            for msg in messages:
                print(f"  - {msg}")

        if args.strict:
            return 1
        else:
            print("\n警告: 配置不一致，但非严格模式，继续运行")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
