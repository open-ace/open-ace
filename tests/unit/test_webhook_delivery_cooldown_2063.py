"""Webhook delivery cooldown regression tests for Issue #2063."""

import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.modules.governance.alert_notifier import (
    Alert,
    AlertNotifier,
    DeliveryCooldownClaim,
    DeliveryResult,
    NotificationPreference,
)

pytestmark = [pytest.mark.issue(2063), pytest.mark.regression]


class _SyncThread:
    def __init__(self, **kwargs):
        self._target = kwargs.get("target")

    def start(self):
        self._target()


@pytest.fixture
def notifier():
    with (
        tempfile.TemporaryDirectory() as tmpdir,
        patch("app.repositories.database.is_postgresql", return_value=False),
        patch("app.modules.governance.alert_notifier.is_postgresql", return_value=False),
    ):
        db_path = os.path.join(tmpdir, "alerts.db")
        n = AlertNotifier(db_path=db_path)
        n._ensure_tables()
        n._subscribers = []
        yield n


def _rows(notifier):
    conn = notifier._get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM webhook_deliveries ORDER BY id")
    rows = [dict(zip([d[0] for d in cursor.description], row)) for row in cursor.fetchall()]
    conn.close()
    return rows


def _create_quota_alert(notifier, *, user_id=1, quota_type="platform", alert_id_suffix=""):
    return notifier.create_alert(
        alert_type="quota",
        severity="critical",
        title=f"Quota Exceeded {alert_id_suffix}",
        message="quota exceeded",
        user_id=user_id,
        username=f"user-{user_id}",
        metadata={"quota_type": quota_type},
    )


def test_recent_same_user_quota_webhook_dispatches_once(notifier):
    notifier.set_notification_preferences(
        NotificationPreference(
            user_id=1,
            push_enabled=True,
            webhook_url="https://alerts.example.com/webhook",
            alert_types=["quota"],
            min_severity="info",
        )
    )
    posts = []

    def record_post(alert, snapshot):
        posts.append((alert.alert_id, snapshot.webhook_url))
        return DeliveryResult(delivered=True)

    with (
        patch.object(notifier, "_post_webhook_snapshot", side_effect=record_post),
        patch("app.modules.governance.alert_notifier.threading.Thread", _SyncThread),
    ):
        _create_quota_alert(notifier, alert_id_suffix="first")
        _create_quota_alert(notifier, alert_id_suffix="second")

    assert len(posts) == 1
    rows = _rows(notifier)
    assert len(rows) == 1
    assert rows[0]["status"] == "delivered"
    assert rows[0]["cooldown_key"]
    assert rows[0]["receiver_identity_hash"]


def test_receiver_url_change_allows_new_delivery(notifier):
    posts = []

    def record_post(alert, snapshot):
        posts.append(snapshot.webhook_url)
        return DeliveryResult(delivered=True)

    with (
        patch.object(notifier, "_post_webhook_snapshot", side_effect=record_post),
        patch("app.modules.governance.alert_notifier.threading.Thread", _SyncThread),
    ):
        notifier.set_notification_preferences(
            NotificationPreference(
                user_id=1,
                push_enabled=True,
                webhook_url="https://alerts.example.com/webhook-a",
                alert_types=["quota"],
                min_severity="info",
            )
        )
        _create_quota_alert(notifier, alert_id_suffix="a")

        notifier.set_notification_preferences(
            NotificationPreference(
                user_id=1,
                push_enabled=True,
                webhook_url="https://alerts.example.com/webhook-b",
                alert_types=["quota"],
                min_severity="info",
            )
        )
        _create_quota_alert(notifier, alert_id_suffix="b")

    assert posts == [
        "https://alerts.example.com/webhook-a",
        "https://alerts.example.com/webhook-b",
    ]
    assert len(_rows(notifier)) == 2


