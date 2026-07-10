"""Unit tests for AlertNotifier module - Scene-specific alert functions (Issue #1489)."""

from unittest.mock import MagicMock, patch

import pytest

from app.modules.governance.alert_notifier import (
    AlertNotifier,
    AlertSeverity,
    AlertType,
    create_service_down_alert,
    create_resource_alert,
    create_config_error_alert,
    create_api_error_alert,
    create_auth_failure_alert,
    create_permission_violation_alert,
    create_suspicious_activity_alert,
)


class TestCreateServiceDownAlert:
    """Test create_service_down_alert function."""

    @patch("app.modules.governance.alert_notifier.get_alert_notifier")
    def test_creates_critical_alert(self, mock_get_notifier):
        mock_notifier = MagicMock()
        mock_get_notifier.return_value = mock_notifier

        create_service_down_alert(
            service_name="api-server",
            details="Connection timeout after 30 seconds",
        )

        mock_notifier.create_alert.assert_called_once()
        call_args = mock_notifier.create_alert.call_args

        assert call_args.kwargs["alert_type"] == AlertType.SYSTEM.value
        assert call_args.kwargs["severity"] == AlertSeverity.CRITICAL.value
        assert "api-server" in call_args.kwargs["title"]
        assert call_args.kwargs["metadata"]["service_name"] == "api-server"

    @patch("app.modules.governance.alert_notifier.get_alert_notifier")
    def test_with_user_id(self, mock_get_notifier):
        mock_notifier = MagicMock()
        mock_get_notifier.return_value = mock_notifier

        create_service_down_alert(
            service_name="database",
            details="Connection refused",
            user_id=123,
        )

        call_args = mock_notifier.create_alert.call_args
        assert call_args.kwargs["user_id"] == 123


class TestCreateResourceAlert:
    """Test create_resource_alert function with automatic severity."""

    @patch("app.modules.governance.alert_notifier.get_alert_notifier")
    def test_critical_threshold(self, mock_get_notifier):
        mock_notifier = MagicMock()
        mock_get_notifier.return_value = mock_notifier

        create_resource_alert(
            resource_type="memory",
            current=9.5,
            limit=10.0,
        )

        call_args = mock_notifier.create_alert.call_args
        # 95% usage >= 0.95 threshold -> critical
        assert call_args.kwargs["severity"] == AlertSeverity.CRITICAL.value
        assert "Critical" in call_args.kwargs["title"]

    @patch("app.modules.governance.alert_notifier.get_alert_notifier")
    def test_warning_threshold(self, mock_get_notifier):
        mock_notifier = MagicMock()
        mock_get_notifier.return_value = mock_notifier

        create_resource_alert(
            resource_type="cpu",
            current=8.0,
            limit=10.0,
        )

        call_args = mock_notifier.create_alert.call_args
        # 80% usage >= 0.8 threshold -> warning
        assert call_args.kwargs["severity"] == AlertSeverity.WARNING.value
        assert "Warning" in call_args.kwargs["title"]

    @patch("app.modules.governance.alert_notifier.get_alert_notifier")
    def test_info_below_threshold(self, mock_get_notifier):
        mock_notifier = MagicMock()
        mock_get_notifier.return_value = mock_notifier

        create_resource_alert(
            resource_type="disk",
            current=5.0,
            limit=10.0,
        )

        call_args = mock_notifier.create_alert.call_args
        # 50% usage < 0.8 threshold -> info
        assert call_args.kwargs["severity"] == AlertSeverity.INFO.value
        assert "Notice" in call_args.kwargs["title"]

    @patch("app.modules.governance.alert_notifier.get_alert_notifier")
    def test_custom_thresholds(self, mock_get_notifier):
        mock_notifier = MagicMock()
        mock_get_notifier.return_value = mock_notifier

        create_resource_alert(
            resource_type="connections",
            current=50,
            limit=100,
            threshold_warning=0.4,
            threshold_critical=0.6,
        )

        call_args = mock_notifier.create_alert.call_args
        # 50% usage >= 0.4 (warning) but < 0.6 (critical) -> warning
        assert call_args.kwargs["severity"] == AlertSeverity.WARNING.value

    @patch("app.modules.governance.alert_notifier.get_alert_notifier")
    def test_metadata_contains_usage_info(self, mock_get_notifier):
        mock_notifier = MagicMock()
        mock_get_notifier.return_value = mock_notifier

        create_resource_alert(
            resource_type="memory",
            current=8.5,
            limit=10.0,
        )

        call_args = mock_notifier.create_alert.call_args
        metadata = call_args.kwargs["metadata"]
        assert metadata["resource_type"] == "memory"
        assert metadata["current"] == 8.5
        assert metadata["limit"] == 10.0
        assert "usage_percent" in metadata


