"""Encrypted storage for the model gateway admin configuration.

Mirrors ``smtp_config_repository``: a single system-wide row, Fernet encryption
via the shared ``smtp_crypto`` password manager (same key derivation as API-key
encryption), masked-on-GET display, and a ``get_config_with_key`` accessor for
runtime use by the planner.
"""

from __future__ import annotations


import logging
from datetime import datetime, timezone
from typing import Any

from app.modules.workspace.model_gateway.config import GatewayConfig
from app.repositories.database import adapt_sql, get_database_url, is_postgresql
from app.utils.smtp_crypto import get_password_manager

logger = logging.getLogger(__name__)


class ModelGatewayConfigRepository:
    """Repository for the single model_gateway_config admin row."""

    def __init__(self):
        self.password_manager = get_password_manager()

    def _get_connection(self) -> Any:
        if is_postgresql():
            import psycopg2
            from psycopg2.extras import RealDictCursor

            conn = psycopg2.connect(get_database_url())
            conn.cursor_factory = RealDictCursor
            return conn
        import sqlite3

        url = get_database_url()
        db_path = url[len("sqlite:///") :] if url.startswith("sqlite:///") else "app.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_config(self) -> dict[str, Any] | None:
        """Get the gateway config for display (API key masked, ciphertext removed)."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            adapt_sql(
                """
                SELECT id, mode, base_url, encrypted_api_key, encryption_version,
                       model_prefix_mode, model_prefix, created_by, created_at, updated_at
                FROM model_gateway_config
                ORDER BY id DESC LIMIT 1
                """
            )
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None

        config = dict(row)
        masked = ""
        if config.get("encrypted_api_key"):
            try:
                decrypted = self.password_manager.decrypt(config["encrypted_api_key"])
                masked = self.password_manager.mask_password(decrypted)
            except Exception as exc:
                logger.error("Failed to decrypt gateway key: %s", exc)
                masked = "****"
        config["api_key_masked"] = masked
        config.pop("encrypted_api_key", None)
        return config

    def get_config_with_key(self) -> GatewayConfig | None:
        """Get the decrypted gateway config for runtime forwarding (planner use)."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            adapt_sql(
                """
                SELECT mode, base_url, encrypted_api_key, model_prefix_mode, model_prefix
                FROM model_gateway_config
                ORDER BY id DESC LIMIT 1
                """
            )
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None

        data = dict(row)
        api_key = ""
        if data.get("encrypted_api_key"):
            try:
                api_key = self.password_manager.decrypt(data["encrypted_api_key"])
            except Exception as exc:
                logger.error("Failed to decrypt gateway key for runtime use: %s", exc)
                return None

        prefix_mode = bool(data.get("model_prefix_mode"))
        if not is_postgresql() and data.get("model_prefix_mode") is not None:
            prefix_mode = bool(int(data["model_prefix_mode"]))

        return GatewayConfig(
            base_url=(data.get("base_url") or ""),
            api_key=api_key,
            model_prefix_mode=prefix_mode,
            model_prefix=(data.get("model_prefix") or None),
        )

    def save_config(
        self,
        base_url: str,
        api_key: str,
        model_prefix_mode: bool = False,
        model_prefix: str | None = None,
        created_by: int | None = None,
    ) -> dict[str, Any]:
        """Save the gateway config (replaces the single existing row)."""
        encrypted_key = self.password_manager.encrypt(api_key) if api_key else ""
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(adapt_sql("DELETE FROM model_gateway_config"))

        if is_postgresql():
            cursor.execute(
                """
                INSERT INTO model_gateway_config
                (mode, base_url, encrypted_api_key, encryption_version,
                 model_prefix_mode, model_prefix, created_by, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    "gateway",
                    base_url,
                    encrypted_key,
                    1,
                    model_prefix_mode,
                    model_prefix,
                    created_by,
                    now,
                    now,
                ),
            )
            config_id = cursor.fetchone()["id"]
        else:
            cursor.execute(
                """
                INSERT INTO model_gateway_config
                (mode, base_url, encrypted_api_key, encryption_version,
                 model_prefix_mode, model_prefix, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "gateway",
                    base_url,
                    encrypted_key,
                    1,
                    1 if model_prefix_mode else 0,
                    model_prefix,
                    created_by,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            config_id = cursor.lastrowid

        conn.commit()
        conn.close()
        logger.info("Model gateway configuration saved with ID %s", config_id)
        return {
            "id": config_id,
            "mode": "gateway",
            "base_url": base_url,
            "api_key_masked": self.password_manager.mask_password(api_key or ""),
            "model_prefix_mode": model_prefix_mode,
            "model_prefix": model_prefix,
            "created_by": created_by,
        }

    def delete_config(self) -> bool:
        """Delete the gateway config row. Returns True if a row was removed."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(adapt_sql("DELETE FROM model_gateway_config"))
        count = cursor.rowcount
        conn.commit()
        conn.close()
        return bool(count and count > 0)


_gateway_repo: ModelGatewayConfigRepository | None = None


def get_gateway_repository() -> ModelGatewayConfigRepository:
    """Get the global gateway config repository instance."""
    global _gateway_repo
    if _gateway_repo is None:
        _gateway_repo = ModelGatewayConfigRepository()
    return _gateway_repo
