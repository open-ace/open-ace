"""TDD red tests for the confirmed review findings on PR #1771.

One failing test per fix-targeted finding. These cover:

1. HIGH  - webhook delivery must not block the request path (async dispatch).
2. MED   - generic webhook payload must be HMAC-signed (X-OpenACE-Signature).
3. MED   - generic webhook payload must not leak ``alert.metadata`` (allowlist).
4. MED   - Feishu/Lark path-based bot token must be masked in storage + GET.
5. LOW   - Feishu/Lark host detection must be anchored (not ``in`` substring).
6. LOW   - delivery-failure log must not interpolate the raw URL-bearing exception.
"""

import json
import logging
import socket
import threading
import time
from unittest.mock import MagicMock, patch

from app.modules.governance.alert_notifier import (
    Alert,
    AlertNotifier,
    NotificationPreference,
    _redact_dingtalk_secret,
)


def _alert() -> Alert:
    return Alert(
        alert_id="alert-1",
        alert_type="quota",
        severity="warning",
        title="Quota Warning",
        message="Usage reached 80%",
        user_id=1,
        username="alice",
        # Sensitive-looking metadata that must never reach a third-party endpoint.
        metadata={
            "usage_percent": 82.0,
            "quota_type": "tokens",
            "secret_token": "should-not-leak",
            "internal_email": "root@example.invalid",
        },
    )


def _prefs(url: str = "https://webhook.example.com/hook") -> NotificationPreference:
    return NotificationPreference(
        user_id=1,
        email_enabled=False,
        push_enabled=True,
        webhook_url=url,
        alert_types=["quota", "system", "security"],
        min_severity="warning",
    )


def _dns_patch():
    def fake_getaddrinfo(host, *args, **kwargs):
        port = args[1] if len(args) > 1 else (kwargs.get("port") or 443)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    return patch(
        "app.modules.governance.alert_notifier.socket.getaddrinfo",
        side_effect=fake_getaddrinfo,
    )


def _capturing_session(captured: dict):
    """A requests.Session patch that records the outgoing POST."""

    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.status_code = 200

    mock_session = MagicMock()
    mock_session.post.return_value = mock_response

    def fake_session_ctor(*args, **kwargs):
        original_post = mock_session.post

        def capturing_post(url, *a, **kw):
            captured.setdefault("urls", []).append(url)
            captured.setdefault("payloads", []).append(kw.get("data"))
            captured.setdefault("headers", []).append(kw.get("headers", {}) or {})
            return original_post(url, *a, **kw)

        mock_session.post = capturing_post
        return mock_session

    return patch(
        "app.modules.governance.alert_notifier.requests.Session",
        side_effect=fake_session_ctor,
    )


# ---------------------------------------------------------------------------
# Finding 1 (HIGH): delivery must not block the request path
# ---------------------------------------------------------------------------


class TestWebhookAsyncDispatch:
    def test_create_alert_does_not_block_on_slow_webhook(self):
        """``create_alert`` must return before a slow webhook POST completes.

        Before the fix ``_send_webhook_notification`` ran synchronously inside
        ``create_alert``, so a receiver that hangs for the per-alert timeout
        adds that whole latency to the user-facing request that triggered the
        alert (e.g. the LLM proxy 429 path calling ``create_quota_alert``).
        After the fix delivery is dispatched to a background worker, so the
        slow POST happens off the request thread.
        """

        notifier = AlertNotifier()
        notifier._subscribers = []

        started = threading.Event()
        release = threading.Event()

        def slow_post(url, *a, **kw):
            started.set()
            # Block until the test releases us — simulating a hung receiver.
            release.wait(timeout=5)
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.status_code = 200
            return resp

        mock_session = MagicMock()
        mock_session.post.side_effect = slow_post

        with (
            patch(
                "app.modules.governance.alert_notifier.socket.getaddrinfo",
                side_effect=lambda host, *a, **k: [
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
                ],
            ),
            patch(
                "app.modules.governance.alert_notifier.get_config_value",
                return_value=False,
            ),
            patch(
                "app.modules.governance.alert_notifier.requests.Session",
                return_value=mock_session,
            ),
            patch.object(AlertNotifier, "get_notification_preferences", return_value=_prefs()),
            patch.object(AlertNotifier, "_save_alert"),
            patch.object(AlertNotifier, "_send_email_notification"),
        ):
            t0 = time.monotonic()
            notifier.create_alert(
                alert_type="quota",
                severity="warning",
                title="t",
                message="m",
                user_id=1,
            )
            elapsed = time.monotonic() - t0

        # ``create_alert`` must return promptly without waiting for the POST.
        assert elapsed < 1.0, f"create_alert blocked {elapsed:.2f}s on synchronous webhook delivery"
        assert started.is_set(), "webhook delivery never started"
        release.set()


