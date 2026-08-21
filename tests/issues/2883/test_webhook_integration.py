"""Integration tests for webhook delivery (Issue #2883).

Tests cover:
1. Feishu/Lark bot webhook simulation
2. DingTalk signed webhook simulation
3. IPv6 scenarios
4. Full delivery chain
"""

import json
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import pytest

from app.modules.governance.alert_notifier import (
    Alert,
    AlertNotifier,
    NotificationPreference,
)


def _create_test_alert(severity: str = "warning") -> Alert:
    """Create a test alert."""
    return Alert(
        alert_id=f"test-alert-{severity}",
        alert_type="quota",
        severity=severity,
        title="Test Alert",
        message="Test message",
        user_id=1,
        username="testuser",
    )


class TestFeishuWebhook:
    """Tests for Feishu/Lark bot webhook delivery."""

    def test_feishu_payload_format(self, tmp_path):
        """Verify Feishu payload format is correct (Issue #2883)."""
        db_path = str(tmp_path / "test_alerts.db")
        notifier = AlertNotifier(db_path=db_path)
        notifier._ensure_tables()

        # Build payload for Feishu webhook
        alert = _create_test_alert()
        webhook_url = "https://open.feishu.cn/open-apis/bot/v2/hook/test-token"

        payload = notifier._build_webhook_payload(alert, webhook_url)

        # Verify Feishu-specific format
        assert payload["msg_type"] == "text"
        assert "content" in payload
        assert "text" in payload["content"]
        assert "Test Alert" in payload["content"]["text"]

    def test_feishu_webhook_delivery(self, tmp_path):
        """Test Feishu webhook delivery with correct SNI (Issue #2883)."""
        db_path = str(tmp_path / "test_alerts.db")
        notifier = AlertNotifier(db_path=db_path)
        notifier._ensure_tables()

        with patch.object(
            notifier,
            "_resolve_webhook_target_ips",
            return_value=(["93.184.216.34"], None),
        ):
            prefs = NotificationPreference(
                user_id=1,
                webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/test-token",
                push_enabled=True,
                alert_types=["quota"],
                min_severity="warning",
            )

            # Capture request details
            captured = {}

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.raise_for_status = MagicMock()

            def capturing_post(url, *args, **kwargs):
                captured["url"] = url
                captured["headers"] = kwargs.get("headers", {})
                captured["data"] = kwargs.get("data")
                return mock_response

            with patch("requests.Session") as mock_session_class:
                mock_session = MagicMock()
                mock_session.post = capturing_post
                mock_session_class.return_value = mock_session

                result = notifier._post_webhook_secure(_create_test_alert(), prefs)

            # Verify Host header uses original hostname
            assert captured["headers"].get("Host") == "open.feishu.cn"

            # Verify URL uses pinned IP
            url_host = urlparse(captured["url"]).hostname
            assert url_host == "93.184.216.34"


class TestDingtalkWebhook:
    """Tests for DingTalk bot webhook delivery."""

    def test_dingtalk_payload_format(self, tmp_path):
        """Verify DingTalk payload format is correct (Issue #2883)."""
        db_path = str(tmp_path / "test_alerts.db")
        notifier = AlertNotifier(db_path=db_path)
        notifier._ensure_tables()

        alert = _create_test_alert()
        webhook_url = "https://oapi.dingtalk.com/robot/send?access_token=test-token"

        payload = notifier._build_webhook_payload(alert, webhook_url)

        # Verify DingTalk-specific format
        assert payload["msgtype"] == "text"
        assert "text" in payload
        assert "content" in payload["text"]

    def test_dingtalk_signed_webhook_delivery(self, tmp_path):
        """Test DingTalk signed webhook delivery with correct SNI (Issue #2883)."""
        db_path = str(tmp_path / "test_alerts.db")
        notifier = AlertNotifier(db_path=db_path)
        notifier._ensure_tables()

        with patch.object(
            notifier,
            "_resolve_webhook_target_ips",
            return_value=(["93.184.216.34"], None),
        ):
            prefs = NotificationPreference(
                user_id=1,
                webhook_url="https://oapi.dingtalk.com/robot/send?access_token=test-token",
                push_enabled=True,
                alert_types=["quota"],
                min_severity="warning",
                dingtalk_webhook_secret=None,  # Will use global config
            )

            # Mock global DingTalk secret
            with patch(
                "app.modules.governance.alert_notifier.get_config_value",
                return_value="test-secret",
            ):
                # Capture request details
                captured = {}

                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.raise_for_status = MagicMock()

                def capturing_post(url, *args, **kwargs):
                    captured["url"] = url
                    captured["headers"] = kwargs.get("headers", {})
                    return mock_response

                with patch("requests.Session") as mock_session_class:
                    mock_session = MagicMock()
                    mock_session.post = capturing_post
                    mock_session_class.return_value = mock_session

                    result = notifier._post_webhook_secure(_create_test_alert(), prefs)

            # Verify Host header uses original hostname
            assert captured["headers"].get("Host") == "oapi.dingtalk.com"

            # Verify URL uses pinned IP
            url_host = urlparse(captured["url"]).hostname
            assert url_host == "93.184.216.34"

            # Verify signing params were added
            assert "timestamp" in captured["url"]
            assert "sign" in captured["url"]