def test_cooldown_does_not_cross_users_alert_types_or_quota_types(notifier):
    for user_id in (1, 2):
        notifier.set_notification_preferences(
            NotificationPreference(
                user_id=user_id,
                push_enabled=True,
                webhook_url="https://alerts.example.com/webhook",
                alert_types=["quota", "security"],
                min_severity="info",
            )
        )
    posts = []

    def record_post(alert, snapshot):
        posts.append((snapshot.user_id, alert.alert_type, alert.metadata.get("quota_type")))
        return DeliveryResult(delivered=True)

    with (
        patch.object(notifier, "_post_webhook_snapshot", side_effect=record_post),
        patch("app.modules.governance.alert_notifier.threading.Thread", _SyncThread),
    ):
        _create_quota_alert(notifier, user_id=1, quota_type="platform")
        _create_quota_alert(notifier, user_id=2, quota_type="platform")
        _create_quota_alert(notifier, user_id=1, quota_type="tokens")
        notifier.create_alert(
            alert_type="security",
            severity="critical",
            title="Security",
            message="security event",
            user_id=1,
            metadata={},
        )

    assert posts == [
        (1, "quota", "platform"),
        (2, "quota", "platform"),
        (1, "quota", "tokens"),
        (1, "security", None),
    ]
    assert len(_rows(notifier)) == 4


def test_direct_concurrent_claim_same_key_allows_one_claim(notifier):
    prefs = NotificationPreference(
        user_id=1,
        push_enabled=True,
        webhook_url="https://alerts.example.com/webhook",
        alert_types=["quota"],
        min_severity="info",
    )
    notifier.set_notification_preferences(prefs)
    alert = Alert(
        alert_id="concurrent-claim",
        alert_type="quota",
        severity="critical",
        title="Quota",
        message="quota exceeded",
        user_id=1,
        metadata={"quota_type": "platform"},
    )
    barrier = threading.Barrier(2)
    results = []

    def claim_once():
        local = AlertNotifier(db_path=notifier.db_path)
        snapshot = local._build_webhook_delivery_snapshot(local.get_notification_preferences(1))
        barrier.wait()
        results.append(local._delivery_try_claim_cooldown(alert, 1, snapshot).status)

    threads = [threading.Thread(target=claim_once), threading.Thread(target=claim_once)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == ["claimed", "suppressed"]
    assert len(_rows(notifier)) == 1


def test_reaper_claimed_pending_row_gets_active_lease_and_suppresses_new_claim(notifier):
    prefs = NotificationPreference(
        user_id=1,
        push_enabled=True,
        webhook_url="https://alerts.example.com/webhook",
        alert_types=["quota"],
        min_severity="info",
    )
    notifier.set_notification_preferences(prefs)
    alert = Alert(
        alert_id="reaper-lease",
        alert_type="quota",
        severity="critical",
        title="Quota",
        message="quota exceeded",
        user_id=1,
        metadata={"quota_type": "platform"},
    )
    snapshot = notifier._build_webhook_delivery_snapshot(prefs)
    first = notifier._delivery_try_claim_cooldown(alert, 1, snapshot)
    assert first.status == "claimed"

    notifier._delivery_set_outcome(
        first.delivery_id,
        DeliveryResult(retriable=True, error_type="timeout"),
        attempt=1,
        final=False,
        claim_token=first.claim_token,
    )
    conn = notifier._get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE webhook_deliveries SET next_retry_at=NULL WHERE id=?",
        (first.delivery_id,),
    )
    conn.commit()
    conn.close()
    claimed_rows = notifier._delivery_claim_due(1)
    assert len(claimed_rows) == 1

    row = _rows(notifier)[0]
    assert row["status"] == "in_flight"
    assert row["delivery_claim_token"]
    assert row["delivery_claim_expires_at"]

    second = notifier._delivery_try_claim_cooldown(alert, 1, snapshot)
    assert second.status == "suppressed"
    assert len(_rows(notifier)) == 1


