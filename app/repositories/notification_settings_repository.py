"""Centralized, encrypted system settings for notification integrations."""

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

from app.repositories.database import CONFIG_DIR, adapt_sql, get_database_url, is_postgresql
from app.utils.smtp_crypto import get_password_manager


class NotificationSettingsRepository:
    """Persist singleton notification settings and import legacy file values once."""

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

    def _table(self, kind: str) -> str:
        """Resolve a table from the closed integration-kind allowlist."""
        try:
            return self.TABLES[kind]
        except KeyError as exc:
            raise ValueError(f"Unsupported notification integration kind: {kind}") from exc

    def _connection(self) -> Any:
        """Open a repository connection for the configured database backend."""
        if is_postgresql():
            return psycopg2.connect(get_database_url(), cursor_factory=RealDictCursor)
        import sqlite3

        conn = sqlite3.connect("app.db")
        conn.row_factory = sqlite3.Row
        return conn

    def _get_raw(self, kind: str) -> dict[str, Any] | None:
        """Read the database row without triggering legacy import."""
        table = self._table(kind)
        conn = self._connection()
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT * FROM {table} WHERE id = 1")  # nosec B608: allowlisted table
            row = cur.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get(self, kind: str, include_secrets: bool = False) -> dict[str, Any] | None:
        """Return masked settings, importing a legacy value at most once."""
        row = self._get_raw(kind)
        if row is None and self._import_legacy(kind):
            row = self._get_raw(kind)
        if row is None:
            return None

        secret_name, column = self.SECRET_FIELDS[kind]
        encrypted = row.pop(column, None)
        row[f"{secret_name}_configured"] = bool(encrypted)
        if include_secrets and encrypted:
            row[secret_name] = get_password_manager().decrypt(encrypted)
        if kind == "dingtalk":
            encrypted_fallback = row.pop("fallback_webhook_secret_enc", None)
            row["fallback_webhook_secret_configured"] = bool(encrypted_fallback)
            if include_secrets and encrypted_fallback:
                row["fallback_webhook_secret"] = get_password_manager().decrypt(encrypted_fallback)
        return row

    def _legacy_values(self, kind: str, root: dict[str, Any]) -> dict[str, Any] | None:
        """Translate a supported config.json section into database columns."""
        alerts = root.get("alerts", {})
        source = root.get(kind) or (alerts if kind == "webhook" else {})
        if kind == "feishu" and source:
            return {
                "app_id": source.get("app_id", ""),
                "app_secret": source.get("app_secret", ""),
                "sync_enabled": bool(source.get("org_sync_enabled", False)),
                "target_tenant_id": source.get("org_sync_tenant_id"),
                "interval_minutes": source.get("org_sync_interval_minutes", 60),
                "max_runtime_seconds": source.get("org_sync_max_runtime_seconds", 1800),
                "auto_recovery": bool(source.get("org_sync_auto_recover", False)),
            }
        if kind == "dingtalk" and (source or alerts.get("dingtalk_webhook_secret")):
            return {
                "app_key": source.get("app_key", ""),
                "app_secret": source.get("app_secret", ""),
                "fallback_webhook_secret": alerts.get("dingtalk_webhook_secret", ""),
                "sync_enabled": bool(source.get("org_sync_enabled", False)),
                "target_tenant_id": source.get("org_sync_tenant_id"),
                "interval_minutes": source.get("org_sync_interval_minutes", 60),
                "root_dept_id": str(source.get("org_sync_root_dept_id", "1")),
                "max_runtime_seconds": source.get("org_sync_max_runtime_seconds", 1800),
                "auto_recovery": bool(source.get("org_sync_auto_recover", False)),
            }
        if kind == "webhook" and source:
            return {
                "webhook_secret": source.get("webhook_secret", ""),
                "allow_private_webhook_urls": bool(source.get("allow_private_webhook_urls", False)),
                "enabled": True,
            }
        return None

    def _import_legacy(self, kind: str) -> bool:
        """Import config.json atomically unless a managed marker or tombstone exists."""
        self._table(kind)
        conn = self._connection()
        try:
            cur = conn.cursor()
            cur.execute(
                adapt_sql("SELECT state FROM config_import_state WHERE config_key = ?"),
                (kind,),
            )
            if cur.fetchone():
                return False
        finally:
            conn.close()

        path = os.path.join(CONFIG_DIR, "config.json")
        try:
            with open(path, encoding="utf-8") as handle:
                root = json.load(handle)
        except (OSError, ValueError):
            return False
        values = self._legacy_values(kind, root)
        if values is None:
            return False
        self._write(kind, values, user_id=None, import_state=("imported", path))
        return True

    def _prepare_columns(
        self, kind: str, values: dict[str, Any], user_id: int | None
    ) -> dict[str, Any]:
        """Encrypt supplied secrets and preserve omitted encrypted columns."""
        current = self._get_raw(kind) or {}
        columns = dict(values)
        secret_name, secret_column = self.SECRET_FIELDS[kind]
        if secret_name in columns:
            secret = columns.pop(secret_name)
            columns[secret_column] = get_password_manager().encrypt(secret) if secret else None
        elif secret_column in current:
            columns[secret_column] = current[secret_column]
        if kind == "dingtalk":
            if "fallback_webhook_secret" in columns:
                secret = columns.pop("fallback_webhook_secret")
                columns["fallback_webhook_secret_enc"] = (
                    get_password_manager().encrypt(secret) if secret else None
                )
            elif "fallback_webhook_secret_enc" in current:
                columns["fallback_webhook_secret_enc"] = current["fallback_webhook_secret_enc"]

        # Handle verification status fields
        # Compute new fingerprint and reset verification status when config changes
        # Merge current first, then override with new columns so new values take precedence
        merged = {**current, **columns}
        new_fingerprint = self._compute_config_fingerprint(kind, merged)
        old_fingerprint = current.get("verified_config_fingerprint")

        # If fingerprint changed (config changed), reset verification status
        if kind == "feishu":
            if new_fingerprint != old_fingerprint:
                columns["verification_status"] = "configured_unverified"
                columns["verified_config_fingerprint"] = new_fingerprint
                columns["last_tested_at"] = None
                columns["last_test_error_code"] = None
                columns["last_test_error_summary"] = None
            else:
                # Preserve existing verification status
                for field in [
                    "verification_status",
                    "last_tested_at",
                    "last_test_error_code",
                    "last_test_error_summary",
                    "verified_config_fingerprint",
                ]:
                    if field in current and field not in columns:
                        columns[field] = current[field]

        columns.update(
            id=1,
            created_by=user_id if user_id is not None else current.get("created_by"),
            updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        return columns

    def _write(
        self,
        kind: str,
        values: dict[str, Any],
        user_id: int | None,
        import_state: tuple[str, str],
    ) -> None:
        """Upsert settings and their import marker in one transaction."""
        table = self._table(kind)
        columns = self._prepare_columns(kind, values, user_id)
        names = list(columns)
        placeholders = ", ".join("?" for _ in names)
        assignments = ", ".join(f"{name} = excluded.{name}" for name in names if name != "id")
        sql = adapt_sql(
            f"INSERT INTO {table} ({', '.join(names)}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {assignments}"  # nosec B608: allowlisted columns/table
        )
        conn = self._connection()
        try:
            cur = conn.cursor()
            cur.execute(sql, tuple(columns[name] for name in names))
            cur.execute(adapt_sql("DELETE FROM config_import_state WHERE config_key = ?"), (kind,))
            cur.execute(
                adapt_sql(
                    "INSERT INTO config_import_state (config_key, state, source) VALUES (?, ?, ?)"
                ),
                (kind, import_state[0], import_state[1]),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def save(self, kind: str, values: dict[str, Any], user_id: int | None = None) -> dict[str, Any]:
        """Atomically upsert a supported singleton configuration."""
        self._write(kind, values, user_id, import_state=("managed", "database"))
        return self.get(kind) or {}

    def delete(self, kind: str) -> bool:
        """Delete settings and atomically write a tombstone against re-import."""
        table = self._table(kind)
        conn = self._connection()
        try:
            cur = conn.cursor()
            cur.execute(f"DELETE FROM {table} WHERE id = 1")  # nosec B608: allowlisted table
            deleted = bool(cur.rowcount > 0)
            cur.execute(adapt_sql("DELETE FROM config_import_state WHERE config_key = ?"), (kind,))
            cur.execute(
                adapt_sql(
                    "INSERT INTO config_import_state (config_key, state, source) VALUES (?, ?, ?)"
                ),
                (kind, "tombstone", "admin"),
            )
            conn.commit()
            return bool(deleted)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def update_verification_status(
        self,
        kind: str,
        status: str,
        error_code: str | None = None,
        error_summary: str | None = None,
    ) -> None:
        """Update verification status for an integration after testing connection.

        Args:
            kind: Integration type (feishu, dingtalk, webhook)
            status: Verification status (connected, connection_failed, configuration_error)
            error_code: Error code if status is connection_failed or configuration_error
            error_summary: Human-readable error summary (should be sanitized, no secrets)
        """
        table = self._table(kind)
        conn = self._connection()
        try:
            cur = conn.cursor()
            cur.execute(
                adapt_sql(
                    f"UPDATE {table} SET verification_status = ?, last_tested_at = ?, "
                    f"last_test_error_code = ?, last_test_error_summary = ? WHERE id = 1"
                ),  # nosec B608: allowlisted table
                (
                    status,
                    datetime.now(timezone.utc).replace(tzinfo=None),
                    error_code,
                    error_summary,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _compute_config_fingerprint(self, kind: str, values: dict[str, Any]) -> str:
        """Compute a fingerprint for configuration change detection.

        Uses HMAC with the encryption key to create a stable fingerprint that
        can detect when key configuration parameters have changed.
        """
        if kind == "feishu":
            # Fingerprint based on app_id (app_secret is encrypted, use its presence)
            app_id = values.get("app_id", "")
            secret_present = bool(values.get("app_secret") or values.get("app_secret_enc"))
            payload = f"{app_id}:{secret_present}"
        elif kind == "dingtalk":
            app_key = values.get("app_key", "")
            secret_present = bool(values.get("app_secret") or values.get("app_secret_enc"))
            payload = f"{app_key}:{secret_present}"
        else:
            # For webhook, just use secret presence
            payload = str(bool(values.get("webhook_secret") or values.get("webhook_secret_enc")))

        # Use encryption key as HMAC key for consistency
        key = os.environ.get("OPENACE_ENCRYPTION_KEY", "default-fingerprint-key")
        return hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]


_repository = NotificationSettingsRepository()


def get_notification_settings_repository() -> NotificationSettingsRepository:
    """Return the process-wide notification settings repository."""
    return _repository
