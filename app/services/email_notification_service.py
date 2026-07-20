"""
Open ACE - Email Notification Service

Handles email notification sending for alerts.
Provides:
- Asynchronous email sending
- Retry mechanism
- Rate limiting
- Email templates
"""

import logging
import queue
import smtplib
import socket
import ssl
import threading
import time
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from app.repositories.email_notification_log_repository import get_email_log_repository
from app.repositories.smtp_config_repository import get_smtp_config_repository

logger = logging.getLogger(__name__)

# Rate limit: max emails per user per hour
RATE_LIMIT_PER_HOUR = 10
RATE_LIMIT_WINDOW = timedelta(hours=1)


class EmailTemplateManager:
    """Manages email templates for notifications."""

    # Default templates (can be extended)
    DEFAULT_TEMPLATES = {
        "alert_en": {
            "subject": "Open ACE Alert: {title}",
            "body": """
You have received an alert from Open ACE:

Title: {title}
Severity: {severity}
Type: {alert_type}
Message: {message}
Time: {timestamp}

Please check your dashboard for more details.
{action_text}: {action_url}

---
Open ACE - AI Computing Explorer
""",
        },
        "alert_zh": {
            "subject": "Open ACE 告警：{title}",
            "body": """
您收到了来自 Open ACE 的告警通知：

标题：{title}
严重级别：{severity}
类型：{alert_type}
消息：{message}
时间：{timestamp}

请登录仪表板查看详情。
{action_text}: {action_url}

---
Open ACE - AI Computing Explorer
""",
        },
        "alert_ja": {
            "subject": "Open ACE 警告：{title}",
            "body": """
Open ACEからの警告通知：

タイトル：{title}
重要度：{severity}
タイプ：{alert_type}
メッセージ：{message}
時間：{timestamp}

ダッシュボードで詳細を確認してください。
{action_text}: {action_url}

---
Open ACE - AI Computing Explorer
""",
        },
        "alert_ko": {
            "subject": "Open ACE 알림: {title}",
            "body": """
Open ACE에서 알림을 받았습니다:

제목: {title}
중요도: {severity}
유형: {alert_type}
메시지: {message}
시간: {timestamp}

대시보드에서 상세 정보를 확인하세요.
{action_text}: {action_url}

---
Open ACE - AI Computing Explorer
""",
        },
    }

    def get_template(self, template_name: str, language: str = "en") -> dict[str, str]:
        """
        Get email template.

        Args:
            template_name: Template name (e.g., "alert").
            language: Language code (en, zh, ja, ko).

        Returns:
            Dict with 'subject' and 'body' templates.
        """
        key = f"{template_name}_{language}"
        if key in self.DEFAULT_TEMPLATES:
            return self.DEFAULT_TEMPLATES[key]

        # Fallback to English
        fallback_key = f"{template_name}_en"
        return self.DEFAULT_TEMPLATES.get(fallback_key, self.DEFAULT_TEMPLATES["alert_en"])

    def render(self, template: dict[str, str], variables: dict[str, Any]) -> tuple[str, str]:
        """
        Render email template with variables.

        Args:
            template: Template dict with 'subject' and 'body'.
            variables: Variables to substitute.

        Returns:
            Tuple of (subject, body).
        """
        subject = template["subject"]
        body = template["body"]

        # Simple variable substitution
        for key, value in variables.items():
            placeholder = "{" + key + "}"
            if value is not None:
                subject = subject.replace(placeholder, str(value))
                body = body.replace(placeholder, str(value))

        # Remove unfilled placeholders
        import re

        subject = re.sub(r"\{[^}]+\}", "", subject)
        body = re.sub(r"\{[^}]+\}", "", body)

        return subject.strip(), body.strip()


class RateLimiter:
    """Rate limiter for email sending."""

    def __init__(self):
        """Initialize rate limiter."""
        self._user_send_times: dict[int, list[datetime]] = {}
        self._lock = threading.Lock()

    def check_rate_limit(self, user_id: int) -> tuple[bool, int]:
        """
        Check if user is within rate limit.

        Args:
            user_id: User ID.

        Returns:
            Tuple of (allowed, remaining_count).
        """
        with self._lock:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            cutoff = now - RATE_LIMIT_WINDOW

            # Get user's send times
            if user_id not in self._user_send_times:
                self._user_send_times[user_id] = []

            # Filter out old entries
            self._user_send_times[user_id] = [
                t for t in self._user_send_times[user_id] if t > cutoff
            ]

            # Check count
            send_count = len(self._user_send_times[user_id])
            remaining = RATE_LIMIT_PER_HOUR - send_count

            if send_count >= RATE_LIMIT_PER_HOUR:
                return False, 0

            return True, remaining

    def record_send(self, user_id: int) -> None:
        """
        Record an email send for rate limiting.

        Args:
            user_id: User ID.
        """
        with self._lock:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            if user_id not in self._user_send_times:
                self._user_send_times[user_id] = []
            self._user_send_times[user_id].append(now)


