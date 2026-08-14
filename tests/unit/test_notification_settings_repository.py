import json
import sqlite3

import pytest

from app.repositories import notification_settings_repository as module
from app.utils.smtp_crypto import get_password_manager


@pytest.fixture
def repository(tmp_path, monkeypatch):
    database_path = tmp_path / "settings.db"

    def connection():
        conn = sqlite3.connect(database_path)
        conn.row_factory = sqlite3.Row
        return conn

    conn = connection()
    conn.executescript("""
        CREATE TABLE feishu_settings (
            id INTEGER PRIMARY KEY, app_id TEXT NOT NULL, app_secret_enc TEXT NOT NULL,
            sync_enabled INTEGER NOT NULL DEFAULT 0, target_tenant_id INTEGER,
            interval_minutes INTEGER NOT NULL DEFAULT 60,
            max_runtime_seconds INTEGER NOT NULL DEFAULT 1800,
            auto_recovery INTEGER NOT NULL DEFAULT 0, created_by INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT
        );
        CREATE TABLE dingtalk_settings (
            id INTEGER PRIMARY KEY, app_key TEXT, app_secret_enc TEXT,
            fallback_webhook_secret_enc TEXT, sync_enabled INTEGER NOT NULL DEFAULT 0,
            target_tenant_id INTEGER, interval_minutes INTEGER NOT NULL DEFAULT 60,
            root_dept_id TEXT NOT NULL DEFAULT '1',
            max_runtime_seconds INTEGER NOT NULL DEFAULT 1800,
            auto_recovery INTEGER NOT NULL DEFAULT 0, created_by INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT
        );
        CREATE TABLE webhook_settings (
            id INTEGER PRIMARY KEY, webhook_secret_enc TEXT,
            allow_private_webhook_urls INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1, created_by INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT
        );
        CREATE TABLE config_import_state (
            config_key TEXT PRIMARY KEY, state TEXT NOT NULL, source TEXT,
            imported_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.close()
    monkeypatch.setattr(module, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(module, "is_postgresql", lambda: False)
    monkeypatch.setattr(module, "adapt_sql", lambda sql: sql)
    monkeypatch.setenv("OPENACE_ENCRYPTION_KEY", "notification-settings-test-key-32c")
    get_password_manager.cache_clear()
    result = module.NotificationSettingsRepository()
    monkeypatch.setattr(result, "_connection", connection)
    yield result, tmp_path, connection
    get_password_manager.cache_clear()


@pytest.mark.parametrize(
    ("kind", "legacy", "configured_field"),
    [
        (
            "feishu",
            {"feishu": {"app_id": "cli_test", "app_secret": "feishu-secret"}},
            "app_secret_configured",
        ),
        (
            "webhook",
            {"alerts": {"webhook_secret": "webhook-secret"}},
            "webhook_secret_configured",
        ),
        (
            "dingtalk",
            {"alerts": {"dingtalk_webhook_secret": "dingtalk-secret"}},
            "fallback_webhook_secret_configured",
        ),
    ],
)
def test_imports_supported_legacy_settings(repository, kind, legacy, configured_field):
    repo, config_dir, _connection = repository
    (config_dir / "config.json").write_text(json.dumps(legacy), encoding="utf-8")

    result = repo.get(kind)

    assert result is not None
    assert result[configured_field] is True
    assert all(not key.endswith("_enc") for key in result)


def test_save_preserves_omitted_secret_and_updates_non_secret(repository):
    repo, _config_dir, _connection = repository
    repo.save(
        "webhook",
        {"webhook_secret": "keep-me", "enabled": True, "allow_private_webhook_urls": False},
    )

    updated = repo.save("webhook", {"enabled": False, "allow_private_webhook_urls": True})
    with_secret = repo.get("webhook", include_secrets=True)

    assert updated["enabled"] == 0
    assert updated["allow_private_webhook_urls"] == 1
    assert with_secret is not None
    assert with_secret["webhook_secret"] == "keep-me"


def test_delete_writes_tombstone_and_prevents_legacy_revival(repository):
    repo, config_dir, connection = repository
    (config_dir / "config.json").write_text(
        json.dumps({"alerts": {"webhook_secret": "must-not-return"}}), encoding="utf-8"
    )
    repo.save(
        "webhook",
        {"webhook_secret": "managed", "enabled": True, "allow_private_webhook_urls": False},
    )

    assert repo.delete("webhook") is True
    assert repo.get("webhook") is None
    conn = connection()
    state = conn.execute(
        "SELECT state FROM config_import_state WHERE config_key = 'webhook'"
    ).fetchone()[0]
    conn.close()
    assert state == "tombstone"


def test_save_rolls_back_when_import_state_write_fails(repository, monkeypatch):
    repo, _config_dir, connection = repository
    repo.save(
        "webhook",
        {"webhook_secret": "original", "enabled": True, "allow_private_webhook_urls": False},
    )
    original_connection = repo._connection

    class FailingCursor:
        def __init__(self, cursor):
            self.cursor = cursor

        def execute(self, sql, parameters=()):
            if "INSERT INTO config_import_state" in sql:
                raise sqlite3.IntegrityError("simulated marker failure")
            return self.cursor.execute(sql, parameters)

        def __getattr__(self, name):
            return getattr(self.cursor, name)

    class FailingConnection:
        def __init__(self):
            self.connection = original_connection()

        def cursor(self):
            return FailingCursor(self.connection.cursor())

        def __getattr__(self, name):
            return getattr(self.connection, name)

    monkeypatch.setattr(repo, "_connection", FailingConnection)
    with pytest.raises(sqlite3.IntegrityError):
        repo.save("webhook", {"enabled": False, "allow_private_webhook_urls": True})
    monkeypatch.setattr(repo, "_connection", original_connection)

    current = repo.get("webhook", include_secrets=True)
    assert current is not None
    assert current["enabled"] == 1
    assert current["webhook_secret"] == "original"
