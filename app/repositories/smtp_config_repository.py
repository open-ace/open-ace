"""
Open ACE - SMTP Config Repository

Provides database access for SMTP configuration management.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional, Union

import psycopg2
from psycopg2.extras import RealDictCursor

from app.repositories.database import adapt_sql, get_database_url, is_postgresql
from app.utils.smtp_password_manager import get_password_manager

logger = logging.getLogger(__name__)


class SMTPConfigRepository:
    """Repository for SMTP configuration data."""

    def __init__(self):
        """Initialize repository."""
        self.password_manager = get_password_manager()

    def _get_connection(self) -> Union[Any, Any]:
        """Get database connection."""
        if is_postgresql():
            url = get_database_url()
            conn = psycopg2.connect(url)
            conn.cursor_factory = RealDictCursor
            return conn
        else:
            import sqlite3

            conn = sqlite3.connect("app.db")
            conn.row_factory = sqlite3.Row
            return conn

    def get_config(self) -> Optional[dict[str, Any]]:
        """
        Get SMTP configuration (only one config per system).

        Returns:
            SMTP config dict with masked password, or None if not configured.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            adapt_sql(
                """
                SELECT id, smtp_host, smtp_port, smtp_user, encrypted_password,
                       encryption_version, from_address, use_tls, is_verified,
                       last_verified_at, created_at, updated_at, created_by
                FROM smtp_settings
                ORDER BY id DESC LIMIT 1
            """
            )
        )

        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        # Mask password for display
        config = dict(row)
        if config.get("smtp_user") and config.get("encrypted_password"):
            try:
                # Decrypt temporarily to mask
                decrypted = self.password_manager.decrypt(config["encrypted_password"])
                config["smtp_password_masked"] = self.password_manager.mask_password(decrypted)
            except Exception as e:
                logger.error(f"Failed to decrypt password: {e}")
                config["smtp_password_masked"] = "****"
        else:
            config["smtp_password_masked"] = ""

        # Remove encrypted password from response
        config.pop("encrypted_password", None)

        return config

    def get_config_with_password(self) -> Optional[dict[str, Any]]:
        """
        Get SMTP configuration with decrypted password (for sending emails).

        Returns:
            SMTP config dict with decrypted password, or None.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            adapt_sql(
                """
                SELECT id, smtp_host, smtp_port, smtp_user, encrypted_password,
                       encryption_version, from_address, use_tls, is_verified,
                       last_verified_at
                FROM smtp_settings
                ORDER BY id DESC LIMIT 1
            """
            )
        )

        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        config = dict(row)

        # Decrypt password
        if config.get("encrypted_password"):
            try:
                config["smtp_password"] = self.password_manager.decrypt(
                    config["encrypted_password"]
                )
            except Exception as e:
                logger.error(f"Failed to decrypt SMTP password: {e}")
                return None
        else:
            config["smtp_password"] = ""

        return config

    def save_config(
        self,
        smtp_host: str,
        smtp_port: int,
        from_address: str,
        smtp_user: Optional[str] = None,
        smtp_password: Optional[str] = None,
        use_tls: bool = True,
        created_by: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        Save SMTP configuration.

        Args:
            smtp_host: SMTP server hostname.
            smtp_port: SMTP server port.
            from_address: Email sender address.
            smtp_user: SMTP username.
            smtp_password: SMTP password (will be encrypted).
            use_tls: Whether to use TLS.
            created_by: User ID who created the config.

        Returns:
            Saved config dict (without encrypted password).
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        # Encrypt password
        encrypted_password = None
        if smtp_password:
            encrypted_password = self.password_manager.encrypt(smtp_password)

        # Delete existing config (only one allowed)
        cursor.execute(adapt_sql("DELETE FROM smtp_settings"))

        # Insert new config
        if is_postgresql():
            cursor.execute(
                """
                INSERT INTO smtp_settings
                (smtp_host, smtp_port, smtp_user, encrypted_password, from_address,
                 use_tls, is_verified, created_by, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    smtp_host,
                    smtp_port,
                    smtp_user,
                    encrypted_password,
                    from_address,
                    use_tls,
                    False,  # is_verified - needs testing
                    created_by,
                    datetime.now(timezone.utc).replace(tzinfo=None),
                    datetime.now(timezone.utc).replace(tzinfo=None),
                ),
            )
            config_id = cursor.fetchone()["id"]
        else:
            cursor.execute(
                """
                INSERT INTO smtp_settings
                (smtp_host, smtp_port, smtp_user, encrypted_password, from_address,
                 use_tls, is_verified, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    smtp_host,
                    smtp_port,
                    smtp_user,
                    encrypted_password,
                    from_address,
                    1 if use_tls else 0,
                    0,
                    created_by,
                    datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                    datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                ),
            )
            config_id = cursor.lastrowid

        conn.commit()
        conn.close()

        logger.info(f"SMTP configuration saved with ID {config_id}")

        # Return config without encrypted password
        return {
            "id": config_id,
            "smtp_host": smtp_host,
            "smtp_port": smtp_port,
            "smtp_user": smtp_user,
            "smtp_password_masked": self.password_manager.mask_password(smtp_password or ""),
            "from_address": from_address,
            "use_tls": use_tls,
            "is_verified": False,
            "created_by": created_by,
        }

    def update_verified_status(self, config_id: int, is_verified: bool) -> bool:
        """
        Update SMTP configuration verification status.

        Args:
            config_id: Config ID.
            is_verified: Verification status.

        Returns:
            True if successful.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            adapt_sql(
                """
                UPDATE smtp_settings
                SET is_verified = ?, last_verified_at = ?, updated_at = ?
                WHERE id = ?
            """
            ),
            (
                is_verified if is_postgresql() else (1 if is_verified else 0),
                datetime.now(timezone.utc).replace(tzinfo=None),
                datetime.now(timezone.utc).replace(tzinfo=None),
                config_id,
            ),
        )

        success = bool(cursor.rowcount > 0)
        conn.commit()
        conn.close()

        return success

    def delete_config(self) -> bool:
        """
        Delete SMTP configuration.

        Returns:
            True if successful.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(adapt_sql("DELETE FROM smtp_settings"))
        count = cursor.rowcount

        conn.commit()
        conn.close()

        return bool(count > 0)


# Global repository instance
_smtp_config_repo: Optional[SMTPConfigRepository] = None


def get_smtp_config_repository() -> SMTPConfigRepository:
    """Get the global SMTP config repository instance."""
    global _smtp_config_repo
    if _smtp_config_repo is None:
        _smtp_config_repo = SMTPConfigRepository()
    return _smtp_config_repo