class TestCreateConfigErrorAlert:
    """Test create_config_error_alert function."""

    @patch("app.modules.governance.alert_notifier.get_alert_notifier")
    def test_creates_warning_alert(self, mock_get_notifier):
        mock_notifier = MagicMock()
        mock_get_notifier.return_value = mock_notifier

        create_config_error_alert(
            config_key="database.url",
            error_details="Invalid connection string format",
        )

        call_args = mock_notifier.create_alert.call_args
        assert call_args.kwargs["alert_type"] == AlertType.SYSTEM.value
        assert call_args.kwargs["severity"] == AlertSeverity.WARNING.value
        assert "database.url" in call_args.kwargs["title"]
        assert call_args.kwargs["metadata"]["config_key"] == "database.url"


class TestCreateApiErrorAlert:
    """Test create_api_error_alert function."""

    @patch("app.modules.governance.alert_notifier.get_alert_notifier")
    def test_creates_warning_alert(self, mock_get_notifier):
        mock_notifier = MagicMock()
        mock_get_notifier.return_value = mock_notifier

        create_api_error_alert(
            api_name="openai",
            error_code=500,
            error_message="Internal server error",
        )

        call_args = mock_notifier.create_alert.call_args
        assert call_args.kwargs["alert_type"] == AlertType.SYSTEM.value
        assert call_args.kwargs["severity"] == AlertSeverity.WARNING.value
        assert "openai" in call_args.kwargs["title"]
        assert call_args.kwargs["metadata"]["error_code"] == 500


class TestCreateAuthFailureAlert:
    """Test create_auth_failure_alert function with automatic severity."""

    @patch("app.modules.governance.alert_notifier.get_alert_notifier")
    def test_warning_below_threshold(self, mock_get_notifier):
        mock_notifier = MagicMock()
        mock_get_notifier.return_value = mock_notifier

        create_auth_failure_alert(
            username="testuser",
            failure_count=2,
            threshold=5,
        )

        call_args = mock_notifier.create_alert.call_args
        assert call_args.kwargs["severity"] == AlertSeverity.WARNING.value
        assert call_args.kwargs["username"] == "testuser"

    @patch("app.modules.governance.alert_notifier.get_alert_notifier")
    def test_critical_at_threshold(self, mock_get_notifier):
        mock_notifier = MagicMock()
        mock_get_notifier.return_value = mock_notifier

        create_auth_failure_alert(
            username="attacker",
            failure_count=5,
            threshold=5,
        )

        call_args = mock_notifier.create_alert.call_args
        assert call_args.kwargs["severity"] == AlertSeverity.CRITICAL.value
        assert "Repeated" in call_args.kwargs["title"]

    @patch("app.modules.governance.alert_notifier.get_alert_notifier")
    def test_critical_above_threshold(self, mock_get_notifier):
        mock_notifier = MagicMock()
        mock_get_notifier.return_value = mock_notifier

        create_auth_failure_alert(
            username="suspect",
            failure_count=10,
            threshold=5,
        )

        call_args = mock_notifier.create_alert.call_args
        assert call_args.kwargs["severity"] == AlertSeverity.CRITICAL.value

    @patch("app.modules.governance.alert_notifier.get_alert_notifier")
    def test_metadata_contains_threshold_info(self, mock_get_notifier):
        mock_notifier = MagicMock()
        mock_get_notifier.return_value = mock_notifier

        create_auth_failure_alert(
            username="testuser",
            failure_count=3,
            threshold=5,
        )

        call_args = mock_notifier.create_alert.call_args
        metadata = call_args.kwargs["metadata"]
        assert metadata["failure_count"] == 3
        assert metadata["threshold"] == 5