def test_stale_reclaim_clears_old_token_and_old_owner_cannot_begin_attempt(notifier):
    prefs = NotificationPreference(
        user_id=1,
        push_enabled=True,
        webhook_url="https://alerts.example.com/webhook",
        alert_types=["quota"],
        min_severity="info",
    )
    notifier.set_notification_preferences(prefs)
    alert = Alert(
        alert_id="stale-token",
        alert_type="quota",
        severity="critical",
        title="Quota",
        message="quota exceeded",
        user_id=1,
        metadata={"quota_type": "platform"},
    )
    snapshot = notifier._build_webhook_delivery_snapshot(prefs)
    claim = notifier._delivery_try_claim_cooldown(alert, 1, snapshot)
    assert claim.status == "claimed"

    old = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)).isoformat()
    conn = notifier._get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE webhook_deliveries SET updated_at=?, delivery_claim_expires_at=? WHERE id=?",
        (old, old, claim.delivery_id),
    )
    conn.commit()
    conn.close()

    assert notifier._delivery_reclaim_stale() == 1
    row = _rows(notifier)[0]
    assert row["status"] == "pending"
    assert row["delivery_claim_token"] is None
    assert row["delivery_claim_expires_at"] is None
    assert notifier._delivery_begin_attempt(claim.delivery_id, claim.claim_token) is None


def test_dingtalk_secret_change_allows_new_delivery_same_url(notifier):
    url = "https://oapi.dingtalk.com/robot/send?access_token=abc123"
    posts = []

    def record_post(alert, snapshot):
        posts.append(snapshot.receiver_identity_hash)
        return DeliveryResult(delivered=True)

    with (
        patch.object(notifier, "_post_webhook_snapshot", side_effect=record_post),
        patch("app.modules.governance.alert_notifier.threading.Thread", _SyncThread),
    ):
        notifier.set_notification_preferences(
            NotificationPreference(
                user_id=1,
                push_enabled=True,
                webhook_url=f"{url}&openace_dingtalk_secret=secret-a",
                alert_types=["quota"],
                min_severity="info",
            )
        )
        _create_quota_alert(notifier, alert_id_suffix="a")

        notifier.set_notification_preferences(
            NotificationPreference(
                user_id=1,
                push_enabled=True,
                webhook_url=f"{url}&openace_dingtalk_secret=secret-b",
                alert_types=["quota"],
                min_severity="info",
            )
        )
        _create_quota_alert(notifier, alert_id_suffix="b")

    assert len(posts) == 2
    assert posts[0] != posts[1]
    assert len(_rows(notifier)) == 2


def test_generic_webhook_secret_change_allows_new_delivery_same_url(notifier):
    notifier.set_notification_preferences(
        NotificationPreference(
            user_id=1,
            push_enabled=True,
            webhook_url="https://alerts.example.com/webhook",
            alert_types=["quota"],
            min_severity="info",
        )
    )
    posts = []

    def record_post(alert, snapshot):
        posts.append(snapshot.receiver_identity_hash)
        return DeliveryResult(delivered=True)

    with (
        patch.object(notifier, "_get_generic_webhook_secret", side_effect=["secret-a", "secret-b"]),
        patch.object(notifier, "_post_webhook_snapshot", side_effect=record_post),
        patch("app.modules.governance.alert_notifier.threading.Thread", _SyncThread),
    ):
        _create_quota_alert(notifier, alert_id_suffix="a")
        _create_quota_alert(notifier, alert_id_suffix="b")

    assert len(posts) == 2
    assert posts[0] != posts[1]
    assert len(_rows(notifier)) == 2


