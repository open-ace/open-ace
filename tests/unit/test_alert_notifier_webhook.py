import json
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from app.modules.governance.alert_notifier import AlertNotifier, NotificationPreference


def _wait_for_post(mock_session, timeout=2.0):
    """Block until the async webhook worker issues its POST (or timeout).

    Webhook delivery now runs on a background daemon thread, so the caller must
    wait for the POST to actually happen before asserting on it.
    """
    post = mock_session.return_value.post
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if post.called:
            return post
        time.sleep(0.005)
    return post


class TestAlertNotifierWebhook(unittest.TestCase):
    def test_validate_webhook_url_rejects_private_ip_by_default(self):
        notifier = AlertNotifier()

        with patch("app.modules.governance.alert_notifier.get_config_value", return_value=False):
            valid, error = notifier.validate_webhook_url("http://127.0.0.1:8000/hook")

        assert not valid
        assert error and "blocked by default" in error

    def test_validate_webhook_url_allows_private_ip_when_enabled(self):
        notifier = AlertNotifier()

        with patch("app.modules.governance.alert_notifier.get_config_value", return_value=True):
            valid, error = notifier.validate_webhook_url("http://127.0.0.1:8000/hook")

        assert valid
        assert error is None

    @patch("app.modules.governance.alert_notifier.AlertNotifier._resolve_webhook_target_ips")
    @patch("app.modules.governance.alert_notifier.requests.Session")
    @patch("app.modules.governance.alert_notifier.AlertNotifier.get_notification_preferences")
    @patch("app.modules.governance.alert_notifier.AlertNotifier._save_alert")
    def test_create_alert_triggers_generic_webhook(
        self, mock_save, mock_get_prefs, mock_session, mock_resolve
    ):
        notifier = AlertNotifier()
        notifier._subscribers = []

        # IP-pinned delivery path: resolve returns a verified public IP, and the
        # session's POST is captured instead of dialing the network.
        mock_resolve.return_value = (["93.184.216.34"], None)
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_session.return_value.post.return_value = mock_response
        mock_get_prefs.return_value = NotificationPreference(
            user_id=1,
            email_enabled=False,
            push_enabled=True,
            webhook_url="https://alerts.example.com/webhook",
            alert_types=["quota", "system", "security"],
            min_severity="warning",
        )

        notifier.create_alert(
            alert_type="quota",
            severity="warning",
            title="Quota Warning",
            message="Usage reached 80%",
            user_id=1,
            username="alice",
        )

        post = _wait_for_post(mock_session)
        post.assert_called_once()
        post_args = post.call_args
        post_kwargs = post_args.kwargs
        assert post_kwargs["allow_redirects"] is False
        # The verified IP is pinned into the outbound URL (first positional arg).
        pinned_url = post_args.args[0]
        assert "93.184.216.34" in pinned_url.split("/")[2]
        # The original hostname is preserved as Host for SNI / virtual hosting.
        assert post_kwargs["headers"]["Host"] == "alerts.example.com"
        # The body is signed bytes (data=) rather than json=.
        payload = json.loads(post_kwargs["data"])
        assert payload["event"] == "openace.alert"
        assert payload["alert"]["title"] == "Quota Warning"
        assert "Usage reached 80%" in payload["summary"]

    @patch("app.modules.governance.alert_notifier.AlertNotifier._resolve_webhook_target_ips")
    @patch("app.modules.governance.alert_notifier.requests.Session")
    @patch("app.modules.governance.alert_notifier.AlertNotifier.get_notification_preferences")
    @patch("app.modules.governance.alert_notifier.AlertNotifier._save_alert")
    def test_create_alert_uses_feishu_payload(
        self, mock_save, mock_get_prefs, mock_session, mock_resolve
    ):
        notifier = AlertNotifier()
        notifier._subscribers = []

        mock_resolve.return_value = (["93.184.216.34"], None)
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_session.return_value.post.return_value = mock_response
        mock_get_prefs.return_value = NotificationPreference(
            user_id=1,
            email_enabled=False,
            push_enabled=True,
            webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/abc123",
            alert_types=["system"],
            min_severity="info",
        )

        notifier.create_alert(
            alert_type="system",
            severity="critical",
            title="System Alert",
            message="Service unavailable",
            user_id=1,
            username="alice",
        )

        post = _wait_for_post(mock_session)
        payload = json.loads(post.call_args.kwargs["data"])
        assert payload["msg_type"] == "text"
        assert "System Alert" in payload["content"]["text"]
        assert "Service unavailable" in payload["content"]["text"]

    @patch("app.modules.governance.alert_notifier.AlertNotifier._resolve_webhook_target_ips")
    @patch("app.modules.governance.alert_notifier.requests.Session")
    @patch("app.modules.governance.alert_notifier.AlertNotifier.get_notification_preferences")
    @patch("app.modules.governance.alert_notifier.AlertNotifier._save_alert")
    def test_create_alert_uses_dingtalk_payload(
        self, mock_save, mock_get_prefs, mock_session, mock_resolve
    ):
        notifier = AlertNotifier()
        notifier._subscribers = []

        mock_resolve.return_value = (["93.184.216.34"], None)
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_session.return_value.post.return_value = mock_response
        mock_get_prefs.return_value = NotificationPreference(
            user_id=1,
            email_enabled=False,
            push_enabled=True,
            webhook_url="https://oapi.dingtalk.com/robot/send?access_token=abc123",
            alert_types=["system"],
            min_severity="info",
        )

        notifier.create_alert(
            alert_type="system",
            severity="critical",
            title="System Alert",
            message="Service unavailable",
            user_id=1,
            username="alice",
        )

        post = _wait_for_post(mock_session)
        payload = json.loads(post.call_args.kwargs["data"])
        assert payload["msgtype"] == "text"
        assert "System Alert" in payload["text"]["content"]
        assert "Service unavailable" in payload["text"]["content"]

    @patch("app.modules.governance.alert_notifier.AlertNotifier._resolve_webhook_target_ips")
    @patch("app.modules.governance.alert_notifier.requests.Session")
    @patch("app.modules.governance.alert_notifier.AlertNotifier.get_notification_preferences")
    @patch("app.modules.governance.alert_notifier.AlertNotifier._save_alert")
    def test_create_alert_does_not_treat_lookalike_host_as_dingtalk(
        self, mock_save, mock_get_prefs, mock_session, mock_resolve
    ):
        notifier = AlertNotifier()
        notifier._subscribers = []

        mock_resolve.return_value = (["93.184.216.34"], None)
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_session.return_value.post.return_value = mock_response
        mock_get_prefs.return_value = NotificationPreference(
            user_id=1,
            email_enabled=False,
            push_enabled=True,
            webhook_url="https://notdingtalk.com/robot/send?access_token=abc123",
            alert_types=["system"],
            min_severity="info",
        )

        notifier.create_alert(
            alert_type="system",
            severity="critical",
            title="System Alert",
            message="Service unavailable",
            user_id=1,
            username="alice",
        )

        post = _wait_for_post(mock_session)
        payload = json.loads(post.call_args.kwargs["data"])
        assert payload["event"] == "openace.alert"
        assert "System Alert" in payload["summary"]

    @patch("app.modules.governance.alert_notifier.time.time", return_value=1710000000.123)
    @patch(
        "app.modules.governance.alert_notifier.get_config_value",
        return_value="global-dingtalk-secret",
    )
    def test_prepare_dingtalk_webhook_url_adds_signature(self, mock_config, mock_time):
        notifier = AlertNotifier()

        url = notifier._prepare_webhook_url(
            "https://oapi.dingtalk.com/robot/send?access_token=abc123"
        )

        assert "access_token=abc123" in url
        assert "timestamp=1710000000123" in url
        assert "sign=" in url

    @patch("app.modules.governance.alert_notifier.AlertNotifier._resolve_webhook_target_ips")
    @patch("app.modules.governance.alert_notifier.requests.Session")
    @patch("app.modules.governance.alert_notifier.AlertNotifier.get_notification_preferences")
    @patch("app.modules.governance.alert_notifier.AlertNotifier._save_alert")
    def test_create_alert_skips_webhook_when_push_disabled(
        self, mock_save, mock_get_prefs, mock_session, mock_resolve
    ):
        notifier = AlertNotifier()
        notifier._subscribers = []

        mock_resolve.return_value = (["93.184.216.34"], None)
        mock_get_prefs.return_value = NotificationPreference(
            user_id=1,
            email_enabled=False,
            push_enabled=False,
            webhook_url="https://alerts.example.com/webhook",
            alert_types=["quota"],
            min_severity="warning",
        )

        notifier.create_alert(
            alert_type="quota",
            severity="critical",
            title="Quota Critical",
            message="Quota exhausted",
            user_id=1,
        )

        mock_session.return_value.post.assert_not_called()

    def test_set_then_get_preferences_strips_dingtalk_secret_from_webhook_url(self):
        """Persisted and returned webhook_url must never carry the DingTalk signing secret."""
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("app.repositories.database.is_postgresql", return_value=False),
            patch("app.modules.governance.alert_notifier.is_postgresql", return_value=False),
        ):
            db_path = os.path.join(tmpdir, "test_alerts.db")
            notifier = AlertNotifier(db_path=db_path)
            notifier._ensure_tables()

            secret_url = (
                "https://oapi.dingtalk.com/robot/send"
                "?access_token=abc&openace_dingtalk_secret=TOPSECRET"
            )
            prefs = NotificationPreference(
                user_id=42,
                webhook_url=secret_url,
                alert_types=["quota"],
            )
            notifier.set_notification_preferences(prefs)

            stored = notifier.get_notification_preferences(42)

            assert "access_token=abc" in stored.webhook_url
            assert "TOPSECRET" not in stored.webhook_url
            assert "openace_dingtalk_secret" not in stored.webhook_url
            assert "dingtalk_secret" not in stored.webhook_url

    def test_prepare_webhook_url_still_signs_without_in_url_secret(self):
        """Outbound signing must keep working when the secret lives only in global config."""
        notifier = AlertNotifier()
        with (
            patch("app.modules.governance.alert_notifier.time.time", return_value=1710000000.123),
            patch(
                "app.modules.governance.alert_notifier.get_config_value",
                return_value="global-dingtalk-secret",
            ),
        ):
            url = notifier._prepare_webhook_url(
                "https://oapi.dingtalk.com/robot/send?access_token=abc"
            )

        assert "access_token=abc" in url
        assert "timestamp=1710000000123" in url
        assert "sign=" in url
        # The cleaned URL passed in must not gain a secret query key back.
        assert "openace_dingtalk_secret" not in url
        assert "dingtalk_secret" not in url


if __name__ == "__main__":
    unittest.main()
