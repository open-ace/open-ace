"""
Open ACE - SSO Manager

Manages SSO providers and authentication sessions.
"""

import json
import logging
import secrets
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, cast

from app.modules.sso.oauth2 import OAuth2Provider
from app.modules.sso.oidc import OIDCProvider, get_provider_class
from app.modules.sso.provider import (
    SSOAuthResult,
    SSOProvider,
    SSOProviderConfig,
    get_provider_config,
)
from app.repositories.database import (
    Database,
    adapt_boolean_condition,
    adapt_boolean_value,
    is_postgresql,
)

logger = logging.getLogger(__name__)


class SSOManager:
    """
    SSO Manager for handling authentication across multiple providers.

    Features:
    - Register and manage multiple SSO providers
    - Handle OAuth2/OIDC authentication flows
    - Store and retrieve SSO sessions
    - Link SSO identities to local users
    """

    # Session expiration time (24 hours)
    SESSION_EXPIRATION_HOURS = 24

    def __init__(self, db: Optional[Database] = None):
        """
        Initialize SSO manager.

        Args:
            db: Optional Database instance.
        """
        self.db = db or Database()
        self._providers: dict[str, SSOProvider] = {}
        self._providers_lock = threading.Lock()

    def _ensure_tables(self) -> None:
        """Ensure SSO-related tables exist."""
        with self.db.connection() as conn:
            cursor = conn.cursor()

            # Use SERIAL for PostgreSQL, AUTOINCREMENT for SQLite
            id_type = (
                "SERIAL PRIMARY KEY"
                if self.db.is_postgresql
                else "INTEGER PRIMARY KEY AUTOINCREMENT"
            )
            bool_true = "BOOLEAN DEFAULT TRUE" if self.db.is_postgresql else "INTEGER DEFAULT 1"

            # SSO providers table
            cursor.execute(
                f"""
                CREATE TABLE IF NOT EXISTS sso_providers (
                    id {id_type},
                    name TEXT UNIQUE NOT NULL,
                    provider_type TEXT NOT NULL,
                    config TEXT NOT NULL,
                    tenant_id INTEGER,
                    is_active {bool_true},
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (tenant_id) REFERENCES tenants(id)
                )
            """
            )

            # SSO identities table (links SSO users to local users)
            cursor.execute(
                f"""
                CREATE TABLE IF NOT EXISTS sso_identities (
                    id {id_type},
                    user_id INTEGER NOT NULL,
                    provider_name TEXT NOT NULL,
                    provider_user_id TEXT NOT NULL,
                    provider_data TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_used_at TIMESTAMP,
                    UNIQUE(provider_name, provider_user_id),
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """
            )

            # SSO sessions table
            cursor.execute(
                f"""
                CREATE TABLE IF NOT EXISTS sso_sessions (
                    id {id_type},
                    session_token TEXT UNIQUE NOT NULL,
                    user_id INTEGER NOT NULL,
                    provider_name TEXT NOT NULL,
                    access_token TEXT,
                    refresh_token TEXT,
                    expires_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """
            )

            # Create indexes
            indexes = [
                "CREATE INDEX IF NOT EXISTS idx_sso_providers_tenant ON sso_providers(tenant_id)",
                "CREATE INDEX IF NOT EXISTS idx_sso_identities_user ON sso_identities(user_id)",
                "CREATE INDEX IF NOT EXISTS idx_sso_identities_provider ON sso_identities(provider_name, provider_user_id)",
                "CREATE INDEX IF NOT EXISTS idx_sso_sessions_token ON sso_sessions(session_token)",
                "CREATE INDEX IF NOT EXISTS idx_sso_sessions_user ON sso_sessions(user_id)",
            ]
            for idx in indexes:
                cursor.execute(idx)

            conn.commit()

    def register_provider(
        self,
        name: str,
        provider_type: str,
        client_id: str,
        client_secret: str,
        authorization_url: str,
        token_url: str,
        userinfo_url: Optional[str] = None,
        redirect_uri: Optional[str] = None,
        scope: Optional[list[str]] = None,
        issuer_url: Optional[str] = None,
        tenant_id: Optional[int] = None,
        extra_params: Optional[dict[str, Any]] = None,
    ) -> bool:
        """
        Register a new SSO provider.

        Args:
            name: Provider name (e.g., 'google', 'github').
            provider_type: Provider type ('oauth2' or 'oidc').
            client_id: OAuth client ID.
            client_secret: OAuth client secret.
            authorization_url: Authorization endpoint URL.
            token_url: Token endpoint URL.
            userinfo_url: User info endpoint URL.
            redirect_uri: Redirect URI for callbacks.
            scope: OAuth scopes.
            issuer_url: OIDC issuer URL.
            tenant_id: Associated tenant ID.
            extra_params: Additional parameters.

        Returns:
            bool: True if successful.
        """
        config = SSOProviderConfig(
            name=name,
            provider_type=provider_type,
            client_id=client_id,
            client_secret=client_secret,
            authorization_url=authorization_url,
            token_url=token_url,
            userinfo_url=userinfo_url,
            redirect_uri=redirect_uri,
            scope=scope or ["openid", "profile", "email"],
            issuer_url=issuer_url,
            tenant_id=tenant_id,
            extra_params=extra_params or {},
        )

        try:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            self.db.execute(
                """
                INSERT INTO sso_providers
                (name, provider_type, config, tenant_id)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    provider_type = ?,
                    config = ?,
                    tenant_id = ?,
                    updated_at = ?
            """,
                (
                    name,
                    provider_type,
                    json.dumps(config.__dict__),
                    tenant_id,
                    provider_type,
                    json.dumps(config.__dict__),
                    tenant_id,
                    now,
                ),
            )

            # Clear cached provider
            with self._providers_lock:
                if name in self._providers:
                    del self._providers[name]

            logger.info(f"Registered SSO provider: {name}")
            return True

        except Exception:
            logger.error(f"Failed to register SSO provider: {e}")
            return False

    def register_predefined_provider(
        self,
        provider_name: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        tenant_id: Optional[int] = None,
        extra_params: Optional[dict[str, Any]] = None,
    ) -> bool:
        """
        Register a predefined SSO provider (e.g., 'google', 'github').

        Args:
            provider_name: Predefined provider name.
            client_id: OAuth client ID.
            client_secret: OAuth client secret.
            redirect_uri: Redirect URI.
            tenant_id: Associated tenant ID.
            extra_params: Additional parameters.

        Returns:
            bool: True if successful.
        """
        predefined = get_provider_config(provider_name)
        if not predefined:
            logger.error(f"Unknown predefined provider: {provider_name}")
            return False

        return self.register_provider(
            name=provider_name,
            provider_type=predefined["provider_type"],
            client_id=client_id,
            client_secret=client_secret,
            authorization_url=predefined["authorization_url"],
            token_url=predefined["token_url"],
            userinfo_url=predefined.get("userinfo_url"),
            redirect_uri=redirect_uri,
            scope=predefined.get("scope"),
            issuer_url=predefined.get("issuer_url"),
            tenant_id=tenant_id,
            extra_params=extra_params,
        )

    def get_provider(self, name: str) -> Optional[SSOProvider]:
        """
        Get an SSO provider by name.

        Args:
            name: Provider name.

        Returns:
            Optional[SSOProvider]: Provider instance or None.
        """
        # Check cache
        with self._providers_lock:
            if name in self._providers:
                return self._providers[name]

        # Load from database
        row = self.db.fetch_one(
            f"SELECT * FROM sso_providers WHERE name = ? AND {adapt_boolean_condition('is_active', True)}",
            (name,),
        )

        if not row:
            return None

        try:
            config_data = json.loads(row["config"])
            config = SSOProviderConfig(
                name=config_data.get("name", name),
                provider_type=config_data.get("provider_type", "oauth2"),
                client_id=config_data.get("client_id", ""),
                client_secret=config_data.get("client_secret", ""),
                authorization_url=config_data.get("authorization_url", ""),
                token_url=config_data.get("token_url", ""),
                userinfo_url=config_data.get("userinfo_url"),
                redirect_uri=config_data.get("redirect_uri"),
                scope=config_data.get("scope", ["openid", "profile", "email"]),
                issuer_url=config_data.get("issuer_url"),
                tenant_id=config_data.get("tenant_id"),
                extra_params=config_data.get("extra_params", {}),
            )

            # Create provider instance
            provider_class = get_provider_class(config.provider_type)
            provider = provider_class(config)

            # Cache it
            with self._providers_lock:
                self._providers[name] = provider

            return cast("Optional[SSOProvider]", provider)

        except Exception:
            logger.error(f"Failed to load SSO provider {name}: {e}")
            return None

    def list_providers(
        self,
        tenant_id: Optional[int] = None,
        is_active: Optional[bool] = None,
        provider_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """
        List all registered SSO providers.

        Args:
            tenant_id: Filter by tenant ID.
            is_active: Filter by active status.
            provider_type: Filter by provider type.
            limit: Limit number of results (max 1000).
            offset: Offset for pagination.

        Returns:
            Tuple[List[Dict], int]: List of provider info (without sensitive fields) and total count.
        """
        # Build WHERE conditions
        conditions = []
        params: list[Any] = []

        if tenant_id is not None:
            conditions.append("tenant_id = ?")
            params.append(tenant_id)

        if is_active is not None:
            conditions.append(f"is_active = {adapt_boolean_value(is_active)}")

        if provider_type is not None:
            conditions.append("provider_type = ?")
            params.append(provider_type)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        # Get total count
        count_row = self.db.fetch_one(
            f"SELECT COUNT(*) as total FROM sso_providers WHERE {where_clause}",
            tuple(params) if params else (),
        )
        total = count_row["total"] if count_row else 0

        # Get paginated results (only metadata, no config field)
        limit = min(limit, 1000)  # Cap at 1000
        rows = self.db.fetch_all(
            f"""
            SELECT name, provider_type, tenant_id, is_active, created_at, updated_at
            FROM sso_providers
            WHERE {where_clause}
            ORDER BY name
            LIMIT ? OFFSET ?
            """,
            tuple(params + [limit, offset]) if params else (limit, offset),
        )

        # Return metadata only (no sensitive config data)
        providers = []
        for row in rows:
            provider_data = {
                "name": row["name"],
                "provider_type": row["provider_type"],
                "tenant_id": row["tenant_id"],
                "is_active": bool(row["is_active"]),
            }
            # Add timestamps if available
            if "created_at" in row and row["created_at"]:
                provider_data["created_at"] = row["created_at"]
            if "updated_at" in row and row["updated_at"]:
                provider_data["updated_at"] = row["updated_at"]
            providers.append(provider_data)

        return providers, total

    def disable_provider(self, name: str) -> bool:
        """Disable an SSO provider."""
        try:
            self.db.execute(
                "UPDATE sso_providers SET is_active = ? WHERE name = ?",
                (adapt_boolean_value(False), name),
            )

            with self._providers_lock:
                if name in self._providers:
                    del self._providers[name]

            return True

        except Exception:
            logger.error(f"Failed to disable provider: {e}")
            return False

    def enable_provider(self, name: str) -> bool:
        """Enable an SSO provider."""
        try:
            self.db.execute(
                "UPDATE sso_providers SET is_active = ? WHERE name = ?",
                (adapt_boolean_value(True), name),
            )

            return True

        except Exception:
            logger.error(f"Failed to enable provider: {e}")
            return False

    def get_provider_info(self, name: str) -> Optional[dict[str, Any]]:
        """
        Get detailed information for a single SSO provider.

        Args:
            name: Provider name.

        Returns:
            Optional[Dict]: Provider details (without client_secret) or None.
        """
        row = self.db.fetch_one(
            "SELECT * FROM sso_providers WHERE name = ?",
            (name,),
        )

        if not row:
            return None

        try:
            config_data = json.loads(row["config"])

            # Build response without sensitive fields
            provider_info = {
                "name": row["name"],
                "provider_type": row["provider_type"],
                "tenant_id": row["tenant_id"],
                "is_active": bool(row["is_active"]),
                # Config fields (excluding client_secret)
                "client_id": config_data.get("client_id", ""),
                "redirect_uri": config_data.get("redirect_uri"),
                "scope": config_data.get("scope", ["openid", "profile", "email"]),
                "authorization_url": config_data.get("authorization_url", ""),
                "token_url": config_data.get("token_url", ""),
                "userinfo_url": config_data.get("userinfo_url"),
                "issuer_url": config_data.get("issuer_url"),
                "extra_params": config_data.get("extra_params", {}),
            }

            # Add timestamps if available
            if "created_at" in row and row["created_at"]:
                provider_info["created_at"] = row["created_at"]
            if "updated_at" in row and row["updated_at"]:
                provider_info["updated_at"] = row["updated_at"]

            return provider_info

        except Exception:
            logger.error(f"Failed to get provider info for {name}: {e}")
            return None

    def update_provider(self, name: str, updates: dict[str, Any]) -> bool:
        """
        Update an SSO provider configuration.

        Args:
            name: Provider name.
            updates: Fields to update (supports partial updates).
                Allowed fields: client_secret, redirect_uri, scope, is_active, extra_params

        Returns:
            bool: True if successful.
        """
        # Validate allowed fields
        allowed_fields = {"client_secret", "redirect_uri", "scope", "is_active", "extra_params"}
        invalid_fields = set(updates.keys()) - allowed_fields
        if invalid_fields:
            logger.error(f"Invalid fields for update: {invalid_fields}")
            return False

        try:
            # Get current config
            row = self.db.fetch_one(
                "SELECT config FROM sso_providers WHERE name = ?",
                (name,),
            )

            if not row:
                logger.error(f"Provider not found: {name}")
                return False

            config_data = json.loads(row["config"])

            # Apply updates to config
            for field, value in updates.items():
                if field == "is_active":
                    # is_active is a separate column, handle specially
                    continue
                config_data[field] = value

            # Update database
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            update_fields = ["config = ?", "updated_at = ?"]
            update_values = [json.dumps(config_data), now]

            if "is_active" in updates:
                update_fields.append("is_active = ?")
                update_values.append(adapt_boolean_value(updates["is_active"]))

            update_values.append(name)

            self.db.execute(
                f"""
                UPDATE sso_providers
                SET {', '.join(update_fields)}
                WHERE name = ?
                """,
                tuple(update_values),
            )

            # Clear cached provider
            with self._providers_lock:
                if name in self._providers:
                    del self._providers[name]

            logger.info(f"Updated SSO provider: {name}")
            return True

        except Exception:
            logger.error(f"Failed to update provider {name}: {e}")
            return False

    def delete_provider(self, name: str, hard: bool = False) -> bool:
        """
        Delete an SSO provider.

        Args:
            name: Provider name.
            hard: If True, permanently delete; if False, soft delete (disable).

        Returns:
            bool: True if successful.
        """
        try:
            if hard:
                # Hard delete - remove all data
                # Clear sessions
                self.db.execute(
                    "DELETE FROM sso_sessions WHERE provider_name = ?",
                    (name,),
                )

                # Clear identities (optional - could preserve for history)
                self.db.execute(
                    "DELETE FROM sso_identities WHERE provider_name = ?",
                    (name,),
                )

                # Delete provider record
                self.db.execute(
                    "DELETE FROM sso_providers WHERE name = ?",
                    (name,),
                )

            else:
                # Soft delete - disable and preserve data
                self.db.execute(
                    "UPDATE sso_providers SET is_active = ?, updated_at = ? WHERE name = ?",
                    (
                        adapt_boolean_value(False),
                        datetime.now(timezone.utc).replace(tzinfo=None),
                        name,
                    ),
                )

                # Optionally clear sessions (disable active logins)
                self.db.execute(
                    "DELETE FROM sso_sessions WHERE provider_name = ?",
                    (name,),
                )

            # Clear cached provider
            with self._providers_lock:
                if name in self._providers:
                    del self._providers[name]

            logger.info(f"{'Hard' if hard else 'Soft'} deleted SSO provider: {name}")
            return True

        except Exception:
            logger.error(f"Failed to delete provider {name}: {e}")
            return False

    def test_provider_connection(self, name: str) -> dict[str, Any]:
        """
        Test SSO provider connection and configuration.

        Args:
            name: Provider name.

        Returns:
            Dict: Test results with detailed information.
        """
        result: Dict[str, Any] = {
            "success": False,
            "tests": {},
            "warnings": [],
            "errors": [],
        }

        # Get provider info
        provider_info = self.get_provider_info(name)
        if not provider_info:
            result["errors"].append("Provider not found")
            return result

        result["tests"]["provider_exists"] = True

        # Test authorization_url reachability
        auth_url = provider_info.get("authorization_url", "")
        if auth_url:
            test_result = self._test_url_reachability(auth_url)
            result["tests"]["authorization_url"] = test_result
            if not test_result.get("reachable"):
                result["errors"].append(f"Authorization URL not reachable: {auth_url}")
        else:
            result["warnings"].append("Authorization URL is empty")

        # Test token_url reachability
        token_url = provider_info.get("token_url", "")
        if token_url:
            test_result = self._test_url_reachability(token_url)
            result["tests"]["token_url"] = test_result
            if not test_result.get("reachable"):
                result["errors"].append(f"Token URL not reachable: {token_url}")
        else:
            result["warnings"].append("Token URL is empty")

        # Test userinfo_url reachability (optional)
        userinfo_url = provider_info.get("userinfo_url")
        if userinfo_url:
            test_result = self._test_url_reachability(userinfo_url)
            result["tests"]["userinfo_url"] = test_result
        else:
            # userinfo_url is optional for some providers
            result["warnings"].append("Userinfo URL is empty (optional)")

        # Try OIDC discovery for OIDC providers
        if provider_info.get("provider_type") == "oidc":
            discovery_result = self._test_oidc_discovery(name, provider_info)
            result["tests"]["discovery_doc"] = discovery_result
            if discovery_result.get("available"):
                # Add recommended endpoints from discovery
                if discovery_result.get("endpoints"):
                    result["discovered_endpoints"] = discovery_result["endpoints"]

        # Test SSL certificate validity
        test_urls = [auth_url, token_url, userinfo_url]
        for url in test_urls:
            if url and url.startswith("https://"):
                ssl_result = self._test_ssl_certificate(url)
                result["tests"]["ssl"] = ssl_result
                if not ssl_result.get("valid"):
                    result["warnings"].append(f"SSL certificate issue: {url}")
                break  # Only test one SSL URL

        # Validate client_id format (basic check)
        client_id = provider_info.get("client_id", "")
        if client_id:
            result["tests"]["client_id_format"] = {"valid": True}
        else:
            result["errors"].append("client_id is empty")

        # Determine overall success
        critical_tests_passed = (
            result["tests"].get("provider_exists", False)
            and (
                not auth_url or result["tests"].get("authorization_url", {}).get("reachable", False)
            )
            and (not token_url or result["tests"].get("token_url", {}).get("reachable", False))
        )
        result["success"] = critical_tests_passed and len(result["errors"]) == 0

        return result

    def _test_url_reachability(self, url: str, timeout: int = 10) -> dict[str, Any]:
        """
        Test if a URL is reachable.

        Args:
            url: URL to test.
            timeout: Timeout in seconds.

        Returns:
            Dict: Test result with reachable status and latency.
        """
        result: Dict[str, Any] = {"reachable": False, "latency_ms": None, "error": None}

        try:
            start_time = time.time()
            request = urllib.request.Request(url, method="HEAD")
            request.add_header("User-Agent", "Open-ACE-SSO-Test/1.0")

            response = urllib.request.urlopen(request, timeout=timeout)
            end_time = time.time()

            result["reachable"] = True
            result["latency_ms"] = int((end_time - start_time) * 1000)
            result["status_code"] = response.getcode()

        except urllib.error.HTTPError as e:
            # HTTP errors (4xx, 5xx) - endpoint exists but returned error
            result["reachable"] = True  # URL is reachable
            result["status_code"] = e.code
            result["latency_ms"] = int((time.time() - start_time) * 1000)
            result["warning"] = f"HTTP {e.code}"

        except urllib.error.URLError as e:
            result["reachable"] = False
            result["error"] = str(e.reason)

        except Exception:
            result["reachable"] = False
            result["error"] = str(e)

        return result

    def _test_oidc_discovery(
        self, provider_name: str, provider_info: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Test OIDC discovery document availability.

        Args:
            provider_name: Provider name.
            provider_info: Provider configuration.

        Returns:
            Dict: Discovery test result.
        """
        result: Dict[str, Any] = {"available": False, "endpoints": None, "error": None}

        # Try to get issuer URL
        issuer_url = provider_info.get("issuer_url", "")

        # For predefined providers, use known discovery URL
        predefined_config = get_provider_config(provider_name)
        if predefined_config and predefined_config.get("issuer_url"):
            issuer_url = predefined_config["issuer_url"]

        if not issuer_url:
            result["error"] = "No issuer URL available for discovery"
            return result

        discovery_url = f"{issuer_url.rstrip('/')}/.well-known/openid-configuration"

        try:
            request = urllib.request.Request(discovery_url)
            request.add_header("User-Agent", "Open-ACE-SSO-Test/1.0")
            request.add_header("Accept", "application/json")

            response = urllib.request.urlopen(request, timeout=10)
            data = json.loads(response.read().decode("utf-8"))

            result["available"] = True
            result["endpoints"] = {
                "authorization_endpoint": data.get("authorization_endpoint"),
                "token_endpoint": data.get("token_endpoint"),
                "userinfo_endpoint": data.get("userinfo_endpoint"),
                "jwks_uri": data.get("jwks_uri"),
                "issuer": data.get("issuer"),
            }

        except urllib.error.URLError as e:
            result["error"] = str(e.reason)

        except Exception:
            result["error"] = str(e)

        return result

    def _test_ssl_certificate(self, url: str) -> dict[str, Any]:
        """
        Test SSL certificate validity for a URL.

        Args:
            url: URL to test.

        Returns:
            Dict: SSL test result.
        """
        result: Dict[str, Any] = {"valid": True, "error": None}

        try:
            import ssl

            context = ssl.create_default_context()
            request = urllib.request.Request(url, method="HEAD")
            urllib.request.urlopen(request, timeout=10, context=context)

        except ssl.SSLError as e:
            result["valid"] = False
            result["error"] = str(e)

        except Exception:
            # Non-SSL errors don't affect SSL validity
            pass

        return result

    def start_authentication(
        self, provider_name: str, redirect_uri: str
    ) -> Optional[dict[str, str]]:
        """
        Start the SSO authentication flow.

        Args:
            provider_name: Provider name.
            redirect_uri: Callback redirect URI.

        Returns:
            Optional[Dict]: Dict with 'authorization_url' and 'state' or None.
        """
        provider = self.get_provider(provider_name)
        if not provider:
            logger.error(f"Provider not found: {provider_name}")
            return None

        # Generate state for CSRF protection
        state = secrets.token_urlsafe(32)

        # Generate PKCE for added security
        code_verifier, code_challenge = OAuth2Provider.generate_pkce()

        # Generate nonce for OIDC
        nonce = None
        if hasattr(provider, "generate_nonce"):
            nonce = OIDCProvider.generate_nonce()

        # Build authorization URL
        auth_url = provider.get_authorization_url(
            state=state,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            nonce=nonce,
        )

        # Store state for verification (in production, use Redis or similar)
        self._store_auth_state(state, code_verifier, provider_name, nonce)

        return {
            "authorization_url": auth_url,
            "state": state,
        }

    def complete_authentication(
        self, provider_name: str, code: str, state: str, redirect_uri: str
    ) -> SSOAuthResult:
        """
        Complete the SSO authentication flow.

        Args:
            provider_name: Provider name.
            code: Authorization code.
            state: State parameter from callback.
            redirect_uri: Redirect URI used in authorization.

        Returns:
            SSOAuthResult: Authentication result.
        """
        provider = self.get_provider(provider_name)
        if not provider:
            return SSOAuthResult(
                success=False,
                error="provider_not_found",
            )

        # Verify state
        auth_state = self._get_auth_state(state)
        if not auth_state:
            return SSOAuthResult(
                success=False,
                error="invalid_state",
            )

        # Get PKCE code verifier
        auth_state.get("code_verifier")

        # Exchange code for tokens
        result = provider.authenticate(code, redirect_uri)

        # Clean up state
        self._delete_auth_state(state)

        return result

    def link_identity(
        self,
        user_id: int,
        provider_name: str,
        provider_user_id: str,
        provider_data: Optional[dict[str, Any]] = None,
    ) -> bool:
        """
        Link an SSO identity to a local user.

        Args:
            user_id: Local user ID.
            provider_name: SSO provider name.
            provider_user_id: User ID from the provider.
            provider_data: Additional provider data.

        Returns:
            bool: True if successful.
        """
        try:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            provider_data_json = json.dumps(provider_data) if provider_data else None
            self.db.execute(
                """
                INSERT INTO sso_identities
                (user_id, provider_name, provider_user_id, provider_data, last_used_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(provider_name, provider_user_id) DO UPDATE SET
                    user_id = ?,
                    provider_data = ?,
                    last_used_at = ?
            """,
                (
                    user_id,
                    provider_name,
                    provider_user_id,
                    provider_data_json,
                    now,
                    user_id,
                    provider_data_json,
                    now,
                ),
            )

            logger.info(
                f"Linked SSO identity: {provider_name}:{provider_user_id} -> user:{user_id}"
            )
            return True

        except Exception:
            logger.error(f"Failed to link SSO identity: {e}")
            return False

    def get_user_by_sso_identity(self, provider_name: str, provider_user_id: str) -> Optional[int]:
        """
        Get local user ID by SSO identity.

        Args:
            provider_name: SSO provider name.
            provider_user_id: User ID from the provider.

        Returns:
            Optional[int]: Local user ID or None.
        """
        row = self.db.fetch_one(
            """
            SELECT user_id FROM sso_identities
            WHERE provider_name = ? AND provider_user_id = ?
        """,
            (provider_name, provider_user_id),
        )

        return row["user_id"] if row else None

    def create_sso_session(
        self,
        user_id: int,
        provider_name: str,
        access_token: str,
        refresh_token: Optional[str] = None,
        expires_in: int = 3600,
    ) -> Optional[str]:
        """
        Create an SSO session.

        Args:
            user_id: Local user ID.
            provider_name: SSO provider name.
            access_token: OAuth access token.
            refresh_token: OAuth refresh token.
            expires_in: Token expiration in seconds.

        Returns:
            Optional[str]: Session token or None.
        """
        session_token = secrets.token_hex(32)
        expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=expires_in)

        try:
            self.db.execute(
                """
                INSERT INTO sso_sessions
                (session_token, user_id, provider_name, access_token, refresh_token, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (
                    session_token,
                    user_id,
                    provider_name,
                    access_token,
                    refresh_token,
                    expires_at,
                ),
            )

            return session_token

        except Exception:
            logger.error(f"Failed to create SSO session: {e}")
            return None

    def get_sso_session(self, session_token: str) -> Optional[dict[str, Any]]:
        """
        Get SSO session by token.

        Args:
            session_token: Session token.

        Returns:
            Optional[Dict]: Session data or None.
        """
        row = self.db.fetch_one(
            """
            SELECT * FROM sso_sessions
            WHERE session_token = ? AND expires_at > ?
        """,
            (session_token, datetime.now(timezone.utc).replace(tzinfo=None)),
        )

        if not row:
            return None

        return dict(row)

    def delete_sso_session(self, session_token: str) -> bool:
        """Delete an SSO session."""
        try:
            self.db.execute("DELETE FROM sso_sessions WHERE session_token = ?", (session_token,))
            return True

        except Exception:
            logger.error(f"Failed to delete SSO session: {e}")
            return False

    def cleanup_expired_sessions(self) -> int:
        """Delete expired SSO sessions."""
        try:
            cursor = self.db.execute(
                "DELETE FROM sso_sessions WHERE expires_at < ?",
                (datetime.now(timezone.utc).replace(tzinfo=None),),
            )
            return cast("int", cursor.rowcount)

        except Exception:
            logger.error(f"Failed to cleanup sessions: {e}")
            return 0

    def _store_auth_state(
        self, state: str, code_verifier: str, provider_name: str, nonce: Optional[str] = None
    ) -> None:
        """Store authentication state for verification.

        Requires the ``sso_auth_states`` table to already exist. In production it
        is created at startup from the authoritative schema files via
        ``schema_init.load_schema_from_file()`` (and by the
        ``20260703_002_add_sso_auth_states`` migration for pure-Alembic
        upgrades). Tests that exercise this path must run ``ensure_all_tables()``
        or ``get_ddl_statements()`` first — otherwise the INSERT below fails and
        is swallowed by the broad except, surfacing as a downstream SSO failure
        instead of a clear error (Issue #237 item 4, review note).
        """
        try:
            self.db.execute(
                """
                INSERT INTO sso_auth_states (state, code_verifier, provider_name, nonce)
                VALUES (?, ?, ?, ?)
            """,
                (state, code_verifier, provider_name, nonce),
            )

        except Exception:
            logger.error(f"Failed to store auth state: {e}")

    def _get_auth_state(self, state: str) -> Optional[dict[str, Any]]:
        """Get authentication state."""
        try:
            row = self.db.fetch_one("SELECT * FROM sso_auth_states WHERE state = ?", (state,))
            return dict(row) if row else None

        except Exception:
            logger.error(f"Failed to get auth state: {e}")
            return None

    def _delete_auth_state(self, state: str) -> None:
        """Delete authentication state."""
        try:
            self.db.execute("DELETE FROM sso_auth_states WHERE state = ?", (state,))

        except Exception:
            logger.error(f"Failed to delete auth state: {e}")


def get_ddl_statements() -> list[str]:
    """Return DDL statements for SSO tables."""
    id_type = "SERIAL PRIMARY KEY" if is_postgresql() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    bool_true = "BOOLEAN DEFAULT TRUE" if is_postgresql() else "INTEGER DEFAULT 1"
    return [
        f"""
        CREATE TABLE IF NOT EXISTS sso_providers (
            id {id_type},
            name TEXT UNIQUE NOT NULL,
            provider_type TEXT NOT NULL,
            config TEXT NOT NULL,
            tenant_id INTEGER,
            is_active {bool_true},
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (tenant_id) REFERENCES tenants(id)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS sso_identities (
            id {id_type},
            user_id INTEGER NOT NULL,
            provider_name TEXT NOT NULL,
            provider_user_id TEXT NOT NULL,
            provider_data TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_used_at TIMESTAMP,
            UNIQUE(provider_name, provider_user_id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS sso_sessions (
            id {id_type},
            session_token TEXT UNIQUE NOT NULL,
            user_id INTEGER NOT NULL,
            provider_name TEXT NOT NULL,
            access_token TEXT,
            refresh_token TEXT,
            expires_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS sso_auth_states (
            state TEXT PRIMARY KEY,
            code_verifier TEXT NOT NULL,
            provider_name TEXT NOT NULL,
            nonce TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_sso_providers_tenant ON sso_providers(tenant_id)",
        "CREATE INDEX IF NOT EXISTS idx_sso_identities_user ON sso_identities(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_sso_identities_provider ON sso_identities(provider_name, provider_user_id)",
        "CREATE INDEX IF NOT EXISTS idx_sso_sessions_token ON sso_sessions(session_token)",
        "CREATE INDEX IF NOT EXISTS idx_sso_sessions_user ON sso_sessions(user_id)",
    ]
