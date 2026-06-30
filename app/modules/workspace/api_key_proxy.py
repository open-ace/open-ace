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
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Union, cast

from app.modules.workspace.api_key_router import APIKeyRouter
from app.repositories.database import DB_PATH, get_database_url, is_postgresql
from app.utils.tool_names import TOOL_NAME_ALIASES, normalize_tool_name

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib

logger = logging.getLogger(__name__)

# Environment variable keys that contain API credentials.
# These must NEVER be written to settings.json — they are injected
# via environment variables by the remote agent at process launch time.
# Keep in sync with remote-agent/constants.py.
_SENSITIVE_ENV_KEYS = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
    }
)


def _collect_dynamic_env_keys(settings: dict[str, Any]) -> set[str]:
    """Collect dynamic envKey names from modelProviders entries.

    Qwen Code's modelProviders can specify custom envKey names like
    "ZAI_API_KEY" or "BAILIAN_CODING_PLAN_API_KEY". These must also
    be stripped from the env block to prevent API key leakage.

    Keep in sync with remote-agent/constants.py:collect_dynamic_env_keys().
    """
    dynamic: set[str] = set()
    for provider_models in settings.get("modelProviders", {}).values():
        if isinstance(provider_models, list):
            for model in provider_models:
                if isinstance(model, dict) and isinstance(model.get("envKey"), str):
                    dynamic.add(model["envKey"])
    return dynamic


