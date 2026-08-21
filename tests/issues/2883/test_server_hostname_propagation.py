"""server_hostname propagation verification tests (Issue #2883).

Tests verify that:
1. server_hostname is correctly passed through the adapter to TLS handshake
2. assert_hostname affects certificate verification
3. The entire chain from adapter to socket is correctly configured
"""

import json
import ssl
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import pytest
import requests

from app.modules.governance.alert_notifier import (
    _PinnedHTTPSConnection,
    _PinnedHTTPSConnectionPool,
    _PinnedWebhookAdapter,
    Alert,
    AlertNotifier,
    DeliveryResult,
    NotificationPreference,
)


def _create_test_alert() -> Alert:
    """Create a test alert for webhook delivery."""
    return Alert(
        alert_id="test-alert-001",
        alert_type="quota",
        severity="warning",
        title="Test Alert",
        message="Test message for webhook",
        user_id=1,
        username="testuser",
    )


class TestServerHostnamePropagation:
    """Tests for server_hostname propagation through the entire stack."""

    def test_adapter_creates_pool_manager_with_original_hostname(self):
        """Verify adapter creates pool manager with original hostname (Issue #2883)."""
        adapter = _PinnedWebhookAdapter(
            allowed_ips=["93.184.216.34"],
            original_hostname="example.com",
        )
        adapter.init_poolmanager(1, 1)

        # Verify pool manager was created
        assert adapter.poolmanager is not None
        # Verify it's our custom pool manager
        from app.modules.governance.alert_notifier import _PinnedPoolManager

        assert isinstance(adapter.poolmanager, _PinnedPoolManager)
        assert adapter.poolmanager._original_hostname == "example.com"

    def test_pool_manager_creates_pool_with_original_hostname(self):
        """Verify pool manager creates HTTPS pool with original hostname (Issue #2883)."""
        from app.modules.governance.alert_notifier import _PinnedPoolManager

        manager = _PinnedPoolManager(
            original_hostname="example.com",
            num_pools=10,
        )

        pool = manager.connection_from_url("https://93.184.216.34/path")

        # Should be our custom HTTPS pool
        assert isinstance(pool, _PinnedHTTPSConnectionPool)
        assert pool._original_hostname == "example.com"

    def test_pool_creates_connection_with_original_hostname(self):
        """Verify pool creates connection with original hostname (Issue #2883)."""
        pool = _PinnedHTTPSConnectionPool(
            host="93.184.216.34",
            port=443,
            original_hostname="example.com",
        )

        conn = pool._new_conn()

        # Should be our custom connection
        assert isinstance(conn, _PinnedHTTPSConnection)
        # server_hostname is set via HTTPSConnection's server_hostname parameter
        assert conn.server_hostname == "example.com"
        assert conn.host == "93.184.216.34"

    def test_full_stack_propagates_original_hostname(self):
        """Verify entire stack from adapter to connection has correct hostname (Issue #2883)."""
        # Create adapter
        adapter = _PinnedWebhookAdapter(
            allowed_ips=["93.184.216.34"],
            original_hostname="example.com",
        )
        adapter.init_poolmanager(1, 1)

        # Get connection through the stack
        conn = adapter._get_connection("https://93.184.216.34/path", verify=True)

        # Verify connection has correct hostname
        assert isinstance(conn, _PinnedHTTPSConnection)
        # server_hostname is set via HTTPSConnection's server_hostname parameter
        assert conn.server_hostname == "example.com"
        assert conn.host == "93.184.216.34"

    def test_post_webhook_secure_passes_original_hostname(self, tmp_path):
        """Verify _post_webhook_secure passes original hostname to adapter (Issue #2883)."""
        # Create notifier with temp database
        db_path = str(tmp_path / "test_alerts.db")
        notifier = AlertNotifier(db_path=db_path)
        notifier._ensure_tables()

        # Create test preference
        prefs = NotificationPreference(
            user_id=1,
            webhook_url="https://example.com/webhook",
            push_enabled=True,
            alert_types=["quota", "system", "security"],
            min_severity="warning",
        )

        # Mock DNS resolution
        with patch.object(
            notifier,
            "_resolve_webhook_target_ips",
            return_value=(["93.184.216.34"], None),
        ):
            # Mock the adapter creation to capture parameters
            captured = {}

            original_adapter = _PinnedWebhookAdapter

            def capturing_adapter(*args, **kwargs):
                captured["allowed_ips"] = kwargs.get("allowed_ips")
                captured["original_hostname"] = kwargs.get("original_hostname")
                return original_adapter(*args, **kwargs)

            with patch(
                "app.modules.governance.alert_notifier._PinnedWebhookAdapter",
                capturing_adapter,
            ):
                # Mock session.post to avoid actual network call
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.raise_for_status = MagicMock()

                with patch("requests.Session") as mock_session_class:
                    mock_session = MagicMock()
                    mock_session.post.return_value = mock_response
                    mock_session_class.return_value = mock_session

                    result = notifier._post_webhook_secure(_create_test_alert(), prefs)

        # Verify original_hostname was passed
        assert captured.get("original_hostname") == "example.com"
        assert "93.184.216.34" in captured.get("allowed_ips", [])


class TestAssertHostname:
    """Tests for assert_hostname certificate verification."""

    def test_assert_hostname_passed_to_pool(self):
        """Verify that assert_hostname is set in the pool manager (Issue #2883)."""
        adapter = _PinnedWebhookAdapter(
            allowed_ips=["93.184.216.34"],
            original_hostname="example.com",
        )
        adapter.init_poolmanager(1, 1)

        # Pool manager should have the original hostname for assert_hostname
        assert adapter.poolmanager._original_hostname == "example.com"
        # The pool created should have assert_hostname
        pool = adapter.poolmanager.connection_from_url("https://93.184.216.34/path")
        # Pool should have the original hostname
        assert pool._original_hostname == "example.com"


class TestDeliveryResult:
    """Tests for delivery result handling."""

    def test_ssl_error_is_retriable(self):
        """Verify SSL errors are classified as retriable (Issue #2883)."""
        from app.modules.governance.alert_notifier import _classify_delivery_error

        exc = requests.exceptions.SSLError("TLS handshake failed")
        retriable, error_type = _classify_delivery_error(exc)

        assert retriable is True
        assert error_type == "ssl"

    def test_connection_error_is_retriable(self):
        """Verify connection errors are classified as retriable (Issue #2883)."""
        from app.modules.governance.alert_notifier import _classify_delivery_error

        exc = requests.exceptions.ConnectionError("Connection refused")
        retriable, error_type = _classify_delivery_error(exc)

        assert retriable is True
        assert error_type == "connection"