class TestCreatePermissionViolationAlert:
    """Test create_permission_violation_alert function."""

    @patch("app.modules.governance.alert_notifier.get_alert_notifier")
    def test_creates_critical_alert(self, mock_get_notifier):
        mock_notifier = MagicMock()
        mock_get_notifier.return_value = mock_notifier

        create_permission_violation_alert(
            username="unauthorized_user",
            resource="/admin/settings",
            action="write",
        )

        call_args = mock_notifier.create_alert.call_args
        assert call_args.kwargs["alert_type"] == AlertType.SECURITY.value
        assert call_args.kwargs["severity"] == AlertSeverity.CRITICAL.value
        assert "unauthorized_user" in call_args.kwargs["title"]
        assert call_args.kwargs["metadata"]["resource"] == "/admin/settings"
        assert call_args.kwargs["metadata"]["action"] == "write"


class TestCreateSuspiciousActivityAlert:
    """Test create_suspicious_activity_alert function with automatic severity."""

    @patch("app.modules.governance.alert_notifier.get_alert_notifier")
    def test_warning_below_risk_threshold(self, mock_get_notifier):
        mock_notifier = MagicMock()
        mock_get_notifier.return_value = mock_notifier

        create_suspicious_activity_alert(
            username="user1",
            activity_type="unusual_login_time",
            risk_score=30.0,
        )

        call_args = mock_notifier.create_alert.call_args
        assert call_args.kwargs["severity"] == AlertSeverity.WARNING.value
        assert call_args.kwargs["username"] == "user1"

    @patch("app.modules.governance.alert_notifier.get_alert_notifier")
    def test_critical_at_risk_threshold(self, mock_get_notifier):
        mock_notifier = MagicMock()
        mock_get_notifier.return_value = mock_notifier

        create_suspicious_activity_alert(
            username="user2",
            activity_type="multiple_ip_addresses",
            risk_score=50.0,
        )

        call_args = mock_notifier.create_alert.call_args
        assert call_args.kwargs["severity"] == AlertSeverity.CRITICAL.value
        assert "High-Risk" in call_args.kwargs["title"]

    @patch("app.modules.governance.alert_notifier.get_alert_notifier")
    def test_critical_above_risk_threshold(self, mock_get_notifier):
        mock_notifier = MagicMock()
        mock_get_notifier.return_value = mock_notifier

        create_suspicious_activity_alert(
            username="user3",
            activity_type="brute_force_attempt",
            risk_score=85.0,
        )

        call_args = mock_notifier.create_alert.call_args
        assert call_args.kwargs["severity"] == AlertSeverity.CRITICAL.value

    @patch("app.modules.governance.alert_notifier.get_alert_notifier")
    def test_metadata_contains_risk_info(self, mock_get_notifier):
        mock_notifier = MagicMock()
        mock_get_notifier.return_value = mock_notifier

        create_suspicious_activity_alert(
            username="testuser",
            activity_type="rate_limit_exceeded",
            risk_score=45.0,
        )

        call_args = mock_notifier.create_alert.call_args
        metadata = call_args.kwargs["metadata"]
        assert metadata["activity_type"] == "rate_limit_exceeded"
        assert metadata["risk_score"] == 45.0


