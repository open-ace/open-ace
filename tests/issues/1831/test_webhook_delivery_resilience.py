"""Tests for webhook delivery resilience (Issue #1831, finding #2).

Covers the durable delivery-state table + bounded worker retry + reaper:

* ``webhook_deliveries`` persists attempt state so a transient receiver
  failure (5xx / timeout / reset) is retried with backoff instead of being
  silently dropped.
* The plaintext webhook URL (which embeds Feishu/DingTalk bot tokens) is
  never persisted — only a SHA-256 hash.
* Cross-process atomic claim (SQLite path exercised here; PG uses
  ``FOR UPDATE SKIP LOCKED``).
* Dead-lettering on non-retriable failures and exhausted retries.
* ``delete_alert`` cascades to delivery rows so reaped alerts leave no orphans.
"""

import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.modules.governance.alert_notifier import (
    AlertNotifier,
    DeliveryResult,
    NotificationPreference,
    _hash_webhook_url,
)


@pytest.fixture
def notifier():
    """A fresh SQLite AlertNotifier with all tables (incl. webhook_deliveries)."""
    with (
        tempfile.TemporaryDirectory() as tmpdir,
        patch("app.repositories.database.is_postgresql", return_value=False),
        patch("app.modules.governance.alert_notifier.is_postgresql", return_value=False),
    ):
        db_path = os.path.join(tmpdir, "test_alerts.db")
        n = AlertNotifier(db_path=db_path)
        n._ensure_tables()
        n._subscribers = []
        yield n


def _insert_alert_direct(notifier, user_id, alert_id):
    """Insert a real alert row, bypassing create_alert (which dispatches a webhook)."""
    from app.modules.governance.alert_notifier import Alert

    alert = Alert(
        alert_id=alert_id,
        alert_type="quota",
        severity="warning",
        title="Quota Warning",
        message="80%",
        user_id=user_id,
        username="alice",
        tool_name=None,
        metadata={},
    )
    notifier._save_alert(alert)
    return alert


def _all_delivery_rows(notifier):
    conn = notifier._get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM webhook_deliveries")
    rows = [dict(zip([d[0] for d in cursor.description], r)) for r in cursor.fetchall()]
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# Hash / plaintext-never-persisted
# ---------------------------------------------------------------------------


class TestHashAndPlaintext:
    def test_hash_is_hex_and_none_for_empty(self):
        assert _hash_webhook_url(None) is None
        assert _hash_webhook_url("") is None
        h = _hash_webhook_url("https://open.feishu.cn/open-apis/bot/v2/hook/TOKEN")
        assert h != "TOKEN"
        assert all(c in "0123456789abcdef" for c in h)
        assert len(h) == 64  # sha256 hex

    def test_plaintext_url_never_persisted(self, notifier):
        """No webhook_deliveries column may contain the token-bearing URL."""
        secret_url = "https://open.feishu.cn/open-apis/bot/v2/hook/SUPERSECRETTOKEN"
        notifier.set_notification_preferences(
            NotificationPreference(
                user_id=1, push_enabled=True, webhook_url=secret_url, alert_types=["quota"]
            )
        )
        alert = _insert_alert_direct(notifier, 1, "alert-secret")

        delivery_id = notifier._delivery_enqueue(alert, 1)
        assert delivery_id is not None

        rows = _all_delivery_rows(notifier)
        assert len(rows) == 1
        row = rows[0]
        # The hash column is a hex digest, never the URL.
        assert row["webhook_url_hash"] == _hash_webhook_url(
            notifier.get_notification_preferences(1).webhook_url
        )
        # Grep every column value for the token — it must not appear anywhere.
        for value in row.values():
            assert "SUPERSECRETTOKEN" not in str(value)


# ---------------------------------------------------------------------------
# Outcome recording
# ---------------------------------------------------------------------------


