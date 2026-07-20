"""
Open ACE - Alert Notifier Module

Provides real-time alert notification system for:
- Quota alerts (approaching limits)
- System alerts (errors, warnings)
- Security alerts (suspicious activity)

Supports WebSocket push, email, and webhook notifications.
"""
from __future__ import annotations


import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from requests.adapters import HTTPAdapter

from app.repositories.database import (
    DB_PATH,
    adapt_boolean_condition,
    adapt_boolean_value,
    adapt_sql,
    get_database_url,
    is_postgresql,
)
from app.services.email_notification_service import get_email_notification_service
from app.utils.config import get_config_value
from app.utils.outbound_url_guard import is_public_address

logger = logging.getLogger(__name__)

_SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}
_WEBHOOK_TIMEOUT_SECONDS = 5
# Cap concurrent outbound webhook deliveries so a burst of alerts can't spawn an
# unbounded number of background threads. Waiters block (off the request path)
# until a slot frees.
_WEBHOOK_MAX_WORKERS = 4
_webhook_delivery_semaphore = threading.BoundedSemaphore(_WEBHOOK_MAX_WORKERS)
_FEISHU_WEBHOOK_HOST_SNIPPETS = ("feishu.cn", "larksuite.com", "larkoffice.com")
_DINGTALK_WEBHOOK_HOST_SNIPPETS = ("dingtalk.com",)
_DINGTALK_SECRET_QUERY_KEYS = ("openace_dingtalk_secret", "dingtalk_secret")
# Feishu/Lark bot webhooks carry the bot token in the final path segment:
#   https://open.feishu.cn/open-apis/bot/v2/hook/<TOKEN>
# When masking, we only consider these known bot-hook path prefixes so we never
# touch unrelated query strings or paths.
_FEISHU_BOT_HOOK_PATH_PREFIXES = (
    "/open-apis/bot/v2/hook/",
    "/bot/v2/hook/",
    "/open-apis/bot/v1/hook/",
)
# Fields shipped to third-party generic webhook receivers. ``alert.metadata`` is
# free-form and has historically carried usage_percent/quota_type/username; it
# could equally carry tokens/IPs/emails at future call sites, so it is never
# forwarded wholesale.
_WEBHOOK_ALERT_ALLOWLIST = (
    "alert_id",
    "alert_type",
    "severity",
    "title",
    "message",
    "created_at",
)


def _redact_webhook_credentials(webhook_url, *, mask_feishu=True):
    """Strip embedded webhook credentials from a webhook URL.

    Two credential shapes are handled:

    * DingTalk signing secret query params (``openace_dingtalk_secret`` /
      ``dingtalk_secret``) — always stripped. The outbound signer reads the
      secret from the global ``alerts.dingtalk_webhook_secret`` config instead,
      so stripping it is lossless on the write path.
    * Feishu/Lark bot webhooks, which carry the bot token in the final URL path
      segment (``/open-apis/bot/v2/hook/<TOKEN>``). When ``mask_feishu`` is true
      the token is replaced with ``<redacted>``.

    The two credential shapes are *not* symmetric on the persistence path. The
    DingTalk secret lives in the query string and is rebuilt from global config
    on outbound signing, so it can safely be stripped before writing to the DB.
    The Feishu token lives only in the path and has no global-config equivalent
    (``alerts.webhook_secret``/``alerts.dingtalk_webhook_secret`` are unrelated),
    so it must NOT be masked on the write path — otherwise it is destroyed and
    every Feishu/Lark delivery silently fails. ``mask_feishu=False`` is therefore
    used at the persistence chokepoint (``set_notification_preferences``), while
    the default (``mask_feishu=True``) is used on every read/echo/log path so the
    token is never surfaced to the frontend or to logs.
    """
    if not webhook_url:
        return webhook_url

    # 1. DingTalk secret query params (always; lossless on the write path).
    try:
        parsed = urlparse(webhook_url)
    except ValueError:
        return webhook_url
    if parsed.query:
        original_items = parse_qsl(parsed.query, keep_blank_values=True)
        sanitized = [
            (key, value) for key, value in original_items if key not in _DINGTALK_SECRET_QUERY_KEYS
        ]
        if len(sanitized) != len(original_items):
            webhook_url = urlunparse(parsed._replace(query=urlencode(sanitized)))
            parsed = urlparse(webhook_url)

    # 2. Feishu/Lark path-based bot token (display/echo/log only).
    if mask_feishu:
        path = parsed.path or ""
        for prefix in _FEISHU_BOT_HOOK_PATH_PREFIXES:
            # ``len(path) > len(prefix)`` skips an empty token (URL ending exactly
            # at the prefix): an empty tail is not a real credential, so it is
            # left untouched rather than turned into ``.../hook/<redacted>``.
            if path.startswith(prefix) and len(path) > len(prefix):
                masked = prefix + "<redacted>"
                webhook_url = urlunparse(parsed._replace(path=masked))
                parsed = urlparse(webhook_url)
                break
    return webhook_url


# Back-compat alias: earlier code imported ``_redact_dingtalk_secret``. Keep the
# old name working for the read/echo path (full masking, including the Feishu
# path token). The write path now calls ``_redact_webhook_credentials(...,
# mask_feishu=False)`` directly so the Feishu token survives persistence.
_redact_dingtalk_secret = _redact_webhook_credentials


def _pin_host_to_url(url: str, pinned_ip: str) -> str:
    """Rewrite ``url`` so its host is the IP literal ``pinned_ip``.

    The original hostname is preserved separately as the ``Host`` header (set by
    the caller) so TLS SNI / virtual-host routing keeps working. Because the
    rewritten URL's host is an IP literal, ``urllib3`` will not re-resolve via
    the system resolver, which closes the DNS-rebinding TOCTOU window.
    """
    parsed = urlparse(url)
    host_header = f"[{pinned_ip}]" if ":" in pinned_ip else pinned_ip
    port_suffix = f":{parsed.port}" if parsed.port else ""
    netloc = f"{host_header}{port_suffix}"
    return urlunparse(parsed._replace(netloc=netloc))


