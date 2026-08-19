"""Unit tests for organization sync exceptions."""

from __future__ import annotations

import pytest

from app.exceptions import DingTalkSyncError, FeishuSyncError, OrgSyncError


class TestOrgSyncError:
    """Tests for OrgSyncError base class."""

    def test_base_exception_creation(self):
        """OrgSyncError should initialize with all attributes."""
        exc = OrgSyncError(
            message="Test error",
            code="TEST_ERROR",
            provider="test_provider",
            http_status=400,
            details={"key": "value"},
        )

        assert str(exc) == "Test error"
        assert exc.code == "TEST_ERROR"
        assert exc.provider == "test_provider"
        assert exc.http_status == 400
        assert exc.details == {"key": "value"}

    def test_default_http_status(self):
        """OrgSyncError should default to HTTP 400."""
        exc = OrgSyncError(
            message="Test",
            code="TEST",
            provider="test",
        )

        assert exc.http_status == 400

    def test_default_details_includes_provider(self):
        """Details should default to containing provider if not specified."""
        exc = OrgSyncError(
            message="Test",
            code="TEST",
            provider="test_provider",
        )

        assert exc.details == {"provider": "test_provider"}

    def test_details_overrides_default(self):
        """Explicit details should override the default."""
        exc = OrgSyncError(
            message="Test",
            code="TEST",
            provider="test_provider",
            details={"custom": "value"},
        )

        assert exc.details == {"custom": "value"}


class TestFeishuSyncError:
    """Tests for FeishuSyncError."""

    def test_creates_with_provider_feishu(self):
        """FeishuSyncError should automatically set provider='feishu'."""
        exc = FeishuSyncError(
            message="Feishu error",
            code=FeishuSyncError.CODE_CREDENTIALS_MISSING,
        )

        assert exc.provider == "feishu"
        assert exc.code == "FEISHU_CREDENTIALS_MISSING"
        assert exc.http_status == 400
        assert exc.details == {"provider": "feishu"}

    def test_all_error_codes_are_stable(self):
        """Verify all Feishu error codes are defined and stable."""
        assert FeishuSyncError.CODE_CREDENTIALS_MISSING == "FEISHU_CREDENTIALS_MISSING"
        assert FeishuSyncError.CODE_CREDENTIALS_PLACEHOLDER == "FEISHU_CREDENTIALS_PLACEHOLDER"
        assert FeishuSyncError.CODE_TARGET_TENANT_MISSING == "FEISHU_TARGET_TENANT_MISSING"

    def test_custom_http_status(self):
        """FeishuSyncError should accept custom HTTP status."""
        exc = FeishuSyncError(
            message="Custom status",
            code=FeishuSyncError.CODE_CREDENTIALS_MISSING,
            http_status=403,
        )

        assert exc.http_status == 403

    def test_custom_details(self):
        """FeishuSyncError should accept custom details."""
        exc = FeishuSyncError(
            message="With details",
            code=FeishuSyncError.CODE_CREDENTIALS_PLACEHOLDER,
            details={"app_id": "placeholder_value"},
        )

        assert exc.details == {"app_id": "placeholder_value"}


class TestDingTalkSyncError:
    """Tests for DingTalkSyncError."""

    def test_creates_with_provider_dingtalk(self):
        """DingTalkSyncError should automatically set provider='dingtalk'."""
        exc = DingTalkSyncError(
            message="DingTalk error",
            code=DingTalkSyncError.CODE_CREDENTIALS_MISSING,
        )

        assert exc.provider == "dingtalk"
        assert exc.code == "DINGTALK_CREDENTIALS_MISSING"
        assert exc.http_status == 400
        assert exc.details == {"provider": "dingtalk"}

    def test_all_error_codes_are_stable(self):
        """Verify all DingTalk error codes are defined and stable."""
        assert DingTalkSyncError.CODE_CREDENTIALS_MISSING == "DINGTALK_CREDENTIALS_MISSING"
        assert DingTalkSyncError.CODE_CREDENTIALS_PLACEHOLDER == "DINGTALK_CREDENTIALS_PLACEHOLDER"
        assert DingTalkSyncError.CODE_TARGET_TENANT_MISSING == "DINGTALK_TARGET_TENANT_MISSING"

    def test_custom_http_status(self):
        """DingTalkSyncError should accept custom HTTP status."""
        exc = DingTalkSyncError(
            message="Custom status",
            code=DingTalkSyncError.CODE_CREDENTIALS_MISSING,
            http_status=403,
        )

        assert exc.http_status == 403

    def test_custom_details(self):
        """DingTalkSyncError should accept custom details."""
        exc = DingTalkSyncError(
            message="With details",
            code=DingTalkSyncError.CODE_CREDENTIALS_PLACEHOLDER,
            details={"app_key": "placeholder_value"},
        )

        assert exc.details == {"app_key": "placeholder_value"}


class TestExceptionHierarchy:
    """Tests for exception inheritance."""

    def test_feishu_is_org_sync_error(self):
        """FeishuSyncError should be an instance of OrgSyncError."""
        exc = FeishuSyncError(
            message="Test",
            code=FeishuSyncError.CODE_CREDENTIALS_MISSING,
        )

        assert isinstance(exc, OrgSyncError)
        assert isinstance(exc, Exception)

    def test_dingtalk_is_org_sync_error(self):
        """DingTalkSyncError should be an instance of OrgSyncError."""
        exc = DingTalkSyncError(
            message="Test",
            code=DingTalkSyncError.CODE_CREDENTIALS_MISSING,
        )

        assert isinstance(exc, OrgSyncError)
        assert isinstance(exc, Exception)

    def test_can_catch_with_base_class(self):
        """Should be able to catch all sync errors with OrgSyncError."""
        feishu_exc = FeishuSyncError(
            message="Feishu error",
            code=FeishuSyncError.CODE_CREDENTIALS_MISSING,
        )
        dingtalk_exc = DingTalkSyncError(
            message="DingTalk error",
            code=DingTalkSyncError.CODE_CREDENTIALS_MISSING,
        )

        # Should be catchable with base class
        with pytest.raises(OrgSyncError) as exc_info:
            raise feishu_exc
        assert "Feishu error" in str(exc_info.value)

        with pytest.raises(OrgSyncError) as exc_info:
            raise dingtalk_exc
        assert "DingTalk error" in str(exc_info.value)