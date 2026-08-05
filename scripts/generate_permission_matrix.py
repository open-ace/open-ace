#!/usr/bin/env python3
"""
Generate API permission matrix documentation.

Issue #2276: Document permission requirements for all admin endpoints.
"""

import re
from pathlib import Path


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

            # Find route decorator
            route_line = None
            for dec_line_num, dec_line_text in current_decorators:
                if dec_line_text.startswith("@tenant_bp.route") or dec_line_text.startswith(
                    "@bp.route"
                ):
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
                # Extract HTTP method
                method_match = re.search(r"methods=\[(.*?)\]", route_line)
                if method_match:
                    method = method_match.group(1).strip("\"'")
                else:
                    method = "GET"

                # Extract path - first argument of route decorator
                route_match = re.search(r'@tenant_bp\.route\(\s*["\']([^"\']*)["\']', route_line)
                if not route_match:
                    route_match = re.search(r'@bp\.route\(\s*["\']([^"\']*)["\']', route_line)

                if route_match:
                    path = route_match.group(1)
                else:
                    path = "unknown"

                # Combine blueprint prefix with path
                if path == "":
                    # Empty path means the blueprint prefix is the full path
                    full_path = bp_prefix
                elif path.startswith("/"):
                    full_path = f"{bp_prefix}{path}"
                elif path != "unknown":
                    full_path = f"{bp_prefix}/{path}"
                else:
                    full_path = path

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


def generate_permission_matrix():
    """Generate permission matrix markdown document."""
    # Scan route files
    routes_dir = Path("app/routes")
    all_endpoints = []

    for route_file in routes_dir.glob("*.py"):
        if route_file.name.startswith("_"):
            continue

        endpoints = extract_endpoint_info(route_file)
        all_endpoints.extend(endpoints)

    # Generate markdown
    md_content = """# API Permission Matrix

Issue #2276: Permission requirements for all admin endpoints.

## Overview

This document lists all API endpoints that require elevated permissions (admin, platform_admin, or tenant_admin).

## Permission Model

| Role | Description | Platform Admin APIs | Tenant APIs |
|------|-------------|-------------------|------------|
| `admin` | Legacy admin role (backward compatible) | ✅ Full access | ✅ Full access |
| `platform_admin` | Platform admin (recommended) | ✅ Full access | ✅ Full access |
| `tenant_admin` | Tenant admin | ❌ No access | ✅ Own tenant only |
| `user` | Regular user | ❌ No access | ❌ No access |

## Platform Admin Required Endpoints

These endpoints require `platform_admin` or `admin` role.

"""

    # Group by decorator type
    platform_admin_endpoints = [
        ep for ep in all_endpoints if ep["decorator"] == "platform_admin_required"
    ]
    admin_required_endpoints = [ep for ep in all_endpoints if ep["decorator"] == "admin_required"]
    same_tenant_endpoints = [
        ep for ep in all_endpoints if ep["decorator"] == "same_tenant_or_platform_admin"
    ]

    # Platform admin required
    if platform_admin_endpoints:
        md_content += "| Method | Path | Function | File | Line |\n"
        md_content += "|--------|-----|----------|------|------|\n"
        for ep in sorted(platform_admin_endpoints, key=lambda x: x["path"]):
            md_content += f"| {ep['method']} | `{ep['path']}` | `{ep['function_name']}` | {ep['file']} | {ep['line_number']} |\n"
        md_content += "\n"

    # Admin required
    md_content += "## Admin Required Endpoints\n\n"
    md_content += "These endpoints require `admin`, `platform_admin`, or `tenant_admin` role.\n\n"

    if admin_required_endpoints:
        md_content += "| Method | Path | Function | File | Line |\n"
        md_content += "|--------|-----|----------|------|------|\n"
        for ep in sorted(admin_required_endpoints, key=lambda x: x["path"]):
            md_content += f"| {ep['method']} | `{ep['path']}` | `{ep['function_name']}` | {ep['file']} | {ep['line_number']} |\n"
        md_content += "\n"

    # Same tenant or platform admin
    md_content += "## Same Tenant or Platform Admin Endpoints\n\n"
    md_content += "These endpoints allow tenant_admin to access their own tenant, or platform_admin to access any tenant.\n\n"

    if same_tenant_endpoints:
        md_content += "| Method | Path | Function | File | Line |\n"
        md_content += "|--------|-----|----------|------|------|\n"
        for ep in sorted(same_tenant_endpoints, key=lambda x: x["path"]):
            md_content += f"| {ep['method']} | `{ep['path']}` | `{ep['function_name']}` | {ep['file']} | {ep['line_number']} |\n"
        md_content += "\n"

    # Summary
    md_content += f"""## Summary

- Total platform_admin_required endpoints: {len(platform_admin_endpoints)}
- Total admin_required endpoints: {len(admin_required_endpoints)}
- Total same_tenant_or_platform_admin endpoints: {len(same_tenant_endpoints)}

**Note**: Issue #2276 ensures backward compatibility - `admin` role can access all `platform_admin_required` endpoints.

---

Generated by: `scripts/generate_permission_matrix.py`
Last updated: 2026-08-05
"""

    return md_content


if __name__ == "__main__":
    import sys

    output_path = Path("docs/api_permission_matrix.md")

    if len(sys.argv) > 1:
        output_path = Path(sys.argv[1])

    content = generate_permission_matrix()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content)

    print(f"Permission matrix generated: {output_path}")
    print(f"Total endpoints documented: {content.count('|') // 5}")
