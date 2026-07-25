#!/usr/bin/env python3
"""Issue #1829 — F6 [HIGH]: per-user DingTalk signing-secret isolation.

Each tenant now signs webhooks with its own secret instead of every user
sharing one global ``alerts.dingtalk_webhook_secret``. Coverage:

* write path: secret lifted from the URL, Fernet-encrypted, persisted in the
  per-user column; URL stored redacted.
* read path: ciphertext carried through (lazy decrypt), plaintext never echoed.
* three-tier signing priority: per-user > global config > URL query.
* multi-tenant isolation: two users produce different signatures.
* preservation: an unrelated update does not wipe the stored secret.
* legacy-DB back-fill: ``_ensure_tables`` adds the column to pre-F6 schemas.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import sqlite3
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qsl, urlparse

import pytest

from app.modules.governance.alert_notifier import (
    AlertNotifier,
    NotificationPreference,
    _extract_dingtalk_secret_from_url,
)
from app.utils.smtp_crypto import get_password_manager

DT_URL = "https://oapi.dingtalk.com/robot/send?access_token=abc123"
_FIXED_NOW = 1_750_000_000.0  # deterministic timestamp for signature asserts


@pytest.fixture(autouse=True)
def _force_sqlite(monkeypatch):
    """Force AlertNotifier onto an isolated SQLite file via db_path.

    AlertNotifier._get_connection follows the global is_postgresql() (which reads
    the repo config file and may point at PostgreSQL), ignoring ``db_path`` on the
    postgres branch. For unit tests we pin SQLite so each test owns its DB and the
    F6 CREATE TABLE / ALTER back-fill path is exercised. PostgreSQL compatibility
    is covered by the migration + the CI postgres-test job.
    """
    monkeypatch.setattr("app.modules.governance.alert_notifier.is_postgresql", lambda: False)
    # adapt_sql lives in app.repositories.database and consults THAT module's
    # is_postgresql, so patch both references or the placeholder style (? vs %s)
    # drifts out of sync with the connection type.
    monkeypatch.setattr("app.repositories.database.is_postgresql", lambda: False)


def _notifier(tmp_path: Path) -> AlertNotifier:
    n = AlertNotifier(db_path=str(tmp_path / "alerts.db"))
    # AlertNotifier does not auto-create tables in __init__ (schema_init /
    # alembic do that in production). Call it here to stand up the schema,
    # which also exercises the F6 CREATE TABLE column + ALTER back-fill path.
    n._ensure_tables()
    return n


def _qs(url: str) -> dict:
    return dict(parse_qsl(urlparse(url).query))


def _compute_sign(secret: str, ts_ms: int) -> str:
    """Mirror AlertNotifier._prepare_webhook_url's DingTalk signing."""
    string_to_sign = f"{ts_ms}\n{secret}".encode()
    digest = hmac.new(secret.encode("utf-8"), string_to_sign, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _expected_ts_ms() -> int:
    return int(_FIXED_NOW * 1000)


def _config(value: str):
    """Patch the global config getter used by _prepare_webhook_url."""
    return patch("app.modules.governance.alert_notifier.get_config_value", return_value=value)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
class TestF6Helpers:
    def test_extract_secret_from_url(self):
        assert _extract_dingtalk_secret_from_url(DT_URL + "&openace_dingtalk_secret=abc") == "abc"
        assert _extract_dingtalk_secret_from_url(DT_URL + "&dingtalk_secret=xyz") == "xyz"

    def test_extract_secret_missing_returns_none(self):
        assert _extract_dingtalk_secret_from_url(DT_URL) is None
        assert _extract_dingtalk_secret_from_url(None) is None
        assert _extract_dingtalk_secret_from_url("") is None


# --------------------------------------------------------------------------- #
# write path
# --------------------------------------------------------------------------- #
class TestF6WritePath:
    def test_secret_extracted_encrypted_and_persisted(self, tmp_path):
        n = _notifier(tmp_path)
        n.set_notification_preferences(
            NotificationPreference(
                user_id=1, push_enabled=True, webhook_url=DT_URL + "&openace_dingtalk_secret=sec-a"
            )
        )
        prefs = n.get_notification_preferences(1)
        # Ciphertext is stored, not the plaintext.
        assert prefs.dingtalk_webhook_secret
        assert prefs.dingtalk_webhook_secret != "sec-a"
        assert "sec-a" not in prefs.dingtalk_webhook_secret
        # And it round-trips through decryption.
        assert get_password_manager().decrypt(prefs.dingtalk_webhook_secret) == "sec-a"

    def test_url_stored_without_secret(self, tmp_path):
        n = _notifier(tmp_path)
        n.set_notification_preferences(
            NotificationPreference(
                user_id=1,
                push_enabled=True,
                webhook_url=DT_URL + "&openace_dingtalk_secret=topsecret",
            )
        )
        prefs = n.get_notification_preferences(1)
        assert "topsecret" not in (prefs.webhook_url or "")
        assert "openace_dingtalk_secret" not in (prefs.webhook_url or "")
        assert "access_token=abc123" in prefs.webhook_url  # non-secret query kept


# --------------------------------------------------------------------------- #
# three-tier signing priority
# --------------------------------------------------------------------------- #
class TestF6ThreeTierPriority:
    def test_per_user_secret_wins_over_global_config(self, tmp_path):
        n = _notifier(tmp_path)
        user_ct = get_password_manager().encrypt("user-sec")
        with (
            _config("global-sec"),
            patch("app.modules.governance.alert_notifier.time.time", return_value=_FIXED_NOW),
        ):
            out = n._prepare_webhook_url(DT_URL, user_secret_encrypted=user_ct)
        sign = _qs(out)["sign"]
        assert sign == _compute_sign("user-sec", _expected_ts_ms())
        assert sign != _compute_sign("global-sec", _expected_ts_ms())

    def test_global_config_used_when_no_user_secret(self, tmp_path):
        n = _notifier(tmp_path)
        with (
            _config("global-sec"),
            patch("app.modules.governance.alert_notifier.time.time", return_value=_FIXED_NOW),
        ):
            out = n._prepare_webhook_url(DT_URL, user_secret_encrypted=None)
        assert _qs(out)["sign"] == _compute_sign("global-sec", _expected_ts_ms())

    def test_url_query_fallback_when_no_user_or_global(self, tmp_path):
        n = _notifier(tmp_path)
        url = DT_URL + "&openace_dingtalk_secret=url-sec"
        with (
            _config(""),
            patch("app.modules.governance.alert_notifier.time.time", return_value=_FIXED_NOW),
        ):
            out = n._prepare_webhook_url(url, user_secret_encrypted=None)
        q = _qs(out)
        assert q["sign"] == _compute_sign("url-sec", _expected_ts_ms())
        assert "openace_dingtalk_secret" not in q  # stripped after use

    def test_no_secret_means_no_sign(self, tmp_path):
        n = _notifier(tmp_path)
        with _config(""):
            out = n._prepare_webhook_url(DT_URL, user_secret_encrypted=None)
        assert "sign" not in _qs(out)


# --------------------------------------------------------------------------- #
# multi-tenant isolation
# --------------------------------------------------------------------------- #
class TestF6MultiTenantIsolation:
    def test_each_user_signs_with_own_secret(self, tmp_path):
        n = _notifier(tmp_path)
        n.set_notification_preferences(
            NotificationPreference(
                user_id=1, push_enabled=True, webhook_url=DT_URL + "&openace_dingtalk_secret=sec-a"
            )
        )
        n.set_notification_preferences(
            NotificationPreference(
                user_id=2, push_enabled=True, webhook_url=DT_URL + "&openace_dingtalk_secret=sec-b"
            )
        )
        prefs_a = n.get_notification_preferences(1)
        prefs_b = n.get_notification_preferences(2)
        with (
            _config(""),
            patch("app.modules.governance.alert_notifier.time.time", return_value=_FIXED_NOW),
        ):
            out_a = n._prepare_webhook_url(DT_URL, prefs_a.dingtalk_webhook_secret)
            out_b = n._prepare_webhook_url(DT_URL, prefs_b.dingtalk_webhook_secret)
        sign_a = _qs(out_a)["sign"]
        sign_b = _qs(out_b)["sign"]
        # Different tenants → different signatures, each matching its own secret.
        assert sign_a != sign_b
        assert sign_a == _compute_sign("sec-a", _expected_ts_ms())
        assert sign_b == _compute_sign("sec-b", _expected_ts_ms())
        # And neither matches a signature computed under the other's secret.
        assert sign_a != _compute_sign("sec-b", _expected_ts_ms())


# --------------------------------------------------------------------------- #
# secret preservation across unrelated updates
# --------------------------------------------------------------------------- #
class TestF6SecretPreservation:
    def test_unrelated_update_preserves_existing_secret(self, tmp_path):
        n = _notifier(tmp_path)
        n.set_notification_preferences(
            NotificationPreference(
                user_id=1,
                push_enabled=True,
                webhook_url=DT_URL + "&openace_dingtalk_secret=keep-me",
            )
        )
        ct_before = n.get_notification_preferences(1).dingtalk_webhook_secret
        # Later update toggles only email_enabled; URL carries no secret.
        n.set_notification_preferences(
            NotificationPreference(
                user_id=1, push_enabled=True, webhook_url=DT_URL, email_enabled=False
            )
        )
        ct_after = n.get_notification_preferences(1).dingtalk_webhook_secret
        assert ct_after == ct_before
        assert get_password_manager().decrypt(ct_after) == "keep-me"


# --------------------------------------------------------------------------- #
# legacy-DB column back-fill
# --------------------------------------------------------------------------- #
class TestF6ColumnBackfill:
    def test_ensure_tables_adds_column_to_legacy_db(self, tmp_path):
        db_path = tmp_path / "legacy.db"
        conn = sqlite3.connect(db_path)
        # Pre-F6 schema: no dingtalk_webhook_secret column.
        conn.executescript(
            """
            CREATE TABLE notification_preferences (
                user_id INTEGER PRIMARY KEY,
                email_enabled INTEGER DEFAULT 1,
                push_enabled INTEGER DEFAULT 1,
                webhook_url TEXT,
                alert_types TEXT,
                min_severity TEXT DEFAULT 'warning',
                notification_email TEXT,
                email_verified INTEGER DEFAULT 0
            );
            """
        )
        conn.commit()
        conn.close()

        # AlertNotifier does not auto-create tables in __init__; invoke
        # _ensure_tables explicitly (as schema_init does in production) to
        # trigger the back-fill on the pre-F6 schema.
        AlertNotifier(db_path=str(db_path))._ensure_tables()

        conn = sqlite3.connect(db_path)
        cols = [
            r[1] for r in conn.execute("PRAGMA table_info(notification_preferences)").fetchall()
        ]
        conn.close()
        assert "dingtalk_webhook_secret" in cols

    def test_backfill_is_idempotent(self, tmp_path):
        """Re-instantiating on an already-migrated DB must not error."""
        n = _notifier(tmp_path)
        n.set_notification_preferences(
            NotificationPreference(
                user_id=1, push_enabled=True, webhook_url=DT_URL + "&openace_dingtalk_secret=s"
            )
        )
        # A second notifier over the same DB re-runs _ensure_tables (idempotent:
        # the column already exists, so the back-fill guard skips the ALTER).
        n2 = AlertNotifier(db_path=str(tmp_path / "alerts.db"))
        n2._ensure_tables()
        prefs = n2.get_notification_preferences(1)
        assert get_password_manager().decrypt(prefs.dingtalk_webhook_secret) == "s"
