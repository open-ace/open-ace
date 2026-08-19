"""
Exceptions for organization synchronization.

These exceptions carry stable error codes that the frontend can use for i18n.
Error codes are guaranteed to be stable - they will not be removed, but may
be marked as deprecated in future versions.
"""

from __future__ import annotations


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
        # Ensure details never contains sensitive data
        self.details = details if details is not None else {"provider": provider}


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
        )]
