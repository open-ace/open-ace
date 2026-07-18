"""TDD red tests for the webhook SSRF TOCTOU / DNS-rebinding group (PR #1771).

``validate_webhook_url`` resolves the hostname once via ``socket.getaddrinfo``
and rejects private/loopback/link-local IPs. But ``_send_webhook_notification``
then hands the *original* URL back to ``requests.post``, which performs its OWN
independent DNS resolution at connect time. Between the two resolutions the DNS
answer can change (DNS rebinding, short TTL, round-robin), so a URL that passed
validation can still connect to ``127.0.0.1`` / ``169.254.169.254`` / RFC1918
space.

The fix: pin the validated IP into the actual request so the connect-time
resolution cannot rebind to a private target. This test forces the two
resolutions to disagree (public then private) and asserts that delivery either
dials the validated public IP or is skipped — never the rebound private IP.
"""

import socket
from unittest.mock import MagicMock, patch

from app.modules.governance.alert_notifier import Alert, AlertNotifier, NotificationPreference


def _alert() -> Alert:
    return Alert(
        alert_id="alert-1",
        alert_type="quota",
        severity="warning",
        title="Quota Warning",
        message="Usage reached 80%",
        user_id=1,
        username="alice",
    )


class _RebindingGetaddrinfo:
    """First call (validate) returns a public IP, second call (connect) returns
    ``127.0.0.1`` — simulating a DNS-rebinding attack."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, host, *args, **kwargs):
        self.calls += 1
        ip = "93.184.216.34" if self.calls == 1 else "127.0.0.1"
        port = args[1] if len(args) > 1 else (kwargs.get("port") or 443)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]


class TestWebhookIpPinning:
    def test_validate_then_post_pins_resolved_ip_against_dns_rebinding(self):
        """The webhook delivery must NOT connect to the rebound loopback IP.

        Before the fix: validation passed (public IP) but the HTTP client was
        given the raw hostname and re-resolved to ``127.0.0.1`` at connect time.
        After the fix: the validated public IP is pinned into the request URL
        (with the original hostname preserved as the ``Host`` header), closing
        the rebinding window.

        The rebind resolver returns a public IP on the validation call and
        ``127.0.0.1`` on every subsequent call. The fix must make only ONE
        resolution, so the public IP is what gets pinned.
        """
        notifier = AlertNotifier()
        notifier._subscribers = []

        prefs = NotificationPreference(
            user_id=1,
            email_enabled=False,
            push_enabled=True,
            webhook_url="https://webhook.example.com/hook",
            alert_types=["quota", "system", "security"],
            min_severity="warning",
        )

        rebind = _RebindingGetaddrinfo()

        captured: dict[str, object] = {}

        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.status_code = 200

        mock_session = MagicMock()
        mock_session.post.return_value = mock_response

        def fake_session_ctor(*args, **kwargs):
            # Capture each POST so we can inspect the pinned URL + Host header.
            original_post = mock_session.post

            def capturing_post(url, *a, **kw):
                captured.setdefault("urls", []).append(url)
                captured.setdefault("headers", []).append(kw.get("headers", {}) or {})
                return original_post(url, *a, **kw)

            mock_session.post = capturing_post
            return mock_session

        with (
            patch(
                "app.modules.governance.alert_notifier.socket.getaddrinfo",
                side_effect=rebind,
            ),
            patch(
                "app.modules.governance.alert_notifier.get_config_value",
                return_value=False,
            ),
            patch(
                "app.modules.governance.alert_notifier.requests.Session",
                side_effect=fake_session_ctor,
            ),
            patch.object(AlertNotifier, "get_notification_preferences", return_value=prefs),
            patch.object(AlertNotifier, "_save_alert"),
        ):
            notifier._send_webhook_notification(_alert(), user_id=1)

        sent_urls = captured.get("urls", [])
        # Delivery MUST have happened — otherwise this test is vacuous.
        assert len(sent_urls) == 1, f"expected exactly one pinned POST, got {sent_urls!r}"

        sent_url = sent_urls[0]
        # The validated public IP is pinned into the outbound URL...
        assert (
            "93.184.216.34" in sent_url
        ), f"webhook delivery did not pin validated public IP. url={sent_url!r}"
        # ...and the rebound loopback IP must never be the dial target.
        assert (
            "127.0.0.1" not in sent_url
        ), f"TOCTOU: webhook delivery would reach loopback. url={sent_url!r}"

        # The original hostname is preserved as Host for SNI / virtual hosting.
        sent_headers = captured.get("headers", [{}])[0]
        host_header = sent_headers.get("Host") or sent_headers.get("host")
        assert (
            host_header == "webhook.example.com"
        ), f"Host header not preserved when pinning IP: {host_header!r}"
