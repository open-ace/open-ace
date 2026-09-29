#!/usr/bin/env python3
"""
Generate API permission matrix documentation.

Issue #2276: Document permission requirements for all admin endpoints.

The output is a single bilingual (English + Chinese) markdown file.  Table
content is identifiers only; prose is duplicated per language.
"""

import re
from datetime import date
from pathlib import Path

ROUTE_DECORATOR_RE = re.compile(
    r"@(\w+)\.(route|get|post|put|delete|patch)\(\s*[\"']([^\"']*)[\"']"
)


def extract_endpoint_info(file_path: Path) -> list[dict]:
    """
    Extract endpoint information from a route file.

    Handles both decorator orders:
        @route                  @permission_decorator
        @permission_decorator   @route
        def func():             def func():

    The algorithm accumulates all decorator lines (lines starting with '@')
    until it encounters a 'def' statement, then matches the route decorator
    with the permission decorator.  Blank lines and comments between
    decorators do not break accumulation; any other code line resets the
    accumulator.

    Returns list of dicts with keys: method, path, decorator, function_name, line_number
    """
    endpoints = []
    content = file_path.read_text()
    lines = content.split("\n")

    # Extract blueprint url_prefix from file content
    # Look for patterns like: Blueprint("name", __name__, url_prefix="/api/tenants")
    bp_prefix_match = re.search(r'url_prefix\s*=\s*["\']([^"\']+)["\']', content)
    if bp_prefix_match:
        bp_prefix = bp_prefix_match.group(1)
    else:
        # Fallback: derive from file name
        bp_name = file_path.stem.replace(".py", "")
        bp_prefix = f"/api/{bp_name}"

    # Accumulate decorators for the current function being defined.
    # Each entry is (line_number, line_text).
    current_decorators: list[tuple[int, str]] = []

    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        if stripped.startswith("@"):
            current_decorators.append((i, stripped))
        elif stripped.startswith("def "):
            # Process accumulated decorators for this function
            func_match = re.search(r"def (\w+)", stripped)
            func_name = func_match.group(1) if func_match else "unknown"

            # Find route decorator (any blueprint variable name)
            route_line = None
            for dec_line_num, dec_line_text in current_decorators:
                if ROUTE_DECORATOR_RE.search(dec_line_text):
                    route_line = dec_line_text
                    break

            # Find permission decorator (first match wins)
            perm_decorator = None
            perm_line = 0
            for dec_line_num, dec_line_text in current_decorators:
                if "@platform_admin_required" in dec_line_text:
                    perm_decorator = "platform_admin_required"
                    perm_line = dec_line_num
                    break
                elif "@admin_required" in dec_line_text:
                    perm_decorator = "admin_required"
                    perm_line = dec_line_num
                    break
                elif "@same_tenant_or_platform_admin" in dec_line_text:
                    perm_decorator = "same_tenant_or_platform_admin"
                    perm_line = dec_line_num
                    break

            # Only add endpoint if both route and permission decorator exist
            if route_line and perm_decorator:
                route_match = ROUTE_DECORATOR_RE.search(route_line)
                verb = route_match.group(2)
                path = route_match.group(3)

                if verb == "route":
                    # Extract HTTP methods from methods=[...] when present
                    method_match = re.search(r"methods=\[(.*?)\]", route_line)
                    if method_match:
                        verbs = re.findall(r"[\"\'](\w+)[\"\']", method_match.group(1))
                        method = "/".join(v.upper() for v in verbs) if verbs else "GET"
                    else:
                        method = "GET"
                else:
                    method = verb.upper()

                # Combine blueprint prefix with path
                if path == "":
                    # Empty path means the blueprint prefix is the full path
                    full_path = bp_prefix
                elif path.startswith("/"):
                    full_path = f"{bp_prefix}{path}"
                else:
                    full_path = f"{bp_prefix}/{path}"

                endpoints.append(
                    {
                        "method": method,
                        "path": full_path,
                        "decorator": perm_decorator,
                        "function_name": func_name,
                        "line_number": perm_line,
                        "file": file_path.name,
                    }
                )

            # Reset for next function
            current_decorators = []
        elif stripped and not stripped.startswith("#"):
            # Non-decorator, non-def, non-comment, non-blank line
            # Reset accumulated decorators (they don't belong to a function)
            current_decorators = []

    return endpoints


def render_table(endpoints: list[dict]) -> str:
    if not endpoints:
        return ""
    rows = "| Method | Path | Function | File | Line |\n"
    rows += "|--------|-----|----------|------|------|\n"
    for ep in sorted(endpoints, key=lambda x: x["path"]):
        rows += (
            f"| {ep['method']} | `{ep['path']}` | `{ep['function_name']}` "
            f"| {ep['file']} | {ep['line_number']} |\n"
        )
    return rows + "\n"


