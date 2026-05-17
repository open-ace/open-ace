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
from typing import Any, Optional, Union, cast

from app.repositories.database import DB_PATH, get_database_url, is_postgresql

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

    def _get_encryption_key(self) -> bytes:
        """Get the AES encryption key from environment variable."""
        key_env = os.environ.get("OPENACE_ENCRYPTION_KEY")
        if not key_env:
            secret = os.environ.get("SECRET_KEY")
            if not secret:
                raise RuntimeError(
                    "OPENACE_ENCRYPTION_KEY or SECRET_KEY must be set for API key encryption. "
                    "Refusing to use default key in production."
                )
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
        bool_true = "BOOLEAN DEFAULT TRUE" if is_postgresql() else "INTEGER DEFAULT 1"

        # api_key_store table is created by migration, but ensure it exists for
        # environments that don't run migrations
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS api_key_store (
                id {id_type},
                tenant_id INTEGER,
                provider TEXT NOT NULL,
                key_name TEXT NOT NULL,
                encrypted_key TEXT NOT NULL,
                key_hash TEXT NOT NULL,
                base_url TEXT,
                is_active {bool_true},
                created_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(tenant_id, provider, key_name)
            )
        """)

        conn.commit()
        conn.close()

    def _encrypt_key(self, api_key: str) -> str:
        """Encrypt an API key using Fernet (requires cryptography package)."""
        try:
            import base64

            from cryptography.fernet import Fernet

            f = Fernet(base64.urlsafe_b64encode(self._encryption_key))
            return cast("str", f.encrypt(api_key.encode()).decode())
        except ImportError:
            raise RuntimeError(
                "cryptography package is required for API key encryption. "
                "Install with: pip install cryptography"
            )

    def _decrypt_key(self, encrypted_key: str) -> str:
        """Decrypt an API key (requires cryptography package)."""
        try:
            import base64

            from cryptography.fernet import Fernet

            f = Fernet(base64.urlsafe_b64encode(self._encryption_key))
            return cast("str", f.decrypt(encrypted_key.encode()).decode())
        except ImportError:
            raise RuntimeError(
                "cryptography package is required for API key encryption. "
                "Install with: pip install cryptography"
            )

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
        cli_tools: Optional[str] = None,
        cli_settings: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Store an encrypted API key for a tenant/provider.

        Args:
            tenant_id: Tenant ID.
            provider: Provider name (openai, anthropic, google, etc.).
            key_name: Display name for this key.
            api_key: The plaintext API key to encrypt and store.
            base_url: Optional custom base URL for the provider.
            created_by: User ID who created this key.
            cli_tools: JSON array of CLI tool names (e.g., ["claude-code", "qwen-code"]).
            cli_settings: JSON object with settings for each tool.

        Returns:
            Dict with success status and key info.
        """
        encrypted = self._encrypt_key(api_key)
        key_hash = self._hash_key(api_key)

        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute(
                f"""
                INSERT INTO api_key_store (tenant_id, provider, key_name, encrypted_key, key_hash, base_url, created_by, cli_tools, cli_settings)
                VALUES ({_params(9)})
                ON CONFLICT (tenant_id, provider, key_name) DO UPDATE SET
                    encrypted_key = EXCLUDED.encrypted_key,
                    key_hash = EXCLUDED.key_hash,
                    base_url = EXCLUDED.base_url,
                    is_active = TRUE,
                    cli_tools = EXCLUDED.cli_tools,
                    cli_settings = EXCLUDED.cli_settings,
                    updated_at = CURRENT_TIMESTAMP
            """,
                (
                    tenant_id,
                    provider,
                    key_name,
                    encrypted,
                    key_hash,
                    base_url,
                    created_by,
                    cli_tools,
                    cli_settings,
                ),
            )

            conn.commit()
            logger.info(
                f"Stored API key for tenant {tenant_id}, provider {provider}, name {key_name}"
            )

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

    def resolve_api_key(self, tenant_id: int, provider: str) -> Optional[tuple[str, Optional[str]]]:
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
                WHERE tenant_id = {_param()} AND provider = {_param()} AND is_active = TRUE
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

    def list_api_keys(self, tenant_id: int) -> list[dict[str, Any]]:
        """List API keys for a tenant (without revealing the actual keys)."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            f"""
            SELECT id, provider, key_name, base_url, is_active, created_at, updated_at, cli_tools, cli_settings
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
            result.append(
                {
                    "id": row["id"],
                    "provider": row["provider"],
                    "key_name": row["key_name"],
                    "base_url": row["base_url"],
                    "is_active": bool(row["is_active"]),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "cli_tools": row["cli_tools"],
                    "cli_settings": row["cli_settings"],
                }
            )
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

    def update_api_key_by_id(
        self,
        key_id: int,
        tenant_id: int,
        key_name: Optional[str] = None,
        base_url: Optional[str] = None,
        cli_tools: Optional[str] = None,
        cli_settings: Optional[str] = None,
    ) -> bool:
        """
        Update an API key by its ID.

        Args:
            key_id: The key ID to update.
            tenant_id: Tenant ID for security check.
            key_name: Optional new key name.
            base_url: Optional new base URL.
            cli_tools: Optional JSON array of CLI tool names.
            cli_settings: Optional JSON object with settings for each tool.

        Returns:
            True if updated successfully, False otherwise.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        # Build dynamic UPDATE query
        updates = []
        values: list[Any] = []
        if key_name is not None:
            updates.append(f"key_name = {_param()}")
            values.append(key_name)
        if base_url is not None:
            updates.append(f"base_url = {_param()}")
            values.append(base_url)
        if cli_tools is not None:
            updates.append(f"cli_tools = {_param()}")
            values.append(cli_tools)
        if cli_settings is not None:
            updates.append(f"cli_settings = {_param()}")
            values.append(cli_settings)

        if not updates:
            conn.close()
            return False

        updates.append("updated_at = CURRENT_TIMESTAMP")
        values.extend([key_id, tenant_id])

        cursor.execute(
            f"""
            UPDATE api_key_store
            SET {', '.join(updates)}
            WHERE id = {_param()} AND tenant_id = {_param()}
        """,
            values,
        )

        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success

    def get_cli_settings_for_tool(self, tenant_id: int, tool_name: str) -> Optional[dict[str, Any]]:
        """
        Get CLI settings for a specific tool from active API keys.

        Searches api_key_store where cli_tools contains the tool_name.
        Returns the tool-specific settings from cli_settings, merged with
        the actual API key and base_url.

        Args:
            tenant_id: Tenant ID.
            tool_name: CLI tool name (e.g., "claude-code", "qwen-code").

        Returns:
            Dict with complete settings ready for agent to write to settings.json,
            or None if no matching API key found.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        # Find API key where cli_tools contains the tool_name
        cursor.execute(
            f"""
            SELECT id, provider, encrypted_key, base_url, cli_tools, cli_settings
            FROM api_key_store
            WHERE tenant_id = {_param()} AND is_active = TRUE
            ORDER BY id DESC
            LIMIT 10
        """,
            (tenant_id,),
        )

        rows = cursor.fetchall()
        conn.close()

        # Find matching row
        for row in rows:
            cli_tools_str = row["cli_tools"] or ""
            cli_settings_str = row["cli_settings"] or "{}"

            # Parse cli_tools JSON array
            try:
                cli_tools = json.loads(cli_tools_str) if cli_tools_str else []
            except json.JSONDecodeError:
                cli_tools = []

            if tool_name not in cli_tools:
                continue

            # Found matching key - build settings
            try:
                cli_settings = json.loads(cli_settings_str) if cli_settings_str else {}
            except json.JSONDecodeError:
                cli_settings = {}

            # Get tool-specific settings from cli_settings
            tool_settings = cli_settings.get(tool_name, {})

            # Decrypt the API key
            api_key = self._decrypt_key(row["encrypted_key"])
            base_url = row["base_url"] or ""

            # Build complete settings using CLI adapter logic
            # Import adapters to build settings
            return self._build_cli_settings_for_tool(
                tool_name, tool_settings, api_key, base_url, row["provider"]
            )

        return None

    def _build_cli_settings_for_tool(
        self,
        tool_name: str,
        base_settings: dict,
        api_key: str,
        base_url: str,
        provider: str,
    ) -> dict[str, Any]:
        """
        Build complete CLI settings by merging user settings with API credentials.

        Args:
            tool_name: CLI tool name.
            base_settings: User-configured settings (from cli_settings column).
            api_key: Decrypted API key.
            base_url: Base URL for API requests.
            provider: Provider name (anthropic, openai, etc.).

        Returns:
            Complete settings dict ready for settings.json.
        """
        settings = base_settings.copy()
        settings.setdefault("env", {})

        if tool_name == "claude-code":
            # Claude Code settings format
            settings["env"]["ANTHROPIC_API_KEY"] = api_key
            if base_url:
                settings["env"]["ANTHROPIC_BASE_URL"] = base_url.rstrip("/")
        elif tool_name == "qwen-code":
            # Qwen Code settings (bailian format)
            settings.setdefault("modelProviders", {})
            settings.setdefault("security", {"auth": {"selectedType": "openai"}})
            settings["$version"] = 3

            provider_name = "openai"
            settings["modelProviders"].setdefault(provider_name, [])

            # Determine env key name
            env_key_name = f"{provider_name.upper()}_API_KEY"
            for model_config in settings["modelProviders"].get(provider_name, []):
                if "envKey" in model_config:
                    env_key_name = model_config["envKey"]
                if "baseUrl" not in model_config:
                    model_config["baseUrl"] = base_url.rstrip("/") if base_url else ""

            settings["env"][env_key_name] = api_key

        return settings

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

    def generate_proxy_token(
        self,
        user_id: int,
        session_id: str,
        tenant_id: int,
        provider: str,
        expires_minutes: int = 1440,
        session_type: str = "agent",
    ) -> str:
        """
        Generate a proxy token for a remote agent session.

        The token is a signed JSON payload containing user/session/tenant info
        that the agent presents when making LLM proxy calls.

        Args:
            user_id: User ID making the request.
            session_id: Session ID.
            tenant_id: Tenant ID for API key lookup.
            provider: LLM provider name.
            expires_minutes: Token validity in minutes (default 24 hours).
            session_type: Type of session - "agent" or "terminal".

        Returns:
            Proxy token string.
        """
        import hmac

        payload = {
            "user_id": user_id,
            "session_id": session_id,
            "tenant_id": tenant_id,
            "provider": provider,
            "session_type": session_type,
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

    def validate_proxy_token(self, token: str) -> Optional[dict[str, Any]]:
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

            # Check session is still active (skip for terminal sessions)
            session_id = payload.get("session_id")
            session_type = payload.get("session_type", "agent")
            if session_id and session_type == "agent":
                try:
                    from app.repositories.database import adapt_sql, get_db_connection

                    with get_db_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute(
                            adapt_sql("SELECT status FROM agent_sessions WHERE session_id = ?"),
                            (session_id,),
                        )
                        row = cursor.fetchone()
                        if not row:
                            logger.warning("Proxy token session not found: %s", session_id[:8])
                            return None
                        status = row[0] if isinstance(row, (list, tuple)) else row.get("status")
                        if status not in ("active", "paused"):
                            logger.warning(
                                "Proxy token session not active: %s (status=%s)",
                                session_id[:8],
                                status,
                            )
                            return None
                except Exception as e:
                    logger.warning("Failed to check session status for proxy token: %s", e)

            return cast("Optional[dict[str, Any]]", payload)
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


def get_ddl_statements() -> list[str]:
    """Return DDL statements for API key proxy tables."""
    id_type = "SERIAL PRIMARY KEY" if is_postgresql() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    bool_true = "BOOLEAN DEFAULT TRUE" if is_postgresql() else "INTEGER DEFAULT 1"
    return [
        f"""
        CREATE TABLE IF NOT EXISTS api_key_store (
            id {id_type},
            tenant_id INTEGER,
            provider TEXT NOT NULL,
            key_name TEXT NOT NULL,
            encrypted_key TEXT NOT NULL,
            key_hash TEXT NOT NULL,
            base_url TEXT,
            is_active {bool_true},
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(tenant_id, provider, key_name)
        )
        """,
    ]


# Module-level singleton
_instance: Optional[APIKeyProxyService] = None


def get_api_key_proxy_service() -> APIKeyProxyService:
    """Get the module-level APIKeyProxyService singleton."""
    global _instance
    if _instance is None:
        _instance = APIKeyProxyService()
    return _instance