# ---------------------------------------------------------------------------
# Finding 2 (MED): generic webhook payload must be HMAC-signed
# ---------------------------------------------------------------------------


class TestGenericWebhookSignature:
    def test_generic_webhook_payload_carries_hmac_signature(self):
        """The generic (non-Feishu/non-DingTalk) webhook branch must sign the
        body with an HMAC-SHA256 of a shared secret and surface it via an
        ``X-OpenACE-Signature`` header so receivers can verify authenticity."""

        captured: dict = {}
        secret = "shared-secret-12345"

        # config returns the secret for the generic signing key, False otherwise
        # (so the private-URL gate stays open for the example.com target).
        def config_value(section, key, default=None):
            if key == "webhook_secret":
                return secret
            return False

        with (
            patch(
                "app.modules.governance.alert_notifier.get_config_value",
                side_effect=config_value,
            ),
            _dns_patch(),
            _capturing_session(captured),
            patch.object(AlertNotifier, "get_notification_preferences", return_value=_prefs()),
        ):
            notifier = AlertNotifier()
            notifier._send_webhook_notification(_alert(), user_id=1)

        assert captured.get("headers"), "no webhook POST was captured"
        header = captured["headers"][0]
        assert (
            "X-OpenACE-Signature" in header
        ), f"generic webhook missing X-OpenACE-Signature header: {header!r}"


# ---------------------------------------------------------------------------
# Finding 3 (MED): generic webhook payload must not leak metadata
# ---------------------------------------------------------------------------


class TestGenericWebhookPayloadAllowlist:
    def test_generic_payload_only_includes_allowlisted_fields(self):
        """``alert.metadata`` is free-form and already carries tokens/emails in
        some call sites. The generic webhook payload must ship an explicit
        allowlist (alert_id, alert_type, severity, title, message, created_at)
        and never the raw ``to_dict()`` / ``metadata`` blob."""

        captured: dict = {}

        with (
            patch(
                "app.modules.governance.alert_notifier.get_config_value",
                return_value=False,
            ),
            _dns_patch(),
            _capturing_session(captured),
            patch.object(AlertNotifier, "get_notification_preferences", return_value=_prefs()),
        ):
            notifier = AlertNotifier()
            notifier._send_webhook_notification(_alert(), user_id=1)

        payloads = captured.get("payloads", [])
        assert payloads, "no webhook payload was captured"
        payload = json.loads(payloads[0])

        # The leaky fields from metadata must not appear anywhere in the payload.
        blob = repr(payload)
        assert (
            "should-not-leak" not in blob
        ), f"secret_token leaked into webhook payload: {payload!r}"
        assert (
            "root@example.invalid" not in blob
        ), f"internal_email leaked into webhook payload: {payload!r}"
        # The allowlisted alert fields must be present.
        alert = payload.get("alert", {})
        for field in ("alert_id", "alert_type", "severity", "title", "message"):
            assert field in alert, f"allowlisted field {field!r} missing from payload"
        # Raw metadata must not be shipped wholesale.
        assert (
            "metadata" not in alert
        ), f"raw metadata blob shipped to webhook: {alert.get('metadata')!r}"


# ---------------------------------------------------------------------------
# Finding 4 (MED): Feishu/Lark path token masked in storage + GET
# ---------------------------------------------------------------------------


