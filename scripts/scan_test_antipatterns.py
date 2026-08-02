#!/usr/bin/env python3
"""
扫描测试代码中的反模式

Issue #2189: 全仓扫描假阳性问题

扫描模式：
1. broad except: pass - except Exception: pass（高严重程度）
2. 无断言测试 - 测试函数中无 assert 或 pytest.fail（中严重程度）
3. 无条件 return true - 测试函数末尾无条件 return True（高严重程度）
4. 错误 skip - 在失败断言前 pytest.skip（高严重程度）

用法：
    python scripts/scan_test_antipatterns.py [--output json|markdown] [--severity high|medium|all]

输出：
    - JSON 报告：包含问题列表、严重程度、修复建议
    - Markdown 报告：供人工 review 使用
    - 统计摘要：按类型、严重程度分布
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any, NamedTuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class AntiPatternIssue(NamedTuple):
    """反模式问题"""

    file: Path
    line: int
    issue_type: str
    severity: str
    message: str
    recommendation: str


def find_test_files() -> list[Path]:
    """查找所有测试文件"""
    test_dir = PROJECT_ROOT / "tests"
    if not test_dir.exists():
        return []

    files = []
    # 包含 test_*.py 和 e2e_*.py
    files.extend(test_dir.rglob("test_*.py"))
    files.extend(test_dir.rglob("e2e_*.py"))

    return sorted(set(files))


def scan_broad_except_pass(file: Path, tree: ast.AST) -> list[AntiPatternIssue]:
    """扫描 except Exception: pass 模式"""
    issues = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            # 检查是否是 except Exception:
            if node.type and isinstance(node.type, ast.Name) and node.type.id == "Exception":
                # 检查 body 是否只有 pass
                if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                    issues.append(
                        AntiPatternIssue(
                            file=file,
                            line=node.lineno,
                            issue_type="broad_except_pass",
                            severity="high",
                            message="Found 'except Exception: pass' which may swallow test failures",
                            recommendation="Replace with explicit exception handling or pytest.fail",
                        )
                    )

    return issues


def scan_no_assertion(file: Path, tree: ast.AST, content: str) -> list[AntiPatternIssue]:
    """扫描无断言测试"""
    issues = []

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            # 检查函数体中是否有 assert 或 pytest.fail
            has_assertion = False
            for child in ast.walk(node):
                if isinstance(child, ast.Assert):
                    has_assertion = True
                    break
                elif isinstance(child, ast.Call):
                    # 检查 pytest.fail 或 pytest.raises
                    if isinstance(child.func, ast.Attribute):
                        if child.func.attr in ("fail", "raises", "assert"):
                            has_assertion = True
                            break

            # 检查是否显式标记为 smoke test
            is_smoke_test = "smoke" in content or "SMOKE" in content

            if not has_assertion and not is_smoke_test:
                issues.append(
                    AntiPatternIssue(
                        file=file,
                        line=node.lineno,
                        issue_type="no_assertion",
                        severity="medium",
                        message=f"Test function '{node.name}' has no assertions",
                        recommendation="Add assert statements or pytest.fail to verify expected behavior",
                    )
                )

    return issues


def scan_unconditional_return_true(file: Path, tree: ast.AST) -> list[AntiPatternIssue]:
    """扫描无条件 return True"""
    issues = []

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            # 检查函数末尾是否有 return True
            if node.body:
                last_stmt = node.body[-1]
                if isinstance(last_stmt, ast.Return) and isinstance(last_stmt.value, ast.Constant):
                    if last_stmt.value.value is True:
                        issues.append(
                            AntiPatternIssue(
                                file=file,
                                line=node.lineno,
                                issue_type="unconditional_return_true",
                                severity="high",
                                message=f"Test function '{node.name}' unconditionally returns True",
                                recommendation="Remove return True or replace with assertion",
                            )
                        )

    return issues


def scan_wrong_skip(file: Path, tree: ast.AST) -> list[AntiPatternIssue]:
    """扫描错误 skip"""
    issues = []

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            # 检查是否在 assert 前有 pytest.skip
            has_skip_before_assert = False
            for i, child in enumerate(node.body):
                # 检查 pytest.skip
                if isinstance(child, ast.Expr) and isinstance(child.value, ast.Call):
                    if isinstance(child.value.func, ast.Attribute):
                        if child.value.func.attr == "skip":
                            # 检查后面是否有 assert
                            for j in range(i + 1, len(node.body)):
                                if isinstance(node.body[j], ast.Assert):
                                    has_skip_before_assert = True
                                    break

            if has_skip_before_assert:
                issues.append(
                    AntiPatternIssue(
                        file=file,
                        line=node.lineno,
                        issue_type="wrong_skip",
                        severity="high",
                        message=f"Test function '{node.name}' has pytest.skip before assertion",
                        recommendation="Move pytest.skip after assertions or use pytest.xfail",
                    )
                )

    return issues


def scan_file(file: Path) -> list[AntiPatternIssue]:
    """扫描单个文件"""
    issues = []

    try:
        content = file.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(file))
    except Exception as e:
        print(f"警告: 无法解析文件 {file}: {e}")
        return issues

    # 扫描各种反模式
    issues.extend(scan_broad_except_pass(file, tree))
    issues.extend(scan_no_assertion(file, tree, content))
    issues.extend(scan_unconditional_return_true(file, tree))
    issues.extend(scan_wrong_skip(file, tree))

    return issues


def output_json(issues: list[AntiPatternIssue]) -> str:
    """输出 JSON 格式报告"""
    data = {
        "summary": {
            "total_issues": len(issues),
            "by_type": {},
            "by_severity": {},
        },
        "issues": [
            {
                "file": str(issue.file.relative_to(PROJECT_ROOT)),
                "line": issue.line,
                "issue_type": issue.issue_type,
                "severity": issue.severity,
                "message": issue.message,
                "recommendation": issue.recommendation,
            }
            for issue in issues
        ],
    }

    # 统计
    for issue in issues:
        data["summary"]["by_type"][issue.issue_type] = (
            data["summary"]["by_type"].get(issue.issue_type, 0) + 1
        )
        data["summary"]["by_severity"][issue.severity] = (
            data["summary"]["by_severity"].get(issue.severity, 0) + 1
        )

    return json.dumps(data, indent=2)


def output_markdown(issues: list[AntiPatternIssue]) -> str:
    """输出 Markdown 格式报告"""
    lines = ["# 测试反模式扫描报告\n"]
    lines.append(f"**扫描时间**: {__import__('datetime').datetime.now().isoformat()}\n")
    lines.append(f"**发现问题**: {len(issues)}\n\n")

    # 按严重程度分组
    by_severity: dict[str, list[AntiPatternIssue]] = {}
    for issue in issues:
        by_severity.setdefault(issue.severity, []).append(issue)

    for severity in ["high", "medium", "low"]:
        if severity in by_severity:
            lines.append(f"## {severity.upper()} Severity\n\n")
            for issue in by_severity[severity]:
                relative_path = issue.file.relative_to(PROJECT_ROOT)
                lines.append(f"### {relative_path}:{issue.line}\n\n")
                lines.append(f"- **类型**: {issue.issue_type}\n")
                lines.append(f"- **描述**: {issue.message}\n")
                lines.append(f"- **建议**: {issue.recommendation}\n\n")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """主函数"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        "-o",
        choices=["json", "markdown", "text"],
        default="text",
        help="输出格式",
    )
    parser.add_argument(
        "--severity",
        "-s",
        choices=["high", "medium", "all"],
        default="all",
        help="仅显示指定严重程度的问题",
    )
    parser.add_argument(
        "--limit",
        "-l",
        type=int,
        default=0,
        help="限制输出数量（0 表示不限制）",
    )
    args = parser.parse_args(argv)

    print("=" * 70)
    print("扫描测试代码中的反模式")
    print("=" * 70)

    # 查找所有测试文件
    files = find_test_files()
    print(f"\n扫描 {len(files)} 个测试文件...")

    if not files:
        print("\n警告: 没有找到任何测试文件")
        return 0

    # 扫描每个文件
    all_issues = []
    for file in files:
        issues = scan_file(file)
        all_issues.extend(issues)

    # 过滤严重程度
    if args.severity != "all":
        all_issues = [issue for issue in all_issues if issue.severity == args.severity]

    # 限制输出数量
    if args.limit > 0:
        all_issues = all_issues[: args.limit]

    # 输出结果
    if args.output == "json":
        print(output_json(all_issues))
    elif args.output == "markdown":
        print(output_markdown(all_issues))
    else:
        # 文本格式
        print(f"\n发现 {len(all_issues)} 个问题\n")

        for issue in all_issues:
            relative_path = issue.file.relative_to(PROJECT_ROOT)
            print(f"{relative_path}:{issue.line} [{issue.severity}] {issue.issue_type}")
            print(f"  {issue.message}")
            print(f"  建议: {issue.recommendation}\n")

    # 返回码
    high_severity_count = sum(1 for issue in all_issues if issue.severity == "high")
    if high_severity_count > 0:
        return 1
    else:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())