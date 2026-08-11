"""Route tests for GET/PUT /alerts/preferences secret redaction.

Ensures the DingTalk signing secret embedded in a webhook URL is never
persisted to, nor echoed back from, the preferences API.
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest


@pytest.fixture
def client():
    """Flask test client with alerts routes and a stubbed auth user.

    Backed by an AlertNotifier over a throwaway SQLite database, with
    is_postgresql pinned to False for the whole test so all persistence
    code paths use SQLite.
    """
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test_alerts.db")

    with (
        patch("app.repositories.database.is_postgresql", return_value=False),
        patch("app.modules.governance.alert_notifier.is_postgresql", return_value=False),
    ):
        from app.modules.governance.alert_notifier import AlertNotifier

        notifier = AlertNotifier(db_path=db_path)
        notifier._ensure_tables()

        from flask import Flask

        from app.routes.alerts import alerts_bp

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret-key"
        app.register_blueprint(alerts_bp)

        with (
            app.test_client() as test_client,
            patch("app.routes.alerts.get_alert_notifier", return_value=notifier),
            patch("app.routes.alerts._extract_session_token", return_value="test-token"),
            patch(
                "app.routes.alerts._load_user_from_token",
                return_value={"id": 7, "role": "user", "username": "tester"},
            ),
        ):
            yield test_client


def test_put_then_get_preferences_redacts_dingtalk_secret(client):
    """GET /alerts/preferences must not expose the secret stored via PUT."""
    secret_url = (
        "https://oapi.dingtalk.com/robot/send" "?access_token=abc&openace_dingtalk_secret=TOPSECRET"
    )

    resp = client.put(
        "/alerts/preferences",
        json={"webhook_url": secret_url},
    )
    assert resp.status_code == 200
    assert resp.get_json()["success"] is True

    resp = client.get("/alerts/preferences")
    assert resp.status_code == 200
    data = resp.get_json()["data"]

    assert "access_token=abc" in data["webhook_url"]
    assert "TOPSECRET" not in data["webhook_url"]
    assert "openace_dingtalk_secret" not in data["webhook_url"]
    assert "dingtalk_secret" not in data["webhook_url"]


def test_put_then_get_preferences_masks_feishu_path_token(client):
    """GET /alerts/preferences must mask the Feishu/Lark bot token carried in
    the URL path, while the persisted value (and thus delivery) keeps it intact.

    Regression for PR #1807 round-2 S1: the Feishu token lives only in the path
    and has no global-config equivalent, so it must NOT be destroyed at persist
    time (otherwise every Feishu delivery would POST to ``/.../<redacted>``).
    It is masked only on the read/echo path.
    """
    feishu_token = "FEISHU-PATH-TOKEN-123"
    feishu_url = f"https://open.feishu.cn/open-apis/bot/v2/hook/{feishu_token}"

    resp = client.put("/alerts/preferences", json={"webhook_url": feishu_url})
    assert resp.status_code == 200

    resp = client.get("/alerts/preferences")
    assert resp.status_code == 200
    data = resp.get_json()["data"]

    # The echo path masks the path token...
    assert (
        feishu_token not in data["webhook_url"]
    ), f"Feishu token leaked via GET echo: {data['webhook_url']!r}"
    assert "open.feishu.cn" in data["webhook_url"]
    assert "/open-apis/bot/v2/hook/<redacted>" in data["webhook_url"]

    # ...but the persisted value still carries the token (delivery needs it).
    from app.routes.alerts import get_alert_notifier

    notifier = get_alert_notifier()
    persisted = notifier.get_notification_preferences(7)
    assert (
        feishu_token in persisted.webhook_url
    ), "Feishu token must survive persistence so delivery can use it"