class TestSetOutcome:
    def test_delivered_marks_terminal_delivered(self, notifier):
        alert = _insert_alert_direct(notifier, 1, "a-d1")
        did = notifier._delivery_enqueue(alert, 1)
        notifier._delivery_set_outcome(did, DeliveryResult(delivered=True), attempt=1, final=True)
        rows = _all_delivery_rows(notifier)
        assert rows[0]["status"] == "delivered"
        assert rows[0]["attempts"] == 1
        assert rows[0]["last_error_type"] is None
        assert rows[0]["next_retry_at"] is None

    def test_skipped_resolves_as_delivered(self, notifier):
        """A prefs-gated skip is terminal (resolved), not dead-lettered."""
        alert = _insert_alert_direct(notifier, 1, "a-skip")
        did = notifier._delivery_enqueue(alert, 1)
        notifier._delivery_set_outcome(did, DeliveryResult(skipped=True), attempt=1, final=True)
        assert _all_delivery_rows(notifier)[0]["status"] == "delivered"

    def test_non_retriable_final_dead_letters(self, notifier):
        alert = _insert_alert_direct(notifier, 1, "a-dead")
        did = notifier._delivery_enqueue(alert, 1)
        notifier._delivery_set_outcome(
            did,
            DeliveryResult(retriable=False, error_type="http_4xx"),
            attempt=1,
            final=True,
        )
        row = _all_delivery_rows(notifier)[0]
        assert row["status"] == "dead"
        assert row["last_error_type"] == "http_4xx"
        assert row["next_retry_at"] is None

    def test_retriable_non_final_schedules_backoff(self, notifier):
        alert = _insert_alert_direct(notifier, 1, "a-retry")
        did = notifier._delivery_enqueue(alert, 1)
        notifier._delivery_set_outcome(
            did,
            DeliveryResult(retriable=True, error_type="timeout"),
            attempt=2,
            final=False,
        )
        row = _all_delivery_rows(notifier)[0]
        assert row["status"] == "pending"
        assert row["attempts"] == 2
        assert row["last_error_type"] == "timeout"
        # next_retry_at must be in the future (backoff = base * attempt).
        from app.modules.governance.alert_notifier import _WEBHOOK_DELIVERY_BACKOFF_BASE_SEC

        assert row["next_retry_at"] is not None
        # Linear backoff ~ base * 2 seconds out; allow slop.
        expected = datetime.utcnow() + timedelta(seconds=_WEBHOOK_DELIVERY_BACKOFF_BASE_SEC * 2 - 5)
        assert datetime.fromisoformat(row["next_retry_at"]) >= expected


# ---------------------------------------------------------------------------
# Claim / reaper
# ---------------------------------------------------------------------------


class TestClaimDue:
    def _enqueue_pending(self, notifier, alert_id, *, next_retry_at=None, attempts=0):
        conn = notifier._get_connection()
        cursor = conn.cursor()
        now = datetime.utcnow().isoformat()
        cursor.execute(
            "INSERT INTO webhook_deliveries "
            "(alert_id, user_id, webhook_url_hash, status, attempts, max_attempts, "
            " next_retry_at, created_at, updated_at) "
            "VALUES (?, ?, NULL, 'pending', ?, 3, ?, ?, ?)",
            (alert_id, 1, attempts, next_retry_at, now, now),
        )
        conn.commit()
        conn.close()

    def test_claims_due_pending_row(self, notifier):
        self._enqueue_pending(notifier, "due-1", next_retry_at=None)
        rows = notifier._delivery_claim_due(10)
        assert len(rows) == 1
        assert rows[0]["status"] == "in_flight"

    def test_skips_future_retry_row(self, notifier):
        future = (datetime.utcnow() + timedelta(hours=1)).isoformat()
        self._enqueue_pending(notifier, "future-1", next_retry_at=future)
        rows = notifier._delivery_claim_due(10)
        assert rows == []
        # Row stays pending, untouched.
        assert _all_delivery_rows(notifier)[0]["status"] == "pending"

    def test_skips_exhausted_attempts(self, notifier):
        self._enqueue_pending(notifier, "max-1", attempts=3)
        rows = notifier._delivery_claim_due(10)
        assert rows == []


