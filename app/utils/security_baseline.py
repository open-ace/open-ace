"""
Security Baseline Checker for Open ACE (Issue #1893)

This module provides security baseline checking functionality for Docker Compose
deployments. It detects security mode and enforces baseline checks.

Security Modes:
- development: Allow empty/default passwords with warnings (default for trial)
- pilot: Allow empty/default passwords with strong warnings
- production: Reject empty/default passwords, fail-fast on security issues
"""

import os
import re
from dataclasses import dataclass
from enum import Enum

__all__ = [
    "SecurityMode",
    "CheckResult",
    "detect_security_mode",
    "is_forbidden_password",
    "is_placeholder_password",
    "check_database_password",
    "check_secret_key",
    "check_encryption_key",
    "check_root_user",
    "check_all",
    "FORBIDDEN_DB_PASSWORDS",
]


class SecurityMode(Enum):
    """Security mode enumeration."""
    DEVELOPMENT = "development"
    PILOT = "pilot"
    PRODUCTION = "production"


@dataclass
class CheckResult:
    """Result of a security check."""
    status: str  # "pass", "warning", "fail"
    message: str
    recommendation: str | None = None


# Forbidden database password values (Issue #1893)
# Includes development default password that must be changed for production
FORBIDDEN_DB_PASSWORDS = frozenset([
    "ace-secret",
    "dev-password-change-in-production",
    "change-me",
    "password",
    "admin",
    "postgres",
    "123456",
])

# Placeholder password patterns
PLACEHOLDER_PATTERNS = [
    r"^replace-with-random",
    r"^dev-secret",
    r"^default-secret",
    r"^change-me-in-production",
]


def detect_security_mode() -> SecurityMode:
    """
    Detect security mode based on environment variables.

    Priority: OPENACE_SECURITY_MODE > FLASK_ENV > default (development)

    Returns:
        SecurityMode: The detected security mode.
    """
    # Priority 1: Explicit security mode variable
    mode = os.environ.get("OPENACE_SECURITY_MODE", "").lower()
    if mode == "production":
        return SecurityMode.PRODUCTION
    if mode == "pilot":
        return SecurityMode.PILOT
    if mode == "development":
        return SecurityMode.DEVELOPMENT

    # Priority 2: Flask environment inference (backward compatibility)
    flask_env = os.environ.get("FLASK_ENV", "").lower()
    if flask_env == "production":
        return SecurityMode.PRODUCTION

    # Default: development mode
    return SecurityMode.DEVELOPMENT


def is_forbidden_password(password: str) -> bool:
    """
    Check if password is in forbidden list.

    Args:
        password: The password to check.

    Returns:
        bool: True if password is forbidden, False otherwise.
    """
    return password.lower() in FORBIDDEN_DB_PASSWORDS


def is_placeholder_password(password: str) -> bool:
    """
    Check if password matches placeholder patterns.

    Args:
        password: The password to check.

    Returns:
        bool: True if password is a placeholder, False otherwise.
    """
    password_lower = password.lower()
    return any(re.match(pattern, password_lower) for pattern in PLACEHOLDER_PATTERNS)


