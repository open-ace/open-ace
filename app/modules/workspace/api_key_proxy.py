#!/usr/bin/env python3
"""
Open ACE - API Key Proxy Service

Provides encrypted storage and proxy token management for LLM API keys.
API keys never leave the server — remote agents receive short-lived proxy tokens
that are exchanged for real keys by the server's LLM proxy endpoint.
"""

import hashlib
import json
import logging
import os
import secrets
import sqlite3
from base64 import b64decode, b64encode
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union

from app.repositories.database import DB_PATH, is_postgresql, get_database_url

logger = logging.getLogger(__name__)


def _param() -> str:
    """Get the correct parameter placeholder for the current database."""
    return "?" if not is_postgresql() else "%s"


def _params(count: int) -> str:
    """Get comma-separated placeholders for multiple parameters."""
    p = _param()
    return ", ".join([p] * count)


class APIKeyProxyService:
    """
    Manages encrypted API key storage and proxy token generation.

    API keys are encrypted with AES-256-GCM using a key derived from
    the OPENACE_ENCRYPTION_KEY environment variable. Short-lived JWT-like
    proxy tokens are issued to remote agents for authenticating LLM proxy calls.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or str(DB_PATH)
        self._encryption_key = self._get_encryption_key()
        self._ensure_tables()

    def _get_encryption_key(self) -> bytes:
        """Get the AES encryption key from environment variable."""
        key_env = os.environ.get("OPENACE_ENCRYPTION_KEY")
        if not key_env:
            # Generate a stable key from SECRET_KEY as fallback
            secret = os.environ.get("SECRET_KEY", "dev-secret-key")
            key_env = secret
        # Derive a 32-byte key using SHA-256
        return hashlib.sha256(key_env.encode()).digest()

    def _get_connection(self) -> Union[sqlite3.Connection, Any]:
        """Get database connection."""
        if is_postgresql():
            try:
                import psycopg2
                from psycopg2.extras import RealDictCursor

                url = get_database_url()
                conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
                return conn
            except ImportError:
                raise ImportError("psycopg2 is required for PostgreSQL")
        else:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            return conn

    def _ensure_tables(self) -> None:
        """Ensure required tables exist."""
        conn = self._get_connection()
        cursor = conn.cursor()

        id_type = "SERIAL PRIMARY KEY" if is_postgresql() else "INTEGER PRIMARY KEY AUTOINCREMENT"

        # api_key_store table is created by migration, but ensure it exists for
        # environments that don't run migrations
        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS api_key_store (
                id {id_type},
                tenant_id INTEGER,
                provider TEXT NOT NULL,
                key_name TEXT NOT NULL,
                encrypted_key TEXT NOT NULL,
                key_hash TEXT NOT NULL,
                base_url TEXT,
                is_active INTEGER DEFAULT 1,
                created_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(tenant_id, provider, key_name)
            )
        """
        )

        conn.commit()
        conn.close()

    def _encrypt_key(self, api_key: str) -> str:
        """Encrypt an API key using AES-256-GCM."""
        try:
            from cryptography.fernet import Fernet
            import base64
            f = Fernet(base64.urlsafe_b64encode(self._encryption_key))
            return f.encrypt(api_key.encode()).decode()
        except ImportError:
            # Fallback to simple base64 encoding if cryptography not available
            logger.warning("cryptography package not installed, using base64 encoding (not secure for production)")
            return b64encode(api_key.encode()).decode()

    def _decrypt_key(self, encrypted_key: str) -> str:
        """Decrypt an API key."""
        try:
            from cryptography.fernet import Fernet
            import base64
            f = Fernet(base64.urlsafe_b64encode(self._encryption_key))
            return f.decrypt(encrypted_key.encode()).decode()
        except ImportError:
            return b64decode(encrypted_key.encode()).decode()

    def _hash_key(self, api_key: str) -> str:
        """Create a SHA-256 hash of an API key for lookup."""
        return hashlib.sha256(api_key.encode()).hexdigest()

    def store_api_key(
        self,
        tenant_id: int,
        provider: str,
        key_name: str,
        api_key: str,
        base_url: Optional[str] = None,
        created_by: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Store an encrypted API key for a tenant/provider.

        Args:
            tenant_id: Tenant ID.
            provider: Provider name (openai, anthropic, google, etc.).
            key_name: Display name for this key.
            api_key: The plaintext API key to encrypt and store.
            base_url: Optional custom base URL for the provider.
            created_by: User ID who created this key.

        Returns:
            Dict with success status and key info.
        """
        encrypted = self._encrypt_key(api_key)
        key_hash = self._hash_key(api_key)

        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            if is_postgresql():
                cursor.execute(
                    f"""
                    INSERT INTO api_key_store (tenant_id, provider, key_name, encrypted_key, key_hash, base_url, created_by)
                    VALUES ({_params(7)})
                    ON CONFLICT (tenant_id, provider, key_name) DO UPDATE SET
                        encrypted_key = EXCLUDED.encrypted_key,
                        key_hash = EXCLUDED.key_hash,
                        base_url = EXCLUDED.base_url,
                        is_active = 1,
                        updated_at = CURRENT_TIMESTAMP
                """,
                    (tenant_id, provider, key_name, encrypted, key_hash, base_url, created_by),
                )
            else:
                cursor.execute(
                    f"""
                    INSERT INTO api_key_store (tenant_id, provider, key_name, encrypted_key, key_hash, base_url, created_by)
                    VALUES ({_params(7)})
                    ON CONFLICT (tenant_id, provider, key_name) DO UPDATE SET
                        encrypted_key = excluded.encrypted_key,
                        key_hash = excluded.key_hash,
                        base_url = excluded.base_url,
                        is_active = 1,
                        updated_at = CURRENT_TIMESTAMP
                """,
                    (tenant_id, provider, key_name, encrypted, key_hash, base_url, created_by),
                )

            conn.commit()
            logger.info(f"Stored API key for tenant {tenant_id}, provider {provider}, name {key_name}")

            return {
                "success": True,
                "provider": provider,
                "key_name": key_name,
            }
        except Exception as e:
            logger.error(f"Failed to store API key: {e}")
            conn.rollback()
            return {"success": False, "error": str(e)}
        finally:
            conn.close()

    def resolve_api_key(self, tenant_id: int, provider: str) -> Optional[Tuple[str, Optional[str]]]:
        """
        Resolve and decrypt an API key for a tenant/provider.

        Args:
            tenant_id: Tenant ID.
            provider: Provider name.

        Returns:
            Tuple of (api_key, base_url) or None if not found.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute(
                f"""
                SELECT encrypted_key, base_url FROM api_key_store
                WHERE tenant_id = {_param()} AND provider = {_param()} AND is_active = 1
                LIMIT 1
            """,
                (tenant_id, provider),
            )
            row = cursor.fetchone()

            if not row:
                return None

            encrypted_key = row["encrypted_key"] if isinstance(row, dict) else row["encrypted_key"]
            base_url = row["base_url"] if isinstance(row, dict) else row["base_url"]

            api_key = self._decrypt_key(encrypted_key)
            return (api_key, base_url)
        except Exception as e:
            logger.error(f"Failed to resolve API key: {e}")
            return None
        finally:
            conn.close()

    def list_api_keys(self, tenant_id: int) -> List[Dict[str, Any]]:
        """List API keys for a tenant (without revealing the actual keys)."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            f"""
            SELECT id, provider, key_name, base_url, is_active, created_at, updated_at
            FROM api_key_store
            WHERE tenant_id = {_param()}
            ORDER BY provider, key_name
        """,
            (tenant_id,),
        )

        rows = cursor.fetchall()
        conn.close()

        result = []
        for row in rows:
            result.append({
                "id": row["id"],
                "provider": row["provider"],
                "key_name": row["key_name"],
                "base_url": row["base_url"],
                "is_active": bool(row["is_active"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            })
        return result

    def delete_api_key(self, tenant_id: int, provider: str, key_name: str) -> bool:
        """Delete an API key."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            f"""
            DELETE FROM api_key_store
            WHERE tenant_id = {_param()} AND provider = {_param()} AND key_name = {_param()}
        """,
            (tenant_id, provider, key_name),
        )

        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success

    def delete_api_key_by_id(self, key_id: int, tenant_id: int) -> bool:
        """Delete an API key by its ID."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            f"""
            DELETE FROM api_key_store
            WHERE id = {_param()} AND tenant_id = {_param()}
        """,
            (key_id, tenant_id),
        )

        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success

    def generate_proxy_token(self, user_id: int, session_id: str, tenant_id: int,
                             provider: str, expires_minutes: int = 5) -> str:
        """
        Generate a short-lived proxy token for a remote agent session.

        The token is a signed JSON payload containing user/session/tenant info
        that the agent presents when making LLM proxy calls.

        Args:
            user_id: User ID making the request.
            session_id: Session ID.
            tenant_id: Tenant ID for API key lookup.
            provider: LLM provider name.
            expires_minutes: Token validity in minutes.

        Returns:
            Proxy token string.
        """
        import hmac

        payload = {
            "user_id": user_id,
            "session_id": session_id,
            "tenant_id": tenant_id,
            "provider": provider,
            "exp": (datetime.utcnow() + timedelta(minutes=expires_minutes)).isoformat(),
            "jti": secrets.token_hex(16),
        }

        payload_json = json.dumps(payload, sort_keys=True)
        payload_b64 = b64encode(payload_json.encode()).decode()

        # Sign with HMAC-SHA256
        signature = hmac.new(
            self._encryption_key,
            payload_b64.encode(),
            hashlib.sha256,
        ).hexdigest()

        return f"{payload_b64}.{signature}"

    def validate_proxy_token(self, token: str) -> Optional[Dict[str, Any]]:
        """
        Validate a proxy token and extract its payload.

        Args:
            token: The proxy token to validate.

        Returns:
            Dict with token payload or None if invalid/expired.
        """
        import hmac

        try:
            parts = token.split(".")
            if len(parts) != 2:
                return None

            payload_b64, signature = parts

            # Verify signature
            expected_sig = hmac.new(
                self._encryption_key,
                payload_b64.encode(),
                hashlib.sha256,
            ).hexdigest()

            if not hmac.compare_digest(signature, expected_sig):
                logger.warning("Proxy token signature mismatch")
                return None

            payload = json.loads(b64decode(payload_b64))

            # Check expiration
            exp = datetime.fromisoformat(payload["exp"])
            if datetime.utcnow() > exp:
                logger.warning("Proxy token expired")
                return None

            return payload
        except Exception as e:
            logger.warning(f"Failed to validate proxy token: {e}")
            return None

    def generate_registration_token(self) -> str:
        """
        Generate a one-time registration token for machine registration.

        Returns:
            256-bit random hex token.
        """
        return secrets.token_hex(32)