def test_claim_unavailable_fails_open_with_one_worker_post_and_no_row(notifier):
    notifier.set_notification_preferences(
        NotificationPreference(
            user_id=1,
            push_enabled=True,
            webhook_url="https://alerts.example.com/webhook",
            alert_types=["quota"],
            min_severity="info",
        )
    )
    posts = []

    def record_post(alert, snapshot):
        posts.append(alert.alert_id)
        return DeliveryResult(delivered=True)

    with (
        patch.object(
            notifier,
            "_delivery_try_claim_cooldown",
            return_value=DeliveryCooldownClaim(status="unavailable"),
        ),
        patch.object(notifier, "_post_webhook_snapshot", side_effect=record_post),
        patch("app.modules.governance.alert_notifier.threading.Thread", _SyncThread),
    ):
        _create_quota_alert(notifier)

    assert len(posts) == 1
    assert _rows(notifier) == []


def test_disabled_webhook_does_not_create_cooldown_then_enable_posts(notifier):
    posts = []

    def record_post(alert, snapshot):
        posts.append(alert.alert_id)
        return DeliveryResult(delivered=True)

    with (
        patch.object(notifier, "_post_webhook_snapshot", side_effect=record_post),
        patch("app.modules.governance.alert_notifier.threading.Thread", _SyncThread),
    ):
        notifier.set_notification_preferences(
            NotificationPreference(
                user_id=1,
                push_enabled=False,
                webhook_url="https://alerts.example.com/webhook",
                alert_types=["quota"],
                min_severity="info",
            )
        )
        _create_quota_alert(notifier, alert_id_suffix="disabled")
        assert _rows(notifier) == []

        notifier.set_notification_preferences(
            NotificationPreference(
                user_id=1,
                push_enabled=True,
                webhook_url="https://alerts.example.com/webhook",
                alert_types=["quota"],
                min_severity="info",
            )
        )
        _create_quota_alert(notifier, alert_id_suffix="enabled")

    assert len(posts) == 1
    assert len(_rows(notifier)) == 1


def test_cleanup_and_delete_preserve_active_cooldown_rows(notifier):
    notifier.set_notification_preferences(
        NotificationPreference(
            user_id=1,
            push_enabled=True,
            webhook_url="https://alerts.example.com/webhook",
            alert_types=["quota"],
            min_severity="info",
        )
    )

    with (
        patch.object(
            notifier, "_post_webhook_snapshot", return_value=DeliveryResult(delivered=True)
        ),
        patch("app.modules.governance.alert_notifier.threading.Thread", _SyncThread),
    ):
        alert = _create_quota_alert(notifier)

    row = _rows(notifier)[0]
    old = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=60)).isoformat()
    future = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)).isoformat()
    conn = notifier._get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE webhook_deliveries SET updated_at=?, cooldown_expires_at=? WHERE id=?",
        (old, future, row["id"]),
    )
    conn.commit()
    conn.close()

    assert notifier.cleanup_old_deliveries(days=30) == 0
    assert notifier.delete_alert(alert.alert_id) is True

    rows = _rows(notifier)
    assert len(rows) == 1
    assert rows[0]["cooldown_key"]
    assert rows[0]["status"] == "delivered"


def test_ensure_tables_upgrades_legacy_webhook_deliveries_schema(tmp_path):
    db_path = tmp_path / "legacy.db"
    with (
        patch("app.repositories.database.is_postgresql", return_value=False),
        patch("app.modules.governance.alert_notifier.is_postgresql", return_value=False),
    ):
        notifier = AlertNotifier(db_path=str(db_path))
        conn = notifier._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE webhook_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                webhook_url_hash TEXT,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                next_retry_at TIMESTAMP,
                last_error_type TEXT,
                last_error_at TIMESTAMP,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
            """)
        conn.commit()
        conn.close()

        notifier._ensure_tables()
        conn = notifier._get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(webhook_deliveries)")
        columns = {row[1] for row in cursor.fetchall()}
        conn.close()

    assert {
        "receiver_identity_hash",
        "cooldown_key",
        "cooldown_expires_at",
        "delivery_claim_token",
        "delivery_claim_expires_at",
    }.issubset(columns)