class TestFeishuPathTokenRedaction:
    def test_feishu_webhook_url_path_token_is_masked(self):
        """Feishu/Lark bot webhooks carry the bot token in the URL *path*
        (``/open-apis/bot/v2/hook/<TOKEN>``). The redaction helper must mask
        that path tail so the token is not persisted or echoed back verbatim
        by GET /alerts/preferences."""

        url = "https://open.feishu.cn/open-apis/bot/v2/hook/ABCDEFG-token-in-path"
        redacted = _redact_dingtalk_secret(url)
        # The existing helper name is kept for the write/read chokepoint, but
        # it must now also mask Feishu/Lark path tokens.
        assert (
            "ABCDEFG-token-in-path" not in redacted
        ), f"Feishu path token not masked: {redacted!r}"
        # The host is preserved so delivery still works.
        assert "open.feishu.cn" in redacted


# ---------------------------------------------------------------------------
# Finding 5 (LOW): Feishu host detection must be anchored
# ---------------------------------------------------------------------------


class TestFeishuHostDetectionAnchored:
    def test_feishu_detection_rejects_lookalike_hosts(self):
        """``_is_feishu_webhook`` used ``snippet in host``, so ``feishu.cn``
        matched ``notfeishu.cn`` / ``feishu.cn.evil.com``. Detection must be
        exact-host-or-suffix anchored (reusing the existing
        ``_matches_webhook_host`` helper)."""

        notifier = AlertNotifier()
        assert notifier._is_feishu_webhook(
            "https://open.feishu.cn/open-apis/bot/v2/hook/x"
        ), "real Feishu host must be detected"
        assert not notifier._is_feishu_webhook(
            "https://notfeishu.cn/hook"
        ), "lookalike 'notfeishu.cn' must NOT be detected as Feishu"
        assert not notifier._is_feishu_webhook(
            "https://feishu.cn.evil.com/hook"
        ), "lookalike 'feishu.cn.evil.com' must NOT be detected as Feishu"


# ---------------------------------------------------------------------------
# Finding 6 (LOW): failure log must not interpolate the raw exception
# ---------------------------------------------------------------------------


class TestWebhookFailureLogRedacted:
    def test_failure_log_does_not_embed_url_bearing_exception(self):
        """The broad except in ``_send_webhook_notification`` interpolated the
        raw exception, which for ``requests`` errors embeds the full URL —
        and for Feishu/Lark that URL contains the bot token in the path. The
        log line must use ``type(e).__name__`` and a redacted host, never the
        raw exception / URL."""

        notifier = AlertNotifier()

        prefs = NotificationPreference(
            user_id=1,
            email_enabled=False,
            push_enabled=True,
            webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/SECRET-TOKEN-123",
            alert_types=["quota", "system", "security"],
            min_severity="warning",
        )

        def boom(*a, **kw):
            raise ConnectionError(
                "HTTPSConnectionPool(host='open.feishu.cn', "
                "url='/open-apis/bot/v2/hook/SECRET-TOKEN-123')"
            )

        mock_session = MagicMock()
        mock_session.post.side_effect = boom

        records: list[str] = []

        class _Handler(logging.Handler):
            def emit(self, record):
                records.append(self.format(record))

        handler = _Handler(level=logging.WARNING)
        logger = logging.getLogger("app.modules.governance.alert_notifier")
        logger.addHandler(handler)

        try:
            with (
                patch(
                    "app.modules.governance.alert_notifier.socket.getaddrinfo",
                    side_effect=lambda host, *a, **k: [
                        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
                    ],
                ),
                patch(
                    "app.modules.governance.alert_notifier.get_config_value",
                    return_value=False,
                ),
                patch(
                    "app.modules.governance.alert_notifier.requests.Session",
                    return_value=mock_session,
                ),
                patch.object(
                    AlertNotifier,
                    "get_notification_preferences",
                    return_value=prefs,
                ),
            ):
                notifier._send_webhook_notification(_alert(), user_id=1)
        finally:
            logger.removeHandler(handler)

        joined = "\n".join(records)
        assert "SECRET-TOKEN-123" not in joined, f"failure log leaked URL/token: {joined!r}"
