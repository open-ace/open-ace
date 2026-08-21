"""Concurrency safety tests for webhook delivery (Issue #2883).

Tests verify:
1. IP addresses are not mixed up across deliveries
2. Adapters instances are independent
3. No resource leaks
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.modules.governance.alert_notifier import (
    _PinnedWebhookAdapter,
    Alert,
    AlertNotifier,
    NotificationPreference,
)


def _create_test_alert(alert_id: str) -> Alert:
    """Create a test alert with specific ID."""
    return Alert(
        alert_id=alert_id,
        alert_type="quota",
        severity="warning",
        title="Test Alert",
        message="Test message",
        user_id=1,
        username="testuser",
    )


class TestConcurrencySafety:
    """Tests for webhook delivery safety."""

    def test_sequential_deliveries_use_correct_ips(self, tmp_path):
        """Verify that sequential deliveries use correct pinned IPs (Issue #2883)."""
        db_path = str(tmp_path / "test_alerts.db")
        notifier = AlertNotifier(db_path=db_path)
        notifier._ensure_tables()

        # Test with different IPs
        test_cases = [
            {
                "alert_id": "test-alert-1",
                "ip": "93.184.216.100",
                "hostname": "example1.com",
            },
            {
                "alert_id": "test-alert-2",
                "ip": "93.184.216.200",
                "hostname": "example2.com",
            },
        ]

        for case in test_cases:
            with patch.object(
                notifier,
                "_resolve_webhook_target_ips",
                return_value=([case["ip"]], None),
            ):
                prefs = NotificationPreference(
                    user_id=1,
                    webhook_url=f"https://{case['hostname']}/webhook",
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

                    alert = _create_test_alert(case["alert_id"])
                    result = notifier._post_webhook_secure(alert, prefs)

                # Verify URL contains correct pinned ip
                assert case["ip"] in captured["url"], (
                    f"Alert {case['alert_id']}: Expected IP {case['ip']} in URL, "
                    f"got {captured['url']}"
                )

                # Verify Host header is correct hostname
                assert captured["headers"].get("Host") == case["hostname"], (
                    f"Alert {case['alert_id']}: Expected Host header {case['hostname']}, "
                    f"got {captured['headers'].get('Host')}"
                )

    def test_adapter_instances_are_independent(self, tmp_path):
        """Verify that adapter instances are independent (Issue #2883)."""
        # Create two adapters with different configurations
        adapter1 = _PinnedWebhookAdapter(
            allowed_ips=["93.184.216.100"],
            original_hostname="example1.com",
        )

        adapter2 = _PinnedWebhookAdapter(
            allowed_ips=["93.184.216.200"],
            original_hostname="example2.com",
        )

        # Verify they have independent configurations
        assert adapter1._allowed_ips == {"93.184.216.100"}
        assert adapter1._original_hostname == "example1.com"

        assert adapter2._allowed_ips == {"93.184.216.200"}
        assert adapter2._original_hostname == "example2.com"

        # Verify they don't share the same pool manager
        adapter1.init_poolmanager(1, 1)
        adapter2.init_poolmanager(1, 1)

        assert adapter1.poolmanager is not adapter2.poolmanager

    def test_each_delivery_creates_new_session(self, tmp_path):
        """Verify that each delivery creates a new session (Issue #2883)."""
        db_path = str(tmp_path / "test_alerts.db")
        notifier = AlertNotifier(db_path=db_path)
        notifier._ensure_tables()

        sessions_created = []

        def tracking_session(*args, **kwargs):
            session = MagicMock()
            sessions_created.append(session)
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.raise_for_status = MagicMock()
            session.post.return_value = mock_response
            return session

        with patch("requests.Session", tracking_session):
            with patch.object(
                notifier,
                "_resolve_webhook_target_ips",
                return_value=(["93.184.216.34"], None),
            ):
                # Run multiple deliveries
                for i in range(3):
                    prefs = NotificationPreference(
                        user_id=1,
                        webhook_url="https://example.com/webhook",
                        push_enabled=True,
                        alert_types=["quota"],
                        min_severity="warning",
                    )

                    result = notifier._post_webhook_secure(
                        _create_test_alert(f"test-{i}"),
                        prefs,
                    )

        # Each delivery should create its own session
        assert len(sessions_created) == 3, "Each delivery should create its own session"

    def test_ip_pinning_enforced_in_adapter(self, tmp_path):
        """Verify that IP pinning is enforced in the adapter (Issue #2883)."""
        adapter = _PinnedWebhookAdapter(
            allowed_ips=["93.184.216.34"],
            original_hostname="example.com",
        )

        # Should accept allowlisted IP
        adapter._assert_pinned("https://93.184.216.34/path")

        # Should reject non-allowlisted IP
        with pytest.raises(ValueError, match="unpinned or rebound IP"):
            adapter._assert_pinned("https://192.168.1.1/path")

        # Should reject DNS hostname
        with pytest.raises(ValueError, match="unpinned or rebound IP"):
            adapter._assert_pinned("https://example.com/path")