class TestReaper:
    def test_reaper_redelivers_and_marks_delivered(self, notifier):
        notifier.set_notification_preferences(
            NotificationPreference(
                user_id=1,
                push_enabled=True,
                webhook_url="https://x.example/hook",
                alert_types=["quota"],
            )
        )
        alert = _insert_alert_direct(notifier, 1, "rep-1")
        did = notifier._delivery_enqueue(alert, 1)
        # Force the row due immediately.
        conn = notifier._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE webhook_deliveries SET status='pending', next_retry_at=NULL WHERE id=?",
            (did,),
        )
        conn.commit()
        conn.close()

        with patch.object(
            notifier, "_deliver_to_prefs", return_value=DeliveryResult(delivered=True)
        ):
            attempted = notifier.process_due_deliveries()

        assert attempted == 1
        assert _all_delivery_rows(notifier)[0]["status"] == "delivered"

    def test_reaper_dead_letters_when_retries_exhausted(self, notifier):
        alert = _insert_alert_direct(notifier, 1, "rep-2")
        did = notifier._delivery_enqueue(alert, 1)
        conn = notifier._get_connection()
        cursor = conn.cursor()
        # Pretend attempts already at max-1 so one reaper attempt exhausts it.
        cursor.execute(
            "UPDATE webhook_deliveries SET status='pending', next_retry_at=NULL, attempts=2 WHERE id=?",
            (did,),
        )
        conn.commit()
        conn.close()

        with patch.object(
            notifier,
            "_deliver_to_prefs",
            return_value=DeliveryResult(retriable=True, error_type="timeout"),
        ):
            notifier.process_due_deliveries()

        # attempt 3 == max_attempts(3) → dead-lettered despite being retriable.
        assert _all_delivery_rows(notifier)[0]["status"] == "dead"

    def test_reaper_schedules_retry_when_under_max(self, notifier):
        notifier.set_notification_preferences(
            NotificationPreference(
                user_id=1,
                push_enabled=True,
                webhook_url="https://x.example/hook",
                alert_types=["quota"],
            )
        )
        alert = _insert_alert_direct(notifier, 1, "rep-3")
        did = notifier._delivery_enqueue(alert, 1)
        conn = notifier._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE webhook_deliveries SET status='pending', next_retry_at=NULL, attempts=1 WHERE id=?",
            (did,),
        )
        conn.commit()
        conn.close()

        with patch.object(
            notifier,
            "_deliver_to_prefs",
            return_value=DeliveryResult(retriable=True, error_type="connection"),
        ):
            notifier.process_due_deliveries()

        row = _all_delivery_rows(notifier)[0]
        assert row["status"] == "pending"  # requeued for another reaper pass
        assert row["next_retry_at"] is not None  # backoff scheduled
        assert row["attempts"] == 2

    def test_reaper_dead_letters_when_alert_gone(self, notifier):
        """A delivery whose source alert is gone must dead-letter, not loop.

        Normally ``delete_alert`` cascade-removes the delivery row first; this
        exercises the defensive path where a delivery outlives its alert (e.g. a
        row enqueued against an alert deleted by another path, or a race).
        """
        # Insert a delivery pointing at an alert_id that was never saved.
        conn = notifier._get_connection()
        cursor = conn.cursor()
        now = datetime.utcnow().isoformat()
        cursor.execute(
            "INSERT INTO webhook_deliveries "
            "(alert_id, user_id, status, attempts, max_attempts, "
            " next_retry_at, created_at, updated_at) "
            "VALUES ('never-saved', 1, 'pending', 0, 3, NULL, ?, ?)",
            (now, now),
        )
        conn.commit()
        conn.close()

        with patch.object(notifier, "_deliver_to_prefs") as mock_send:
            attempted = notifier.process_due_deliveries()
            mock_send.assert_not_called()  # no POST for a missing alert

        assert attempted == 1
        assert _all_delivery_rows(notifier)[0]["status"] == "dead"

    def test_reaper_disabled_returns_zero(self, notifier):
        with patch("app.modules.governance.alert_notifier._WEBHOOK_DELIVERY_REAPER_ENABLED", False):
            assert notifier.process_due_deliveries() == 0


