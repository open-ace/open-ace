"""Round-2 review follow-ups for PR #1807 (branch fix/high-webhook-async-signing).

Two confirmed severe items, plus one unit guard:

S1. Persisting webhook URLs through ``_redact_webhook_credentials`` destroyed the
    Feishu/Lark bot token, because that token lives in the URL *path* and has no
    global-config equivalent to rebuild it (unlike the DingTalk query secret).
    The token must be preserved in storage + on the delivery path, and masked
    only on the read/echo path (GET /alerts/preferences).

M1. ``_send_webhook_notification`` referenced ``prefs`` in its ``except`` block,
    but ``prefs`` was only assigned inside the ``try``. If
    ``get_notification_preferences`` itself raised, ``prefs`` was unbound and the
    handler crashed with ``UnboundLocalError`` instead of logging the real error.
"""

import json
import logging
import os
import socket
import tempfile
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from app.modules.governance.alert_notifier import (
    Alert,
    AlertNotifier,
    NotificationPreference,
    _redact_dingtalk_secret,
)


@contextmanager
def _sqlite_notifier():
    """An AlertNotifier backed by a private temp SQLite DB with tables created.

    Both is_postgresql() references (in repositories.database and in the
    notifier module) are forced off so the notifier uses the SQLite branch even
    when a real Postgres DATABASE_URL is present in the environment.
    """
    with (
        tempfile.TemporaryDirectory() as tmpdir,
        patch("app.repositories.database.is_postgresql", return_value=False),
        patch("app.modules.governance.alert_notifier.is_postgresql", return_value=False),
    ):
        db_path = os.path.join(tmpdir, "round2_alerts.db")
        notifier = AlertNotifier(db_path=db_path)
        notifier._ensure_tables()
        yield notifier


def _dns_patch():
    def fake_getaddrinfo(host, *args, **kwargs):
        port = args[1] if len(args) > 1 else (kwargs.get("port") or 443)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    return patch(
        "app.modules.governance.alert_notifier.socket.getaddrinfo",
        side_effect=fake_getaddrinfo,
    )


def _capturing_session(captured: dict):
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


def _alert() -> Alert:
    return Alert(
        alert_id="alert-feishu-1",
        alert_type="quota",
        severity="warning",
        title="Quota Warning",
        message="Usage reached 80%",
        user_id=1,
        username="alice",
        metadata={"usage_percent": 82.0, "quota_type": "tokens"},
    )


# ---------------------------------------------------------------------------
# S1: Feishu/Lark bot token must survive storage + delivery, be masked on echo
# ---------------------------------------------------------------------------


class TestFeishuWebhookRoundTrip:
    """End-to-end regression: store a Feishu webhook URL, then deliver an alert.
    The delivered POST path must still contain the original bot token (otherwise
    Feishu/Lark rejects every delivery), while the echoed preferences URL must
    mask the token so it is not leaked to the frontend.
    """

    FEISHU_TOKEN = "ABCDEFG-token-in-path"
    FEISHU_URL = f"https://open.feishu.cn/open-apis/bot/v2/hook/{FEISHU_TOKEN}"

    def test_feishu_token_preserved_in_storage_and_delivery_masked_on_echo(self):
        with _sqlite_notifier() as notifier:
            # 1. Persist a Feishu webhook URL.
            notifier.set_notification_preferences(
                NotificationPreference(
                    user_id=1,
                    email_enabled=False,
                    push_enabled=True,
                    webhook_url=self.FEISHU_URL,
                    alert_types=["quota", "system", "security"],
                    min_severity="warning",
                )
            )

            # 2. The notifier read path returns the URL with the Feishu token
            #    intact (delivery needs it); the frontend echo path re-masks
            #    both credentials via the route-layer helper. Mirror that here.
            read_back = notifier.get_notification_preferences(1)
            assert (
                self.FEISHU_TOKEN in read_back.webhook_url
            ), "delivery path must see the original Feishu token"
            echoed = _redact_dingtalk_secret(read_back.webhook_url)
            assert (
                self.FEISHU_TOKEN not in echoed
            ), f"token leaked on the frontend echo path: {echoed!r}"
            assert "open.feishu.cn" in echoed

            # 3. Delivery must use the ORIGINAL URL (token intact). Drive the
            #    delivery method directly with the in-DB prefs.
            captured: dict = {}
            with (
                _dns_patch(),
                _capturing_session(captured),
                patch(
                    "app.modules.governance.alert_notifier.get_config_value",
                    return_value=False,
                ),
            ):
                notifier._send_webhook_notification(_alert(), user_id=1)

            assert captured.get("urls"), "no POST was issued"
            pinned_url = captured["urls"][0]
            # The IP is pinned into the host slot; the original path (incl. token)
            # must still be present on the delivered URL.
            assert self.FEISHU_TOKEN in pinned_url, (
                f"delivery URL lost the Feishu token -> Feishu would reject it: " f"{pinned_url!r}"
            )
            assert "/open-apis/bot/v2/hook/" in pinned_url

    def test_dingtalk_query_secret_still_stripped_on_persist(self):
        """The DingTalk query secret must still be stripped on persist (the
        pre-existing behaviour that S1 must NOT regress). Unlike the Feishu path
        token, the DingTalk secret is rebuildable from global config."""
        with _sqlite_notifier() as notifier:
            notifier.set_notification_preferences(
                NotificationPreference(
                    user_id=2,
                    email_enabled=False,
                    push_enabled=True,
                    webhook_url=(
                        "https://oapi.dingtalk.com/robot/send"
                        "?access_token=abc123&openace_dingtalk_secret=s3cr3t"
                    ),
                    alert_types=["quota", "system", "security"],
                    min_severity="warning",
                )
            )

            echoed = notifier.get_notification_preferences(2)
            assert (
                "s3cr3t" not in echoed.webhook_url
            ), "DingTalk query secret must still be stripped on persist"
            assert "access_token=abc123" in echoed.webhook_url


# ---------------------------------------------------------------------------
# M1: prefs must be defined before the try so the except block cannot UnboundLocal
# ---------------------------------------------------------------------------


class TestWebhookFailurePrefsUnbound:
    def test_prefs_unbound_does_not_mask_real_exception(self):
        """If ``get_notification_preferences`` raises (e.g. DB error), ``prefs``
        was never assigned, so the ``except`` block's ``if prefs`` would itself
        raise ``UnboundLocalError`` and swallow the real error. The handler must
        still log the original exception type without a secondary crash."""

        notifier = AlertNotifier()
        notifier._subscribers = []

        original = notifier.get_notification_preferences

        def boom(user_id):
            raise RuntimeError("db connection lost")

        records: list[str] = []

        class _Handler(logging.Handler):
            def emit(self, record):
                records.append(self.format(record))

        handler = _Handler(level=logging.WARNING)
        logger = logging.getLogger("app.modules.governance.alert_notifier")
        logger.addHandler(handler)
        prev_level = logger.level
        logger.setLevel(logging.WARNING)
        try:
            with patch.object(notifier, "get_notification_preferences", side_effect=boom):
                # Must not raise UnboundLocalError; must log the real error type.
                notifier._send_webhook_notification(_alert(), user_id=1)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(prev_level)

        # The original error type is logged, not a NameError/UnboundLocalError.
        joined = "\n".join(records)
        assert "RuntimeError" in joined, f"real exception type not logged; got: {joined!r}"
        assert "UnboundLocalError" not in joined and "local variable" not in joined
        # Reference original to keep linters happy about unused symbol.
        assert original is not None