class TestBoundaryValidation:
    """Test boundary validation for alert functions (Agent d review)."""

    @patch("app.modules.governance.alert_notifier.get_alert_notifier")
    def test_create_resource_alert_limit_zero_raises(self, mock_get_notifier):
        """Test that limit=0 raises ValueError."""
        mock_notifier = MagicMock()
        mock_get_notifier.return_value = mock_notifier

        with pytest.raises(ValueError, match="limit must be positive"):
            create_resource_alert(
                resource_type="memory",
                current=5.0,
                limit=0,
            )

    @patch("app.modules.governance.alert_notifier.get_alert_notifier")
    def test_create_resource_alert_limit_negative_raises(self, mock_get_notifier):
        """Test that negative limit raises ValueError."""
        mock_notifier = MagicMock()
        mock_get_notifier.return_value = mock_notifier

        with pytest.raises(ValueError, match="limit must be positive"):
            create_resource_alert(
                resource_type="memory",
                current=5.0,
                limit=-10.0,
            )

    @patch("app.modules.governance.alert_notifier.get_alert_notifier")
    def test_create_resource_alert_current_negative_raises(self, mock_get_notifier):
        """Test that negative current raises ValueError."""
        mock_notifier = MagicMock()
        mock_get_notifier.return_value = mock_notifier

        with pytest.raises(ValueError, match="current must be non-negative"):
            create_resource_alert(
                resource_type="memory",
                current=-5.0,
                limit=10.0,
            )

    @patch("app.modules.governance.alert_notifier.get_alert_notifier")
    def test_create_resource_alert_current_zero_allowed(self, mock_get_notifier):
        """Test that current=0 is allowed (valid boundary)."""
        mock_notifier = MagicMock()
        mock_get_notifier.return_value = mock_notifier

        create_resource_alert(
            resource_type="memory",
            current=0.0,
            limit=10.0,
        )

        call_args = mock_notifier.create_alert.call_args
        # 0% usage -> info level
        assert call_args.kwargs["severity"] == AlertSeverity.INFO.value
        assert call_args.kwargs["metadata"]["usage_percent"] == 0

    @patch("app.modules.governance.alert_notifier.get_alert_notifier")
    def test_create_suspicious_activity_alert_risk_score_below_zero_raises(self, mock_get_notifier):
        """Test that risk_score < 0 raises ValueError."""
        mock_notifier = MagicMock()
        mock_get_notifier.return_value = mock_notifier

        with pytest.raises(ValueError, match="risk_score must be between 0 and 100"):
            create_suspicious_activity_alert(
                username="testuser",
                activity_type="test",
                risk_score=-1.0,
            )

    @patch("app.modules.governance.alert_notifier.get_alert_notifier")
    def test_create_suspicious_activity_alert_risk_score_above_100_raises(self, mock_get_notifier):
        """Test that risk_score > 100 raises ValueError."""
        mock_notifier = MagicMock()
        mock_get_notifier.return_value = mock_notifier

        with pytest.raises(ValueError, match="risk_score must be between 0 and 100"):
            create_suspicious_activity_alert(
                username="testuser",
                activity_type="test",
                risk_score=101.0,
            )

    @patch("app.modules.governance.alert_notifier.get_alert_notifier")
    def test_create_suspicious_activity_alert_risk_score_zero_allowed(self, mock_get_notifier):
        """Test that risk_score=0 is allowed (valid boundary)."""
        mock_notifier = MagicMock()
        mock_get_notifier.return_value = mock_notifier

        create_suspicious_activity_alert(
            username="testuser",
            activity_type="test",
            risk_score=0.0,
        )

        call_args = mock_notifier.create_alert.call_args
        # 0 < 50 -> warning
        assert call_args.kwargs["severity"] == AlertSeverity.WARNING.value

    @patch("app.modules.governance.alert_notifier.get_alert_notifier")
    def test_create_suspicious_activity_alert_risk_score_100_allowed(self, mock_get_notifier):
        """Test that risk_score=100 is allowed (valid boundary)."""
        mock_notifier = MagicMock()
        mock_get_notifier.return_value = mock_notifier

        create_suspicious_activity_alert(
            username="testuser",
            activity_type="test",
            risk_score=100.0,
        )

        call_args = mock_notifier.create_alert.call_args
        # 100 >= 50 -> critical
        assert call_args.kwargs["severity"] == AlertSeverity.CRITICAL.value

    @patch("app.modules.governance.alert_notifier.get_alert_notifier")
    def test_create_suspicious_activity_alert_risk_score_exactly_50(self, mock_get_notifier):
        """Test that risk_score=50 (threshold boundary) triggers critical."""
        mock_notifier = MagicMock()
        mock_get_notifier.return_value = mock_notifier

        create_suspicious_activity_alert(
            username="testuser",
            activity_type="test",
            risk_score=50.0,
        )

        call_args = mock_notifier.create_alert.call_args
        # 50 >= 50 -> critical (at threshold)
        assert call_args.kwargs["severity"] == AlertSeverity.CRITICAL.value