class EmailQueue:
    """Asynchronous email queue processor."""

    def __init__(self):
        """Initialize email queue."""
        self._queue: queue.Queue = queue.Queue()
        self._worker_thread: threading.Thread | None = None
        self._running = False
        self._max_retry = 3

    def start(self) -> None:
        """Start the email queue worker."""
        if self._running:
            return

        self._running = True
        self._worker_thread = threading.Thread(target=self._worker, daemon=True)
        self._worker_thread.start()
        logger.info("Email queue worker started")

    def stop(self) -> None:
        """Stop the email queue worker."""
        self._running = False
        if self._worker_thread:
            self._worker_thread.join(timeout=5)
        logger.info("Email queue worker stopped")

    def enqueue(self, email_data: dict[str, Any]) -> None:
        """
        Add email to queue.

        Args:
            email_data: Dict with email parameters.
        """
        self._queue.put(email_data)

    def _worker(self) -> None:
        """Worker thread that processes email queue."""
        smtp_config_repo = get_smtp_config_repository()
        log_repo = get_email_log_repository()

        while self._running:
            try:
                # Get email from queue (timeout 1 second)
                email_data = self._queue.get(timeout=1)

                # Process email
                self._process_email(email_data, smtp_config_repo, log_repo)

            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Email worker error: {e}")

    def _process_email(
        self,
        email_data: dict[str, Any],
        smtp_config_repo,
        log_repo,
    ) -> None:
        """Process a single email."""
        log_id = email_data.get("log_id")
        retry_count = email_data.get("retry_count", 0)

        try:
            # Get SMTP config
            config = smtp_config_repo.get_config_with_password()
            if not config:
                logger.error("No SMTP configuration available")
                log_repo.update_status(log_id, "failed", "No SMTP configuration")
                return

            # Send email
            success = self._send_email(config, email_data)

            if success:
                log_repo.update_status(log_id, "sent")
                logger.info(f"Email sent successfully: log_id={log_id}")
            else:
                # Retry logic
                if retry_count < self._max_retry:
                    next_retry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
                        minutes=retry_count * 5 + 1
                    )
                    log_repo.update_status(
                        log_id, "retrying", increment_retry=True, next_retry_at=next_retry
                    )

                    # Re-enqueue for retry
                    email_data["retry_count"] = retry_count + 1
                    time.sleep(retry_count * 5 + 1)  # Wait before retry
                    self._queue.put(email_data)
                    logger.info(f"Email queued for retry {retry_count + 1}: log_id={log_id}")
                else:
                    log_repo.update_status(log_id, "failed", "Max retries exceeded")
                    logger.error(f"Email failed after {self._max_retry} retries: log_id={log_id}")

        except Exception as e:
            logger.error(f"Error processing email: {e}")
            if log_id:
                log_repo.update_status(log_id, "failed", str(e))

    def _send_email(self, config: dict[str, Any], email_data: dict[str, Any]) -> bool:
        """
        Send email via SMTP.

        Args:
            config: SMTP configuration.
            email_data: Email parameters.

        Returns:
            True if successful.
        """
        try:
            # Create message
            msg = MIMEMultipart()
            msg["From"] = config["from_address"]
            msg["To"] = email_data["recipient_email"]
            msg["Subject"] = email_data["subject"]

            # Add body
            body = email_data.get("email_body", "")
            msg.attach(MIMEText(body, "plain", "utf-8"))

            # Connect and send
            # Port 465 uses SSL connection (SMTPS), other ports use SMTP with optional STARTTLS
            smtp_port = config["smtp_port"]
            use_ssl = smtp_port == 465
            use_tls = config["use_tls"]
            smtp: smtplib.SMTP | smtplib.SMTP_SSL

            if use_ssl:
                smtp = smtplib.SMTP_SSL(config["smtp_host"], smtp_port, timeout=30)
                logger.info(f"SMTP_SSL connection established for port {smtp_port}")
            elif use_tls:
                smtp = smtplib.SMTP(config["smtp_host"], smtp_port, timeout=30)
                smtp.starttls()
                logger.info(f"SMTP STARTTLS connection established for port {smtp_port}")
            else:
                smtp = smtplib.SMTP(config["smtp_host"], smtp_port, timeout=30)
                logger.info(f"SMTP plain connection established for port {smtp_port}")

            # Login if credentials provided
            if config["smtp_user"] and config["smtp_password"]:
                smtp.login(config["smtp_user"], config["smtp_password"])

            # Send
            smtp.sendmail(
                config["from_address"],
                [email_data["recipient_email"]],
                msg.as_string(),
            )
            smtp.quit()

            return True

        except ssl.SSLError as e:
            logger.error(f"SMTP SSL error: {e}")
            return False
        except TimeoutError as e:
            logger.error(f"SMTP timeout error: {e}")
            return False
        except socket.gaierror as e:
            logger.error(f"SMTP address resolution error: {e}")
            return False
        except ConnectionRefusedError as e:
            logger.error(f"SMTP connection refused: {e}")
            return False
        except smtplib.SMTPException as e:
            logger.error(f"SMTP error: {e}")
            return False
        except Exception as e:
            logger.error(f"SMTP send error: {e}")
            return False


