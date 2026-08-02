#!/usr/bin/env python3
"""
验证 e2e_*.py 测试文件

Issue #2189: 确保所有 e2e_*.py 文件包含有效的测试函数

验证内容：
1. 所有 e2e_*.py 文件包含至少一个 test_* 函数
2. 文件不包含语法错误
3. 测试函数命名符合规范

用法：
    python scripts/verify_test_files.py [--verbose] [--fix]

退出码：
    0: 所有文件验证通过
    1: 发现问题
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import NamedTuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestFileIssue(NamedTuple):
    """测试文件问题"""

    file: Path
    line: int
    issue_type: str
    message: str


def find_e2e_test_files() -> list[Path]:
    """查找所有 e2e_*.py 文件"""
    test_dirs = [
        PROJECT_ROOT / "tests" / "e2e",
        PROJECT_ROOT / "tests" / "issues",
    ]

    files = []
    for test_dir in test_dirs:
        if test_dir.exists():
            files.extend(test_dir.rglob("e2e_*.py"))

    return sorted(files)


def verify_test_file(file: Path) -> list[TestFileIssue]:
    """验证单个测试文件"""
    issues = []

    # 检查文件是否存在
    if not file.exists():
        issues.append(
            TestFileIssue(
                file=file, line=0, issue_type="missing_file", message="File does not exist"
            )
        )
        return issues

    # 检查文件是否可读
    try:
        content = file.read_text(encoding="utf-8")
    except Exception as e:
        issues.append(
            TestFileIssue(
                file=file,
                line=0,
                issue_type="read_error",
                message=f"Cannot read file: {e}",
            )
        )
        return issues

    # 检查语法错误
    try:
        tree = ast.parse(content, filename=str(file))
    except SyntaxError as e:
        issues.append(
            TestFileIssue(
                file=file,
                line=e.lineno or 0,
                issue_type="syntax_error",
                message=f"Syntax error: {e.msg}",
            )
        )
        return issues

    # 查找测试函数
    test_functions = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            test_functions.append(node.name)

    # 验证至少有一个测试函数
    if not test_functions:
        issues.append(
            TestFileIssue(
                file=file,
                line=1,
                issue_type="no_test_functions",
                message="No test functions found (function names must start with 'test_')",
            )
        )

    return issues


def main(argv: list[str] | None = None) -> int:
    """主函数"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="输出详细信息",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="尝试自动修复问题（暂不支持）",
    )
    args = parser.parse_args(argv)

    print("=" * 70)
    print("验证 e2e_*.py 测试文件")
    print("=" * 70)

    # 查找所有 e2e_*.py 文件
    files = find_e2e_test_files()
    print(f"\n发现 {len(files)} 个 e2e_*.py 文件")

    if not files:
        print("\n警告: 没有找到任何 e2e_*.py 文件")
        return 0

    # 验证每个文件
    all_issues = []
    for file in files:
        if args.verbose:
            print(f"\n验证: {file.relative_to(PROJECT_ROOT)}")

        issues = verify_test_file(file)
        all_issues.extend(issues)

        if issues:
            for issue in issues:
                print(f"  ✗ Line {issue.line}: {issue.issue_type} - {issue.message}")
        elif args.verbose:
            print("  ✓ 验证通过")

    # 输出统计
    print("\n" + "=" * 70)
    print("验证结果")
    print("=" * 70)

    if not all_issues:
        print("\n✓ 所有文件验证通过")
        return 0

    # 按类型分组统计
    issue_types = {}
    for issue in all_issues:
        issue_types[issue.issue_type] = issue_types.get(issue.issue_type, 0) + 1

    print(f"\n发现 {len(all_issues)} 个问题:")
    for issue_type, count in sorted(issue_types.items()):
        print(f"  - {issue_type}: {count}")

    # 输出详细问题列表
    print("\n问题列表:")
    for issue in all_issues:
        relative_path = issue.file.relative_to(PROJECT_ROOT)
        print(f"  {relative_path}:{issue.line} - {issue.message}")

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
