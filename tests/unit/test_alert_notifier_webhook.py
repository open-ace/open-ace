import unittest
from unittest.mock import MagicMock, patch

from app.modules.governance.alert_notifier import AlertNotifier, NotificationPreference


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

    @patch("app.modules.governance.alert_notifier.AlertNotifier.validate_webhook_url")
    @patch("app.modules.governance.alert_notifier.requests.post")
    @patch("app.modules.governance.alert_notifier.AlertNotifier.get_notification_preferences")
    @patch("app.modules.governance.alert_notifier.AlertNotifier._save_alert")
    def test_create_alert_triggers_generic_webhook(
        self, mock_save, mock_get_prefs, mock_post, mock_validate
    ):
        notifier = AlertNotifier()
        notifier._subscribers = []

        mock_validate.return_value = (True, None)
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response
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

        mock_post.assert_called_once()
        assert mock_post.call_args.kwargs["allow_redirects"] is False
        payload = mock_post.call_args.kwargs["json"]
        assert payload["event"] == "openace.alert"
        assert payload["alert"]["title"] == "Quota Warning"
        assert "Usage reached 80%" in payload["summary"]

    @patch("app.modules.governance.alert_notifier.AlertNotifier.validate_webhook_url")
    @patch("app.modules.governance.alert_notifier.requests.post")
    @patch("app.modules.governance.alert_notifier.AlertNotifier.get_notification_preferences")
    @patch("app.modules.governance.alert_notifier.AlertNotifier._save_alert")
    def test_create_alert_uses_feishu_payload(
        self, mock_save, mock_get_prefs, mock_post, mock_validate
    ):
        notifier = AlertNotifier()
        notifier._subscribers = []

        mock_validate.return_value = (True, None)
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response
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

        payload = mock_post.call_args.kwargs["json"]
        assert payload["msg_type"] == "text"
        assert "System Alert" in payload["content"]["text"]
        assert "Service unavailable" in payload["content"]["text"]

    @patch("app.modules.governance.alert_notifier.AlertNotifier.validate_webhook_url")
    @patch("app.modules.governance.alert_notifier.requests.post")
    @patch("app.modules.governance.alert_notifier.AlertNotifier.get_notification_preferences")
    @patch("app.modules.governance.alert_notifier.AlertNotifier._save_alert")
    def test_create_alert_skips_webhook_when_push_disabled(
        self, mock_save, mock_get_prefs, mock_post, mock_validate
    ):
        notifier = AlertNotifier()
        notifier._subscribers = []

        mock_validate.return_value = (True, None)
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

        mock_post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
