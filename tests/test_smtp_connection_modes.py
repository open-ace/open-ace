#!/usr/bin/env python3
"""
Tests for SMTP Connection Modes

Tests cover:
- Port 465 SSL connection
- Port 587 STARTTLS connection
- Port 25 plain connection
- SSL exception handling
- Timeout exception handling
"""

import smtplib
import socket
import ssl
import unittest
from unittest.mock import MagicMock, patch

import pytest


class TestSMTPConnectionModes(unittest.TestCase):
    """Test SMTP connection modes for different ports."""

    def test_port_465_uses_smtp_ssl(self):
        """Test that port 465 uses SMTP_SSL connection."""
        from app.services.smtp_config_service import SMTPConfigService

        service = SMTPConfigService()

        # Mock SMTP_SSL
        with patch("smtplib.SMTP_SSL") as mock_smtp_ssl:
            mock_smtp_instance = MagicMock()
            mock_smtp_ssl.return_value = mock_smtp_instance

            # Test connection with port 465
            result = service.test_connection(
                smtp_host="smtp.exmail.qq.com",
                smtp_port=465,
                smtp_user="test@example.com",
                smtp_password="test_password",
                from_address="test@example.com",
                use_tls=True,
            )

            # Verify SMTP_SSL was called
            mock_smtp_ssl.assert_called_once_with("smtp.exmail.qq.com", 465, timeout=10)
            # Verify SMTP was NOT called
            mock_smtp_instance.login.assert_called_once()
            mock_smtp_instance.sendmail.assert_called_once()
            mock_smtp_instance.quit.assert_called_once()

            # Verify result contains connection_mode
            assert result["success"] is True
            assert result["connection_mode"] == "ssl"

    def test_port_465_ignores_use_tls(self):
        """Test that port 465 ignores use_tls setting and uses SSL."""
        from app.services.smtp_config_service import SMTPConfigService

        service = SMTPConfigService()

        # Mock SMTP_SSL
        with patch("smtplib.SMTP_SSL") as mock_smtp_ssl:
            mock_smtp_instance = MagicMock()
            mock_smtp_ssl.return_value = mock_smtp_instance

            # Test connection with port 465 and use_tls=False
            result = service.test_connection(
                smtp_host="smtp.exmail.qq.com",
                smtp_port=465,
                smtp_user="test@example.com",
                smtp_password="test_password",
                from_address="test@example.com",
                use_tls=False,
            )

            # Verify SMTP_SSL was called (ignoring use_tls)
            mock_smtp_ssl.assert_called_once()
            assert result["success"] is True
            assert result["connection_mode"] == "ssl"

    def test_port_587_uses_starttls(self):
        """Test that port 587 uses SMTP with STARTTLS."""
        from app.services.smtp_config_service import SMTPConfigService

        service = SMTPConfigService()

        # Mock SMTP
        with patch("smtplib.SMTP") as mock_smtp:
            mock_smtp_instance = MagicMock()
            mock_smtp.return_value = mock_smtp_instance

            # Test connection with port 587
            result = service.test_connection(
                smtp_host="smtp.gmail.com",
                smtp_port=587,
                smtp_user="test@gmail.com",
                smtp_password="test_password",
                from_address="test@gmail.com",
                use_tls=True,
            )

            # Verify SMTP was called
            mock_smtp.assert_called_once_with("smtp.gmail.com", 587, timeout=10)
            # Verify STARTTLS was called
            mock_smtp_instance.starttls.assert_called_once()
            mock_smtp_instance.login.assert_called_once()
            mock_smtp_instance.sendmail.assert_called_once()
            mock_smtp_instance.quit.assert_called_once()

            # Verify result contains connection_mode
            assert result["success"] is True
            assert result["connection_mode"] == "starttls"

    def test_port_25_plain_connection(self):
        """Test that port 25 uses plain SMTP connection."""
        from app.services.smtp_config_service import SMTPConfigService

        service = SMTPConfigService()

        # Mock SMTP
        with patch("smtplib.SMTP") as mock_smtp:
            mock_smtp_instance = MagicMock()
            mock_smtp.return_value = mock_smtp_instance

            # Test connection with port 25 and use_tls=False
            result = service.test_connection(
                smtp_host="smtp.example.com",
                smtp_port=25,
                smtp_user="test@example.com",
                smtp_password="test_password",
                from_address="test@example.com",
                use_tls=False,
            )

            # Verify SMTP was called
            mock_smtp.assert_called_once_with("smtp.example.com", 25, timeout=10)
            # Verify STARTTLS was NOT called
            mock_smtp_instance.starttls.assert_not_called()
            mock_smtp_instance.login.assert_called_once()
            mock_smtp_instance.sendmail.assert_called_once()
            mock_smtp_instance.quit.assert_called_once()

            # Verify result contains connection_mode
            assert result["success"] is True
            assert result["connection_mode"] == "plain"

    def test_ssl_error_handling(self):
        """Test SSL error handling for port 465."""
        from app.services.smtp_config_service import SMTPConfigService

        service = SMTPConfigService()

        # Mock SMTP_SSL to raise SSLError
        with patch("smtplib.SMTP_SSL") as mock_smtp_ssl:
            mock_smtp_ssl.side_effect = ssl.SSLError("SSL handshake failed")

            # Test connection
            result = service.test_connection(
                smtp_host="smtp.exmail.qq.com",
                smtp_port=465,
                smtp_user="test@example.com",
                smtp_password="test_password",
                from_address="test@example.com",
                use_tls=True,
            )

            # Verify error was handled
            assert result["success"] is False
            assert "SSL" in result["message"]

    def test_timeout_error_handling(self):
        """Test timeout error handling for all ports."""
        from app.services.smtp_config_service import SMTPConfigService

        service = SMTPConfigService()

        # Mock SMTP to raise timeout
        with patch("smtplib.SMTP") as mock_smtp:
            mock_smtp.side_effect = TimeoutError("Connection timed out")

            # Test connection with port 587
            result = service.test_connection(
                smtp_host="smtp.gmail.com",
                smtp_port=587,
                smtp_user="test@gmail.com",
                smtp_password="test_password",
                from_address="test@gmail.com",
                use_tls=True,
            )

            # Verify error was handled
            assert result["success"] is False
            assert "timeout" in result["message"].lower()

    def test_gaierror_handling(self):
        """Test DNS resolution error handling."""
        from app.services.smtp_config_service import SMTPConfigService

        service = SMTPConfigService()

        # Mock SMTP to raise gaierror
        with patch("smtplib.SMTP") as mock_smtp:
            mock_smtp.side_effect = socket.gaierror("DNS resolution failed")

            # Test connection
            result = service.test_connection(
                smtp_host="invalid.hostname",
                smtp_port=587,
                smtp_user="test@example.com",
                smtp_password="test_password",
                from_address="test@example.com",
                use_tls=True,
            )

            # Verify error was handled
            assert result["success"] is False
            assert "resolution" in result["message"].lower()

    def test_connection_refused_handling(self):
        """Test connection refused error handling."""
        from app.services.smtp_config_service import SMTPConfigService

        service = SMTPConfigService()

        # Mock SMTP to raise ConnectionRefusedError
        with patch("smtplib.SMTP") as mock_smtp:
            mock_smtp.side_effect = ConnectionRefusedError("Connection refused")

            # Test connection
            result = service.test_connection(
                smtp_host="smtp.example.com",
                smtp_port=25,
                smtp_user="test@example.com",
                smtp_password="test_password",
                from_address="test@example.com",
                use_tls=False,
            )

            # Verify error was handled
            assert result["success"] is False
            assert "refused" in result["message"].lower()