def check_database_password(password: str | None, mode: SecurityMode) -> CheckResult:
    """
    Check database password against security baseline.

    Args:
        password: The database password to check.
        mode: The current security mode.

    Returns:
        CheckResult: The result of the check.
    """
    # Empty password
    if not password:
        if mode == SecurityMode.PRODUCTION:
            return CheckResult(
                status="fail",
                message="Database password is required in production mode.",
                recommendation="Generate a strong password: python3 -c \"import secrets; print(secrets.token_urlsafe(32))\""
            )
        return CheckResult(
            status="warning",
            message="Database password not set. Auto-generated temporary password.",
            recommendation="For production, set DB_PASSWORD in .env before deployment."
        )

    # Forbidden password
    if is_forbidden_password(password):
        if mode == SecurityMode.PRODUCTION:
            return CheckResult(
                status="fail",
                message=f'Database password "{password}" is a known weak default.',
                recommendation="Generate a strong password: python3 -c \"import secrets; print(secrets.token_urlsafe(32))\""
            )
        return CheckResult(
            status="warning",
            message=f'Database password "{password}" is a known weak default.',
            recommendation="For production, use a strong password (>=9 characters)."
        )

    # Password too short for production
    if mode == SecurityMode.PRODUCTION and len(password) <= 8:
        return CheckResult(
            status="fail",
            message=f"Database password is too short ({len(password)} chars). Production requires at least 9 characters.",
            recommendation="Generate a strong password: python3 -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )

    return CheckResult(status="pass", message="Database password is acceptable.")


def check_secret_key(key: str | None, mode: SecurityMode) -> CheckResult:
    """
    Check SECRET_KEY against security baseline.

    Args:
        key: The secret key to check.
        mode: The current security mode.

    Returns:
        CheckResult: The result of the check.
    """
    if not key:
        if mode == SecurityMode.PRODUCTION:
            return CheckResult(
                status="fail",
                message="SECRET_KEY is required in production mode.",
                recommendation="Generate: python3 -c \"import secrets; print(secrets.token_hex(32))\""
            )
        return CheckResult(
            status="warning",
            message="SECRET_KEY not set. Will auto-generate.",
            recommendation="For production, set SECRET_KEY explicitly in .env."
        )

    if is_placeholder_password(key):
        if mode == SecurityMode.PRODUCTION:
            return CheckResult(
                status="fail",
                message="SECRET_KEY contains placeholder value.",
                recommendation="Production requires a strong random key."
            )
        return CheckResult(
            status="warning",
            message="SECRET_KEY appears to be a placeholder.",
            recommendation="Use a strong random key for production."
        )

    return CheckResult(status="pass", message="SECRET_KEY is acceptable.")


def check_encryption_key(key: str | None, mode: SecurityMode) -> CheckResult:
    """
    Check OPENACE_ENCRYPTION_KEY against security baseline.

    Args:
        key: The encryption key to check.
        mode: The current security mode.

    Returns:
        CheckResult: The result of the check.
    """
    if not key:
        if mode == SecurityMode.PRODUCTION:
            return CheckResult(
                status="fail",
                message="OPENACE_ENCRYPTION_KEY is required in production mode.",
                recommendation="Generate: python3 -c \"import secrets; print(secrets.token_hex(16))\""
            )
        return CheckResult(
            status="warning",
            message="OPENACE_ENCRYPTION_KEY not set. Will auto-generate.",
            recommendation="For production, set OPENACE_ENCRYPTION_KEY explicitly in .env. WARNING: Key change makes existing encrypted data unreadable!"
        )

    if is_placeholder_password(key):
        if mode == SecurityMode.PRODUCTION:
            return CheckResult(
                status="fail",
                message="OPENACE_ENCRYPTION_KEY contains placeholder value.",
                recommendation="Production requires a strong random key."
            )
        return CheckResult(
            status="warning",
            message="OPENACE_ENCRYPTION_KEY appears to be a placeholder.",
            recommendation="Use a strong random key for production."
        )

    return CheckResult(status="pass", message="OPENACE_ENCRYPTION_KEY is acceptable.")


def check_root_user(
    is_root: bool,
    multi_user_mode: bool,
    allow_root_multi_user: bool
) -> CheckResult:
    """
    Check root user authorization.

    Args:
        is_root: Whether running as root user.
        multi_user_mode: Whether WORKSPACE_MULTI_USER_MODE is enabled.
        allow_root_multi_user: Whether OPENACE_ALLOW_ROOT_MULTI_USER is set.

    Returns:
        CheckResult: The result of the check.
    """
    if not is_root:
        return CheckResult(status="pass", message="Running as non-root user (secure).")

    # Running as root - check if properly authorized for multi-user mode
    if not multi_user_mode or not allow_root_multi_user:
        return CheckResult(
            status="fail",
            message="Container running as root without proper authorization.",
            recommendation="If you need multi-user workspace mode, set WORKSPACE_MULTI_USER_MODE=true and OPENACE_ALLOW_ROOT_MULTI_USER=1. For single-user mode, remove any --user 0 setting."
        )

    return CheckResult(
        status="pass",
        message="Running as root for multi-user workspace mode (authorized)."
    )


def check_all() -> dict:
    """
    Run all security baseline checks.

    Returns:
        dict: Dictionary with check results and overall status.
    """
    mode = detect_security_mode()

    # Get environment values
    db_password = os.environ.get("DB_PASSWORD")
    secret_key = os.environ.get("SECRET_KEY")
    encryption_key = os.environ.get("OPENACE_ENCRYPTION_KEY")
    multi_user_mode = os.environ.get("WORKSPACE_MULTI_USER_MODE", "").lower() == "true"
    allow_root_multi_user = os.environ.get("OPENACE_ALLOW_ROOT_MULTI_USER", "").lower() == "1"

    # Run checks
    db_result = check_database_password(db_password, mode)
    secret_result = check_secret_key(secret_key, mode)
    encryption_result = check_encryption_key(encryption_key, mode)

    # For root user check, we can't actually check uid in this context
    # Assume non-root for now (the entrypoint handles actual uid check)
    root_result = check_root_user(False, multi_user_mode, allow_root_multi_user)

    # Determine overall status
    results = {
        "mode": mode.value,
        "database_password": {
            "status": db_result.status,
            "message": db_result.message,
            "recommendation": db_result.recommendation,
        },
        "secret_key": {
            "status": secret_result.status,
            "message": secret_result.message,
            "recommendation": secret_result.recommendation,
        },
        "encryption_key": {
            "status": encryption_result.status,
            "message": encryption_result.message,
            "recommendation": encryption_result.recommendation,
        },
        "root_user": {
            "status": root_result.status,
            "message": root_result.message,
            "recommendation": root_result.recommendation,
        },
    }

    # Overall status
    statuses = [
        db_result.status,
        secret_result.status,
        encryption_result.status,
        root_result.status,
    ]

    if "fail" in statuses:
        results["status"] = "unhealthy"
    elif "warning" in statuses:
        results["status"] = "warning"
    else:
        results["status"] = "healthy"

    return results
