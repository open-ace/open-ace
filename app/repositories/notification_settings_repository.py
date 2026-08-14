"""Centralized, encrypted system settings for notification integrations."""

import json
import os
from datetime import datetime, timezone
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

from app.repositories.database import CONFIG_DIR, adapt_sql, get_database_url, is_postgresql
from app.utils.smtp_crypto import get_password_manager


class NotificationSettingsRepository:
    TABLES = {
        "feishu": "feishu_settings",
        "dingtalk": "dingtalk_settings",
        "webhook": "webhook_settings",
    }
    SECRET_FIELDS = {
        "feishu": ("app_secret", "app_secret_enc"),
        "dingtalk": ("app_secret", "app_secret_enc"),
        "webhook": ("webhook_secret", "webhook_secret_enc"),
    }

    def _connection(self):
        if is_postgresql():
            conn = psycopg2.connect(get_database_url())
            conn.cursor_factory = RealDictCursor
            return conn
        import sqlite3

        conn = sqlite3.connect("app.db")
        conn.row_factory = sqlite3.Row
        return conn

    def get(self, kind: str, include_secrets: bool = False) -> dict[str, Any] | None:
        table = self.TABLES[kind]
        conn = self._connection()
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM {table} WHERE id = 1")
        row = cur.fetchone()
        conn.close()
        if not row:
            if self._import_legacy(kind):
                return self.get(kind, include_secrets)
            return None
        result = dict(row)
        secret_name, column = self.SECRET_FIELDS[kind]
        encrypted = result.pop(column, None)
        result[f"{secret_name}_configured"] = bool(encrypted)
        if include_secrets and encrypted:
            result[secret_name] = get_password_manager().decrypt(encrypted)
        if kind == "dingtalk":
            encrypted_fallback = result.pop("fallback_webhook_secret_enc", None)
            result["fallback_webhook_secret_configured"] = bool(encrypted_fallback)
            if include_secrets and encrypted_fallback:
                result["fallback_webhook_secret"] = get_password_manager().decrypt(
                    encrypted_fallback
                )
        return result

    def _import_legacy(self, kind: str) -> bool:
        """Import config.json once; a tombstone prevents deleted secrets reviving."""
        conn = self._connection()
        cur = conn.cursor()
        cur.execute(
            adapt_sql("SELECT state FROM config_import_state WHERE config_key = ?"), (kind,)
        )
        if cur.fetchone():
            conn.close()
            return False
        path = os.path.join(CONFIG_DIR, "config.json")
        try:
            with open(path, encoding="utf-8") as handle:
                root = json.load(handle)
        except (OSError, ValueError):
            conn.close()
            return False
        source = root.get(kind) or (root.get("alerts", {}) if kind == "webhook" else {})
        if not source:
            conn.close()
            return False
        conn.close()
        if kind == "feishu":
            values = {
                "app_id": source.get("app_id", ""),
                "app_secret": source.get("app_secret", ""),
                "sync_enabled": bool(source.get("org_sync_enabled", False)),
                "target_tenant_id": source.get("org_sync_tenant_id"),
                "interval_minutes": source.get("org_sync_interval_minutes", 60),
                "max_runtime_seconds": source.get("org_sync_max_runtime_seconds", 1800),
                "auto_recovery": bool(source.get("org_sync_auto_recover", False)),
            }
        elif kind == "dingtalk":
            values = {
                "app_key": source.get("app_key", ""),
                "app_secret": source.get("app_secret", ""),
                "fallback_webhook_secret": root.get("alerts", {}).get(
                    "dingtalk_webhook_secret", ""
                ),
                "sync_enabled": bool(source.get("org_sync_enabled", False)),
                "target_tenant_id": source.get("org_sync_tenant_id"),
                "interval_minutes": source.get("org_sync_interval_minutes", 60),
                "root_dept_id": str(source.get("org_sync_root_dept_id", "1")),
                "max_runtime_seconds": source.get("org_sync_max_runtime_seconds", 1800),
                "auto_recovery": bool(source.get("org_sync_auto_recover", False)),
            }
        else:
            values = {
                "webhook_secret": source.get("webhook_secret", ""),
                "allow_private_webhook_urls": bool(source.get("allow_private_webhook_urls", False)),
                "enabled": True,
            }
        self.save(kind, values)
        conn = self._connection()
        cur = conn.cursor()
        cur.execute(
            adapt_sql("UPDATE config_import_state SET state = ?, source = ? WHERE config_key = ?"),
            ("imported", path, kind),
        )
        conn.commit()
        conn.close()
        return True

    def save(self, kind: str, values: dict[str, Any], user_id: int | None = None) -> dict[str, Any]:
        table = self.TABLES[kind]
        secret_name, secret_column = self.SECRET_FIELDS[kind]
        needs_current = secret_name not in values or (
            kind == "dingtalk" and "fallback_webhook_secret" not in values
        )
        current = self.get(kind, include_secrets=True) or {} if needs_current else {}
        columns = dict(values)
        if secret_name in columns:
            secret = columns.pop(secret_name)
            columns[secret_column] = get_password_manager().encrypt(secret) if secret else None
        elif current.get(secret_name):
            columns[secret_column] = get_password_manager().encrypt(current[secret_name])
        if kind == "dingtalk":
            if "fallback_webhook_secret" in columns:
                secret = columns.pop("fallback_webhook_secret")
                columns["fallback_webhook_secret_enc"] = (
                    get_password_manager().encrypt(secret) if secret else None
                )
            elif current.get("fallback_webhook_secret"):
                columns["fallback_webhook_secret_enc"] = get_password_manager().encrypt(
                    current["fallback_webhook_secret"]
                )
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        columns.update(id=1, created_by=user_id, updated_at=now)
        conn = self._connection()
        cur = conn.cursor()
        cur.execute(adapt_sql(f"DELETE FROM {table} WHERE id = ?"), (1,))
        names = list(columns)
        placeholders = ", ".join("?" for _ in names)
        cur.execute(
            adapt_sql(f"INSERT INTO {table} ({', '.join(names)}) VALUES ({placeholders})"),
            tuple(columns[n] for n in names),
        )
        cur.execute(adapt_sql("DELETE FROM config_import_state WHERE config_key = ?"), (kind,))
        cur.execute(
            adapt_sql(
                "INSERT INTO config_import_state (config_key, state, source) VALUES (?, ?, ?)"
            ),
            (kind, "managed", "database"),
        )
        conn.commit()
        conn.close()
        return self.get(kind) or {}

    def delete(self, kind: str) -> bool:
        conn = self._connection()
        cur = conn.cursor()
        cur.execute(f"DELETE FROM {self.TABLES[kind]} WHERE id = 1")
        deleted = cur.rowcount > 0
        cur.execute(adapt_sql("DELETE FROM config_import_state WHERE config_key = ?"), (kind,))
        cur.execute(
            adapt_sql(
                "INSERT INTO config_import_state (config_key, state, source) VALUES (?, ?, ?)"
            ),
            (kind, "tombstone", "admin"),
        )
        conn.commit()
        conn.close()
        return deleted


_repository = NotificationSettingsRepository()


def get_notification_settings_repository() -> NotificationSettingsRepository:
    return _repository
