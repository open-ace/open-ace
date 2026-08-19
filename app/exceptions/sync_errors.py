"""
Exceptions for organization synchronization.

These exceptions carry stable error codes that the frontend can use for i18n.
Error codes are guaranteed to be stable - they will not be removed, but may
be marked as deprecated in future versions.
"""

from __future__ import annotations

from typing import Any

from app.modules.governance.audit_logger import _sanitize_details

# Additional denylist for sync-specific sensitive fields
# These are not in the general audit denylist but should never appear in error responses
_SYNC_DETAILS_DENYLIST: frozenset[str] = frozenset(
    {
        "app_id",
        "app_key",
        "app_secret",
        "client_id",
        "client_secret",
    }
)


def _sanitize_sync_details(obj: Any) -> dict:
    """Sanitize details to remove sync-specific sensitive fields.

    Combines the general audit denylist with sync-specific fields.
    Recursively processes nested dicts.

    Args:
        obj: The details dict to sanitize.

    Returns:
        Sanitized dict with sensitive fields removed.
    """
    # First apply general audit sanitization (handles recursion)
    result = _sanitize_details(obj)
    # Then remove sync-specific fields at all levels
    if isinstance(result, dict):
        return {
            k: _sanitize_sync_details(v) if isinstance(v, dict) else v
            for k, v in result.items()
            if k not in _SYNC_DETAILS_DENYLIST
        }
    return result if result else {}


class OrgSyncError(Exception):
    """Base exception for organization sync errors.

    Carries a stable error code that the frontend can use for i18n mapping.
    Subclasses should set the provider attribute automatically.

    Attributes:
        code: Stable error code for frontend i18n (e.g., "FEISHU_CREDENTIALS_MISSING")
        http_status: HTTP status code to return (default: 400)
        details: Optional dict with additional context (must not contain sensitive data)
        provider: Sync provider name (e.g., "feishu", "dingtalk")
    """

    code: str
    http_status: int = 400
    details: dict | None = None
    provider: str = ""

    def __init__(
        self,
        message: str,
        code: str,
        provider: str,
        http_status: int = 400,
        details: dict | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.provider = provider
        self.http_status = http_status
        # Sanitize details to remove sensitive fields (app_secret, token, password, etc.)
        # This ensures credentials are never leaked in error responses
        self.details = _sanitize_sync_details(details) if details else {"provider": provider}


class FeishuSyncError(OrgSyncError):
    """Feishu-specific sync error.

    Automatically sets provider="feishu".

    Error codes:
        FEISHU_CREDENTIALS_MISSING: app_id or app_secret is empty
        FEISHU_CREDENTIALS_PLACEHOLDER: Detected placeholder values
        FEISHU_TARGET_TENANT_MISSING: org_sync_tenant_id not configured
    """

    # Stable error codes for Feishu sync
    CODE_CREDENTIALS_MISSING = "FEISHU_CREDENTIALS_MISSING"
    CODE_CREDENTIALS_PLACEHOLDER = "FEISHU_CREDENTIALS_PLACEHOLDER"
    CODE_TARGET_TENANT_MISSING = "FEISHU_TARGET_TENANT_MISSING"

    def __init__(
        self,
        message: str,
        code: str,
        http_status: int = 400,
        details: dict | None = None,
    ):
        super().__init__(
            message=message,
            code=code,
            provider="feishu",
            http_status=http_status,
            details=details,
        )


class DingTalkSyncError(OrgSyncError):
    """DingTalk-specific sync error.

    Automatically sets provider="dingtalk".

    Error codes:
        DINGTALK_CREDENTIALS_MISSING: app_key or app_secret is empty
        DINGTALK_CREDENTIALS_PLACEHOLDER: Detected placeholder values
        DINGTALK_TARGET_TENANT_MISSING: org_sync_tenant_id not configured
    """

    # Stable error codes for DingTalk sync
    CODE_CREDENTIALS_MISSING = "DINGTALK_CREDENTIALS_MISSING"
    CODE_CREDENTIALS_PLACEHOLDER = "DINGTALK_CREDENTIALS_PLACEHOLDER"
    CODE_TARGET_TENANT_MISSING = "DINGTALK_TARGET_TENANT_MISSING"

    def __init__(
        self,
        message: str,
        code: str,
        http_status: int = 400,
        details: dict | None = None,
    ):
        super().__init__(
            message=message,
            code=code,
            provider="dingtalk",
            http_status=http_status,
            details=details,
        )