class _PinnedWebhookAdapter(HTTPAdapter):
    """HTTPAdapter that refuses to connect to any IP outside the allowlist.

    Defense in depth on top of ``_pin_host_to_url``: even if a proxy or future
    urllib3 change re-introduces a resolution step, this adapter blocks any
    dial whose target IP literal is not on the verified allowlist.
    """

    def __init__(self, *args: Any, allowed_ips: list[str], **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._allowed_ips = set(allowed_ips)

    def get_connection(self, url, proxies=None):
        self._assert_pinned(url)
        return super().get_connection(url, proxies=proxies)

    def get_connection_with_tls_context(self, request, verify, proxies=None, cert=None):
        self._assert_pinned(request.url)
        return super().get_connection_with_tls_context(  # type: ignore[call-arg]
            request, verify, proxies=proxies, cert=cert
        )

    def _assert_pinned(self, url: str) -> None:
        host = (urlparse(url).hostname or "").strip("[]")
        if host not in self._allowed_ips:
            raise ValueError(f"Webhook request would reach unpinned or rebound IP: {host!r}")


class AlertType(Enum):
    """Alert types."""

    QUOTA = "quota"
    SYSTEM = "system"
    SECURITY = "security"
    PERFORMANCE = "performance"


class AlertSeverity(Enum):
    """Alert severity levels."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class Alert:
    """Alert data structure."""

    alert_id: str
    alert_type: str
    severity: str
    title: str
    message: str
    user_id: int | None = None
    username: str | None = None
    tool_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
    read: bool = False
    action_url: str | None = None
    action_text: str | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "alert_id": self.alert_id,
            "alert_type": self.alert_type,
            "severity": self.severity,
            "title": self.title,
            "message": self.message,
            "user_id": self.user_id,
            "username": self.username,
            "tool_name": self.tool_name,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
            "read": self.read,
            "action_url": self.action_url,
            "action_text": self.action_text,
        }


@dataclass
class NotificationPreference:
    """User notification preferences."""

    user_id: int
    email_enabled: bool = True
    push_enabled: bool = True
    webhook_url: str | None = None
    alert_types: list[str] = field(default_factory=lambda: ["quota", "system", "security"])
    min_severity: str = "warning"  # info, warning, critical
    notification_email: str | None = None  # User's notification email address
    email_verified: bool = False  # Whether email has been verified


class AlertNotifier:
    """Real-time alert notification manager."""

    def __init__(self, db_path: str | None = None):
        """
        Initialize the alert notifier.

        Args:
            db_path: Optional custom database path.
        """
        self.db_path = db_path or str(DB_PATH)
        self._subscribers: list[Callable] = []
        self._websocket_clients: dict[str, Any] = {}  # client_id -> websocket
        self._user_clients: dict[int, set[str]] = {}  # user_id -> set of client_ids
        self._email_config: dict[str, Any] = {}
        self._webhooks: dict[str, str] = {}

    def _matches_notification_preferences(
        self, alert: Alert, prefs: NotificationPreference, channel: str
    ) -> bool:
        """Return whether the alert matches the user's notification preferences."""
        if alert.alert_type not in prefs.alert_types:
            logger.debug(
                "Alert type %s not in user %s preferences for %s: %s",
                alert.alert_type,
                prefs.user_id,
                channel,
                prefs.alert_types,
            )
            return False

        if _SEVERITY_ORDER.get(alert.severity, 0) < _SEVERITY_ORDER.get(prefs.min_severity, 1):
            logger.debug(
                "Alert severity %s below user %s threshold %s for %s",
                alert.severity,
                prefs.user_id,
                prefs.min_severity,
                channel,
            )
            return False

        return True

    def _allow_private_webhook_urls(self) -> bool:
        """Whether private/loopback webhook targets are explicitly allowed."""
        return bool(get_config_value("alerts", "allow_private_webhook_urls", False))

    def _is_disallowed_webhook_ip(self, ip: ipaddress._BaseAddress) -> bool:
        """Return whether the resolved webhook IP is blocked by default.

        Uses the shared denylist predicate from :mod:`outbound_url_guard` so the
        webhook path rejects the same NAT64/CGNAT/multicast ranges as the SSO
        outbound guard, not just ``is_private``/``is_loopback``.
        """
        return not is_public_address(ip)

    def validate_webhook_url(
        self, webhook_url: str | None, resolve_dns: bool = True
    ) -> tuple[bool, str | None]:
        """Validate a webhook URL for syntax and outbound safety."""
        if not webhook_url:
            return True, None

        parsed = urlparse(webhook_url.strip())
        if parsed.scheme not in ("http", "https"):
            return False, "Webhook URL must start with http:// or https://"

        if not parsed.hostname:
            return False, "Webhook URL must include a hostname"

        if self._allow_private_webhook_urls():
            return True, None

        host = parsed.hostname.strip().lower()
        if host in {"localhost", "localhost.localdomain"}:
            return False, "Private and loopback webhook targets are blocked by default"

        try:
            ip = ipaddress.ip_address(host.split("%", 1)[0])
        except ValueError:
            ip = None

        if ip is not None:
            if self._is_disallowed_webhook_ip(ip):
                return False, "Private and loopback webhook targets are blocked by default"
            return True, None

        if not resolve_dns:
            return True, None

        try:
            resolved = socket.getaddrinfo(
                parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM
            )
        except OSError:
            return False, "Webhook hostname could not be resolved"

        for entry in resolved:
            resolved_ip = ipaddress.ip_address(entry[4][0].split("%", 1)[0])
            if self._is_disallowed_webhook_ip(resolved_ip):
                return False, "Private and loopback webhook targets are blocked by default"

        return True, None

    def _resolve_webhook_target_ips(self, webhook_url: str) -> tuple[list[str] | None, str | None]:
        """Resolve and validate a webhook URL, returning the verified public IPs.

        Returns ``(ips, error)``. ``ips`` is non-empty when the URL is safe. The
        returned IPs are pinned into the actual request (see
        :meth:`_send_webhook_notification`) so the system resolver cannot rebind
        the destination to a private address between validation and the dial.
        """
        parsed = urlparse(webhook_url.strip())
        host = parsed.hostname.strip().lower() if parsed.hostname else ""
        try:
            literal_ip = ipaddress.ip_address(host.split("%", 1)[0])
            if self._is_disallowed_webhook_ip(literal_ip):
                return None, "Private and loopback webhook targets are blocked by default"
            return [str(literal_ip)], None
        except ValueError:
            pass

        try:
            resolved = socket.getaddrinfo(
                parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM
            )
        except OSError:
            return None, "Webhook hostname could not be resolved"

        allowed_ips: list[str] = []
        for entry in resolved:
            resolved_ip = ipaddress.ip_address(entry[4][0].split("%", 1)[0])
            if self._is_disallowed_webhook_ip(resolved_ip):
                return None, "Private and loopback webhook targets are blocked by default"
            allowed_ips.append(str(resolved_ip))
        if not allowed_ips:
            return None, "Webhook hostname could not be resolved"
        return allowed_ips, None

    def _is_feishu_webhook(self, webhook_url: str) -> bool:
        """Return whether the webhook target looks like a Feishu/Lark bot webhook.

        Detection is exact-host-or-suffix anchored (reusing the shared
        ``_matches_webhook_host`` helper) so lookalike hosts such as
        ``notfeishu.cn`` or ``feishu.cn.evil.com`` are NOT misclassified as
        Feishu — an unanchored ``snippet in host`` check would match both.
        """
        host = (urlparse(webhook_url).hostname or "").lower()
        return self._matches_webhook_host(host, _FEISHU_WEBHOOK_HOST_SNIPPETS)

    def _matches_webhook_host(self, host: str, domains: tuple[str, ...]) -> bool:
        """Return whether host is exactly a known domain or one of its subdomains."""
        return any(host == domain or host.endswith(f".{domain}") for domain in domains)

    def _is_dingtalk_webhook(self, webhook_url: str) -> bool:
        """Return whether the webhook target looks like a DingTalk bot webhook."""
        parsed = urlparse(webhook_url)
        host = (parsed.hostname or "").lower()
        path = parsed.path.lower()
        # rstrip("/") so a user-configured trailing slash (e.g. "/robot/send/")
        # is still recognized as the DingTalk send endpoint rather than falling
        # through to the default-payload path.
        return self._matches_webhook_host(host, _DINGTALK_WEBHOOK_HOST_SNIPPETS) and path.rstrip(
            "/"
        ).endswith("/robot/send")

    def _format_webhook_text(self, alert: Alert) -> str:
        """Render a plain-text alert summary suitable for chat webhook bots."""
        lines = [
            f"[Open ACE] {alert.severity.upper()} - {alert.title}",
            alert.message,
            f"Type: {alert.alert_type}",
        ]
        if alert.username:
            lines.append(f"User: {alert.username}")
        if alert.tool_name:
            lines.append(f"Tool: {alert.tool_name}")
        if alert.action_url:
            lines.append(f"Action: {alert.action_url}")
        return "\n".join(line for line in lines if line)

    def _webhook_alert_view(self, alert: Alert) -> dict[str, Any]:
        """Return an allowlisted view of the alert for generic webhook payloads.

        ``alert.metadata`` is free-form and is already populated by some call
        sites with usage_percent / quota_type / username; future call sites
        could stuff tokens, IPs, or emails into it. To prevent a third-party
        webhook receiver from ever receiving that blob, the generic payload
        ships only an explicit, stable allowlist of alert fields.
        """
        full = alert.to_dict()
        return {field: full[field] for field in _WEBHOOK_ALERT_ALLOWLIST if field in full}

    def _build_webhook_payload(self, alert: Alert, webhook_url: str) -> dict[str, Any]:
        """Build the outbound webhook payload for the given target."""
        summary = self._format_webhook_text(alert)
        if self._is_feishu_webhook(webhook_url):
            return {"msg_type": "text", "content": {"text": summary}}
        if self._is_dingtalk_webhook(webhook_url):
            return {"msgtype": "text", "text": {"content": summary}}
        return {
            "event": "openace.alert",
            "source": "open-ace",
            "summary": summary,
            # Allowlisted view only — never the raw to_dict()/metadata blob.
            "alert": self._webhook_alert_view(alert),
        }

    def _prepare_webhook_url(self, webhook_url: str) -> str:
        """Return the outbound webhook URL, applying DingTalk signing when configured."""
        if not self._is_dingtalk_webhook(webhook_url):
            return webhook_url

        parsed = urlparse(webhook_url)
        query_items = parse_qsl(parsed.query, keep_blank_values=True)
        secret = str(get_config_value("alerts", "dingtalk_webhook_secret", "") or "").strip()
        sanitized_items: list[tuple[str, str]] = []
        for key, value in query_items:
            if key in _DINGTALK_SECRET_QUERY_KEYS:
                if value and not secret:
                    secret = value
                continue
            sanitized_items.append((key, value))

        if secret:
            timestamp = str(int(time.time() * 1000))
            string_to_sign = f"{timestamp}\n{secret}".encode()
            digest = hmac.new(secret.encode("utf-8"), string_to_sign, hashlib.sha256).digest()
            sign = base64.b64encode(digest).decode("utf-8")
            sanitized_items.extend([("timestamp", timestamp), ("sign", sign)])

        return urlunparse(parsed._replace(query=urlencode(sanitized_items)))

    def _sign_webhook_body(self, body: bytes) -> str | None:
        """Return an HMAC-SHA256 hex signature of ``body`` using the configured
        generic webhook secret, or ``None`` if no secret is configured.

        The signature is sent as the ``X-OpenACE-Signature`` header so generic
        (non-Feishu/non-DingTalk) webhook receivers can verify the payload is
        genuinely from this Open ACE instance. Feishu and DingTalk use their
        own provider-specific signing schemes and are unaffected.
        """
        secret = str(get_config_value("alerts", "webhook_secret", "") or "").strip()
        if not secret:
            return None
        return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    def _send_webhook_notification(self, alert: Alert, user_id: int) -> None:
        """Send a webhook notification for an alert if user preferences allow.

        This runs on a background worker thread (see :meth:`create_alert`) so a
        slow or hanging receiver never blocks the user-facing request that
        triggered the alert.
        """
        # Bound ``prefs`` before the try so the broad-except handler below can
        # safely read it even when ``get_notification_preferences`` itself
        # raises (e.g. a DB error). Otherwise referencing ``prefs`` there would
        # raise UnboundLocalError and mask the real exception.
        prefs: NotificationPreference | None = None
        try:
            prefs = self.get_notification_preferences(user_id)

            if not prefs.push_enabled:
                logger.debug("Webhook notifications disabled for user %s", user_id)
                return

            if not prefs.webhook_url:
                logger.debug("No webhook URL configured for user %s", user_id)
                return

            if not self._matches_notification_preferences(alert, prefs, "webhook"):
                return

            # Pin the validated IP into the actual request so the system resolver
            # cannot rebind the destination to a private address between
            # validation and the dial (DNS-rebinding / SSRF TOCTOU).
            pinned_ips, error = self._resolve_webhook_target_ips(prefs.webhook_url)
            if not pinned_ips:
                logger.warning(
                    "Skipping webhook notification for user %s alert %s: %s",
                    user_id,
                    alert.alert_id,
                    error,
                )
                return

            payload = self._build_webhook_payload(alert, prefs.webhook_url)
            body = json.dumps(payload).encode("utf-8")
            outbound_url = self._prepare_webhook_url(prefs.webhook_url)
            pinned_url = _pin_host_to_url(outbound_url, pinned_ips[0])
            headers = {
                "User-Agent": "Open-ACE Alert Webhook",
                "Host": urlparse(outbound_url).hostname,
                "Content-Type": "application/json",
            }
            # Sign generic payloads so receivers can verify authenticity.
            signature = self._sign_webhook_body(body)
            if signature is not None:
                headers["X-OpenACE-Signature"] = signature
            session = requests.Session()
            try:
                session.mount("https://", _PinnedWebhookAdapter(allowed_ips=pinned_ips))
                session.mount("http://", _PinnedWebhookAdapter(allowed_ips=pinned_ips))
                response = session.post(
                    pinned_url,
                    data=body,
                    timeout=_WEBHOOK_TIMEOUT_SECONDS,
                    allow_redirects=False,
                    headers=headers,
                )
            finally:
                session.close()
            response.raise_for_status()
            logger.info(
                "Webhook notification delivered for alert %s to user %s",
                alert.alert_id,
                user_id,
            )
        except Exception as e:
            # Log the exception TYPE + a redacted host only. ``requests``
            # ConnectionError / HTTPError embed the full request URL, which for
            # Feishu/Lark contains the bot token in the path — never interpolate
            # the raw exception. ``prefs`` is pre-bound to None above so this is
            # safe even when get_notification_preferences itself raised.
            webhook_url = prefs.webhook_url if prefs and prefs.webhook_url else None
            redacted_host = _redact_webhook_credentials(webhook_url) if webhook_url else None
            host = (urlparse(redacted_host).hostname if redacted_host else None) or "unknown"
            logger.warning(
                "Failed to deliver webhook notification for alert %s to user %s "
                "(host=%s, error=%s)",
                alert.alert_id,
                user_id,
                host,
                type(e).__name__,
            )

    def _get_connection(self) -> sqlite3.Connection | Any:
        """Get database connection (SQLite or PostgreSQL)."""
        if is_postgresql():
            try:
                import psycopg2
                from psycopg2.extras import RealDictCursor

                url = get_database_url()
                conn = psycopg2.connect(url)
                conn.cursor_factory = RealDictCursor
                return conn
            except ImportError:
                raise ImportError(
                    "psycopg2 is required for PostgreSQL. "
                    "Install it with: pip install psycopg2-binary"
                ) from None
        else:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            return conn

    def _ensure_tables(self) -> None:
        """Ensure required tables exist."""
        conn = self._get_connection()
        cursor = conn.cursor()

        # Use SERIAL for PostgreSQL, AUTOINCREMENT for SQLite
        id_type = "SERIAL PRIMARY KEY" if is_postgresql() else "INTEGER PRIMARY KEY AUTOINCREMENT"
        bool_true = "BOOLEAN DEFAULT TRUE" if is_postgresql() else "INTEGER DEFAULT 1"
        bool_false = "BOOLEAN DEFAULT FALSE" if is_postgresql() else "INTEGER DEFAULT 0"

        # Create alerts table
        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS alerts (
                id {id_type},
                alert_id TEXT UNIQUE NOT NULL,
                alert_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                title TEXT NOT NULL,
                message TEXT,
                user_id INTEGER,
                username TEXT,
                tool_name TEXT,
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                read {bool_false},
                action_url TEXT,
                action_text TEXT
            )
        """
        )

        # Create notification_preferences table
        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS notification_preferences (
                user_id INTEGER PRIMARY KEY,
                email_enabled {bool_true},
                push_enabled {bool_true},
                webhook_url TEXT,
                alert_types TEXT,
                min_severity TEXT DEFAULT 'warning',
                notification_email TEXT,
                email_verified {bool_false}
            )
        """
        )

        # Create indexes
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_alerts_user_id
            ON alerts(user_id)
        """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_alerts_created_at
            ON alerts(created_at)
        """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_alerts_read
            ON alerts(read)
        """
        )

        conn.commit()
        conn.close()

    def subscribe(self, callback: Callable) -> None:
        """
        Subscribe to alert events.

        Args:
            callback: Function to call when an alert is created.
        """
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable) -> None:
        """
        Unsubscribe from alert events.

        Args:
            callback: Function to remove from subscribers.
        """
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    def register_websocket(
        self, client_id: str, websocket: Any, user_id: int | None = None
    ) -> None:
        """
        Register a WebSocket client.

        Args:
            client_id: Unique client identifier.
            websocket: WebSocket connection object.
            user_id: Optional user ID for targeted notifications.
        """
        self._websocket_clients[client_id] = websocket
        if user_id is not None:
            if user_id not in self._user_clients:
                self._user_clients[user_id] = set()
            self._user_clients[user_id].add(client_id)
        logger.info(f"Registered WebSocket client: {client_id} for user: {user_id}")

    def unregister_websocket(self, client_id: str) -> None:
        """
        Unregister a WebSocket client.

        Args:
            client_id: Client identifier to remove.
        """
        self._websocket_clients.pop(client_id, None)
        # Remove from user_clients
        for user_id, clients in list(self._user_clients.items()):
            clients.discard(client_id)
            if not clients:
                del self._user_clients[user_id]
        logger.info(f"Unregistered WebSocket client: {client_id}")

    def create_alert(
        self,
        alert_type: str,
        severity: str,
        title: str,
        message: str,
        user_id: int | None = None,
        username: str | None = None,
        tool_name: str | None = None,
        metadata: dict[str, Any] | None = None,
        action_url: str | None = None,
        action_text: str | None = None,
        language: str = "en",
    ) -> Alert:
        """
        Create a new alert.

        Args:
            alert_type: Type of alert (quota, system, security, performance).
            severity: Severity level (info, warning, critical).
            title: Alert title.
            message: Detailed message.
            user_id: Optional user ID for targeted alert.
            username: Optional username.
            tool_name: Optional tool name.
            metadata: Optional additional metadata.
            action_url: Optional URL for action button.
            action_text: Optional text for action button.
            language: Language for email notification (en, zh, ja, ko).

        Returns:
            Alert: The created alert.
        """
        alert = Alert(
            alert_id=str(uuid.uuid4()),
            alert_type=alert_type,
            severity=severity,
            title=title,
            message=message,
            user_id=user_id,
            username=username,
            tool_name=tool_name,
            metadata=metadata or {},
            action_url=action_url,
            action_text=action_text,
        )

        # Save to database
        self._save_alert(alert)

        # Notify subscribers
        for callback in self._subscribers:
            try:
                if asyncio.iscoroutinefunction(callback):
                    asyncio.create_task(callback(alert))
                else:
                    callback(alert)
            except Exception as e:
                logger.error(f"Error in alert callback: {e}")

        # Send email notification if user preferences allow
        if user_id:
            self._send_email_notification(alert, user_id, language)
            # Deliver the webhook off the request path: a slow/hanging receiver
            # must never add up to _WEBHOOK_TIMEOUT_SECONDS of latency to the
            # user-facing request that created the alert (e.g. the LLM proxy
            # 429 handler calling create_quota_alert). The daemon worker thread
            # logs any delivery error itself.
            self._dispatch_webhook_async(alert, user_id)

        logger.info(f"Created alert: [{severity}] {title}")
        return alert

    def _dispatch_webhook_async(self, alert: Alert, user_id: int) -> None:
        """Deliver ``_send_webhook_notification`` on a background daemon thread.

        Runs out of band with the request that created the alert. Concurrency is
        capped by a process-wide bounded semaphore so a burst of alerts can't
        spawn unbounded threads. Any exception raised by the worker is already
        swallowed/logged inside ``_send_webhook_notification``; the thread body
        also releases the semaphore in a ``finally`` so a slot is never leaked.
        """

        def _worker():
            acquired = False
            try:
                _webhook_delivery_semaphore.acquire()
                acquired = True
                self._send_webhook_notification(alert, user_id)
            except Exception as e:  # pragma: no cover - defensive
                logger.error(
                    "Unexpected error dispatching webhook for alert %s: %s",
                    alert.alert_id,
                    e,
                )
            finally:
                if acquired:
                    _webhook_delivery_semaphore.release()

        thread = threading.Thread(
            target=_worker,
            name=f"openace-webhook-{alert.alert_id[:8]}",
            daemon=True,
        )
        thread.start()

    def _send_email_notification(
        self,
        alert: Alert,
        user_id: int,
        language: str = "en",
    ) -> None:
        """
        Send email notification for an alert if user preferences allow.

        Args:
            alert: The alert to send.
            user_id: User ID to send notification to.
            language: Language for email template.
        """
        try:
            # Get user notification preferences
            prefs = self.get_notification_preferences(user_id)

            # Check if email notifications are enabled
            if not prefs.email_enabled:
                logger.debug(f"Email notifications disabled for user {user_id}")
                return

            # Check notification email is set
            if not prefs.notification_email:
                logger.debug(f"No notification email set for user {user_id}")
                return

            if not self._matches_notification_preferences(alert, prefs, "email"):
                return

            # Prepare alert data for email
            alert_data = alert.to_dict()

            # Send email notification
            email_service = get_email_notification_service()
            result = email_service.send_alert_notification(
                user_id=user_id,
                recipient_email=prefs.notification_email,
                alert_data=alert_data,
                language=language,
            )

            if result["success"]:
                logger.info(
                    f"Email notification queued for alert {alert.alert_id} to user {user_id}"
                )
            else:
                logger.warning(
                    f"Failed to queue email notification for alert {alert.alert_id}: {result['message']}"
                )

        except Exception as e:
            logger.error(f"Error sending email notification for alert {alert.alert_id}: {e}")

    def _save_alert(self, alert: Alert) -> int:
        """Save alert to database."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            adapt_sql(
                """
            INSERT INTO alerts
            (alert_id, alert_type, severity, title, message, user_id, username,
             tool_name, metadata, created_at, read, action_url, action_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
            ),
            (
                alert.alert_id,
                alert.alert_type,
                alert.severity,
                alert.title,
                alert.message,
                alert.user_id,
                alert.username,
                alert.tool_name,
                json.dumps(alert.metadata),
                alert.created_at.isoformat(),
                1 if alert.read else 0,
                alert.action_url,
                alert.action_text,
            ),
        )

        alert_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return int(alert_id) if alert_id is not None else 0

    async def broadcast(self, alert: Alert, target_user_id: int | None = None) -> None:
        """
        Broadcast alert to WebSocket clients.

        Args:
            alert: Alert to broadcast.
            target_user_id: Optional specific user to target.
        """
        alert_dict = alert.to_dict()
        message = json.dumps({"type": "alert", "data": alert_dict})

        if target_user_id is not None:
            # Send to specific user's clients
            client_ids = self._user_clients.get(target_user_id, set())
            for client_id in list(client_ids):
                await self._send_to_client(client_id, message)
        else:
            # Broadcast to all clients
            for client_id in list(self._websocket_clients.keys()):
                await self._send_to_client(client_id, message)

    async def _send_to_client(self, client_id: str, message: str) -> bool:
        """Send message to a specific client."""
        ws = self._websocket_clients.get(client_id)
        if ws is None:
            return False

        try:
            await ws.send(message)
            return True
        except Exception as e:
            logger.error(f"Error sending to client {client_id}: {e}")
            self.unregister_websocket(client_id)
            return False

    def get_alerts(
        self,
        user_id: int | None = None,
        alert_type: str | None = None,
        severity: str | None = None,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Alert]:
        """
        Get alerts with filters.

        Args:
            user_id: Filter by user ID.
            alert_type: Filter by alert type.
            severity: Filter by severity.
            unread_only: Only return unread alerts.
            limit: Maximum number of alerts to return.
            offset: Offset for pagination.

        Returns:
            List of Alert objects.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        conditions = []
        params: list[Any] = []

        if user_id is not None:
            conditions.append("user_id = ?")
            params.append(user_id)

        if alert_type:
            conditions.append("alert_type = ?")
            params.append(alert_type)

        if severity:
            conditions.append("severity = ?")
            params.append(severity)

        if unread_only:
            conditions.append(adapt_boolean_condition("read", False))

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        cursor.execute(
            adapt_sql(
                f"""
            SELECT * FROM alerts
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """
            ),
            params + [limit, offset],
        )

        rows = cursor.fetchall()
        conn.close()

        return [self._row_to_alert(row) for row in rows]

    def get_unread_count(self, user_id: int | None = None) -> int:
        """
        Get count of unread alerts.

        Args:
            user_id: Optional user ID to filter by.

        Returns:
            Number of unread alerts.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        if user_id is not None:
            cursor.execute(
                adapt_sql(
                    f"SELECT COUNT(*) as count FROM alerts WHERE user_id = ? AND {adapt_boolean_condition('read', False)}"
                ),
                (user_id,),
            )
        else:
            cursor.execute(
                f"SELECT COUNT(*) as count FROM alerts WHERE {adapt_boolean_condition('read', False)}"
            )

        count = cursor.fetchone()["count"]
        conn.close()

        return count or 0

    def has_recent_quota_alert(
        self,
        user_id: int,
        quota_type: str,
        hours: int = 1,
    ) -> bool:
        """
        Check if a recent quota alert exists for the user.

        Used to deduplicate quota exceeded alerts and avoid spamming users
        with repeated notifications for the same quota type.

        Args:
            user_id: User ID to check.
            quota_type: Quota type (tokens, requests, platform).
            hours: Time window in hours to check (default: 1).

        Returns:
            True if a recent alert exists within the time window.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        threshold = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)

        # PostgreSQL uses ->> for JSON extraction, SQLite uses json_extract
        if is_postgresql():
            cursor.execute(
                """
                SELECT COUNT(*) as count FROM alerts
                WHERE user_id = %s
                  AND alert_type = %s
                  AND created_at >= %s
                  AND metadata->>'quota_type' = %s
                """,
                (user_id, AlertType.QUOTA.value, threshold.isoformat(), quota_type),
            )
        else:
            cursor.execute(
                """
                SELECT COUNT(*) as count FROM alerts
                WHERE user_id = ?
                  AND alert_type = ?
                  AND created_at >= ?
                  AND json_extract(metadata, '$.quota_type') = ?
                """,
                (user_id, AlertType.QUOTA.value, threshold.isoformat(), quota_type),
            )

        count = cursor.fetchone()["count"]
        conn.close()

        return bool(count > 0)

    def mark_as_read(self, alert_id: str) -> bool:
        """
        Mark an alert as read.

        Args:
            alert_id: Alert ID to mark as read.

        Returns:
            True if successful.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            adapt_sql("UPDATE alerts SET read = ? WHERE alert_id = ?"),
            (adapt_boolean_value(True), alert_id),
        )

        success = cursor.rowcount > 0
        conn.commit()
        conn.close()

        return success

    def mark_all_as_read(self, user_id: int | None = None) -> int:
        """
        Mark all alerts as read.

        Args:
            user_id: Optional user ID to filter by.

        Returns:
            Number of alerts marked as read.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        if user_id is not None:
            cursor.execute(
                adapt_sql(
                    f"UPDATE alerts SET read = ? WHERE user_id = ? AND {adapt_boolean_condition('read', False)}"
                ),
                (adapt_boolean_value(True), user_id),
            )
        else:
            cursor.execute(
                f"UPDATE alerts SET read = ? WHERE {adapt_boolean_condition('read', False)}",
                (adapt_boolean_value(True),),
            )

        count = cursor.rowcount
        conn.commit()
        conn.close()

        return count

    def delete_alert(self, alert_id: str) -> bool:
        """
        Delete an alert.

        Args:
            alert_id: Alert ID to delete.

        Returns:
            True if successful.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(adapt_sql("DELETE FROM alerts WHERE alert_id = ?"), (alert_id,))
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()

        return success

    def cleanup_old_alerts(self, days: int = 30) -> int:
        """
        Delete alerts older than specified days.

        Args:
            days: Number of days to keep.

        Returns:
            Number of deleted alerts.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cutoff = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        ).isoformat()
        cursor.execute(
            adapt_sql(
                f"DELETE FROM alerts WHERE created_at < ? AND {adapt_boolean_condition('read', True)}"
            ),
            (cutoff,),
        )

        count = cursor.rowcount
        conn.commit()
        conn.close()

        logger.info(f"Cleaned up {count} old alerts")
        return count

    def get_notification_preferences(self, user_id: int) -> NotificationPreference:
        """
        Get notification preferences for a user.

        Args:
            user_id: User ID.

        Returns:
            NotificationPreference object.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            adapt_sql("SELECT * FROM notification_preferences WHERE user_id = ?"), (user_id,)
        )
        row = cursor.fetchone()
        conn.close()

        if row:
            columns = set(row.keys())
            return NotificationPreference(
                user_id=row["user_id"],
                email_enabled=bool(row["email_enabled"]),
                push_enabled=bool(row["push_enabled"]),
                # Strip only the (rebuildable) DingTalk query secret here so the
                # Feishu/Lark path token survives for delivery. The frontend
                # echo re-masks both credentials (full masking) at the route
                # layer (app/routes/alerts.py GET /alerts/preferences).
                webhook_url=_redact_webhook_credentials(row["webhook_url"], mask_feishu=False),
                alert_types=(
                    json.loads(row["alert_types"])
                    if row["alert_types"]
                    else ["quota", "system", "security"]
                ),
                min_severity=row["min_severity"] or "warning",
                notification_email=(
                    row["notification_email"] if "notification_email" in columns else None
                ),
                email_verified=bool(
                    row["email_verified"] if "email_verified" in columns else False
                ),
            )

        # Return default preferences
        return NotificationPreference(user_id=user_id)

    def set_notification_preferences(self, preferences: NotificationPreference) -> bool:
        """
        Set notification preferences for a user.

        Args:
            preferences: NotificationPreference object.

        Returns:
            True if successful.
        """
        # On the persistence path strip only the DingTalk query signing secret —
        # it is rebuildable from global config on outbound signing. The Feishu/
        # Lark bot token lives in the URL path and has no global-config
        # equivalent, so it must be preserved verbatim here or every Feishu
        # delivery would POST to ``/.../<redacted>`` and be rejected. The token
        # is masked only on the read/echo path (get_notification_preferences)
        # and in delivery-failure logs.
        preferences.webhook_url = _redact_webhook_credentials(
            preferences.webhook_url, mask_feishu=False
        )

        conn = self._get_connection()
        cursor = conn.cursor()

        if is_postgresql():
            cursor.execute(
                """
                INSERT INTO notification_preferences
                (user_id, email_enabled, push_enabled, webhook_url, alert_types,
                 min_severity, notification_email, email_verified)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    email_enabled = EXCLUDED.email_enabled,
                    push_enabled = EXCLUDED.push_enabled,
                    webhook_url = EXCLUDED.webhook_url,
                    alert_types = EXCLUDED.alert_types,
                    min_severity = EXCLUDED.min_severity,
                    notification_email = EXCLUDED.notification_email,
                    email_verified = EXCLUDED.email_verified
            """,
                (
                    preferences.user_id,
                    preferences.email_enabled,
                    preferences.push_enabled,
                    preferences.webhook_url,
                    json.dumps(preferences.alert_types),
                    preferences.min_severity,
                    preferences.notification_email,
                    preferences.email_verified,
                ),
            )
        else:
            cursor.execute(
                """
                INSERT OR REPLACE INTO notification_preferences
                (user_id, email_enabled, push_enabled, webhook_url, alert_types,
                 min_severity, notification_email, email_verified)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    preferences.user_id,
                    1 if preferences.email_enabled else 0,
                    1 if preferences.push_enabled else 0,
                    preferences.webhook_url,
                    json.dumps(preferences.alert_types),
                    preferences.min_severity,
                    preferences.notification_email,
                    1 if preferences.email_verified else 0,
                ),
            )

        conn.commit()
        conn.close()

        return True

    def _row_to_alert(self, row: sqlite3.Row) -> Alert:
        """Convert a database row to Alert."""
        return Alert(
            alert_id=row["alert_id"],
            alert_type=row["alert_type"],
            severity=row["severity"],
            title=row["title"],
            message=row["message"] or "",
            user_id=row["user_id"],
            username=row["username"],
            tool_name=row["tool_name"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            created_at=(
                row["created_at"]
                if isinstance(row["created_at"], datetime)
                else (
                    datetime.fromisoformat(row["created_at"])
                    if row["created_at"]
                    else datetime.now(timezone.utc).replace(tzinfo=None)
                )
            ),
            read=bool(row["read"]),
            action_url=row["action_url"],
            action_text=row["action_text"],
        )


# Global alert notifier instance
_alert_notifier: AlertNotifier | None = None


def get_alert_notifier(db_path: str | None = None) -> AlertNotifier:
    """
    Get the global alert notifier instance.

    Args:
        db_path: Optional custom database path.

    Returns:
        AlertNotifier instance.
    """
    global _alert_notifier
    if _alert_notifier is None:
        _alert_notifier = AlertNotifier(db_path)
    return _alert_notifier


def create_quota_alert(
    user_id: int,
    username: str,
    usage_percent: float,
    quota_type: str = "tokens",
    language: str = "en",
) -> Alert:
    """
    Create a quota alert.

    Args:
        user_id: User ID.
        username: Username.
        usage_percent: Usage percentage.
        quota_type: Type of quota (tokens or requests).
        language: Language for email notification.

    Returns:
        Created Alert.
    """
    notifier = get_alert_notifier()

    if usage_percent >= 100:
        severity = AlertSeverity.CRITICAL.value
        title = f"Quota Exceeded: {quota_type.title()}"
        message = f"Your {quota_type} quota has been fully used. Please contact administrator."
    elif usage_percent >= 95:
        severity = AlertSeverity.CRITICAL.value
        title = f"Quota Critical: {quota_type.title()}"
        message = f"You have used {usage_percent:.1f}% of your {quota_type} quota."
    elif usage_percent >= 80:
        severity = AlertSeverity.WARNING.value
        title = f"Quota Warning: {quota_type.title()}"
        message = f"You have used {usage_percent:.1f}% of your {quota_type} quota."
    else:
        severity = AlertSeverity.INFO.value
        title = f"Quota Notice: {quota_type.title()}"
        message = f"You have used {usage_percent:.1f}% of your {quota_type} quota."

    return notifier.create_alert(
        alert_type=AlertType.QUOTA.value,
        severity=severity,
        title=title,
        message=message,
        user_id=user_id,
        username=username,
        metadata={
            "usage_percent": usage_percent,
            "quota_type": quota_type,
        },
        action_url="/report",
        action_text="View Usage",
        language=language,
    )


def create_system_alert(
    title: str,
    message: str,
    severity: str = AlertSeverity.WARNING.value,
    tool_name: str | None = None,
    language: str = "en",
) -> Alert:
    """
    Create a system alert.

    Args:
        title: Alert title.
        message: Alert message.
        severity: Severity level.
        tool_name: Optional tool name.
        language: Language for email notification.

    Returns:
        Created Alert.
    """
    notifier = get_alert_notifier()
    return notifier.create_alert(
        alert_type=AlertType.SYSTEM.value,
        severity=severity,
        title=title,
        message=message,
        tool_name=tool_name,
        language=language,
    )


def create_security_alert(
    title: str,
    message: str,
    user_id: int | None = None,
    username: str | None = None,
    severity: str = AlertSeverity.CRITICAL.value,
    language: str = "en",
) -> Alert:
    """
    Create a security alert.

    Args:
        title: Alert title.
        message: Alert message.
        user_id: Optional user ID.
        username: Optional username.
        severity: Severity level.
        language: Language for email notification.

    Returns:
        Created Alert.
    """
    notifier = get_alert_notifier()
    return notifier.create_alert(
        alert_type=AlertType.SECURITY.value,
        severity=severity,
        title=title,
        message=message,
        user_id=user_id,
        username=username,
        language=language,
    )


# =============================================================================
# Scene-Specific Alert Functions
# =============================================================================
# These functions provide built-in severity determination for common scenarios.
# Developers should prefer these functions over generic create_system_alert /
# create_security_alert for better consistency and maintainability.


def create_service_down_alert(
    service_name: str,
    details: str,
    language: str = "en",
) -> Alert:
    """
    Create a service down alert (CRITICAL severity).

    Used when a critical service becomes unavailable.

    Severity: CRITICAL (service down is always critical)

    Args:
        service_name: Name of the affected service.
        details: Additional details about the service failure.
        language: Language for email notification.

    Returns:
        Created Alert.
    """
    return create_system_alert(
        title=f"Service Down: {service_name}",
        message=f"Service '{service_name}' is unavailable. Details: {details}",
        severity=AlertSeverity.CRITICAL.value,
        language=language,
    )


def create_service_startup_alert(
    service_name: str,
    startup_time: float,
    threshold: float,
    language: str = "en",
) -> Alert:
    """
    Create a service startup alert (WARNING or CRITICAL based on startup time).

    Used when a service takes longer than expected to start.

    Severity determination:
    - startup_time > threshold * 2: CRITICAL
    - startup_time > threshold: WARNING

    Args:
        service_name: Name of the service.
        startup_time: Actual startup time in seconds.
        threshold: Expected startup time threshold in seconds.
        language: Language for email notification.

    Returns:
        Created Alert.
    """
    if startup_time > threshold * 2:
        severity = AlertSeverity.CRITICAL.value
        title = f"Service Startup Critical: {service_name}"
        message = (
            f"Service '{service_name}' startup took {startup_time:.1f}s, "
            f"which is {startup_time / threshold:.1f}x the expected threshold ({threshold}s)."
        )
    else:
        severity = AlertSeverity.WARNING.value
        title = f"Service Startup Warning: {service_name}"
        message = (
            f"Service '{service_name}' startup took {startup_time:.1f}s, "
            f"exceeding the expected threshold ({threshold}s)."
        )

    return create_system_alert(
        title=title,
        message=message,
        severity=severity,
        language=language,
    )


def create_resource_alert(
    resource_type: str,
    current: float,
    limit: float,
    threshold_warning: float = 0.8,
    threshold_critical: float = 0.95,
    language: str = "en",
) -> Alert:
    """
    Create a resource shortage alert (INFO, WARNING, or CRITICAL based on usage).

    Used for memory, CPU, disk, or other resource shortage alerts.

    Severity determination:
    - usage >= 100%: CRITICAL (resource exhausted)
    - usage >= threshold_critical: CRITICAL (approaching limit)
    - usage >= threshold_warning: WARNING (moderate shortage)
    - usage < threshold_warning: INFO (notification only)

    Args:
        resource_type: Type of resource (memory, cpu, disk, etc.).
        current: Current resource usage.
        limit: Resource limit.
        threshold_warning: Warning threshold as percentage (default 80%).
        threshold_critical: Critical threshold as percentage (default 95%).
        language: Language for email notification.

    Returns:
        Created Alert.
    """
    usage_percent = (current / limit) * 100 if limit > 0 else 100

    if usage_percent >= 100:
        severity = AlertSeverity.CRITICAL.value
        title = f"Resource Exhausted: {resource_type}"
        message = f"{resource_type} is fully used ({current}/{limit})."
    elif usage_percent >= threshold_critical * 100:
        severity = AlertSeverity.CRITICAL.value
        title = f"Resource Critical: {resource_type}"
        message = f"{resource_type} usage at {usage_percent:.1f}% ({current}/{limit})."
    elif usage_percent >= threshold_warning * 100:
        severity = AlertSeverity.WARNING.value
        title = f"Resource Warning: {resource_type}"
        message = f"{resource_type} usage at {usage_percent:.1f}% ({current}/{limit})."
    else:
        severity = AlertSeverity.INFO.value
        title = f"Resource Notice: {resource_type}"
        message = f"{resource_type} usage at {usage_percent:.1f}% ({current}/{limit})."

    return create_system_alert(
        title=title,
        message=message,
        severity=severity,
        language=language,
    )


def create_config_error_alert(
    config_key: str,
    error_details: str,
    language: str = "en",
) -> Alert:
    """
    Create a configuration error alert (WARNING severity).

    Used for configuration validation errors or invalid settings.

    Severity: WARNING (configuration errors need attention but are not immediately critical)

    Args:
        config_key: The configuration key that has an error.
        error_details: Details about the configuration error.
        language: Language for email notification.

    Returns:
        Created Alert.
    """
    return create_system_alert(
        title=f"Configuration Error: {config_key}",
        message=f"Configuration key '{config_key}' has an error: {error_details}",
        severity=AlertSeverity.WARNING.value,
        language=language,
    )


def create_api_error_alert(
    api_name: str,
    error_code: int,
    error_message: str,
    language: str = "en",
) -> Alert:
    """
    Create an API error alert (WARNING severity).

    Used for API call failures or unexpected responses.

    Severity: WARNING (API errors typically need investigation)

    Args:
        api_name: Name of the API or endpoint.
        error_code: Error code returned by the API.
        error_message: Error message from the API.
        language: Language for email notification.

    Returns:
        Created Alert.
    """
    return create_system_alert(
        title=f"API Error: {api_name}",
        message=f"API '{api_name}' returned error {error_code}: {error_message}",
        severity=AlertSeverity.WARNING.value,
        language=language,
    )


def create_auth_failure_alert(
    username: str,
    failure_count: int,
    threshold: int = 5,
    language: str = "en",
) -> Alert:
    """
    Create an authentication failure alert (WARNING or CRITICAL based on count).

    Used for login failures, token validation failures, etc.

    Severity determination:
    - failure_count >= threshold: CRITICAL (repeated failures indicate potential attack)
    - failure_count < threshold: WARNING (single failure needs monitoring)

    Args:
        username: Username that failed authentication.
        failure_count: Number of consecutive failures for this user.
        threshold: Threshold for upgrading to CRITICAL (default 5).
        language: Language for email notification.

    Returns:
        Created Alert.
    """
    if failure_count >= threshold:
        severity = AlertSeverity.CRITICAL.value
        title = f"Authentication Failure Alert: {username}"
        message = (
            f"User '{username}' has {failure_count} consecutive authentication failures. "
            f"This may indicate a brute-force attack attempt."
        )
    else:
        severity = AlertSeverity.WARNING.value
        title = f"Authentication Failure: {username}"
        message = (
            f"User '{username}' authentication failed ({failure_count} failures). "
            f"Monitoring suggested."
        )

    return create_security_alert(
        title=title,
        message=message,
        username=username,
        severity=severity,
        language=language,
    )


def create_permission_violation_alert(
    username: str,
    resource: str,
    action: str,
    language: str = "en",
) -> Alert:
    """
    Create a permission violation alert (CRITICAL severity).

    Used when a user attempts to access a resource without proper permissions.

    Severity: CRITICAL (permission violations are security incidents)

    Args:
        username: Username that attempted the unauthorized action.
        resource: Resource that was accessed.
        action: Action that was attempted.
        language: Language for email notification.

    Returns:
        Created Alert.
    """
    return create_security_alert(
        title=f"Permission Violation: {username}",
        message=f"User '{username}' attempted '{action}' on '{resource}' without authorization.",
        username=username,
        severity=AlertSeverity.CRITICAL.value,
        language=language,
    )


def create_suspicious_activity_alert(
    username: str,
    activity_type: str,
    risk_score: float,
    language: str = "en",
) -> Alert:
    """
    Create a suspicious activity alert (WARNING or CRITICAL based on risk score).

    Used for detecting unusual user behavior patterns.

    Severity determination:
    - risk_score >= 50: CRITICAL (high-risk activity)
    - risk_score < 50: WARNING (moderate-risk activity)

    Args:
        username: Username showing suspicious behavior.
        activity_type: Type of suspicious activity detected.
        risk_score: Risk score from 0-100 (higher = more suspicious).
        language: Language for email notification.

    Returns:
        Created Alert.
    """
    if risk_score >= 50:
        severity = AlertSeverity.CRITICAL.value
        title = f"High-Risk Activity: {username}"
        message = (
            f"User '{username}' detected performing '{activity_type}' with risk score {risk_score:.1f}. "
            f"Immediate investigation recommended."
        )
    else:
        severity = AlertSeverity.WARNING.value
        title = f"Suspicious Activity: {username}"
        message = (
            f"User '{username}' detected performing '{activity_type}' with risk score {risk_score:.1f}. "
            f"Monitoring recommended."
        )

    return create_security_alert(
        title=title,
        message=message,
        username=username,
        severity=severity,
        language=language,
    )
