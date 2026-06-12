#!/usr/bin/env python3
"""
Tests for Email Notification Service

Tests cover:
- SMTP password encryption/decryption
- Email notification service
- Rate limiting
- Email templates
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


class TestSMTPPasswordManager(unittest.TestCase):
    """Test SMTP password encryption and decryption."""

    def test_encrypt_decrypt_password(self):
        """Test that passwords can be encrypted and decrypted correctly."""
        from app.utils.smtp_password_manager import get_password_manager

        manager = get_password_manager()

        # Test encryption
        plain_password = "test_smtp_password_123"
        encrypted = manager.encrypt(plain_password)

        # Encrypted should be different from plain
        assert encrypted != plain_password
        assert len(encrypted) > 0

        # Test decryption
        decrypted = manager.decrypt(encrypted)
        assert decrypted == plain_password

    def test_encrypt_empty_password(self):
        """Test that empty password returns empty string."""
        from app.utils.smtp_password_manager import get_password_manager

        manager = get_password_manager()

        encrypted = manager.encrypt("")
        assert encrypted == ""

        decrypted = manager.decrypt("")
        assert decrypted == ""

    def test_mask_password(self):
        """Test password masking for display."""
        from app.utils.smtp_password_manager import get_password_manager

        manager = get_password_manager()

        # Test normal password masking
        password = "mySecretPassword"
        masked = manager.mask_password(password)
        assert masked.startswith("mySe")
        assert masked.endswith("***")
        assert len(masked) == len(password)

        # Test short password
        short_password = "abc"
        masked_short = manager.mask_password(short_password)
        assert masked_short == "***"

    def test_generate_key(self):
        """Test key generation."""
        from app.utils.smtp_password_manager import SMTPPasswordManager

        key = SMTPPasswordManager().generate_key()
        assert len(key) > 0
        # Fernet keys are base64 encoded 32-byte keys
        import base64

        decoded = base64.urlsafe_b64decode(key)
        assert len(decoded) == 32


class TestEmailTemplateManager(unittest.TestCase):
    """Test email template management."""

    def test_get_template_english(self):
        """Test getting English template."""
        from app.services.email_notification_service import EmailTemplateManager

        manager = EmailTemplateManager()
        template = manager.get_template("alert", "en")

        assert "subject" in template
        assert "body" in template
        assert "Open ACE" in template["body"]

    def test_get_template_chinese(self):
        """Test getting Chinese template."""
        from app.services.email_notification_service import EmailTemplateManager

        manager = EmailTemplateManager()
        template = manager.get_template("alert", "zh")

        assert "subject" in template
        assert "body" in template
        assert "Open ACE" in template["body"]

    def test_get_template_fallback(self):
        """Test fallback to English for unknown language."""
        from app.services.email_notification_service import EmailTemplateManager

        manager = EmailTemplateManager()
        template = manager.get_template("alert", "unknown")

        # Should fallback to English
        assert "subject" in template
        assert "Open ACE" in template["body"]

    def test_render_template(self):
        """Test template rendering with variables."""
        from app.services.email_notification_service import EmailTemplateManager

        manager = EmailTemplateManager()
        template = manager.get_template("alert", "en")

        variables = {
            "title": "Test Alert Title",
            "severity": "warning",
            "alert_type": "quota",
            "message": "This is a test message",
            "timestamp": "2026-06-11T10:00:00Z",
            "action_text": "View Details",
            "action_url": "/report",
        }

        subject, body = manager.render(template, variables)

        assert "Test Alert Title" in subject
        assert "Test Alert Title" in body
        assert "warning" in body
        assert "quota" in body
        assert "This is a test message" in body
        assert "/report" in body


class TestRateLimiter(unittest.TestCase):
    """Test rate limiting functionality."""

    def test_rate_limit_initial_state(self):
        """Test initial rate limit state."""
        from app.services.email_notification_service import RateLimiter

        limiter = RateLimiter()

        # New user should be allowed
        allowed, remaining = limiter.check_rate_limit(user_id=123)
        assert allowed is True
        assert remaining == 10  # Default RATE_LIMIT_PER_HOUR

    def test_rate_limit_enforcement(self):
        """Test rate limit enforcement."""
        from app.services.email_notification_service import RateLimiter

        limiter = RateLimiter()
        user_id = 456

        # Exhaust the rate limit
        for i in range(10):
            allowed, remaining = limiter.check_rate_limit(user_id)
            assert allowed is True
            limiter.record_send(user_id)

        # Should be blocked after limit exhausted
        allowed, remaining = limiter.check_rate_limit(user_id)
        assert allowed is False
        assert remaining == 0

    def test_rate_limit_different_users(self):
        """Test rate limits are per-user."""
        from app.services.email_notification_service import RateLimiter

        limiter = RateLimiter()

        # Exhaust limit for user A
        user_a = 100
        for i in range(10):
            limiter.record_send(user_a)

        # User B should still be allowed
        user_b = 200
        allowed, remaining = limiter.check_rate_limit(user_b)
        assert allowed is True
        assert remaining == 10

    def test_rate_limit_expiry(self):
        """Test rate limit expires after time window."""
        from app.services.email_notification_service import RateLimiter, RATE_LIMIT_WINDOW

        limiter = RateLimiter()
        user_id = 789

        # Record a send
        limiter.record_send(user_id)

        # Manually expire the send time
        # This simulates the time passing
        from datetime import datetime, timezone

        # Add an old timestamp
        limiter._user_send_times[user_id].append(
            datetime.now(timezone.utc).replace(tzinfo=None)
            - RATE_LIMIT_WINDOW
            - timedelta(minutes=1)
        )

        # Should be allowed again (old entry filtered out)
        allowed, remaining = limiter.check_rate_limit(user_id)
        # The old entry should be filtered out, but the new one still counts
        assert allowed is True


class TestEmailNotificationService(unittest.TestCase):
    """Test email notification service."""

    @patch("app.services.email_notification_service.get_smtp_config_repository")
    @patch("app.services.email_notification_service.get_email_log_repository")
    def test_send_alert_notification_no_smtp_config(self, mock_log_repo, mock_smtp_repo):
        """Test notification fails when SMTP not configured."""
        # Mock SMTP repo to return no config
        mock_smtp_repo.return_value.get_config.return_value = None

        from app.services.email_notification_service import EmailNotificationService

        service = EmailNotificationService()

        result = service.send_alert_notification(
            user_id=1,
            recipient_email="test@example.com",
            alert_data={
                "title": "Test Alert",
                "severity": "warning",
                "alert_type": "system",
                "message": "Test message",
                "created_at": datetime.now().isoformat(),
            },
        )

        assert result["success"] is False
        assert "SMTP" in result["message"]

    @patch("app.services.email_notification_service.get_smtp_config_repository")
    @patch("app.services.email_notification_service.get_email_log_repository")
    def test_send_alert_notification_smtp_not_verified(self, mock_log_repo, mock_smtp_repo):
        """Test notification fails when SMTP not verified."""
        # Mock SMTP repo to return unverified config
        mock_config = {
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "from_address": "noreply@example.com",
            "is_verified": False,
        }
        mock_smtp_repo.return_value.get_config.return_value = mock_config

        from app.services.email_notification_service import EmailNotificationService

        service = EmailNotificationService()

        result = service.send_alert_notification(
            user_id=1,
            recipient_email="test@example.com",
            alert_data={
                "title": "Test Alert",
                "severity": "warning",
                "alert_type": "system",
                "message": "Test message",
                "created_at": datetime.now().isoformat(),
            },
        )

        assert result["success"] is False
        assert "verified" in result["message"].lower()

    @patch("app.services.email_notification_service.get_smtp_config_repository")
    @patch("app.services.email_notification_service.get_email_log_repository")
    def test_send_alert_notification_rate_limited(self, mock_log_repo, mock_smtp_repo):
        """Test notification fails when rate limited."""
        # Mock SMTP repo to return verified config
        mock_config = {
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "from_address": "noreply@example.com",
            "is_verified": True,
        }
        mock_smtp_repo.return_value.get_config.return_value = mock_config

        from app.services.email_notification_service import EmailNotificationService

        service = EmailNotificationService()

        # Exhaust rate limit
        user_id = 999
        for i in range(10):
            service.rate_limiter.record_send(user_id)

        result = service.send_alert_notification(
            user_id=user_id,
            recipient_email="test@example.com",
            alert_data={
                "title": "Test Alert",
                "severity": "warning",
                "alert_type": "system",
                "message": "Test message",
                "created_at": datetime.now().isoformat(),
            },
        )

        assert result["success"] is False
        assert "Rate limit" in result["message"]

    @patch("app.services.email_notification_service.get_smtp_config_repository")
    @patch("app.services.email_notification_service.get_email_log_repository")
    def test_send_alert_notification_success(self, mock_log_repo, mock_smtp_repo):
        """Test successful notification queuing."""
        # Mock SMTP repo to return verified config
        mock_config = {
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "from_address": "noreply@example.com",
            "is_verified": True,
        }
        mock_smtp_repo.return_value.get_config.return_value = mock_config

        # Mock log repo
        mock_log_repo.return_value.create_log.return_value = 123

        from app.services.email_notification_service import EmailNotificationService

        service = EmailNotificationService()

        result = service.send_alert_notification(
            user_id=1,
            recipient_email="test@example.com",
            alert_data={
                "title": "Test Alert",
                "severity": "warning",
                "alert_type": "quota",
                "message": "Test message",
                "created_at": datetime.now().isoformat(),
                "alert_id": "alert-123",
            },
        )

        assert result["success"] is True
        assert "queued" in result["message"].lower()
        assert result["log_id"] == 123

    def test_send_test_email(self):
        """Test sending test email."""
        from app.services.email_notification_service import EmailNotificationService

        with patch.object(EmailNotificationService, "send_alert_notification") as mock_send:
            mock_send.return_value = {"success": True, "message": "Queued"}

            service = EmailNotificationService()
            result = service.send_test_email("test@example.com", "en")

            mock_send.assert_called_once()
            assert result["success"] is True


class TestEmailQueue(unittest.TestCase):
    """Test email queue processing."""

    def test_enqueue_email(self):
        """Test adding email to queue."""
        from app.services.email_notification_service import EmailQueue

        queue = EmailQueue()

        # Queue starts not running
        assert queue._running is False

        email_data = {
            "log_id": 1,
            "user_id": 123,
            "recipient_email": "test@example.com",
            "subject": "Test Subject",
            "email_body": "Test body",
            "retry_count": 0,
        }

        # Start the queue first
        queue.start()
        assert queue._running is True

        queue.enqueue(email_data)

        # Stop the queue
        queue.stop()
        assert queue._running is False

    def test_queue_start_stop(self):
        """Test queue start and stop."""
        from app.services.email_notification_service import EmailQueue

        queue = EmailQueue()
        assert queue._running is False

        queue.start()
        assert queue._running is True

        # Starting again should not change state
        queue.start()
        assert queue._running is True

        queue.stop()
        assert queue._running is False


class TestAlertNotifierEmailIntegration(unittest.TestCase):
    """Test AlertNotifier email integration."""

    @patch("app.modules.governance.alert_notifier.AlertNotifier._save_alert")
    @patch("app.modules.governance.alert_notifier.AlertNotifier.get_notification_preferences")
    @patch("app.modules.governance.alert_notifier.get_email_notification_service")
    def test_create_alert_with_email_notification(
        self, mock_email_service, mock_get_prefs, mock_save
    ):
        """Test alert creation triggers email notification."""
        # Mock email service
        mock_service = MagicMock()
        mock_service.send_alert_notification.return_value = {
            "success": True,
            "message": "Email queued",
        }
        mock_email_service.return_value = mock_service

        # Mock preferences
        from app.modules.governance.alert_notifier import NotificationPreference

        mock_prefs = NotificationPreference(
            user_id=1,
            email_enabled=True,
            notification_email="user@example.com",
            alert_types=["quota", "system", "security"],
            min_severity="warning",
        )
        mock_get_prefs.return_value = mock_prefs

        from app.modules.governance.alert_notifier import AlertNotifier, get_alert_notifier

        # Get notifier and clear subscribers
        notifier = get_alert_notifier()
        notifier._subscribers = []

        # Create an alert for user 1
        alert = notifier.create_alert(
            alert_type="quota",
            severity="warning",
            title="Quota Warning",
            message="You have used 80% of your quota",
            user_id=1,
            username="testuser",
        )

        # Email service should have been called
        mock_service.send_alert_notification.assert_called_once()
        call_args = mock_service.send_alert_notification.call_args
        assert call_args[1]["user_id"] == 1
        assert call_args[1]["recipient_email"] == "user@example.com"

    @patch("app.modules.governance.alert_notifier.AlertNotifier._save_alert")
    @patch("app.modules.governance.alert_notifier.AlertNotifier.get_notification_preferences")
    @patch("app.modules.governance.alert_notifier.get_email_notification_service")
    def test_create_alert_email_disabled(self, mock_email_service, mock_get_prefs, mock_save):
        """Test alert creation doesn't send email when disabled."""
        mock_service = MagicMock()
        mock_email_service.return_value = mock_service

        # Mock preferences with email disabled
        from app.modules.governance.alert_notifier import NotificationPreference

        mock_prefs = NotificationPreference(
            user_id=1,
            email_enabled=False,
            notification_email="user@example.com",
            alert_types=["quota", "system", "security"],
            min_severity="warning",
        )
        mock_get_prefs.return_value = mock_prefs

        from app.modules.governance.alert_notifier import get_alert_notifier

        notifier = get_alert_notifier()
        notifier._subscribers = []

        # Create an alert
        alert = notifier.create_alert(
            alert_type="quota",
            severity="warning",
            title="Quota Warning",
            message="Test message",
            user_id=1,
        )

        # Email service should NOT have been called
        mock_service.send_alert_notification.assert_not_called()

    @patch("app.modules.governance.alert_notifier.AlertNotifier._save_alert")
    @patch("app.modules.governance.alert_notifier.AlertNotifier.get_notification_preferences")
    @patch("app.modules.governance.alert_notifier.get_email_notification_service")
    def test_create_alert_severity_filter(self, mock_email_service, mock_get_prefs, mock_save):
        """Test email notification respects severity filter."""
        mock_service = MagicMock()
        mock_email_service.return_value = mock_service

        # Mock preferences with min severity critical
        from app.modules.governance.alert_notifier import NotificationPreference

        mock_prefs = NotificationPreference(
            user_id=1,
            email_enabled=True,
            notification_email="user@example.com",
            alert_types=["quota", "system", "security"],
            min_severity="critical",
        )
        mock_get_prefs.return_value = mock_prefs

        from app.modules.governance.alert_notifier import get_alert_notifier

        notifier = get_alert_notifier()
        notifier._subscribers = []

        # Create a warning alert (below threshold)
        alert = notifier.create_alert(
            alert_type="quota",
            severity="warning",
            title="Quota Warning",
            message="Test message",
            user_id=1,
        )

        # Email should NOT be sent for warning when min is critical
        mock_service.send_alert_notification.assert_not_called()

        # Now create a critical alert
        alert2 = notifier.create_alert(
            alert_type="quota",
            severity="critical",
            title="Quota Critical",
            message="Test message",
            user_id=1,
        )

        # Email should be sent for critical
        mock_service.send_alert_notification.assert_called_once()

    @patch("app.modules.governance.alert_notifier.AlertNotifier._save_alert")
    @patch("app.modules.governance.alert_notifier.AlertNotifier.get_notification_preferences")
    @patch("app.modules.governance.alert_notifier.get_email_notification_service")
    def test_create_alert_type_filter(self, mock_email_service, mock_get_prefs, mock_save):
        """Test email notification respects alert type filter."""
        mock_service = MagicMock()
        mock_email_service.return_value = mock_service

        # Mock preferences with only quota alerts enabled
        from app.modules.governance.alert_notifier import NotificationPreference

        mock_prefs = NotificationPreference(
            user_id=1,
            email_enabled=True,
            notification_email="user@example.com",
            alert_types=["quota"],
            min_severity="warning",
        )
        mock_get_prefs.return_value = mock_prefs

        from app.modules.governance.alert_notifier import get_alert_notifier

        notifier = get_alert_notifier()
        notifier._subscribers = []

        # Create a system alert
        alert = notifier.create_alert(
            alert_type="system",
            severity="warning",
            title="System Warning",
            message="Test message",
            user_id=1,
        )

        # Email should NOT be sent for system alert
        mock_service.send_alert_notification.assert_not_called()

        # Now create a quota alert
        alert2 = notifier.create_alert(
            alert_type="quota",
            severity="warning",
            title="Quota Warning",
            message="Test message",
            user_id=1,
        )

        # Email should be sent for quota
        mock_service.send_alert_notification.assert_called_once()

    @patch("app.modules.governance.alert_notifier.AlertNotifier._save_alert")
    @patch("app.modules.governance.alert_notifier.AlertNotifier.get_notification_preferences")
    @patch("app.modules.governance.alert_notifier.get_email_notification_service")
    def test_create_alert_no_notification_email(
        self, mock_email_service, mock_get_prefs, mock_save
    ):
        """Test email notification skipped when no email configured."""
        mock_service = MagicMock()
        mock_email_service.return_value = mock_service

        # Mock preferences with no notification email
        from app.modules.governance.alert_notifier import NotificationPreference

        mock_prefs = NotificationPreference(
            user_id=1,
            email_enabled=True,
            notification_email=None,
            alert_types=["quota", "system", "security"],
            min_severity="warning",
        )
        mock_get_prefs.return_value = mock_prefs

        from app.modules.governance.alert_notifier import get_alert_notifier

        notifier = get_alert_notifier()
        notifier._subscribers = []

        # Create an alert
        alert = notifier.create_alert(
            alert_type="quota",
            severity="warning",
            title="Quota Warning",
            message="Test message",
            user_id=1,
        )

        # Email should NOT be sent
        mock_service.send_alert_notification.assert_not_called()

    @patch("app.modules.governance.alert_notifier.AlertNotifier._save_alert")
    @patch("app.modules.governance.alert_notifier.get_email_notification_service")
    def test_create_alert_no_user_id(self, mock_email_service, mock_save):
        """Test email notification skipped when no user_id."""
        mock_service = MagicMock()
        mock_email_service.return_value = mock_service

        from app.modules.governance.alert_notifier import get_alert_notifier

        notifier = get_alert_notifier()
        notifier._subscribers = []

        # Create a system-wide alert (no user_id)
        alert = notifier.create_alert(
            alert_type="system",
            severity="warning",
            title="System Warning",
            message="Test message",
        )

        # Email should NOT be sent for system-wide alerts
        mock_service.send_alert_notification.assert_not_called()


if __name__ == "__main__":
    unittest.main()