def generate_permission_matrix() -> str:
    """Generate bilingual permission matrix markdown document."""
    routes_dir = Path("app/routes")
    all_endpoints: list[dict] = []

    for route_file in routes_dir.glob("*.py"):
        if route_file.name.startswith("_"):
            continue
        all_endpoints.extend(extract_endpoint_info(route_file))

    platform_admin_endpoints = [
        ep for ep in all_endpoints if ep["decorator"] == "platform_admin_required"
    ]
    admin_required_endpoints = [ep for ep in all_endpoints if ep["decorator"] == "admin_required"]
    same_tenant_endpoints = [
        ep for ep in all_endpoints if ep["decorator"] == "same_tenant_or_platform_admin"
    ]

    table_pa = render_table(platform_admin_endpoints)
    table_ad = render_table(admin_required_endpoints)
    table_st = render_table(same_tenant_endpoints)

    md_content = f"""# API Permission Matrix — API 权限矩阵

[English](#english) | [中文](#中文)

---

## English

Generated by `scripts/generate_permission_matrix.py` — regenerate after
changing route permission decorators; do not hand-edit the tables.

Issue #2276: Permission requirements for all admin endpoints.

This document lists all API endpoints that require elevated permissions
(admin, platform_admin, or tenant_admin).

## Permission Model

| Role | Description | Platform Admin APIs | Tenant APIs |
|------|-------------|-------------------|------------|
| `admin` | Legacy admin role (backward compatible) | ✅ Full access | ✅ Full access |
| `platform_admin` | Platform admin (recommended) | ✅ Full access | ✅ Full access |
| `tenant_admin` | Tenant admin | ❌ No access | ✅ Own tenant only |
| `user` | Regular user | ❌ No access | ❌ No access |

## Platform Admin Required Endpoints

These endpoints require `platform_admin` or `admin` role.

{table_pa}
## Admin Required Endpoints

These endpoints require `admin`, `platform_admin`, or `tenant_admin` role.

{table_ad}
## Same Tenant or Platform Admin Endpoints

These endpoints allow tenant_admin to access their own tenant, or platform_admin to access any tenant.

{table_st}
## Summary

- Total platform_admin_required endpoints: {len(platform_admin_endpoints)}
- Total admin_required endpoints: {len(admin_required_endpoints)}
- Total same_tenant_or_platform_admin endpoints: {len(same_tenant_endpoints)}

**Note**: Issue #2276 ensures backward compatibility — the `admin` role can access all `platform_admin_required` endpoints.

---

## 中文

由 `scripts/generate_permission_matrix.py` 生成——修改路由权限装饰器后请重新生成，不要手工编辑表格。

Issue #2276：所有管理端点的权限要求。

本文档列出所有需要提权（admin、platform_admin 或 tenant_admin）的 API 端点。

## 权限模型

| 角色 | 说明 | 平台管理 API | 租户 API |
|------|------|--------------|-----------|
| `admin` | 旧版管理员角色（向后兼容） | ✅ 完全访问 | ✅ 完全访问 |
| `platform_admin` | 平台管理员（推荐） | ✅ 完全访问 | ✅ 完全访问 |
| `tenant_admin` | 租户管理员 | ❌ 无权访问 | ✅ 仅本租户 |
| `user` | 普通用户 | ❌ 无权访问 | ❌ 无权访问 |

## 需要 platform_admin 的端点

这些端点要求 `platform_admin` 或 `admin` 角色。

{table_pa}
## 需要 admin 的端点

这些端点要求 `admin`、`platform_admin` 或 `tenant_admin` 角色。

{table_ad}
## 同租户或平台管理员端点

这些端点允许 tenant_admin 访问本租户，或 platform_admin 访问任意租户。

{table_st}
## 汇总

- platform_admin_required 端点总数：{len(platform_admin_endpoints)}
- admin_required 端点总数：{len(admin_required_endpoints)}
- same_tenant_or_platform_admin 端点总数：{len(same_tenant_endpoints)}

**注**：Issue #2276 保证向后兼容——`admin` 角色可以访问所有 `platform_admin_required` 端点。

---

Generated by: `scripts/generate_permission_matrix.py`
Last updated: {date.today().isoformat()}
"""

    return md_content


if __name__ == "__main__":
    import sys

    output_path = Path("docs/dev/API_PERMISSION_MATRIX.md")

    if len(sys.argv) > 1:
        output_path = Path(sys.argv[1])

    content = generate_permission_matrix()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content)

    print(f"Permission matrix generated: {output_path}")
    print(f"Total endpoints documented: {content.count('|') // 5}")
