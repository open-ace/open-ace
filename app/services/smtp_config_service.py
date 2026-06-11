"""
Open ACE - SMTP Config Service

Provides SMTP configuration management and connection testing.
"""

import logging
import smtplib
from email.mime.text import MIMEText
from typing import Any, Optional

from app.repositories.smtp_config_repository import get_smtp_config_repository
from app.repositories.email_notification_log_repository import get_email_log_repository

logger = logging.getLogger(__name__)


class SMTPConfigService:
    """Service for SMTP configuration management."""

    def __init__(self):
        """Initialize service."""
        self.config_repo = get_smtp_config_repository()
        self.log_repo = get_email_log_repository()

    def get_config(self) -> Optional[dict[str, Any]]:
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
        smtp_user: Optional[str] = None,
        smtp_password: Optional[str] = None,
        use_tls: bool = True,
        created_by: Optional[int] = None,
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
        smtp_host: Optional[str] = None,
        smtp_port: Optional[int] = None,
        smtp_user: Optional[str] = None,
        smtp_password: Optional[str] = None,
        from_address: Optional[str] = None,
        use_tls: Optional[bool] = None,
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
            smtp_host = saved_config["smtp_host"]
            smtp_port = saved_config["smtp_port"]
            smtp_user = saved_config["smtp_user"]
            smtp_password = saved_config["smtp_password"]
            from_address = saved_config["from_address"]
            use_tls = saved_config["use_tls"]
            config_id = saved_config["id"]
        else:
            config_id = None

        try:
            # Create SMTP connection
            if use_tls:
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

            return {
                "success": True,
                "message": "SMTP connection test successful",
            }

        except smtplib.SMTPAuthenticationError as e:
            error_msg = f"SMTP authentication failed: {e.smtp_code} - {e.smtp_error.decode()}"
            logger.error(error_msg)
            return {
                "success": False,
                "message": error_msg,
            }

        except smtplib.SMTPConnectError as e:
            error_msg = f"SMTP connection failed: {e.smtp_code} - {e.smtp_error.decode()}"
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
_smtp_config_service: Optional[SMTPConfigService] = None


def get_smtp_config_service() -> SMTPConfigService:
    """Get the global SMTP config service instance."""
    global _smtp_config_service
    if _smtp_config_service is None:
        _smtp_config_service = SMTPConfigService()
    return _smtp_config_service