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

    Returns list of dicts with keys: method, path, decorator, function_name, line_number
    """
    endpoints = []
    content = file_path.read_text()
    lines = content.split("\n")

    # Track current decorator
    current_decorator = None
    decorator_line = 0

    for i, line in enumerate(lines, 1):
        # Check for decorator
        if "@platform_admin_required" in line:
            current_decorator = "platform_admin_required"
            decorator_line = i
        elif "@admin_required" in line and "@platform_admin_required" not in line:
            current_decorator = "admin_required"
            decorator_line = i
        elif "@same_tenant_or_platform_admin" in line:
            current_decorator = "same_tenant_or_platform_admin"
            decorator_line = i
        elif line.strip().startswith("@") and not line.strip().startswith("@tenant_bp"):
            # Other decorator, but not route decorator
            continue
        elif line.strip().startswith("@tenant_bp.route") or line.strip().startswith(
            "@bp.route"
        ):
            # Extract route information
            if current_decorator:
                # Extract HTTP method
                method_match = re.search(r"methods=\[(.*?)\]", line)
                if method_match:
                    method = method_match.group(1).strip("\"'")
                else:
                    method = "GET"

                # Extract path
                path_match = re.search(r'"(.*?)"', line)
                if path_match:
                    path = path_match.group(1)
                else:
                    path = "unknown"

                # Extract function name (next non-empty line that starts with 'def')
                func_name = "unknown"
                for j in range(i, min(i + 5, len(lines) + 1)):
                    if j < len(lines):
                        next_line = lines[j]
                        if next_line.strip().startswith("def "):
                            func_match = re.search(r"def (\w+)", next_line)
                            if func_match:
                                func_name = func_match.group(1)
                            break

                endpoints.append(
                    {
                        "method": method,
                        "path": path,
                        "decorator": current_decorator,
                        "function_name": func_name,
                        "line_number": decorator_line,
                        "file": file_path.name,
                    }
                )

            # Reset decorator for next endpoint
            current_decorator = None
            decorator_line = 0

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
    admin_required_endpoints = [
        ep for ep in all_endpoints if ep["decorator"] == "admin_required"
    ]
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