# ---------------------------------------------------------------------------
# Cleanup + cascade
# ---------------------------------------------------------------------------


class TestCleanupAndCascade:
    def test_cleanup_old_deliveries_removes_terminal(self, notifier):
        alert = _insert_alert_direct(notifier, 1, "c-1")
        notifier._delivery_enqueue(alert, 1)
        # Mark one delivered with an old updated_at, one pending (recent).
        conn = notifier._get_connection()
        cursor = conn.cursor()
        old = (datetime.utcnow() - timedelta(days=40)).isoformat()
        cursor.execute(
            "UPDATE webhook_deliveries SET status='delivered', updated_at=? WHERE alert_id='c-1'",
            (old,),
        )
        cursor.execute(
            "INSERT INTO webhook_deliveries "
            "(alert_id, user_id, status, attempts, max_attempts, created_at, updated_at) "
            "VALUES ('c-2', 1, 'pending', 0, 3, ?, ?)",
            (old, old),
        )
        conn.commit()
        conn.close()

        removed = notifier.cleanup_old_deliveries(days=30)
        assert removed == 1  # only the delivered terminal row, not pending
        rows = _all_delivery_rows(notifier)
        assert len(rows) == 1
        assert rows[0]["alert_id"] == "c-2"

    def test_delete_alert_cascades_deliveries(self, notifier):
        alert = _insert_alert_direct(notifier, 1, "del-1")
        notifier._delivery_enqueue(alert, 1)
        assert len(_all_delivery_rows(notifier)) == 1

        assert notifier.delete_alert("del-1") is True
        assert _all_delivery_rows(notifier) == []


# ---------------------------------------------------------------------------
# Delivery identity (review P1-a)
# ---------------------------------------------------------------------------


