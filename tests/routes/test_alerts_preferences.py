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
            patch("app.routes.alerts._extract_token", return_value="test-token"),
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