class TestIPv6Scenarios:
    """Tests for IPv6 scenarios."""

    def test_ipv6_pinned_url_format(self, tmp_path):
        """Verify IPv6 addresses are formatted correctly in pinned URLs (Issue #2883)."""
        from app.modules.governance.alert_notifier import _pin_host_to_url

        # Test IPv6 address formatting
        url = "https://example.com/path"
        ipv6_addr = "2001:db8::1"

        pinned_url = _pin_host_to_url(url, ipv6_addr)

        # IPv6 addresses must be wrapped in brackets in URLs
        assert "[2001:db8::1]" in pinned_url

    def test_ipv6_webhook_delivery(self, tmp_path):
        """Test webhook delivery with IPv6 pinned address (Issue #2883)."""
        db_path = str(tmp_path / "test_alerts.db")
        notifier = AlertNotifier(db_path=db_path)
        notifier._ensure_tables()

        with patch.object(
            notifier,
            "_resolve_webhook_target_ips",
            return_value=(["2001:db8::1"], None),
        ):
            prefs = NotificationPreference(
                user_id=1,
                webhook_url="https://example.com/webhook",
                push_enabled=True,
                alert_types=["quota"],
                min_severity="warning",
            )

            # Capture request details
            captured = {}

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.raise_for_status = MagicMock()

            def capturing_post(url, *args, **kwargs):
                captured["url"] = url
                captured["headers"] = kwargs.get("headers", {})
                return mock_response

            with patch("requests.Session") as mock_session_class:
                mock_session = MagicMock()
                mock_session.post = capturing_post
                mock_session_class.return_value = mock_session

                result = notifier._post_webhook_secure(_create_test_alert(), prefs)

            # Verify URL contains IPv6 in brackets
            assert "[2001:db8::1]" in captured["url"]

            # Verify Host header is original hostname (not IPv6)
            assert captured["headers"].get("Host") == "example.com"


class TestGenericWebhook:
    """Tests for generic HTTPS webhook delivery."""

    def test_generic_webhook_payload(self, tmp_path):
        """Verify generic webhook payload format (Issue #2883)."""
        db_path = str(tmp_path / "test_alerts.db")
        notifier = AlertNotifier(db_path=db_path)
        notifier._ensure_tables()

        alert = _create_test_alert()
        webhook_url = "https://webhook.example.com/hook"

        payload = notifier._build_webhook_payload(alert, webhook_url)

        # Verify generic format
        assert payload["event"] == "openace.alert"
        assert payload["source"] == "open-ace"
        assert "alert" in payload
        assert payload["alert"]["alert_id"] == "test-alert-warning"

    def test_generic_webhook_delivery(self, tmp_path):
        """Test generic webhook delivery (Issue #2883)."""
        db_path = str(tmp_path / "test_alerts.db")
        notifier = AlertNotifier(db_path=db_path)
        notifier._ensure_tables()

        with patch.object(
            notifier,
            "_resolve_webhook_target_ips",
            return_value=(["93.184.216.34"], None),
        ):
            prefs = NotificationPreference(
                user_id=1,
                webhook_url="https://webhook.example.com/hook",
                push_enabled=True,
                alert_types=["quota"],
                min_severity="warning",
            )

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.raise_for_status = MagicMock()

            with patch("requests.Session") as mock_session_class:
                mock_session = MagicMock()
                mock_session.post.return_value = mock_response
                mock_session_class.return_value = mock_session

                result = notifier._post_webhook_secure(_create_test_alert(), prefs)

            # Verify successful delivery
            assert result.delivered is True