class TestDeliveryIdentity:
    """A delivery must never be retried to a later-configured webhook.

    ``webhook_deliveries`` pins each row to the hash of the URL configured at
    enqueue time; if the user later repoints notifications at a different
    endpoint, retrying the historical alert there would break delivery identity
    and could leak alert content across teams/tenants. The row is dead-lettered
    (``config_changed``) instead of POSTing.
    """

    def _force_due(self, notifier, delivery_id):
        conn = notifier._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE webhook_deliveries SET status='pending', next_retry_at=NULL WHERE id=?",
            (delivery_id,),
        )
        conn.commit()
        conn.close()

    def test_pending_delivery_is_not_sent_to_changed_webhook_url(self, notifier):
        old_url = "https://open.feishu.cn/open-apis/bot/v2/hook/TOKEN-A"
        new_url = "https://open.feishu.cn/open-apis/bot/v2/hook/TOKEN-B"
        notifier.set_notification_preferences(
            NotificationPreference(
                user_id=1, push_enabled=True, webhook_url=old_url, alert_types=["quota"]
            )
        )
        alert = _insert_alert_direct(notifier, 1, "p1a-changed")
        did = notifier._delivery_enqueue(alert, 1)
        # Row pinned to the OLD receiver's hash at enqueue time.
        assert _all_delivery_rows(notifier)[0]["webhook_url_hash"] == _hash_webhook_url(old_url)

        # Repoint notifications at a different webhook (e.g. another team/tenant).
        notifier.set_notification_preferences(
            NotificationPreference(
                user_id=1, push_enabled=True, webhook_url=new_url, alert_types=["quota"]
            )
        )
        self._force_due(notifier, did)

        with patch.object(notifier, "_deliver_to_prefs") as mock_send:
            attempted = notifier.process_due_deliveries()
            # No POST — the historical alert is NOT forwarded to the new receiver.
            mock_send.assert_not_called()

        assert attempted == 1
        row = _all_delivery_rows(notifier)[0]
        assert row["status"] == "dead"
        assert row["last_error_type"] == "config_changed"

    def test_pending_delivery_proceeds_when_webhook_unchanged(self, notifier):
        """Guard must not misfire when the URL is unchanged — retry proceeds."""
        url = "https://open.feishu.cn/open-apis/bot/v2/hook/TOKEN-A"
        notifier.set_notification_preferences(
            NotificationPreference(
                user_id=1, push_enabled=True, webhook_url=url, alert_types=["quota"]
            )
        )
        alert = _insert_alert_direct(notifier, 1, "p1a-same")
        did = notifier._delivery_enqueue(alert, 1)
        self._force_due(notifier, did)

        with patch.object(
            notifier, "_deliver_to_prefs", return_value=DeliveryResult(delivered=True)
        ):
            notifier.process_due_deliveries()

        assert _all_delivery_rows(notifier)[0]["status"] == "delivered"

    def test_null_hash_row_is_dead_not_retried(self, notifier):
        """P2: a row with no recorded receiver hash can't be verified against the
        original receiver — dead-letter rather than guess by sending to whatever
        is configured now (which may be a different team/tenant)."""
        url = "https://open.feishu.cn/open-apis/bot/v2/hook/TOKEN-A"
        notifier.set_notification_preferences(
            NotificationPreference(
                user_id=1, push_enabled=True, webhook_url=url, alert_types=["quota"]
            )
        )
        _insert_alert_direct(notifier, 1, "p1a-legacy")
        # Insert a row directly with a NULL hash (as produced by old code paths
        # or an enqueue where no URL was configured).
        conn = notifier._get_connection()
        cursor = conn.cursor()
        now = datetime.utcnow().isoformat()
        cursor.execute(
            "INSERT INTO webhook_deliveries "
            "(alert_id, user_id, webhook_url_hash, status, attempts, max_attempts, "
            " next_retry_at, created_at, updated_at) "
            "VALUES ('p1a-legacy', 1, NULL, 'pending', 0, 3, NULL, ?, ?)",
            (now, now),
        )
        conn.commit()
        conn.close()

        with patch.object(notifier, "_deliver_to_prefs") as mock_deliver:
            notifier.process_due_deliveries()
            # No POST — the row can't be verified, so it is dead-lettered.
            mock_deliver.assert_not_called()

        row = _all_delivery_rows(notifier)[0]
        assert row["status"] == "dead"
        assert row["last_error_type"] == "unverifiable_receiver"

    def test_immediate_retry_does_not_send_to_changed_webhook(self, notifier):
        """P1-2: if the receiver changes during the worker's inter-attempt
        backoff, the second attempt dead-letters pinned to the ORIGINAL hash
        instead of POSTing to the new endpoint."""
        url_x = "https://open.feishu.cn/open-apis/bot/v2/hook/TOKEN-X"
        url_y = "https://open.feishu.cn/open-apis/bot/v2/hook/TOKEN-Y"
        notifier.set_notification_preferences(
            NotificationPreference(
                user_id=1, push_enabled=True, webhook_url=url_x, alert_types=["quota"]
            )
        )
        alert = _insert_alert_direct(notifier, 1, "p12-retry")

        sent_urls: list[str] = []

        def fake_send(_alert, prefs):
            sent_urls.append(prefs.webhook_url)
            return DeliveryResult(retriable=True, error_type="timeout")

        def switch_receiver(_secs):
            notifier.set_notification_preferences(
                NotificationPreference(
                    user_id=1, push_enabled=True, webhook_url=url_y, alert_types=["quota"]
                )
            )

        class _SyncThread:
            def __init__(self, **kwargs):
                self._target = kwargs.get("target")

            def start(self):
                self._target()

        with (
            patch.object(notifier, "_deliver_to_prefs", side_effect=fake_send),
            patch("app.modules.governance.alert_notifier.time.sleep", side_effect=switch_receiver),
            patch("app.modules.governance.alert_notifier.threading.Thread", _SyncThread),
        ):
            notifier._dispatch_webhook_async(alert, 1)

        # Only attempt 1 POSTed (to X); attempt 2 saw the receiver change and
        # dead-lettered without POSTing to Y.
        assert sent_urls == [url_x]
        row = _all_delivery_rows(notifier)[0]
        assert row["status"] == "dead"
        assert row["last_error_type"] == "config_changed"
        assert row["webhook_url_hash"] == _hash_webhook_url(url_x)

    def test_enqueue_uses_pinned_hash_not_current_preferences(self, notifier):
        """P1-2: when the worker pins the hash, _delivery_enqueue stores THAT
        hash (the receiver that actually failed), not one recomputed from the
        current prefs (which may have changed by enqueue time)."""
        url_x = "https://open.feishu.cn/open-apis/bot/v2/hook/TOKEN-X"
        url_y = "https://open.feishu.cn/open-apis/bot/v2/hook/TOKEN-Y"
        # Current prefs point at Y, but the caller pins the ORIGINAL receiver X.
        notifier.set_notification_preferences(
            NotificationPreference(
                user_id=1, push_enabled=True, webhook_url=url_y, alert_types=["quota"]
            )
        )
        alert = _insert_alert_direct(notifier, 1, "p12-enqueue")

        pinned = _hash_webhook_url(url_x)
        notifier._delivery_enqueue(alert, 1, webhook_url_hash=pinned)

        row = _all_delivery_rows(notifier)[0]
        assert row["webhook_url_hash"] == pinned
        assert row["webhook_url_hash"] != _hash_webhook_url(url_y)

    def test_worker_posts_using_same_preferences_snapshot_that_passed_hash_check(self, notifier):
        """P1: the worker reads prefs ONCE per attempt and POSTs with that same
        snapshot — there is no second prefs read between the identity check and
        the POST (closes the check-then-refetch TOCTOU)."""
        url_x = "https://open.feishu.cn/open-apis/bot/v2/hook/TOKEN-X"
        url_y = "https://open.feishu.cn/open-apis/bot/v2/hook/TOKEN-Y"
        notifier.set_notification_preferences(
            NotificationPreference(
                user_id=1, push_enabled=True, webhook_url=url_x, alert_types=["quota"]
            )
        )
        alert = _insert_alert_direct(notifier, 1, "p12-snap-w")

        delivered: list[str] = []

        def fake_deliver(_alert, prefs):
            delivered.append(prefs.webhook_url)
            return DeliveryResult(delivered=True)

        # If the worker refetched prefs after the identity check, a later read
        # would observe Y. The snapshot-based worker must POST with the X it
        # actually checked.
        real_get = notifier.get_notification_preferences
        seq = [url_x, url_y, url_y]

        def mutating_get(uid):
            prefs = real_get(uid)
            if seq:
                prefs.webhook_url = seq.pop(0)
            return prefs

        class _SyncThread:
            def __init__(self, **kwargs):
                self._target = kwargs.get("target")

            def start(self):
                self._target()

        with (
            patch.object(notifier, "get_notification_preferences", side_effect=mutating_get),
            patch.object(notifier, "_deliver_to_prefs", side_effect=fake_deliver),
            patch("app.modules.governance.alert_notifier.threading.Thread", _SyncThread),
        ):
            notifier._dispatch_webhook_async(alert, 1)

        # Posted with the snapshot that was identity-checked (X), not a refetched Y.
        assert delivered == [url_x]

    def test_reaper_posts_using_same_preferences_snapshot_that_passed_hash_check(self, notifier):
        """P1: the reaper reads prefs ONCE and POSTs with that same snapshot —
        no second read between the hash check and the POST."""
        url_x = "https://open.feishu.cn/open-apis/bot/v2/hook/TOKEN-X"
        url_y = "https://open.feishu.cn/open-apis/bot/v2/hook/TOKEN-Y"
        notifier.set_notification_preferences(
            NotificationPreference(
                user_id=1, push_enabled=True, webhook_url=url_x, alert_types=["quota"]
            )
        )
        alert = _insert_alert_direct(notifier, 1, "p12-snap-r")
        did = notifier._delivery_enqueue(alert, 1)
        conn = notifier._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE webhook_deliveries SET status='pending', next_retry_at=NULL WHERE id=?",
            (did,),
        )
        conn.commit()
        conn.close()

        delivered: list[str] = []

        def fake_deliver(_alert, prefs):
            delivered.append(prefs.webhook_url)
            return DeliveryResult(delivered=True)

        real_get = notifier.get_notification_preferences
        seq = [url_x, url_y, url_y]

        def mutating_get(uid):
            prefs = real_get(uid)
            if seq:
                prefs.webhook_url = seq.pop(0)
            return prefs

        with (
            patch.object(notifier, "get_notification_preferences", side_effect=mutating_get),
            patch.object(notifier, "_deliver_to_prefs", side_effect=fake_deliver),
        ):
            notifier.process_due_deliveries()

        assert delivered == [url_x]

    def test_initial_prefs_failure_is_retried_or_persisted_not_dropped(self, notifier):
        """P1-1: a transient prefs read failure is not silently dropped. The
        worker retries the read in-worker; if it still fails it persists a dead
        delivery row (auditable, no identity to verify) rather than vanishing."""
        url_x = "https://open.feishu.cn/open-apis/bot/v2/hook/TOKEN-X"
        notifier.set_notification_preferences(
            NotificationPreference(
                user_id=1, push_enabled=True, webhook_url=url_x, alert_types=["quota"]
            )
        )
        alert = _insert_alert_direct(notifier, 1, "p12-prefs-fail")

        class _SyncThread:
            def __init__(self, **kwargs):
                self._target = kwargs.get("target")

            def start(self):
                self._target()

        with (
            patch.object(
                notifier, "get_notification_preferences", side_effect=Exception("DB blip")
            ),
            patch("app.modules.governance.alert_notifier.time.sleep"),  # skip retry backoff
            patch("app.modules.governance.alert_notifier.threading.Thread", _SyncThread),
        ):
            notifier._dispatch_webhook_async(alert, 1)

        # Not dropped: a dead row is persisted for audit (no identity to verify).
        rows = _all_delivery_rows(notifier)
        assert len(rows) == 1
        assert rows[0]["status"] == "dead"
        assert rows[0]["last_error_type"] == "receiver_unresolved"

    def test_prefs_failure_after_retriable_post_persists_pending_delivery(self, notifier):
        """P1-1 path B: after a retriable POST failure, a subsequent prefs read
        failure persists a pending row pinned to the original receiver (handed
        to the reaper) instead of dropping the notification."""
        url_x = "https://open.feishu.cn/open-apis/bot/v2/hook/TOKEN-X"
        notifier.set_notification_preferences(
            NotificationPreference(
                user_id=1, push_enabled=True, webhook_url=url_x, alert_types=["quota"]
            )
        )
        alert = _insert_alert_direct(notifier, 1, "p12-post-then-prefs")

        # First prefs read succeeds (pins identity X) and the POST fails
        # retriable; the second prefs read (immediate retry) raises a
        # control-plane error.
        real_prefs = notifier.get_notification_preferences
        call = {"n": 0}

        def flaky_prefs(uid):
            call["n"] += 1
            if call["n"] >= 2:
                raise Exception("prefs DB blip")
            return real_prefs(uid)

        class _SyncThread:
            def __init__(self, **kwargs):
                self._target = kwargs.get("target")

            def start(self):
                self._target()

        with (
            patch.object(notifier, "get_notification_preferences", side_effect=flaky_prefs),
            patch.object(
                notifier,
                "_deliver_to_prefs",
                return_value=DeliveryResult(retriable=True, error_type="timeout"),
            ),
            patch("app.modules.governance.alert_notifier.time.sleep"),
            patch("app.modules.governance.alert_notifier.threading.Thread", _SyncThread),
        ):
            notifier._dispatch_webhook_async(alert, 1)

        row = _all_delivery_rows(notifier)[0]
        assert row["status"] == "pending"  # handed to reaper, not dropped
        assert row["webhook_url_hash"] == _hash_webhook_url(url_x)  # pinned to X
        assert row["last_error_type"] == "prefs_unreadable"

    def test_reaper_prefs_failure_at_attempt_budget_does_not_strand_pending_row(self, notifier):
        """P1-2: a control-plane prefs read failure does not consume a delivery
        attempt — the row stays claimable instead of stranding at attempts == max."""
        url_x = "https://open.feishu.cn/open-apis/bot/v2/hook/TOKEN-X"
        notifier.set_notification_preferences(
            NotificationPreference(
                user_id=1, push_enabled=True, webhook_url=url_x, alert_types=["quota"]
            )
        )
        _insert_alert_direct(notifier, 1, "p12-strand")
        # Row one short of the budget: a consuming failure would strand it.
        conn = notifier._get_connection()
        cursor = conn.cursor()
        now = datetime.utcnow().isoformat()
        cursor.execute(
            "INSERT INTO webhook_deliveries "
            "(alert_id, user_id, webhook_url_hash, status, attempts, max_attempts, "
            " next_retry_at, created_at, updated_at) "
            "VALUES ('p12-strand', 1, ?, 'pending', 2, 3, NULL, ?, ?)",
            (_hash_webhook_url(url_x), now, now),
        )
        conn.commit()
        conn.close()

        with patch.object(
            notifier, "get_notification_preferences", side_effect=Exception("prefs DB blip")
        ):
            notifier.process_due_deliveries()

        row = _all_delivery_rows(notifier)[0]
        assert row["status"] == "pending"  # not stranded/dead
        assert row["attempts"] == 2  # unchanged — control-plane failure didn't consume
        assert row["last_error_type"] == "prefs_unreadable"
        assert (
            row["next_retry_at"] is not None
        )  # backoff scheduled — claimable later (attempts 2 < max 3)

    def test_first_non_retriable_post_records_one_attempt(self, notifier):
        """P2: a first-shot non-retriable POST failure records attempts=1, not 0."""
        url_x = "https://open.feishu.cn/open-apis/bot/v2/hook/TOKEN-X"
        notifier.set_notification_preferences(
            NotificationPreference(
                user_id=1, push_enabled=True, webhook_url=url_x, alert_types=["quota"]
            )
        )
        alert = _insert_alert_direct(notifier, 1, "p2-attempts-1")

        class _SyncThread:
            def __init__(self, **kwargs):
                self._target = kwargs.get("target")

            def start(self):
                self._target()

        with (
            patch.object(
                notifier,
                "_deliver_to_prefs",
                return_value=DeliveryResult(retriable=False, error_type="http_4xx"),
            ),
            patch("app.modules.governance.alert_notifier.threading.Thread", _SyncThread),
        ):
            notifier._dispatch_webhook_async(alert, 1)

        row = _all_delivery_rows(notifier)[0]
        assert row["status"] == "dead"
        assert row["attempts"] == 1  # the single POST attempt was counted

    def test_non_retriable_after_retry_records_total_post_attempts(self, notifier):
        """P2: a retriable failure + immediate retry, then a non-retriable failure,
        records the total POST attempts (2)."""
        url_x = "https://open.feishu.cn/open-apis/bot/v2/hook/TOKEN-X"
        notifier.set_notification_preferences(
            NotificationPreference(
                user_id=1, push_enabled=True, webhook_url=url_x, alert_types=["quota"]
            )
        )
        alert = _insert_alert_direct(notifier, 1, "p2-attempts-2")

        results = [
            DeliveryResult(retriable=True, error_type="timeout"),  # attempt 1
            DeliveryResult(retriable=False, error_type="http_4xx"),  # attempt 2
        ]

        class _SyncThread:
            def __init__(self, **kwargs):
                self._target = kwargs.get("target")

            def start(self):
                self._target()

        with (
            patch.object(notifier, "_deliver_to_prefs", side_effect=results),
            patch("app.modules.governance.alert_notifier.time.sleep"),
            patch("app.modules.governance.alert_notifier.threading.Thread", _SyncThread),
        ):
            notifier._dispatch_webhook_async(alert, 1)

        row = _all_delivery_rows(notifier)[0]
        assert row["status"] == "dead"
        assert row["attempts"] == 2  # both POST attempts counted
