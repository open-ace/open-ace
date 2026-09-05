"""
Frontend Build Integrity Check Module

Validates frontend build artifacts exist and are complete before the application starts.
Issue #3277: Prevent "Open ACE could not render" errors due to missing build artifacts.
"""

import json
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class CheckStatus(Enum):
    """Status of individual check item."""

    OK = "ok"
    MISSING = "missing"
    INVALID = "invalid"


class ErrorLevel(Enum):
    """Error level for check results."""

    ERROR = "error"  # Blocks startup
    WARNING = "warning"  # Non-blocking warning


@dataclass
class CheckResult:
    """Result of a single check."""

    name: str
    status: CheckStatus
    message: str
    error_level: ErrorLevel | None = None


@dataclass
class FrontendBuildCheckResult:
    """Overall result of frontend build integrity check."""

    success: bool
    checks: list[CheckResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_error(self, message: str) -> None:
        """Add an error message."""
        self.errors.append(message)
        self.success = False

    def add_warning(self, message: str) -> None:
        """Add a warning message."""
        self.warnings.append(message)


def get_dist_dir() -> Path:
    """Get the frontend build output directory."""
    # The dist directory is at: <project_root>/static/js/dist
    # This file is at: <project_root>/app/utils/frontend_check.py
    project_root = Path(__file__).parent.parent.parent
    return project_root / "static" / "js" / "dist"


def check_index_html(dist_dir: Path) -> CheckResult:
    """Check if index.html exists."""
    index_path = dist_dir / "index.html"

    if not dist_dir.exists():
        return CheckResult(
            name="index.html",
            status=CheckStatus.MISSING,
            message="Build output directory does not exist",
            error_level=ErrorLevel.ERROR,
        )

    if not index_path.exists():
        return CheckResult(
            name="index.html",
            status=CheckStatus.MISSING,
            message="index.html not found - frontend build missing",
            error_level=ErrorLevel.ERROR,
        )

    # Check if it's a valid HTML file
    try:
        content = index_path.read_text(encoding="utf-8")
        if "<!DOCTYPE html>" not in content and "<!doctype html>" not in content:
            return CheckResult(
                name="index.html",
                status=CheckStatus.INVALID,
                message="index.html is not a valid HTML file",
                error_level=ErrorLevel.ERROR,
            )
    except Exception as e:
        return CheckResult(
            name="index.html",
            status=CheckStatus.INVALID,
            message=f"Failed to read index.html: {e}",
            error_level=ErrorLevel.ERROR,
        )

    return CheckResult(
        name="index.html",
        status=CheckStatus.OK,
        message="index.html exists and is valid",
    )


def check_manifest(dist_dir: Path) -> CheckResult:
    """Check if Vite manifest.json exists and is valid."""
    manifest_path = dist_dir / ".vite" / "manifest.json"

    if not manifest_path.exists():
        return CheckResult(
            name="manifest.json",
            status=CheckStatus.MISSING,
            message="Vite manifest.json not found - build may be incomplete",
            error_level=ErrorLevel.ERROR,
        )

    # Validate manifest is valid JSON
    try:
        content = manifest_path.read_text(encoding="utf-8")
        manifest = json.loads(content)

        # Check that manifest has expected structure
        if not isinstance(manifest, dict):
            return CheckResult(
                name="manifest.json",
                status=CheckStatus.INVALID,
                message="manifest.json is not a valid JSON object",
                error_level=ErrorLevel.ERROR,
            )

        # Check that manifest has at least one entry
        if len(manifest) == 0:
            return CheckResult(
                name="manifest.json",
                status=CheckStatus.INVALID,
                message="manifest.json is empty - no build artifacts registered",
                error_level=ErrorLevel.ERROR,
            )

    except json.JSONDecodeError as e:
        return CheckResult(
            name="manifest.json",
            status=CheckStatus.INVALID,
            message=f"manifest.json contains invalid JSON: {e}",
            error_level=ErrorLevel.ERROR,
        )
    except Exception as e:
        return CheckResult(
            name="manifest.json",
            status=CheckStatus.INVALID,
            message=f"Failed to read manifest.json: {e}",
            error_level=ErrorLevel.ERROR,
        )

    return CheckResult(
        name="manifest.json",
        status=CheckStatus.OK,
        message=f"manifest.json exists and is valid ({len(manifest)} entries)",
    )


def check_main_js(dist_dir: Path) -> CheckResult:
    """Check if main entry JavaScript file exists."""
    if not dist_dir.exists():
        return CheckResult(
            name="main.*.js",
            status=CheckStatus.MISSING,
            message="Build output directory does not exist",
            error_level=ErrorLevel.ERROR,
        )

    # Find main.*.js files
    main_js_files = list(dist_dir.glob("main.*.js"))

    if len(main_js_files) == 0:
        return CheckResult(
            name="main.*.js",
            status=CheckStatus.MISSING,
            message="Main entry JavaScript file not found - build incomplete",
            error_level=ErrorLevel.ERROR,
        )

    # Check if at least one main.*.js file has reasonable size (> 0 bytes)
    for main_js in main_js_files:
        if main_js.stat().st_size > 0:
            return CheckResult(
                name="main.*.js",
                status=CheckStatus.OK,
                message=f"Main entry file exists: {main_js.name}",
            )

    return CheckResult(
        name="main.*.js",
        status=CheckStatus.INVALID,
        message="Main entry JavaScript files are empty",
        error_level=ErrorLevel.ERROR,
    )


def check_frontend_build_integrity(
    skip_check: bool = False, strict: bool = False
) -> FrontendBuildCheckResult:
    """
    Check frontend build artifacts integrity.

    Args:
        skip_check: If True, skip all checks (for development/testing)
        strict: If True, treat warnings as errors

    Returns:
        FrontendBuildCheckResult with check results and any errors/warnings
    """
    result = FrontendBuildCheckResult(success=True)

    if skip_check:
        result.add_warning("Frontend build check skipped (OPENACE_SKIP_FRONTEND_CHECK=1)")
        return result

    dist_dir = get_dist_dir()

    # Run all checks
    checks = [
        check_index_html(dist_dir),
        check_manifest(dist_dir),
        check_main_js(dist_dir),
    ]

    result.checks = checks

    # Process check results
    for check in checks:
        if check.status != CheckStatus.OK:
            if check.error_level == ErrorLevel.ERROR:
                result.add_error(f"{check.name}: {check.message}")
            elif check.error_level == ErrorLevel.WARNING:
                if strict:
                    result.add_error(f"{check.name}: {check.message}")
                else:
                    result.add_warning(f"{check.name}: {check.message}")

    return result


def format_error_message(result: FrontendBuildCheckResult) -> str:
    """Format a detailed error message for display."""
    lines = [
        "=" * 40,
        "  ERROR: Frontend build artifacts missing or incomplete",
        "=" * 40,
        "",
        "Check results:",
    ]

    for check in result.checks:
        status_icon = "✓" if check.status == CheckStatus.OK else "✗"
        lines.append(f"  {status_icon} {check.name}: {check.status.value.upper()}")

    lines.extend(
        [
            "",
            "The management platform cannot start without frontend build artifacts.",
            "",
            "To fix this issue:",
            "",
            "1. Build the frontend:",
            "   cd frontend && npm run build",
            "",
            "2. Verify the build:",
            "   ls -la ../static/js/dist/",
            "   ls -la ../static/js/dist/.vite/",
            "",
            "3. Restart the application",
            "",
            "For Docker deployments:",
            "   The build is done automatically during 'docker build'.",
            "   Ensure you are using the official Docker image.",
            "",
            "For non-Docker deployments:",
            "   Run 'npm run build' in the frontend directory before starting the server.",
            "",
            "Documentation: https://github.com/open-ace/open-ace/issues/3277",
            "=" * 40,
        ]
    )

    return "\n".join(lines)


def check_frontend_build_on_startup(
    flask_env: str | None = None, skip_env_var: str | None = None
) -> None:
    """
    Check frontend build on application startup.

    This function is intended to be called during Flask app initialization.
    It will raise RuntimeError if frontend build is missing in production mode.

    Args:
        flask_env: The FLASK_ENV value (from app.config or os.environ)
        skip_env_var: Value of OPENACE_SKIP_FRONTEND_CHECK env var

    Raises:
        RuntimeError: If frontend build is missing and not in development mode
    """
    # Check environment variables
    if flask_env is None:
        flask_env = os.environ.get("FLASK_ENV", "development")

    if skip_env_var is None:
        skip_env_var = os.environ.get("OPENACE_SKIP_FRONTEND_CHECK", "")

    # Skip check if explicitly requested
    if skip_env_var == "1":
        return

    # Skip check in development and testing environments
    if flask_env in ("development", "testing"):
        # In development, just log a warning if build is missing
        result = check_frontend_build_integrity(skip_check=False)
        if not result.success:
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(
                "Frontend build artifacts are missing. Management platform UI will not be available."
            )
        return

    # In production, perform strict check
    result = check_frontend_build_integrity(skip_check=False, strict=True)

    if not result.success:
        error_msg = format_error_message(result)
        raise RuntimeError(error_msg)


def get_frontend_build_status() -> dict:
    """
    Get frontend build status for health check endpoint.

    Returns:
        Dict with status and individual check results
    """
    result = check_frontend_build_integrity(skip_check=False)

    checks_dict = {}
    for check in result.checks:
        checks_dict[check.name.replace(".*", "")] = {
            "status": check.status.value,
            "message": check.message if check.status != CheckStatus.OK else None,
        }

    return {
        "status": "ok" if result.success else "missing",
        "checks": checks_dict,
        "errors": result.errors if result.errors else None,
        "warnings": result.warnings if result.warnings else None,
    }
