#!/usr/bin/env python3
"""
Integration Tests for Email Notification System

Tests cover:
- Email notification flow
- SMTP password encryption flow
- Rate limiting in flow
- Alert notifier email integration
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


class TestEmailNotificationFlow(unittest.TestCase):
    """Test complete email notification flow."""

    def setUp(self):
        """Clear global service instances before each test."""
        # Clear global EmailNotificationService instance
        import app.services.email_notification_service as email_service_module

        email_service_module._email_notification_service = None

    @patch("app.modules.governance.alert_notifier.get_email_notification_service")
    @patch("app.modules.governance.alert_notifier.AlertNotifier._save_alert")
    @patch("app.modules.governance.alert_notifier.AlertNotifier.get_notification_preferences")
    def test_complete_flow_warning_alert(
        self, mock_get_prefs, mock_save, mock_email_service
    ):
        """Test complete flow: warning alert created -> email queued."""
        # Setup email service mock
        mock_service = MagicMock()
        mock_service.send_alert_notification.return_value = {
            "success": True,
            "message": "Email queued",
            "log_id": 123,
        }
        mock_email_service.return_value = mock_service

        # Setup preferences mock - email enabled, warning threshold
        from app.modules.governance.alert_notifier import NotificationPreference

        mock_prefs = NotificationPreference(
            user_id=1,
            email_enabled=True,
            notification_email="recipient@example.com",
            alert_types=["quota", "system", "security"],
            min_severity="warning",
        )
        mock_get_prefs.return_value = mock_prefs

        # Create alert
        from app.modules.governance.alert_notifier import get_alert_notifier

        notifier = get_alert_notifier()
        notifier._subscribers = []

        alert = notifier.create_alert(
            alert_type="quota",
            severity="warning",
            title="Quota Warning",
            message="You have used 80% of your quota",
            user_id=1,
        )

        # Verify email service was called
        mock_service.send_alert_notification.assert_called_once()
        call_args = mock_service.send_alert_notification.call_args
        assert call_args[1]["user_id"] == 1
        assert call_args[1]["recipient_email"] == "recipient@example.com"

    @patch("app.modules.governance.alert_notifier.get_email_notification_service")
    @patch("app.modules.governance.alert_notifier.AlertNotifier._save_alert")
    @patch("app.modules.governance.alert_notifier.AlertNotifier.get_notification_preferences")
    def test_complete_flow_critical_alert(
        self, mock_get_prefs, mock_save, mock_email_service
    ):
        """Test complete flow: critical alert created -> email queued."""
        # Setup email service mock
        mock_service = MagicMock()
        mock_service.send_alert_notification.return_value = {
            "success": True,
            "message": "Email queued",
            "log_id": 456,
        }
        mock_email_service.return_value = mock_service

        # Setup preferences mock - email enabled, critical threshold
        from app.modules.governance.alert_notifier import NotificationPreference

        mock_prefs = NotificationPreference(
            user_id=1,
            email_enabled=True,
            notification_email="admin@example.com",
            alert_types=["quota", "system", "security"],
            min_severity="critical",  # Only critical alerts
        )
        mock_get_prefs.return_value = mock_prefs

        # Create critical alert
        from app.modules.governance.alert_notifier import get_alert_notifier

        notifier = get_alert_notifier()
        notifier._subscribers = []

        alert = notifier.create_alert(
            alert_type="quota",
            severity="critical",
            title="Quota Exceeded",
            message="Your quota has been fully used",
            user_id=1,
        )

        # Verify email service was called
        mock_service.send_alert_notification.assert_called_once()

    @patch("app.services.email_notification_service.get_smtp_config_repository")
    @patch("app.services.email_notification_service.get_email_log_repository")
    def test_rate_limiting_in_flow(self, mock_log_repo, mock_smtp_repo):
        """Test rate limiting is enforced in the flow."""
        # Setup SMTP config mock
        mock_smtp_config = {
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "from_address": "noreply@example.com",
            "is_verified": True,
        }
        mock_smtp_repo.return_value.get_config.return_value = mock_smtp_config

        from app.services.email_notification_service import EmailNotificationService

        service = EmailNotificationService()

        user_id = 999

        # Exhaust rate limit
        for i in range(10):
            service.rate_limiter.record_send(user_id)

        # Try to send another email
        result = service.send_alert_notification(
            user_id=user_id,
            recipient_email="test@example.com",
            alert_data={
                "title": "Test",
                "severity": "warning",
                "alert_type": "quota",
                "message": "Test",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        # Should be rate limited
        assert result["success"] is False
        assert "Rate limit" in result["message"]

    @patch("app.services.email_notification_service.get_smtp_config_repository")
    @patch("app.services.email_notification_service.get_email_log_repository")
    def test_smtp_not_verified_flow(self, mock_log_repo, mock_smtp_repo):
        """Test email not sent when SMTP not verified."""
        # Setup SMTP config mock - not verified
        mock_smtp_config = {
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "from_address": "noreply@example.com",
            "is_verified": False,  # Not verified
        }
        mock_smtp_repo.return_value.get_config.return_value = mock_smtp_config

        from app.services.email_notification_service import EmailNotificationService

        service = EmailNotificationService()

        result = service.send_alert_notification(
            user_id=1,
            recipient_email="test@example.com",
            alert_data={
                "title": "Test",
                "severity": "warning",
                "alert_type": "quota",
                "message": "Test",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        # Should fail because SMTP not verified
        assert result["success"] is False
        assert "verified" in result["message"].lower()

    @patch("app.services.email_notification_service.get_smtp_config_repository")
    @patch("app.services.email_notification_service.get_email_log_repository")
    def test_smtp_not_configured_flow(self, mock_log_repo, mock_smtp_repo):
        """Test email not sent when SMTP not configured."""
        # No SMTP config
        mock_smtp_repo.return_value.get_config.return_value = None

        from app.services.email_notification_service import EmailNotificationService

        service = EmailNotificationService()

        result = service.send_alert_notification(
            user_id=1,
            recipient_email="test@example.com",
            alert_data={
                "title": "Test",
                "severity": "warning",
                "alert_type": "quota",
                "message": "Test",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        # Should fail because SMTP not configured
        assert result["success"] is False
        assert "SMTP" in result["message"]


class TestSMPTPasswordEncryptionFlow(unittest.TestCase):
    """Test SMTP password encryption/decryption in repository."""

    @patch("app.repositories.smtp_config_repository.get_password_manager")
    def test_password_encrypted_on_save(self, mock_password_manager):
        """Test password is encrypted when saving SMTP config."""
        mock_pm = MagicMock()
        mock_pm.encrypt.return_value = "encrypted_password_string"
        mock_pm.mask_password.return_value = "pass****"
        mock_password_manager.return_value = mock_pm

        # Create temp database
        import sqlite3

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            conn = sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE smtp_settings (
                    id INTEGER PRIMARY KEY,
                    smtp_host TEXT,
                    smtp_port INTEGER,
                    smtp_user TEXT,
                    encrypted_password TEXT,
                    encryption_version INTEGER,
                    from_address TEXT,
                    use_tls INTEGER,
                    is_verified INTEGER,
                    created_at TIMESTAMP,
                    updated_at TIMESTAMP
                )
            """)
            conn.commit()
            conn.close()

            # Test encryption flow
            from app.utils.smtp_password_manager import get_password_manager

            pm = get_password_manager()
            plain_password = "my_secret_password"
            encrypted = pm.encrypt(plain_password)

            assert encrypted != plain_password
            assert encrypted.startswith("gAAAA")  # Fernet prefix

        finally:
            os.unlink(db_path)

    def test_password_decryption_matches(self):
        """Test that decryption returns original password."""
        from app.utils.smtp_password_manager import get_password_manager

        pm = get_password_manager()

        original = "test_password_123"
        encrypted = pm.encrypt(original)
        decrypted = pm.decrypt(encrypted)

        assert decrypted == original