class EmailNotificationService:
    """Service for email notification management."""

    def __init__(self):
        """Initialize service."""
        self.template_manager = EmailTemplateManager()
        self.rate_limiter = RateLimiter()
        self.email_queue = EmailQueue()
        self.log_repo = get_email_log_repository()
        self.smtp_config_repo = get_smtp_config_repository()

        # Start queue worker
        self.email_queue.start()

    def send_alert_notification(
        self,
        user_id: int,
        recipient_email: str,
        alert_data: dict[str, Any],
        language: str = "en",
    ) -> dict[str, Any]:
        """
        Send alert notification email.

        Args:
            user_id: User ID.
            recipient_email: Recipient email address.
            alert_data: Alert data dict.
            language: Language code.

        Returns:
            Dict with success status and message.
        """
        # Check rate limit
        allowed, remaining = self.rate_limiter.check_rate_limit(user_id)
        if not allowed:
            logger.warning(f"Rate limit exceeded for user {user_id}")
            return {
                "success": False,
                "message": f"Rate limit exceeded. Maximum {RATE_LIMIT_PER_HOUR} emails per hour.",
                "rate_limit_remaining": 0,
            }

        # Check SMTP config
        config = self.smtp_config_repo.get_config()
        if not config:
            logger.error("No SMTP configuration")
            return {
                "success": False,
                "message": "SMTP server not configured",
            }

        if not config.get("is_verified"):
            logger.error("SMTP configuration not verified")
            return {
                "success": False,
                "message": "SMTP server not verified. Please test connection first.",
            }

        # Get template
        template = self.template_manager.get_template("alert", language)

        # Prepare variables
        variables = {
            "title": alert_data.get("title", "Alert"),
            "severity": alert_data.get("severity", "warning"),
            "alert_type": alert_data.get("alert_type", "system"),
            "message": alert_data.get("message", ""),
            "timestamp": alert_data.get("created_at", datetime.now().isoformat()),
            "action_text": alert_data.get("action_text", "View Details"),
            "action_url": alert_data.get("action_url", ""),
        }

        # Render template
        subject, body = self.template_manager.render(template, variables)

        # Create log entry
        log_id = self.log_repo.create_log(
            user_id=user_id,
            alert_id=alert_data.get("alert_id"),
            recipient_email=recipient_email,
            subject=subject,
            email_body=body,
            status="pending",
        )

        # Enqueue email
        email_data = {
            "log_id": log_id,
            "user_id": user_id,
            "recipient_email": recipient_email,
            "subject": subject,
            "email_body": body,
            "retry_count": 0,
        }

        self.email_queue.enqueue(email_data)

        # Record rate limit
        self.rate_limiter.record_send(user_id)

        logger.info(
            f"Email notification queued: user={user_id}, alert={alert_data.get('alert_id')}"
        )

        return {
            "success": True,
            "message": "Email notification queued for sending",
            "log_id": log_id,
            "rate_limit_remaining": remaining - 1,
        }

    def send_test_email(
        self,
        recipient_email: str,
        language: str = "en",
    ) -> dict[str, Any]:
        """
        Send a test email.

        Args:
            recipient_email: Recipient email address.
            language: Language code.

        Returns:
            Dict with success status.
        """
        test_alert = {
            "title": "Test Alert",
            "severity": "info",
            "alert_type": "system",
            "message": "This is a test email notification from Open ACE.",
            "created_at": datetime.now().isoformat(),
            "action_text": "",
            "action_url": "",
        }

        return self.send_alert_notification(
            user_id=0,  # System user
            recipient_email=recipient_email,
            alert_data=test_alert,
            language=language,
        )

    def get_user_logs(
        self,
        user_id: int,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """
        Get email logs for a user.

        Args:
            user_id: User ID.
            limit: Maximum results.
            offset: Offset for pagination.

        Returns:
            List of log entries.
        """
        return self.log_repo.get_user_logs(user_id, limit, offset)

    def cleanup_old_logs(self, days: int = 90) -> int:
        """
        Cleanup old email logs.

        Args:
            days: Days to keep.

        Returns:
            Number of deleted logs.
        """
        return self.log_repo.cleanup_old_logs(days)


# Global service instance
_email_notification_service: EmailNotificationService | None = None


def get_email_notification_service() -> EmailNotificationService:
    """Get the global email notification service instance."""
    global _email_notification_service
    if _email_notification_service is None:
        _email_notification_service = EmailNotificationService()
    return _email_notification_service
