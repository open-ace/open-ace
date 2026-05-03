#!/usr/bin/env python3
"""
API Security Scanner for Open ACE.

AST-based scanner that detects Flask routes missing authentication or
ownership checks. Designed to run in CI and pre-commit hooks.

Rules:
  SEC001: Route handler has no authentication (no decorator, no before_request,
          no inline auth call within the function body).
  SEC002: Route with <int:id> or <id> parameter handles user-specific data
          but has no ownership check (no comparison of session user to resource).
  SEC003: Blueprint has no @before_request hook and no route-level auth pattern.

Usage:
    # Scan all route files
    python3 scripts/lint/api_security_scanner.py

    # Scan specific files (incremental)
    python3 scripts/lint/api_security_scanner.py app/routes/auth.py

    # Generate baseline
    python3 scripts/lint/api_security_scanner.py --baseline > scripts/lint/security_baseline.json

Exit code: 1 if violations found (not in baseline), 0 otherwise.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Project root
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BASELINE_PATH = PROJECT_ROOT / "scripts" / "lint" / "security_baseline.json"

# ---------------------------------------------------------------------------
# Known public endpoints (no auth required)
# ---------------------------------------------------------------------------

PUBLIC_ENDPOINTS: set[str] = {
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/check",
    "/api/health",
    "/api/compliance/reports",
    "/api/senders",
    "/api/remote/agent/register",
    "/api/remote/agent/poll",
    "/api/remote/agent/ws",
    "/api/remote/agent/install.sh",
    "/api/remote/agent/install.ps1",
    "/api/remote/usage-report",
    "/api/fetch/data",
    "/api/fetch/status",
    "/api/pages/",
    "/",
}

# Auth-related function/ decorator names that indicate authentication is present
AUTH_DECORATORS: set[str] = {
    "require_auth",
    "require_admin",
    "require_upload_auth",
    "login_required",
    "auth_required",
    "admin_required",
    "public_endpoint",
}

AUTH_INLINE_CALLS: set[str] = {
    "require_auth",
    "require_admin",
    "validate_session",
    "get_session",
    "require_upload_auth",
}

# Variable names that indicate session-based auth is being used
SESSION_AUTH_VARS: set[str] = {
    "session_token",
    "session_data",
    "auth_header",
}

# Functions that perform ownership checks
OWNERSHIP_PATTERNS: set[str] = {
    "user_id",
    "owner",
    "ownership",
    "session_user",
    "current_user",
}

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class RouteInfo:
    """Information about a single Flask route."""

    file: str
    line: int
    method: str
    path: str
    full_path: str  # includes blueprint url_prefix
    decorators: list[str] = field(default_factory=list)
    has_inline_auth: bool = False
    has_ownership_check: bool = False
    has_id_param: bool = False
    id_params: list[str] = field(default_factory=list)


@dataclass
class SecurityViolation:
    """A detected security issue."""

    rule: str
    file: str
    line: int
    endpoint: str
    message: str

    def key(self) -> str:
        # Exclude line number to prevent drift when code moves.
        return f"{self.rule}|{self.file}|{self.endpoint}"


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


class APISecurityScanner:
    """Scan Flask route definitions for security issues."""

    def __init__(self) -> None:
        self.violations: list[SecurityViolation] = []
        # Map blueprint var name -> (url_prefix, has_before_request)
        self.blueprints: dict[str, tuple[str, bool]] = {}
        # Map filename -> list of RouteInfo
        self.routes: dict[str, list[RouteInfo]] = {}
        # Cache: blueprint var name -> relative file path
        self._bp_file_cache: dict[str, str] = {}

    def _relative_path(self, filepath: str | Path) -> str:
        try:
            return str(Path(filepath).relative_to(PROJECT_ROOT))
        except ValueError:
            return str(filepath)

    # -- Phase 1: Parse blueprint info from __init__.py --

    def parse_blueprint_registry(self, init_file: Path | None = None) -> None:
        """Parse app/__init__.py to learn blueprint url_prefixes."""
        if init_file is None:
            init_file = PROJECT_ROOT / "app" / "__init__.py"
        if not init_file.exists():
            return

        src = init_file.read_text()
        # Match: app.register_blueprint(xxx_bp, url_prefix="/api/xxx")
        for m in re.finditer(
            r"register_blueprint\s*\(\s*(\w+)\s*(?:,\s*url_prefix\s*=\s*['\"]([^'\"]*)['\"])?",
            src,
        ):
            bp_var = m.group(1)
            prefix = m.group(2) or ""
            self.blueprints[bp_var] = (prefix, False)

    # -- Phase 2: Scan a single route file --

    def scan_file(self, filepath: Path) -> None:
        """Scan a Python file for Flask route definitions."""
        rel_path = self._relative_path(filepath)
        try:
            src = filepath.read_text()
            tree = ast.parse(src, filename=str(filepath))
        except (SyntaxError, UnicodeDecodeError):
            return

        # Detect blueprint var and before_request
        bp_var: str | None = None
        has_before_request = False

        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.endswith("_bp"):
                        if isinstance(node.value, ast.Call) and hasattr(node.value.func, "id"):
                            if node.value.func.id == "Blueprint":
                                bp_var = target.id

            if isinstance(node, ast.FunctionDef):
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Attribute) and dec.attr == "before_request":
                        has_before_request = True

        # Update blueprint registry and cache
        if bp_var and bp_var in self.blueprints:
            old_prefix, _ = self.blueprints[bp_var]
            self.blueprints[bp_var] = (old_prefix, has_before_request)
            self._bp_file_cache[bp_var] = rel_path

        url_prefix = self.blueprints.get(bp_var, ("", False))[0] if bp_var else ""
        file_routes: list[RouteInfo] = []

        # Walk top-level and class-level function defs
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                routes = self._extract_routes(node, rel_path, url_prefix, src, tree)
                file_routes.extend(routes)

        self.routes[rel_path] = file_routes

    def _extract_routes(
        self,
        func: ast.FunctionDef | ast.AsyncFunctionDef,
        rel_path: str,
        url_prefix: str,
        src: str,
        tree: ast.Module,
    ) -> list[RouteInfo]:
        """Extract route info from a function that has @bp.route decorators."""
        results: list[RouteInfo] = []

        for dec in func.decorator_list:
            route_info = self._parse_route_decorator(dec, url_prefix)
            if route_info is None:
                continue

            method, path = route_info
            full_path = url_prefix + path

            # Collect decorator names
            dec_names: list[str] = []
            for d in func.decorator_list:
                if isinstance(d, ast.Name):
                    dec_names.append(d.id)
                elif isinstance(d, ast.Attribute):
                    dec_names.append(d.attr)
                elif isinstance(d, ast.Call):
                    if isinstance(d.func, ast.Name):
                        dec_names.append(d.func.id)
                    elif isinstance(d.func, ast.Attribute):
                        dec_names.append(d.func.attr)

            # Check for inline auth calls in function body
            has_inline_auth = self._has_auth_in_body(func)

            # Check for ID parameters
            id_params: list[str] = []
            for m in re.finditer(r"<(?:\w+:)?(\w+)>", path):
                param_name = m.group(1)
                if any(k in param_name.lower() for k in ("id", "session_id", "user")):
                    id_params.append(param_name)

            # Check for ownership checks
            has_ownership = self._has_ownership_check(func)

            route = RouteInfo(
                file=rel_path,
                line=func.lineno,
                method=method,
                path=path,
                full_path=full_path,
                decorators=dec_names,
                has_inline_auth=has_inline_auth,
                has_ownership_check=has_ownership,
                has_id_param=len(id_params) > 0,
                id_params=id_params,
            )
            results.append(route)

        return results

    def _parse_route_decorator(self, dec: ast.expr, url_prefix: str) -> tuple[str, str] | None:
        """Parse a @bp.route(...) decorator, return (method, path) or None."""
        if not isinstance(dec, ast.Call):
            return None
        if not isinstance(dec.func, ast.Attribute):
            return None
        if dec.func.attr != "route":
            return None

        # Extract path from first argument
        if not dec.args or not isinstance(dec.args[0], ast.Constant):
            return None
        path = dec.args[0].value
        if not isinstance(path, str):
            return None

        # Extract methods from keywords (record all, join for display)
        methods: list[str] = []
        for kw in dec.keywords:
            if kw.arg == "methods" and isinstance(kw.value, ast.List):
                for elt in kw.value.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        methods.append(elt.value)
        method = ",".join(methods) if methods else "GET"

        return (method, path)

    def _has_auth_in_body(self, func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        """Check if function body contains auth-related function calls or patterns."""
        for node in ast.walk(func):
            # Direct auth function calls
            if isinstance(node, ast.Call):
                func_name = None
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr
                if func_name and func_name in AUTH_INLINE_CALLS:
                    return True

            # Variable assignments that look like session auth
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in SESSION_AUTH_VARS:
                        return True

            # request.cookies.get("session_token") pattern
            if isinstance(node, ast.Attribute):
                if node.attr in ("cookies", "headers"):
                    pass  # detected via session_token variable above

        return False

    def _has_ownership_check(self, func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        """Check if function body contains ownership verification patterns.

        NOTE: This is a best-effort heuristic using regex on ast.dump() output.
        Limitations:
        - Variable names not in the pattern list (e.g. caller_id, requester_id)
          will not be detected.
        - ast.dump() format may vary across Python versions.
        - May produce false positives (e.g. logging user_id without comparison).

        For a more robust solution, replace with proper AST node traversal that
        walks Compare nodes and checks for user_id-related attribute access.
        """
        src = ast.dump(func)
        # Look for comparisons involving user_id / owner
        for pattern in (
            r"Compare.*user_id",
            r"Compare.*owner",
            r"session.*user_id",
            r"user_id.*==.*session",
            r"current_user.*id",
            r"ownership",
            r"_check_session_access",
            r"_require_machine_admin",
        ):
            if re.search(pattern, src):
                return True
        return False

    # -- Phase 3: Generate violations --

    def check(self) -> list[SecurityViolation]:
        """Run all checks and return violations."""
        self.violations.clear()

        for rel_path, routes in self.routes.items():
            # Get blueprint-level auth
            bp_var = self._get_bp_var_for_file(rel_path)
            bp_has_before = False
            if bp_var and bp_var in self.blueprints:
                bp_has_before = self.blueprints[bp_var][1]

            for route in routes:
                # Skip public endpoints (hardcoded list or @public_endpoint decorator)
                if route.full_path in PUBLIC_ENDPOINTS:
                    continue
                if "public_endpoint" in route.decorators:
                    continue

                has_decorator_auth = bool(AUTH_DECORATORS.intersection(route.decorators))
                has_auth = has_decorator_auth or route.has_inline_auth or bp_has_before

                # SEC001: No authentication
                if not has_auth:
                    self.violations.append(
                        SecurityViolation(
                            rule="SEC001",
                            file=rel_path,
                            line=route.line,
                            endpoint=route.full_path,
                            message=f"Route {route.method} {route.full_path} has no authentication "
                            f"(no auth decorator, no before_request, no inline auth call)",
                        )
                    )

                # SEC002: User resource without ownership check
                if (
                    has_auth
                    and route.has_id_param
                    and not route.has_ownership_check
                    and not has_decorator_auth  # require_admin doesn't need ownership
                ):
                    # Only flag endpoints that look like user-specific resources
                    resource_patterns = (
                        "/sessions/",
                        "/users/",
                        "/projects/",
                        "/workspace/",
                    )
                    if any(p in route.full_path for p in resource_patterns):
                        self.violations.append(
                            SecurityViolation(
                                rule="SEC002",
                                file=rel_path,
                                line=route.line,
                                endpoint=route.full_path,
                                message=f"Route {route.full_path} has ID param(s) "
                                f"{route.id_params} but no ownership check",
                            )
                        )

        # SEC003: Blueprints without before_request or route-level auth
        self._check_blueprints()

        return self.violations

    def _get_bp_var_for_file(self, rel_path: str) -> str | None:
        """Get the blueprint variable name for a file by scanning its content."""
        filepath = PROJECT_ROOT / rel_path
        if not filepath.exists():
            return None
        src = filepath.read_text()
        m = re.search(r"(\w+_bp)\s*=\s*Blueprint", src)
        return m.group(1) if m else None

    def _check_blueprints(self) -> None:
        """SEC003: Check blueprints that have no before_request and no route-level auth."""
        for bp_var, (prefix, has_before) in self.blueprints.items():
            if has_before:
                continue

            # Find the route file for this blueprint
            bp_file = self._find_bp_file(bp_var)
            if bp_file is None:
                continue

            routes = self.routes.get(bp_file, [])
            if not routes:
                continue

            # Check if any route has decorator-level auth
            any_decorator_auth = False
            for route in routes:
                if AUTH_DECORATORS.intersection(route.decorators):
                    any_decorator_auth = True
                    break

            # If no before_request and no decorator auth, check inline auth coverage
            if not any_decorator_auth:
                routes_with_auth = sum(
                    1
                    for r in routes
                    if r.has_inline_auth or bool(AUTH_DECORATORS.intersection(r.decorators))
                )
                # If less than half of routes have inline auth, flag the blueprint
                if routes_with_auth < len(routes):
                    # Check if all routes are public
                    all_public = all(r.full_path in PUBLIC_ENDPOINTS for r in routes)
                    if not all_public:
                        self.violations.append(
                            SecurityViolation(
                                rule="SEC003",
                                file=bp_file,
                                line=0,
                                endpoint=prefix or bp_var,
                                message=f"Blueprint {bp_var} (prefix={prefix!r}) has no "
                                f"@before_request auth hook. "
                                f"{routes_with_auth}/{len(routes)} routes have inline auth.",
                            )
                        )

    def _find_bp_file(self, bp_var: str) -> str | None:
        """Find the file that defines a blueprint variable."""
        # Use cached mapping from scan_file phase
        if bp_var in self._bp_file_cache:
            return self._bp_file_cache[bp_var]

        # Fallback: derive filename from bp_var naming convention
        name = bp_var.replace("_bp", "")
        candidate = PROJECT_ROOT / "app" / "routes" / f"{name}.py"
        if candidate.exists():
            rel = self._relative_path(candidate)
            self._bp_file_cache[bp_var] = rel
            return rel

        return None


# ---------------------------------------------------------------------------
# Baseline management
# ---------------------------------------------------------------------------


def load_baseline() -> set[str]:
    """Load baseline suppression keys from JSON file."""
    if not BASELINE_PATH.exists():
        return set()
    try:
        data = json.loads(BASELINE_PATH.read_text())
        return {
            (
                item["key"]
                if "key" in item
                else f"{item['rule']}|{item['file']}|{item['line']}|{item['endpoint']}"
            )
            for item in data
        }
    except (json.JSONDecodeError, KeyError):
        return set()


def generate_baseline(violations: list[SecurityViolation]) -> None:
    """Print baseline JSON to stdout."""
    items = []
    for v in violations:
        items.append(
            {
                "key": v.key(),
                "rule": v.rule,
                "file": v.file,
                "line": v.line,
                "endpoint": v.endpoint,
                "message": v.message,
            }
        )
    print(json.dumps(items, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="API Security Scanner")
    parser.add_argument("files", nargs="*", help="Files to scan (default: app/routes/)")
    parser.add_argument("--baseline", action="store_true", help="Generate baseline to stdout")
    args = parser.parse_args()

    scanner = APISecurityScanner()

    # Phase 1: Parse blueprint registry
    scanner.parse_blueprint_registry()

    # Phase 2: Scan files
    # Note: incremental mode (passing specific files) gets correct SEC001/SEC002
    # results, but SEC003 (blueprint-level check) may be incomplete since it
    # only sees the passed files, not the full blueprint.
    if args.files:
        files = [Path(f) for f in args.files]
    else:
        files = sorted(Path(PROJECT_ROOT / "app" / "routes").glob("*.py"))

    for f in files:
        if f.name == "__init__.py":
            continue
        scanner.scan_file(f)

    # Phase 3: Check
    violations = scanner.check()

    if args.baseline:
        generate_baseline(violations)
        return 0

    # Filter against baseline
    baseline_keys = load_baseline()
    new_violations = [v for v in violations if v.key() not in baseline_keys]

    if new_violations:
        print(f"Found {len(new_violations)} violation(s).")
        print(f"({len(baseline_keys)} baseline suppression(s) active)")
        for v in new_violations:
            print(f"  {v.file}:{v.line}: {v.rule} {v.message}")
        return 1

    print(f"No new violations. ({len(baseline_keys)} baseline suppression(s) active)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