class TestEmailNotificationServiceSendEmail(unittest.TestCase):
    """Test EmailQueue._send_email connection modes."""

    def test_send_email_port_465_ssl(self):
        """Test _send_email uses SMTP_SSL for port 465."""
        from app.services.email_notification_service import EmailQueue

        queue = EmailQueue()

        config = {
            "smtp_host": "smtp.exmail.qq.com",
            "smtp_port": 465,
            "smtp_user": "test@example.com",
            "smtp_password": "test_password",
            "from_address": "test@example.com",
            "use_tls": True,
        }

        email_data = {
            "recipient_email": "recipient@example.com",
            "subject": "Test Subject",
            "email_body": "Test Body",
        }

        with patch("smtplib.SMTP_SSL") as mock_smtp_ssl:
            mock_smtp_instance = MagicMock()
            mock_smtp_ssl.return_value = mock_smtp_instance

            result = queue._send_email(config, email_data)

            # Verify SMTP_SSL was called with timeout=30
            mock_smtp_ssl.assert_called_once_with("smtp.exmail.qq.com", 465, timeout=30)
            mock_smtp_instance.login.assert_called_once()
            mock_smtp_instance.sendmail.assert_called_once()
            mock_smtp_instance.quit.assert_called_once()

            assert result is True

    def test_send_email_port_587_starttls(self):
        """Test _send_email uses SMTP with STARTTLS for port 587."""
        from app.services.email_notification_service import EmailQueue

        queue = EmailQueue()

        config = {
            "smtp_host": "smtp.gmail.com",
            "smtp_port": 587,
            "smtp_user": "test@gmail.com",
            "smtp_password": "test_password",
            "from_address": "test@gmail.com",
            "use_tls": True,
        }

        email_data = {
            "recipient_email": "recipient@example.com",
            "subject": "Test Subject",
            "email_body": "Test Body",
        }

        with patch("smtplib.SMTP") as mock_smtp:
            mock_smtp_instance = MagicMock()
            mock_smtp.return_value = mock_smtp_instance

            result = queue._send_email(config, email_data)

            # Verify SMTP was called
            mock_smtp.assert_called_once_with("smtp.gmail.com", 587, timeout=30)
            # Verify STARTTLS was called
            mock_smtp_instance.starttls.assert_called_once()
            mock_smtp_instance.login.assert_called_once()
            mock_smtp_instance.sendmail.assert_called_once()
            mock_smtp_instance.quit.assert_called_once()

            assert result is True

    def test_send_email_ssl_error_returns_false(self):
        """Test _send_email returns False on SSL error."""
        from app.services.email_notification_service import EmailQueue

        queue = EmailQueue()

        config = {
            "smtp_host": "smtp.exmail.qq.com",
            "smtp_port": 465,
            "smtp_user": "test@example.com",
            "smtp_password": "test_password",
            "from_address": "test@example.com",
            "use_tls": True,
        }

        email_data = {
            "recipient_email": "recipient@example.com",
            "subject": "Test Subject",
            "email_body": "Test Body",
        }

        with patch("smtplib.SMTP_SSL") as mock_smtp_ssl:
            mock_smtp_ssl.side_effect = ssl.SSLError("SSL error")

            result = queue._send_email(config, email_data)

            assert result is False

    def test_send_email_timeout_returns_false(self):
        """Test _send_email returns False on timeout."""
        from app.services.email_notification_service import EmailQueue

        queue = EmailQueue()

        config = {
            "smtp_host": "smtp.gmail.com",
            "smtp_port": 587,
            "smtp_user": "test@gmail.com",
            "smtp_password": "test_password",
            "from_address": "test@gmail.com",
            "use_tls": True,
        }

        email_data = {
            "recipient_email": "recipient@example.com",
            "subject": "Test Subject",
            "email_body": "Test Body",
        }

        with patch("smtplib.SMTP") as mock_smtp:
            mock_smtp.side_effect = TimeoutError("Timeout")

            result = queue._send_email(config, email_data)

            assert result is False


if __name__ == "__main__":
    unittest.main()