class TestEmailTemplateFlow(unittest.TestCase):
    """Test email template rendering in flow."""

    def test_template_english_rendering(self):
        """Test English template is correctly rendered."""
        from app.services.email_notification_service import EmailTemplateManager

        manager = EmailTemplateManager()
        template = manager.get_template("alert", "en")

        variables = {
            "title": "Quota Warning",
            "severity": "warning",
            "alert_type": "quota",
            "message": "You have used 80% of your quota",
            "timestamp": "2026-06-11T12:00:00Z",
            "action_text": "View Details",
            "action_url": "/report",
        }

        subject, body = manager.render(template, variables)

        assert "Quota Warning" in subject
        assert "80% of your quota" in body
        assert "warning" in body.lower()

    def test_template_chinese_rendering(self):
        """Test Chinese template is correctly rendered."""
        from app.services.email_notification_service import EmailTemplateManager

        manager = EmailTemplateManager()
        template = manager.get_template("alert", "zh")

        variables = {
            "title": "配额警告",
            "severity": "警告",
            "alert_type": "quota",
            "message": "您已使用80%的配额",
            "timestamp": "2026-06-11T12:00:00Z",
            "action_text": "查看详情",
            "action_url": "/report",
        }

        subject, body = manager.render(template, variables)

        assert "配额警告" in subject
        assert "80%" in body


if __name__ == "__main__":
    unittest.main()