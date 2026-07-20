"""
Open ACE - SMTP Config Service

Provides SMTP configuration management and connection testing.
"""

from __future__ import annotations
import logging
import smtplib
import socket
import ssl
from email.mime.text import MIMEText
from typing import Any

from app.repositories.email_notification_log_repository import get_email_log_repository
from app.repositories.smtp_config_repository import get_smtp_config_repository

logger = logging.getLogger(__name__)


class SMTPConfigService:
    """Service for SMTP configuration management."""

    def __init__(self):
        """Initialize service."""
        self.config_repo = get_smtp_config_repository()
        self.log_repo = get_email_log_repository()

    def get_config(self) -> dict[str, Any] | None:
        """
        Get SMTP configuration.

        Returns:
            SMTP config dict with masked password, or None.
        """
        return self.config_repo.get_config()

    def save_config(
        self,
        smtp_host: str,
        smtp_port: int,
        from_address: str,
        smtp_user: str | None = None,
        smtp_password: str | None = None,
        use_tls: bool = True,
        created_by: int | None = None,
    ) -> dict[str, Any]:
        """
        Save SMTP configuration.

        Args:
            smtp_host: SMTP server hostname.
            smtp_port: SMTP server port.
            from_address: Email sender address.
            smtp_user: SMTP username.
            smtp_password: SMTP password.
            use_tls: Whether to use TLS.
            created_by: User ID who created the config.

        Returns:
            Saved config dict.
        """
        return self.config_repo.save_config(
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            from_address=from_address,
            smtp_user=smtp_user,
            smtp_password=smtp_password,
            use_tls=use_tls,
            created_by=created_by,
        )

    def test_connection(
        self,
        smtp_host: str | None = None,
        smtp_port: int | None = None,
        smtp_user: str | None = None,
        smtp_password: str | None = None,
        from_address: str | None = None,
        use_tls: bool | None = None,
    ) -> dict[str, Any]:
        """
        Test SMTP connection.

        Args:
            smtp_host: Test host (uses saved config if not provided).
            smtp_port: Test port (uses saved config if not provided).
            smtp_user: Test user (uses saved config if not provided).
            smtp_password: Test password (uses saved config if not provided).
            from_address: Test sender (uses saved config if not provided).
            use_tls: Test TLS setting (uses saved config if not provided).

        Returns:
            Dict with success status and message.
        """
        # Use saved config if parameters not provided
        if smtp_host is None:
            saved_config = self.config_repo.get_config_with_password()
            if not saved_config:
                return {
                    "success": False,
                    "message": "No SMTP configuration found",
                }
            smtp_host = saved_config["smtp_host"] or ""
            smtp_port = saved_config["smtp_port"] or 25
            smtp_user = saved_config["smtp_user"]
            smtp_password = saved_config["smtp_password"]
            from_address = saved_config["from_address"] or ""
            use_tls = saved_config["use_tls"] or False
            config_id = saved_config["id"]
        else:
            config_id = None
            # Ensure non-None values when using provided parameters
            smtp_host = smtp_host or ""
            smtp_port = smtp_port or 25
            from_address = from_address or ""
            use_tls = use_tls or False

        try:
            # Create SMTP connection
            # Port 465 uses SSL connection (SMTPS), other ports use SMTP with optional STARTTLS
            smtp: smtplib.SMTP | smtplib.SMTP_SSL
            use_ssl = smtp_port == 465
            if use_ssl:
                smtp = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=10)
            elif use_tls:
                smtp = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
                smtp.starttls()
            else:
                smtp = smtplib.SMTP(smtp_host, smtp_port, timeout=10)

            # Login if credentials provided
            if smtp_user and smtp_password:
                smtp.login(smtp_user, smtp_password)

            # Send test email
            test_msg = MIMEText(
                "This is a test email from Open ACE SMTP configuration test.",
                "plain",
                "utf-8",
            )
            test_msg["Subject"] = "Open ACE SMTP Test"
            test_msg["From"] = from_address
            test_msg["To"] = from_address  # Send to self

            smtp.sendmail(from_address, [from_address], test_msg.as_string())
            smtp.quit()

            # Update verification status for saved config
            if config_id:
                self.config_repo.update_verified_status(config_id, True)

            # Return success message with connection mode information
            if use_ssl:
                success_msg = (
                    "SMTP SSL connection test successful (port 465 automatically uses SSL)"
                )
            elif use_tls:
                success_msg = "SMTP STARTTLS connection test successful"
            else:
                success_msg = (
                    "SMTP connection test successful (plain connection, recommend enabling TLS)"
                )

            return {
                "success": True,
                "message": success_msg,
                "connection_mode": "ssl" if use_ssl else ("starttls" if use_tls else "plain"),
            }

        except smtplib.SMTPAuthenticationError as e:
            smtp_error_str = (
                e.smtp_error.decode() if isinstance(e.smtp_error, bytes) else str(e.smtp_error)
            )
            error_msg = f"SMTP authentication failed: {e.smtp_code} - {smtp_error_str}"
            logger.error(error_msg)
            return {
                "success": False,
                "message": error_msg,
            }

        except smtplib.SMTPConnectError as e:
            smtp_error_str = (
                e.smtp_error.decode() if isinstance(e.smtp_error, bytes) else str(e.smtp_error)
            )
            error_msg = f"SMTP connection failed: {e.smtp_code} - {smtp_error_str}"
            logger.error(error_msg)
            return {
                "success": False,
                "message": error_msg,
            }

        except ssl.SSLError as e:
            error_msg = f"SSL connection failed: certificate verification error or handshake failure - {str(e)}"
            logger.error(error_msg)
            return {
                "success": False,
                "message": error_msg,
            }

        except TimeoutError as e:
            error_msg = f"Connection timeout: unable to connect within 10 seconds - {str(e)}"
            logger.error(error_msg)
            return {
                "success": False,
                "message": error_msg,
            }

        except socket.gaierror as e:
            error_msg = f"Server address resolution failed: unable to find SMTP server - {str(e)}"
            logger.error(error_msg)
            return {
                "success": False,
                "message": error_msg,
            }

        except ConnectionRefusedError as e:
            error_msg = f"Connection refused: SMTP server not responding - {str(e)}"
            logger.error(error_msg)
            return {
                "success": False,
                "message": error_msg,
            }

        except smtplib.SMTPException as e:
            error_msg = f"SMTP error: {str(e)}"
            logger.error(error_msg)
            return {
                "success": False,
                "message": error_msg,
            }

        except Exception as e:
            error_msg = f"Connection test failed: {str(e)}"
            logger.error(error_msg)
            return {
                "success": False,
                "message": error_msg,
            }

    def delete_config(self) -> bool:
        """
        Delete SMTP configuration.

        Returns:
            True if successful.
        """
        return self.config_repo.delete_config()

    def get_statistics(self, days: int = 7) -> dict[str, Any]:
        """
        Get email sending statistics.

        Args:
            days: Number of days to analyze.

        Returns:
            Statistics dict.
        """
        return self.log_repo.get_statistics(days)


# Global service instance
_smtp_config_service: SMTPConfigService | None = None


def get_smtp_config_service() -> SMTPConfigService:
    """Get the global SMTP config service instance."""
    global _smtp_config_service
    if _smtp_config_service is None:
        _smtp_config_service = SMTPConfigService()
    return _smtp_config_service
