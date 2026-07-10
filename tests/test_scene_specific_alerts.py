"""
Tests for scene-specific alert functions.

Tests severity determination logic for system and security alert scenarios.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.modules.governance.alert_notifier import (
    Alert,
    AlertSeverity,
    AlertType,
    create_api_error_alert,
    create_auth_failure_alert,
    create_config_error_alert,
    create_permission_violation_alert,
    create_quota_alert,
    create_resource_alert,
    create_service_down_alert,
    create_service_startup_alert,
    create_suspicious_activity_alert,
)


class TestServiceDownAlert:
    """Tests for create_service_down_alert."""

    @patch("app.modules.governance.alert_notifier.create_system_alert")
    def test_service_down_is_critical(self, mock_create_system):
        """Service down should always be CRITICAL."""
        mock_alert = MagicMock(spec=Alert)
        mock_create_system.return_value = mock_alert

        create_service_down_alert(
            service_name="API Gateway",
            details="Service crashed after memory exhaustion",
        )

        mock_create_system.assert_called_once()
        call_kwargs = mock_create_system.call_args.kwargs

        assert call_kwargs["severity"] == AlertSeverity.CRITICAL.value
        assert "Service Down: API Gateway" in call_kwargs["title"]
        assert "API Gateway" in call_kwargs["message"]

    @patch("app.modules.governance.alert_notifier.create_system_alert")
    def test_service_down_with_language(self, mock_create_system):
        """Service down alert should support language parameter."""
        mock_alert = MagicMock(spec=Alert)
        mock_create_system.return_value = mock_alert

        create_service_down_alert(
            service_name="Database",
            details="Connection refused",
            language="zh",
        )

        call_kwargs = mock_create_system.call_args.kwargs
        assert call_kwargs["language"] == "zh"


class TestServiceStartupAlert:
    """Tests for create_service_startup_alert."""

    @patch("app.modules.governance.alert_notifier.create_system_alert")
    def test_startup_time_warning(self, mock_create_system):
        """Startup time exceeding threshold should be WARNING."""
        mock_alert = MagicMock(spec=Alert)
        mock_create_system.return_value = mock_alert

        # startup_time = 15s, threshold = 10s -> 1.5x threshold -> WARNING
        create_service_startup_alert(
            service_name="Web Server",
            startup_time=15.0,
            threshold=10.0,
        )

        call_kwargs = mock_create_system.call_args.kwargs
        assert call_kwargs["severity"] == AlertSeverity.WARNING.value
        assert "Service Startup Warning" in call_kwargs["title"]

    @patch("app.modules.governance.alert_notifier.create_system_alert")
    def test_startup_time_critical(self, mock_create_system):
        """Startup time exceeding 2x threshold should be CRITICAL."""
        mock_alert = MagicMock(spec=Alert)
        mock_create_system.return_value = mock_alert

        # startup_time = 25s, threshold = 10s -> 2.5x threshold -> CRITICAL
        create_service_startup_alert(
            service_name="Web Server",
            startup_time=25.0,
            threshold=10.0,
        )

        call_kwargs = mock_create_system.call_args.kwargs
        assert call_kwargs["severity"] == AlertSeverity.CRITICAL.value
        assert "Service Startup Critical" in call_kwargs["title"]


class TestResourceAlert:
    """Tests for create_resource_alert."""

    @patch("app.modules.governance.alert_notifier.create_system_alert")
    def test_resource_exhausted(self, mock_create_system):
        """Resource at 100% usage should be CRITICAL."""
        mock_alert = MagicMock(spec=Alert)
        mock_create_system.return_value = mock_alert

        create_resource_alert(
            resource_type="memory",
            current=8192,
            limit=8192,
        )

        call_kwargs = mock_create_system.call_args.kwargs
        assert call_kwargs["severity"] == AlertSeverity.CRITICAL.value
        assert "Resource Exhausted" in call_kwargs["title"]

    @patch("app.modules.governance.alert_notifier.create_system_alert")
    def test_resource_critical(self, mock_create_system):
        """Resource at 95%+ usage should be CRITICAL."""
        mock_alert = MagicMock(spec=Alert)
        mock_create_system.return_value = mock_alert

        create_resource_alert(
            resource_type="cpu",
            current=95,
            limit=100,
        )

        call_kwargs = mock_create_system.call_args.kwargs
        assert call_kwargs["severity"] == AlertSeverity.CRITICAL.value
        assert "Resource Critical" in call_kwargs["title"]

    @patch("app.modules.governance.alert_notifier.create_system_alert")
    def test_resource_warning(self, mock_create_system):
        """Resource at 80%+ usage should be WARNING."""
        mock_alert = MagicMock(spec=Alert)
        mock_create_system.return_value = mock_alert

        create_resource_alert(
            resource_type="disk",
            current=85,
            limit=100,
        )

        call_kwargs = mock_create_system.call_args.kwargs
        assert call_kwargs["severity"] == AlertSeverity.WARNING.value
        assert "Resource Warning" in call_kwargs["title"]

    @patch("app.modules.governance.alert_notifier.create_system_alert")
    def test_resource_info(self, mock_create_system):
        """Resource below 80% usage should be INFO."""
        mock_alert = MagicMock(spec=Alert)
        mock_create_system.return_value = mock_alert

        create_resource_alert(
            resource_type="disk",
            current=50,
            limit=100,
        )

        call_kwargs = mock_create_system.call_args.kwargs
        assert call_kwargs["severity"] == AlertSeverity.INFO.value
        assert "Resource Notice" in call_kwargs["title"]


class TestConfigErrorAlert:
    """Tests for create_config_error_alert."""

    @patch("app.modules.governance.alert_notifier.create_system_alert")
    def test_config_error_is_warning(self, mock_create_system):
        """Configuration error should be WARNING."""
        mock_alert = MagicMock(spec=Alert)
        mock_create_system.return_value = mock_alert

        create_config_error_alert(
            config_key="database_url",
            error_details="Invalid connection string format",
        )

        call_kwargs = mock_create_system.call_args.kwargs
        assert call_kwargs["severity"] == AlertSeverity.WARNING.value
        assert "Configuration Error" in call_kwargs["title"]


class TestApiErrorAlert:
    """Tests for create_api_error_alert."""

    @patch("app.modules.governance.alert_notifier.create_system_alert")
    def test_api_error_is_warning(self, mock_create_system):
        """API error should be WARNING."""
        mock_alert = MagicMock(spec=Alert)
        mock_create_system.return_value = mock_alert

        create_api_error_alert(
            api_name="External Service",
            error_code=500,
            error_message="Internal Server Error",
        )

        call_kwargs = mock_create_system.call_args.kwargs
        assert call_kwargs["severity"] == AlertSeverity.WARNING.value
        assert "API Error" in call_kwargs["title"]


class TestAuthFailureAlert:
    """Tests for create_auth_failure_alert."""

    @patch("app.modules.governance.alert_notifier.create_security_alert")
    def test_single_auth_failure_warning(self, mock_create_security):
        """Single auth failure should be WARNING."""
        mock_alert = MagicMock(spec=Alert)
        mock_create_security.return_value = mock_alert

        create_auth_failure_alert(
            username="testuser",
            failure_count=1,
            threshold=5,
        )

        call_kwargs = mock_create_security.call_args.kwargs
        assert call_kwargs["severity"] == AlertSeverity.WARNING.value
        assert "Authentication Failure" in call_kwargs["title"]
        assert call_kwargs["username"] == "testuser"

    @patch("app.modules.governance.alert_notifier.create_security_alert")
    def test_repeated_auth_failure_critical(self, mock_create_security):
        """Repeated auth failures (>= threshold) should be CRITICAL."""
        mock_alert = MagicMock(spec=Alert)
        mock_create_security.return_value = mock_alert

        create_auth_failure_alert(
            username="attacker",
            failure_count=7,
            threshold=5,
        )

        call_kwargs = mock_create_security.call_args.kwargs
        assert call_kwargs["severity"] == AlertSeverity.CRITICAL.value
        assert "Authentication Failure Alert" in call_kwargs["title"]
        assert "brute-force" in call_kwargs["message"].lower()


class TestPermissionViolationAlert:
    """Tests for create_permission_violation_alert."""

    @patch("app.modules.governance.alert_notifier.create_security_alert")
    def test_permission_violation_is_critical(self, mock_create_security):
        """Permission violation should be CRITICAL."""
        mock_alert = MagicMock(spec=Alert)
        mock_create_security.return_value = mock_alert

        create_permission_violation_alert(
            username="hacker",
            resource="/admin/users",
            action="delete",
        )

        call_kwargs = mock_create_security.call_args.kwargs
        assert call_kwargs["severity"] == AlertSeverity.CRITICAL.value
        assert "Permission Violation" in call_kwargs["title"]


class TestSuspiciousActivityAlert:
    """Tests for create_suspicious_activity_alert."""

    @patch("app.modules.governance.alert_notifier.create_security_alert")
    def test_low_risk_warning(self, mock_create_security):
        """Low risk score (< 50) should be WARNING."""
        mock_alert = MagicMock(spec=Alert)
        mock_create_security.return_value = mock_alert

        create_suspicious_activity_alert(
            username="normaluser",
            activity_type="unusual_login_time",
            risk_score=30.0,
        )

        call_kwargs = mock_create_security.call_args.kwargs
        assert call_kwargs["severity"] == AlertSeverity.WARNING.value
        assert "Suspicious Activity" in call_kwargs["title"]

    @patch("app.modules.governance.alert_notifier.create_security_alert")
    def test_high_risk_critical(self, mock_create_security):
        """High risk score (>= 50) should be CRITICAL."""
        mock_alert = MagicMock(spec=Alert)
        mock_create_security.return_value = mock_alert

        create_suspicious_activity_alert(
            username="suspect",
            activity_type="mass_data_download",
            risk_score=75.0,
        )

        call_kwargs = mock_create_security.call_args.kwargs
        assert call_kwargs["severity"] == AlertSeverity.CRITICAL.value
        assert "High-Risk Activity" in call_kwargs["title"]


class TestRegressionQuotaAlert:
    """Regression tests to ensure create_quota_alert still works."""

    @patch("app.modules.governance.alert_notifier.get_alert_notifier")
    def test_quota_alert_still_works(self, mock_get_notifier):
        """Existing quota alert function should continue to work."""
        mock_notifier = MagicMock()
        mock_alert = MagicMock(spec=Alert)
        mock_notifier.create_alert.return_value = mock_alert
        mock_get_notifier.return_value = mock_notifier

        # Test quota alert at 95%
        create_quota_alert(
            user_id=1,
            username="testuser",
            usage_percent=95.0,
            quota_type="tokens",
        )

        call_kwargs = mock_notifier.create_alert.call_args.kwargs
        assert call_kwargs["severity"] == AlertSeverity.CRITICAL.value
        assert call_kwargs["alert_type"] == AlertType.QUOTA.value