def _parse_codex_settings(raw_settings: Any, *, raise_on_error: bool = False) -> dict[str, Any]:
    """Parse stored Codex settings from TOML string or dict form."""
    if isinstance(raw_settings, dict):
        return raw_settings.copy()
    if isinstance(raw_settings, str):
        try:
            parsed = tomllib.loads(raw_settings)
        except tomllib.TOMLDecodeError as exc:
            if raise_on_error:
                raise ValueError(f"Invalid Codex settings TOML: {exc}") from exc
            logger.warning("Invalid Codex settings TOML in api_key_store: %s", exc)
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def validate_cli_settings_payload(raw_cli_settings: Optional[str]) -> Optional[str]:
    """Validate CLI settings payload before storing it."""
    if not raw_cli_settings:
        return None

    try:
        parsed = json.loads(raw_cli_settings)
    except json.JSONDecodeError as exc:
        return f"cli_settings must be valid JSON: {exc}"

    if not isinstance(parsed, dict):
        return "cli_settings must be a JSON object"

    codex_settings = parsed.get("codex-cli")
    if codex_settings is not None:
        if not isinstance(codex_settings, (str, dict)):
            return "codex-cli settings must be a TOML string or object"
        try:
            _parse_codex_settings(codex_settings, raise_on_error=True)
        except ValueError as exc:
            return str(exc)

    return None


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
        self._router = APIKeyRouter()

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
                is_active {bool_true},
                created_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                cli_tools TEXT,
                cli_settings TEXT,
                scope TEXT DEFAULT 'remote',
                priority INTEGER DEFAULT 0,
                weight INTEGER DEFAULT 100,
                UNIQUE(tenant_id, provider, key_name)
            )
        """
        )

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
        scope: str = "remote",
        priority: int = 0,
        weight: int = 100,
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
                INSERT INTO api_key_store (tenant_id, provider, key_name, encrypted_key, key_hash, base_url, created_by, cli_tools, cli_settings, scope, priority, weight)
                VALUES ({_params(12)})
                ON CONFLICT (tenant_id, provider, key_name) DO UPDATE SET
                    encrypted_key = EXCLUDED.encrypted_key,
                    key_hash = EXCLUDED.key_hash,
                    base_url = EXCLUDED.base_url,
                    is_active = TRUE,
                    cli_tools = EXCLUDED.cli_tools,
                    cli_settings = EXCLUDED.cli_settings,
                    scope = EXCLUDED.scope,
                    priority = EXCLUDED.priority,
                    weight = EXCLUDED.weight,
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
                    scope,
                    priority,
                    weight,
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

        Backward-compatible method that delegates to resolve_api_key_for_scope
        with scope='remote'.

        Args:
            tenant_id: Tenant ID.
            provider: Provider name.

        Returns:
            Tuple of (api_key, base_url) or None if not found.
        """
        result = self.resolve_api_key_for_scope(tenant_id, provider, scope="remote")
        if result is None:
            return None
        return (result[0], result[1])

    def resolve_api_key_for_scope(
        self,
        tenant_id: int,
        provider: str,
        scope: str = "remote",
        exclude_key_ids: Optional[set[int]] = None,
    ) -> Optional[tuple[str, Optional[str], int, Optional[str]]]:
        """
        Resolve and decrypt an API key for a tenant/provider with scope filtering
        and multi-key scheduling.

        Args:
            tenant_id: Tenant ID.
            provider: Provider name.
            scope: Key scope — 'local', 'remote', or 'shared' matches both.
            exclude_key_ids: Key IDs to exclude (for failover retries).

        Returns:
            Tuple of (api_key, base_url, key_id, cli_settings) or None if not found.
        """
        from app.modules.workspace.api_key_router import APIKeyRouter

        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            # Query keys matching scope (exact match or 'shared')
            if is_postgresql():
                cursor.execute(
                    """
                    SELECT id, encrypted_key, base_url, priority, weight, cli_settings
                    FROM api_key_store
                    WHERE tenant_id = %s AND provider = %s AND is_active = TRUE
                      AND (scope = %s OR scope = 'shared')
                    """,
                    (tenant_id, provider, scope),
                )
            else:
                cursor.execute(
                    """
                    SELECT id, encrypted_key, base_url, priority, weight, cli_settings
                    FROM api_key_store
                    WHERE tenant_id = ? AND provider = ? AND is_active = TRUE
                      AND (scope = ? OR scope = 'shared')
                    """,
                    (tenant_id, provider, scope),
                )

            rows = cursor.fetchall()

            if not rows:
                return None

            # Build candidates for the router
            candidates = []
            for row in rows:
                row_dict = row if isinstance(row, dict) else dict(row)
                encrypted_key = row_dict["encrypted_key"]
                base_url = row_dict["base_url"]
                cli_settings = row_dict.get("cli_settings")
                try:
                    decrypted = self._decrypt_key(encrypted_key)
                except Exception as e:
                    logger.warning("Failed to decrypt key id=%s: %s", row_dict["id"], e)
                    continue
                candidates.append(
                    {
                        "id": row_dict["id"],
                        "api_key": decrypted,
                        "base_url": base_url,
                        "priority": row_dict.get("priority") or 0,
                        "weight": row_dict.get("weight") or 100,
                        "cli_settings": cli_settings,
                    }
                )

            if not candidates:
                return None

            router = APIKeyRouter()
            selected = router.select_key(candidates, exclude_key_ids=exclude_key_ids)
            if selected is None:
                return None

            return (
                selected["api_key"],
                selected["base_url"],
                selected["id"],
                selected.get("cli_settings"),
            )
        except Exception as e:
            logger.error("Failed to resolve API key for scope: %s", e)
            return None
        finally:
            conn.close()

    def list_api_keys(self, tenant_id: int) -> list[dict[str, Any]]:
        """List API keys for a tenant (without revealing the actual keys)."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            f"""
            SELECT id, provider, key_name, base_url, is_active, created_at, updated_at, cli_tools, cli_settings, scope, priority, weight
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
                    "scope": row["scope"] or "remote",
                    "priority": row["priority"] if row["priority"] is not None else 0,
                    "weight": row["weight"] if row["weight"] is not None else 100,
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
        is_active: Optional[bool] = None,
        scope: Optional[str] = None,
        priority: Optional[int] = None,
        weight: Optional[int] = None,
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
            is_active: Optional active status.
            scope: Optional scope ('local', 'remote', 'shared').
            priority: Optional priority (higher = preferred).
            weight: Optional weight for weighted random selection.

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
        if is_active is not None:
            updates.append(f"is_active = {_param()}")
            values.append(is_active if is_postgresql() else (1 if is_active else 0))
        if scope is not None:
            updates.append(f"scope = {_param()}")
            values.append(scope)
        if priority is not None:
            updates.append(f"priority = {_param()}")
            values.append(priority)
        if weight is not None:
            updates.append(f"weight = {_param()}")
            values.append(weight)

        if not updates:
            conn.close()
            return False

        updates.append("updated_at = CURRENT_TIMESTAMP")
        values.extend([key_id, tenant_id])

        cursor.execute(
            f"""
            UPDATE api_key_store
            SET {", ".join(updates)}
            WHERE id = {_param()} AND tenant_id = {_param()}
        """,
            values,
        )

        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success

    def get_cli_settings_for_tool(
        self,
        tenant_id: int,
        tool_name: str,
        scope: str = "remote",
    ) -> Optional[dict[str, Any]]:
        """
        Get CLI settings for a specific tool from active API keys.

        Searches api_key_store where cli_tools contains the tool_name.
        When multiple matching keys exist, merges modelProviders from all
        keys so the resulting settings contain the union of available models.

        Tool name normalization is applied so that aliases like "codex-cli"
        and "codex" match each other, and "claude-code" matches "claude".

        Args:
            tenant_id: Tenant ID.
            tool_name: CLI tool name (e.g., "claude-code", "qwen-code", "codex", "codex-cli").
            scope: Key scope filter (default "remote").

        Returns:
            Dict with complete settings ready for agent to write to settings.json,
            or None if no matching API key found.
        """
        ranked_settings = self._collect_tool_key_settings(tenant_id, tool_name, scope)
        if not ranked_settings:
            return None

        # Single key fast path — backward compatible
        if len(ranked_settings) == 1:
            return ranked_settings[0][1]

        # Multiple keys — merge modelProviders from all keys
        return self._merge_multi_key_settings(ranked_settings)

    def _collect_tool_key_settings(
        self,
        tenant_id: int,
        tool_name: str,
        scope: str = "remote",
    ) -> list[tuple[tuple[int, int, int], dict[str, Any]]]:
        """Return per-key settings for every active key matching a tool.

        Shared backing store for :meth:`get_cli_settings_for_tool` (which merges
        the results for agent settings.json) and :meth:`get_tool_models` (which
        unions models across keys). Returns settings ranked by
        ``(-priority, -weight, key_id)`` ascending (highest-priority key first),
        or an empty list when no key matches. Tool-name normalization is applied
        so aliases ("codex"/"codex-cli", "claude"/"claude-code") match.
        """
        canonical_tool = normalize_tool_name(tool_name)

        conn = self._get_connection()
        cursor = conn.cursor()

        # Find API keys where cli_tools contains the tool_name
        cursor.execute(
            f"""
            SELECT id, provider, encrypted_key, base_url, cli_tools, cli_settings,
                   priority, weight
            FROM api_key_store
            WHERE tenant_id = {_param()} AND is_active = TRUE
              AND (scope = {_param()} OR scope = 'shared')
            ORDER BY priority DESC, weight DESC, id ASC
            LIMIT 50
        """,
            (tenant_id, scope),
        )

        rows = cursor.fetchall()
        conn.close()

        ranked_settings: list[tuple[tuple[int, int, int], dict[str, Any]]] = []
        for row in rows:
            cli_tools_str = row["cli_tools"] or ""
            cli_settings_str = row["cli_settings"] or "{}"

            # Parse cli_tools JSON array
            try:
                cli_tools = json.loads(cli_tools_str) if cli_tools_str else []
            except json.JSONDecodeError:
                cli_tools = []

            # Normalize both sides so "codex" matches "codex-cli", etc.
            if canonical_tool not in {normalize_tool_name(t) for t in cli_tools}:
                continue

            key_id = int(row["id"])
            priority = int(row.get("priority") or 0)
            weight = int(row.get("weight") or 100)

            # Build settings for this key
            try:
                cli_settings = json.loads(cli_settings_str) if cli_settings_str else {}
            except json.JSONDecodeError:
                cli_settings = {}

            # Get tool-specific settings from cli_settings. The cli_settings
            # subkey may be stored under any of the tool's alias forms — e.g.
            # qwen-code keys sometimes store settings under "qwen-code" even
            # when the request tool_name is "qwen-code-cli" (canonical "qwen").
            # Try the request name, then every alias, so a mismatch between the
            # requested form and the stored key doesn't silently drop the model
            # list (which left qwen-code/codex dropdowns showing only "default").
            alias_candidates = [tool_name]
            alias_candidates.extend(TOOL_NAME_ALIASES.get(canonical_tool, []))
            seen_keys: set[str] = set()
            tool_settings: Any = {}
            for candidate in alias_candidates:
                key = candidate.strip().lower()
                if not key or key in seen_keys:
                    continue
                seen_keys.add(key)
                value = cli_settings.get(candidate) or cli_settings.get(key)
                if value:
                    tool_settings = value
                    break

            settings = self._build_cli_settings_for_tool(tool_name, tool_settings)
            rank = (-priority, -weight, key_id)
            ranked_settings.append((rank, settings))

        return ranked_settings

    @staticmethod
    def _collect_model_entries(
        ranked_settings: list[tuple[tuple[int, int, int], dict[str, Any]]],
    ) -> dict[str, dict[str, list[tuple[tuple[int, int, int], dict[str, Any]]]]]:
        """Collect per-provider model entries from ranked settings.

        Iterates over all ranked settings and extracts models from
        ``modelProviders.*``, grouping them by (provider_name, model_id).
        Duplicate model IDs within the same key are ignored.

        Args:
            ranked_settings: List of ``(rank_tuple, settings_dict)`` pairs.

        Returns:
            Nested dict: ``{provider_name: {model_id: [(rank, model_dict), ...]}}``
        """
        provider_model_entries: dict[
            str, dict[str, list[tuple[tuple[int, int, int], dict[str, Any]]]]
        ] = {}
        for rank, settings in ranked_settings:
            for provider_name, provider_models in settings.get("modelProviders", {}).items():
                if not isinstance(provider_models, list):
                    continue
                model_map = provider_model_entries.setdefault(provider_name, {})
                seen_in_key: set[str] = set()
                for raw_model in provider_models:
                    if not isinstance(raw_model, dict):
                        continue
                    model_id = raw_model.get("id")
                    if not isinstance(model_id, str) or not model_id:
                        continue
                    # Skip duplicate model IDs within the same key
                    if model_id in seen_in_key:
                        continue
                    seen_in_key.add(model_id)
                    model_map.setdefault(model_id, []).append((rank, deepcopy(raw_model)))
        return provider_model_entries

    @staticmethod
    def _dedup_and_sort_models(
        model_entries: dict[str, list[tuple[tuple[int, int, int], dict[str, Any]]]],
    ) -> list[dict[str, Any]]:
        """Deduplicate models by ID and sort by rank for deterministic output.

        For each model ID, the config from the highest-priority entry (lowest
        rank tuple) is used as the canonical version.

        Args:
            model_entries: ``{model_id: [(rank, model_dict), ...]}``

        Returns:
            Sorted list of deduplicated model dicts.
        """
        ranked: list[tuple[tuple[int, int, int], str, dict[str, Any]]] = []
        for model_id, entries in model_entries.items():
            canonical_rank, canonical_model = sorted(entries, key=lambda x: x[0])[0]
            ranked.append((canonical_rank, model_id, canonical_model))
        ranked.sort(key=lambda x: (x[0], x[1]))
        return [model for _, _, model in ranked]

    def _merge_multi_key_settings(
        self,
        ranked_settings: list[tuple[tuple[int, int, int], dict[str, Any]]],
    ) -> dict[str, Any]:
        """Merge settings from multiple API keys, unioning modelProviders.

        Base settings (theme, permissions, etc.) come from the highest-priority
        key.  ``modelProviders`` is merged: models from all keys are collected,
        deduplicated by model ID (using the highest-priority key's config), and
        sorted by rank for deterministic output.

        Args:
            ranked_settings: List of ``(rank_tuple, settings_dict)`` pairs.
                ``rank_tuple`` is ``(-priority, -weight, key_id)`` so that
                sorting ascending puts the best key first.

        Returns:
            Merged settings dict with the union of all models.
        """
        # Sort so the highest-priority key comes first
        sorted_settings = sorted(ranked_settings, key=lambda item: item[0])
        base_settings = deepcopy(sorted_settings[0][1])

        # Collect per-provider model entries (with per-key dedup)
        provider_model_entries = self._collect_model_entries(sorted_settings)

        # If no modelProviders found, return base settings as-is
        if not provider_model_entries:
            return base_settings

        # Rebuild each provider's model list with deduplication
        merged_providers: dict[str, list[dict[str, Any]]] = {}
        for provider_name, pm_map in provider_model_entries.items():
            merged_providers[provider_name] = self._dedup_and_sort_models(pm_map)

        base_settings["modelProviders"] = merged_providers
        if merged_providers and "$version" not in base_settings:
            base_settings["$version"] = 3

        return base_settings

    def _list_tool_key_rows(
        self,
        tenant_id: int,
        provider: str,
        tool_name: str,
        scope: str,
    ) -> list[dict[str, Any]]:
        """List active API keys for a tool within a scope/shared pool."""
        canonical_tool = normalize_tool_name(tool_name)
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT id, provider, encrypted_key, base_url, cli_tools, cli_settings, priority, weight, scope
                FROM api_key_store
                WHERE tenant_id = {_param()} AND provider = {_param()} AND is_active = TRUE
                  AND (scope = {_param()} OR scope = 'shared')
                ORDER BY priority DESC, weight DESC, id ASC
            """,
                (tenant_id, provider, scope),
            )
            rows = cursor.fetchall()
        finally:
            conn.close()

        matches: list[dict[str, Any]] = []
        for row in rows:
            cli_tools_str = row["cli_tools"] or ""
            try:
                cli_tools = json.loads(cli_tools_str) if cli_tools_str else []
            except json.JSONDecodeError:
                cli_tools = []
            if canonical_tool not in {normalize_tool_name(t) for t in cli_tools}:
                continue
            matches.append(dict(row))
        return matches

    def _get_tool_settings_from_row(self, row: dict[str, Any], tool_name: str) -> dict[str, Any]:
        """Extract non-sensitive tool settings from a DB row."""
        canonical_tool = normalize_tool_name(tool_name)
        cli_settings_str = row.get("cli_settings") or "{}"
        try:
            cli_settings = json.loads(cli_settings_str) if cli_settings_str else {}
        except json.JSONDecodeError:
            cli_settings = {}
        base_settings = cli_settings.get(tool_name, cli_settings.get(canonical_tool, {}))
        return self._build_cli_settings_for_tool(tool_name, base_settings)

    def get_tool_model_pool(
        self,
        tenant_id: int,
        tool_name: str,
        scope: str = "remote",
        provider: str = "openai",
    ) -> dict[str, Any]:
        """Build a deterministic HA model pool for a tool within a scope."""
        rows = self._list_tool_key_rows(tenant_id, provider, tool_name, scope)
        if not rows:
            return {
                "provider": provider,
                "tool_name": tool_name,
                "scope": scope,
                "models": [],
                "candidate_keys": [],
                "model_key_ids": {},
                "settings": {},
                "empty_reason": f"No active {tool_name} API keys configured for scope '{scope}'",
            }

        candidate_keys: list[dict[str, Any]] = []
        model_entries: dict[str, list[tuple[tuple[int, int, int], dict[str, Any]]]] = {}
        model_key_ids: dict[str, list[int]] = {}
        ranked_settings: list[tuple[tuple[int, int, int], dict[str, Any]]] = []

        for row in rows:
            key_id = int(row["id"])
            priority = int(row.get("priority") or 0)
            weight = int(row.get("weight") or 100)
            settings = self._get_tool_settings_from_row(row, tool_name)
            ranked_settings.append(((-priority, -weight, key_id), deepcopy(settings)))

            provider_models = settings.get("modelProviders", {}).get("openai", [])
            supported_model_ids: list[str] = []
            if isinstance(provider_models, list):
                seen_in_key: set[str] = set()
                for raw_model in provider_models:
                    if not isinstance(raw_model, dict):
                        continue
                    model_id = raw_model.get("id")
                    if not isinstance(model_id, str) or not model_id:
                        continue
                    supported_model_ids.append(model_id)
                    # Skip duplicate model IDs within the same key
                    if model_id in seen_in_key:
                        continue
                    seen_in_key.add(model_id)
                    rank = (-priority, -weight, key_id)
                    model_entries.setdefault(model_id, []).append((rank, deepcopy(raw_model)))
                    model_key_ids.setdefault(model_id, []).append(key_id)

            candidate_keys.append(
                {
                    "key_id": key_id,
                    "priority": priority,
                    "weight": weight,
                    "scope": row.get("scope") or scope,
                    "supported_model_ids": sorted(set(supported_model_ids)),
                }
            )

        models = self._dedup_and_sort_models(model_entries)

        base_settings = (
            deepcopy(sorted(ranked_settings, key=lambda item: item[0])[0][1])
            if ranked_settings
            else {}
        )
        model_providers = base_settings.setdefault("modelProviders", {})
        if not isinstance(model_providers, dict):
            model_providers = {}
            base_settings["modelProviders"] = model_providers
        model_providers["openai"] = models
        if models and "$version" not in base_settings:
            base_settings["$version"] = 3

        empty_reason = None
        if not models:
            empty_reason = f"Current {tool_name} API keys do not configure any models"

        return {
            "provider": provider,
            "tool_name": tool_name,
            "scope": scope,
            "models": models,
            "candidate_keys": candidate_keys,
            "model_key_ids": {
                model_id: sorted(set(key_ids)) for model_id, key_ids in model_key_ids.items()
            },
            "settings": base_settings,
            "empty_reason": empty_reason,
        }

    def get_tool_models(
        self,
        tenant_id: int,
        tool_name: str,
        scope: str = "remote",
    ) -> dict[str, Any]:
        """Return the available models for a tool, provider-agnostic.

        Unlike :meth:`get_tool_model_pool` (which filters ``WHERE provider =
        'openai'`` and reads only ``modelProviders.openai``), this queries keys
        by ``cli_tools`` membership and extracts models from wherever the tool
        stores them. This is what the model *dropdown* should use; the pool
        method remains the source of truth for functional HA routing.

        Each tool stores models differently (see frontend templates in
        ``APIKeyManagement.tsx`` and remote-agent ``cli_settings.py``):

        - **qwen / codex**: ``settings["modelProviders"]``, possibly under
          several provider subkeys (openai, anthropic, ...) — flatten all.
        - **claude**: ``settings["env"]["ANTHROPIC_MODEL"]`` and the
          ``ANTHROPIC_DEFAULT_(SONNET|HAIKU)_MODEL`` variants — claude-code has
          no ``modelProviders`` block.
        - **zcode**: top-level ``settings["model"]`` (``{main, lite}`` or a
          bare string), with values like ``"zai/glm-5.2"`` — strip the
          ``provider/`` prefix.

        Models are extracted from **every** matching key and unioned (deduped
        by id), so a tenant with several claude-code/zcode keys sees the union
        of all their models — not just the highest-priority key's.

        Returns a dict shaped like ``get_tool_model_pool``'s:
        ``{"models": [{...}, ...], "empty_reason": str | None}``.
        """
        ranked_settings = self._collect_tool_key_settings(tenant_id, tool_name, scope)
        if not ranked_settings:
            return {
                "models": [],
                "empty_reason": (f"No active {tool_name} API keys configured for scope '{scope}'"),
            }

        canonical = normalize_tool_name(tool_name)
        models: list[dict[str, Any]] = []

        # Walk every matching key (highest priority first) and union its models.
        for _rank, settings in ranked_settings:
            models.extend(self._extract_models_for_tool(canonical, settings))

        # Dedup by id while preserving discovery order (highest-priority key wins).
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for model in models:
            mid = str(model.get("id"))
            if mid in seen:
                continue
            seen.add(mid)
            unique.append(model)

        return {
            "models": unique,
            "empty_reason": (
                None if unique else f"Current {tool_name} API keys do not configure any models"
            ),
        }

    @staticmethod
    def _extract_models_for_tool(canonical: str, settings: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract model entries from one key's settings for a canonical tool."""
        if canonical == "claude":
            # claude-code configures models via env vars, not modelProviders.
            env = settings.get("env") or {}
            models: list[dict[str, Any]] = []
            for key in (
                "ANTHROPIC_MODEL",
                "ANTHROPIC_DEFAULT_SONNET_MODEL",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL",
            ):
                value = env.get(key)
                if isinstance(value, str) and value.strip():
                    models.append({"id": value.strip(), "name": value.strip()})
            return models

        if canonical == "zcode":
            # zcode stores the active model(s) under a top-level `model` field.
            raw_model = settings.get("model")
            candidates: list[str] = []
            if isinstance(raw_model, dict):
                candidates = [v for v in raw_model.values() if isinstance(v, str)]
            elif isinstance(raw_model, str):
                candidates = [raw_model]
            models = []
            for value in candidates:
                # Values look like "zai/glm-5.2"; drop the provider/ prefix.
                name = value.split("/", 1)[1] if "/" in value else value
                name = name.strip()
                if name:
                    models.append({"id": name, "name": name})
            return models

        if canonical == "codex":
            # codex settings are stored as TOML (parsed to a dict). Models live
            # in a top-level ``model`` field (the active model) and optionally
            # model ids under ``model_providers.*`` entries. Unlike qwen's
            # camelCase ``modelProviders.<provider> = [{id,...}]`` list shape,
            # codex's TOML uses snake_case ``model_providers`` whose values are
            # provider definitions, so the generic branch below never matched.
            codex_models: list[dict[str, Any]] = []
            seen_codex: set[str] = set()
            top_model = settings.get("model")
            if isinstance(top_model, str) and top_model.strip():
                mid = top_model.strip()
                codex_models.append({"id": mid, "name": mid})
                seen_codex.add(mid)
            for provider in (settings.get("model_providers") or {}).values():
                if isinstance(provider, dict):
                    mid = str(provider.get("id") or provider.get("model") or "").strip()
                    if mid and mid not in seen_codex:
                        codex_models.append({"id": mid, "name": mid})
                        seen_codex.add(mid)
            return codex_models

        # qwen / codex / future tools: flatten every modelProviders subkey.
        models = []
        for provider_models in (settings.get("modelProviders") or {}).values():
            if not isinstance(provider_models, list):
                continue
            for entry in provider_models:
                if isinstance(entry, dict) and entry.get("id"):
                    models.append({"id": entry["id"], "name": entry.get("name") or entry["id"]})
        return models

    def _build_cli_settings_for_tool(
        self,
        tool_name: str,
        base_settings: Any,
    ) -> dict[str, Any]:
        """
        Build CLI settings containing only non-sensitive configuration.

        API keys and base URLs are intentionally NOT included — they are
        injected via environment variables at process launch time.

        Args:
            tool_name: CLI tool name.
            base_settings: User-configured settings (from cli_settings column).

        Returns:
            Settings dict with non-sensitive config only.
        """
        canonical_tool = normalize_tool_name(tool_name)
        if canonical_tool == "codex":
            settings = _parse_codex_settings(base_settings)
        elif isinstance(base_settings, dict):
            settings = base_settings.copy()
        else:
            settings = {}
        all_sensitive = _SENSITIVE_ENV_KEYS | _collect_dynamic_env_keys(settings)

        # Strip any API credential fields that the user may have
        # accidentally included in the UI.
        env = settings.get("env", {})
        if env:
            env = {k: v for k, v in env.items() if k not in all_sensitive}
            settings["env"] = env

        # Strip baseUrl from modelProviders entries (qwen-code)
        for provider_models in settings.get("modelProviders", {}).values():
            if isinstance(provider_models, list):
                for model in provider_models:
                    if isinstance(model, dict):
                        model.pop("baseUrl", None)

        return settings

    def resolve_api_key_from_key_ids(
        self,
        tenant_id: int,
        provider: str,
        key_ids: list[int],
        exclude_key_ids: Optional[set[int]] = None,
    ) -> Optional[tuple[str, Optional[str], int, Optional[str]]]:
        """Resolve a real API key from an allowed key-id subset using HA routing.

        Returns:
            Tuple of (api_key, base_url, key_id, cli_settings) or None if not found.
        """
        normalized_ids = sorted({int(key_id) for key_id in key_ids if key_id is not None})
        if not normalized_ids:
            return None

        placeholders = _params(len(normalized_ids))
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT id, encrypted_key, base_url, priority, weight, cli_settings
                FROM api_key_store
                WHERE tenant_id = {_param()} AND provider = {_param()} AND is_active = TRUE
                  AND id IN ({placeholders})
            """,
                (tenant_id, provider, *normalized_ids),
            )
            rows = cursor.fetchall()
        finally:
            conn.close()

        candidates: list[dict[str, Any]] = []
        for row in rows:
            try:
                api_key = self._decrypt_key(row["encrypted_key"])
            except Exception as exc:
                logger.warning("Failed to decrypt API key %s: %s", row["id"], exc)
                continue
            candidates.append(
                {
                    "id": int(row["id"]),
                    "priority": int(row.get("priority") or 0),
                    "weight": int(row.get("weight") or 100),
                    "api_key": api_key,
                    "base_url": row.get("base_url"),
                    "cli_settings": row.get("cli_settings"),
                }
            )

        selected = self._router.select_key(candidates, exclude_key_ids=exclude_key_ids)
        if not selected:
            return None
        return (
            cast("str", selected["api_key"]),
            cast("Optional[str]", selected.get("base_url")),
            int(selected["id"]),
            cast("Optional[str]", selected.get("cli_settings")),
        )

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
        extra_payload: Optional[dict[str, Any]] = None,
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
            "exp": (
                datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=expires_minutes)
            ).isoformat(),
            "jti": secrets.token_hex(16),
        }
        if extra_payload:
            payload.update(extra_payload)

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

            session_id = payload.get("session_id")
            session_type = payload.get("session_type", "agent")
            user_id = payload.get("user_id")

            # For WebUI sessions, check instance alive status instead of fixed expiration
            if session_id and session_id.startswith("webui:") and user_id:
                from app.services.webui_manager import get_webui_manager

                manager = get_webui_manager()
                instance = manager.get_user_instance(user_id)
                if instance and instance.is_alive():
                    # Instance alive, skip expiration check
                    pass
                else:
                    logger.warning(
                        "WebUI instance not alive for session: %s, user_id: %s",
                        session_id,
                        user_id,
                    )
                    return None
            else:
                # For other session types (agent), check fixed expiration time
                exp = datetime.fromisoformat(payload["exp"])
                if datetime.now(timezone.utc).replace(tzinfo=None) > exp:
                    logger.warning("Proxy token expired")
                    return None

            # Check session is still active (skip for terminal sessions)
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
            cli_tools TEXT,
            cli_settings TEXT,
            scope TEXT DEFAULT 'remote',
            priority INTEGER DEFAULT 0,
            weight INTEGER DEFAULT 100,
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
