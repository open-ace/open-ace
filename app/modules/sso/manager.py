"""
Open ACE - SSO Manager

Manages SSO providers and authentication sessions.
"""
from __future__ import annotations


import json
import logging
import os
import secrets
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from app.modules.sso.exceptions import SSOConfigDecryptionError
from app.modules.sso.oauth2 import OAuth2Provider
from app.modules.sso.oidc import OIDCProvider, get_provider_class
from app.modules.sso.provider import (
    SSOAuthResult,
    SSOProvider,
    SSOProviderConfig,
    get_provider_config,
)
from app.modules.sso.saml import SAMLProvider
from app.repositories.database import Database, adapt_boolean_condition, adapt_boolean_value
from app.utils.smtp_crypto import get_password_manager

logger = logging.getLogger(__name__)

# ============================================================================
# Configuration Constants (Issue #1815)
# ============================================================================

# Default TTL for auth_state records (10 minutes, typical OAuth code lifetime)
AUTH_STATE_TTL_SECONDS = int(os.environ.get("OPENACE_SSO_AUTH_STATE_TTL_SECONDS", "600"))

# Cleanup task interval (5 minutes)
AUTH_STATE_CLEANUP_INTERVAL_SECONDS = int(
    os.environ.get("OPENACE_SSO_AUTH_STATE_CLEANUP_INTERVAL", "300")
)

# Batch size for cleanup operations
CLEANUP_BATCH_SIZE = int(os.environ.get("OPENACE_SSO_CLEANUP_BATCH_SIZE", "1000"))


# ============================================================================
# Module-level cleanup state
# ============================================================================

_cleanup_lock_path = "/tmp/openace-sso-cleanup.lock"
_cleanup_timer: threading.Timer | None = None
_shutdown_requested = False
_cleanup_lock_file = None  # type: ignore[var-annotated]  # File handle for lock


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

    def __init__(self, db: Database | None = None):
        """
        Initialize SSO manager.

        Args:
            db: Optional Database instance.
        """
        self.db = db or Database()
        self._providers: dict[str, SSOProvider] = {}
        self._providers_lock = threading.Lock()
        self._password_manager = get_password_manager()

    def serialize_provider_config(self, config_data: dict[str, Any]) -> str:
        """Serialize provider config for storage, encrypting the client secret."""
        stored = dict(config_data)
        client_secret = cast("str", stored.pop("client_secret", "") or "")
        stored.pop("client_secret_encrypted", None)
        stored["client_secret_encrypted"] = (
            self._password_manager.encrypt(client_secret) if client_secret else ""
        )
        return json.dumps(stored)

    def deserialize_provider_config(
        self, raw_config: str | dict[str, Any], provider_name: str | None = None
    ) -> dict[str, Any]:
        """Deserialize provider config and restore the decrypted client secret.

        Args:
            raw_config: Raw configuration string or dict from database.
            provider_name: Provider name for error context (used in exception message).

        Returns:
            Dict with decrypted client_secret.

        Raises:
            SSOConfigDecryptionError: If decryption fails (Issue #1815 Finding 1).
        """
        config_data = (
            cast("dict[str, Any]", json.loads(raw_config))
            if isinstance(raw_config, str)
            else dict(raw_config)
        )
        encrypted_secret = config_data.pop("client_secret_encrypted", "")

        client_secret = cast("str", config_data.get("client_secret", "") or "")
        if encrypted_secret:
            try:
                client_secret = self._password_manager.decrypt(encrypted_secret)
            except Exception as e:
                # Issue #1815 Finding 1: Fail-fast instead of silent fallback
                # Determine provider name for error message
                name = provider_name or config_data.get("name", "unknown")
                logger.error(
                    f"SSO provider '{name}' client_secret decryption failed: {e}",
                    exc_info=True,
                )
                raise SSOConfigDecryptionError(
                    provider_name=name,
                    original_error=e,
                ) from e

        config_data["client_secret"] = client_secret
        return config_data

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
        userinfo_url: str | None = None,
        redirect_uri: str | None = None,
        scope: list[str] | None = None,
        issuer_url: str | None = None,
        tenant_id: int | None = None,
        extra_params: dict[str, Any] | None = None,
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
            serialized_config = self.serialize_provider_config(config.__dict__)
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
                    serialized_config,
                    tenant_id,
                    provider_type,
                    serialized_config,
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

        except Exception as e:
            logger.error(f"Failed to register SSO provider: {e}")
            return False

    def register_predefined_provider(
        self,
        provider_name: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        tenant_id: int | None = None,
        extra_params: dict[str, Any] | None = None,
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

    def get_provider(self, name: str) -> SSOProvider | None:
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
            config_data = self.deserialize_provider_config(row["config"], provider_name=name)
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

            return cast("SSOProvider | None", provider)

        except SSOConfigDecryptionError as e:
            # Issue #1815 Finding 1: Audit log for decryption failures
            logger.error(
                f"SSO provider '{e.provider_name}' failed to load due to decryption error",
                exc_info=True,
            )
            try:
                from app.modules.governance.audit_logger import AuditAction, AuditLogger

                AuditLogger().log(
                    action=AuditAction.SYSTEM_CONFIG_CHANGE.value,
                    resource_type="sso_provider",
                    resource_id=e.provider_name,
                    details={"error": "decryption_failed"},
                )
            except Exception as audit_error:
                logger.warning(f"Failed to log audit for decryption error: {audit_error}")
            return None

        except Exception as e:
            logger.error(f"Failed to load SSO provider {name}: {e}")
            return None

    def list_providers(self, tenant_id: int | None = None) -> list[dict[str, Any]]:
        """
        List all registered SSO providers.

        Args:
            tenant_id: Filter by tenant ID.

        Returns:
            List[Dict]: List of provider info.
        """
        if tenant_id:
            rows = self.db.fetch_all(
                "SELECT name, provider_type, tenant_id, is_active FROM sso_providers WHERE tenant_id = ?",
                (tenant_id,),
            )
        else:
            rows = self.db.fetch_all(
                "SELECT name, provider_type, tenant_id, is_active FROM sso_providers"
            )

        return [dict(row) for row in rows]

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

        except Exception as e:
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

        except Exception as e:
            logger.error(f"Failed to enable provider: {e}")
            return False

    def start_authentication(self, provider_name: str, redirect_uri: str) -> dict[str, str] | None:
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

        if provider.provider_type == "saml" and isinstance(provider, SAMLProvider):
            auth_url = provider.get_authorization_url(state=state, redirect_uri=redirect_uri)
            request_id = provider.last_request_id or ""
            self._store_auth_state(state, request_id, provider_name, None)
            return {
                "authorization_url": auth_url,
                "state": state,
            }

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

    def complete_saml_authentication(
        self,
        provider_name: str,
        saml_response: str,
        relay_state: str,
        acs_url: str,
    ) -> SSOAuthResult:
        """Complete a SAML ACS flow after the IdP posts a SAMLResponse."""
        provider = self.get_provider(provider_name)
        if not provider:
            return SSOAuthResult(success=False, error="provider_not_found")
        if provider.provider_type != "saml" or not isinstance(provider, SAMLProvider):
            return SSOAuthResult(success=False, error="unsupported_provider_type")

        auth_state = self._get_auth_state(relay_state)
        if not auth_state:
            return SSOAuthResult(success=False, error="invalid_state")
        if auth_state.get("provider_name") != provider_name:
            return SSOAuthResult(success=False, error="invalid_state")

        # Normalize an empty-string request id (stored under the NOT NULL
        # code_verifier column when no SAML AuthnRequest id was generated) back
        # to None. The InResponseTo strong-check in
        # SAMLProvider._validate_response gates on `if request_id:`; without
        # this normalization a stored "" would silently disable the check,
        # which is exactly the replay gap this PR closes.
        request_id = cast("str | None", auth_state.get("code_verifier")) or None
        result = provider.authenticate_saml_response(
            saml_response=saml_response,
            request_id=request_id,
            acs_url=acs_url,
        )
        self._delete_auth_state(relay_state)
        return result

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

        if auth_state.get("provider_name") != provider_name:
            return SSOAuthResult(
                success=False,
                error="invalid_state",
            )

        code_verifier = cast("str | None", auth_state.get("code_verifier"))
        if not code_verifier:
            logger.error(
                "SSO auth state for provider %s is missing code_verifier; "
                "rejecting to prevent PKCE downgrade (possible failed state "
                "storage).",
                provider_name,
            )
            self._delete_auth_state(state)
            return SSOAuthResult(success=False, error="invalid_state")

        # Exchange code for tokens
        result = provider.authenticate(code, redirect_uri, code_verifier)

        # Clean up state
        self._delete_auth_state(state)

        return result

    def link_identity(
        self,
        user_id: int,
        provider_name: str,
        provider_user_id: str,
        provider_data: dict[str, Any] | None = None,
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

            # SECURITY: never silently re-bind an SSO identity from one local
            # user to another. If the (provider, provider_user_id) row already
            # exists for a *different* local user, refuse so the original
            # binding is preserved and the operator can investigate. This
            # prevents silent identity migration (e.g. via the unverified-email
            # linking path) with no audit trail.
            existing = self.db.fetch_one(
                """
                SELECT user_id FROM sso_identities
                WHERE provider_name = ? AND provider_user_id = ?
            """,
                (provider_name, provider_user_id),
            )
            if existing and int(existing["user_id"]) != int(user_id):
                logger.error(
                    f"Refused to rebind SSO identity {provider_name}:"
                    f"{provider_user_id} from user {existing['user_id']} to user {user_id}"
                )
                return False

            self.db.execute(
                """
                INSERT INTO sso_identities
                (user_id, provider_name, provider_user_id, provider_data, last_used_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(provider_name, provider_user_id) DO UPDATE SET
                    provider_data = ?,
                    last_used_at = ?
            """,
                (
                    user_id,
                    provider_name,
                    provider_user_id,
                    provider_data_json,
                    now,
                    provider_data_json,
                    now,
                ),
            )

            logger.info(
                f"Linked SSO identity: {provider_name}:{provider_user_id} -> user:{user_id}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to link SSO identity: {e}")
            return False

    def get_user_by_sso_identity(self, provider_name: str, provider_user_id: str) -> int | None:
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
        refresh_token: str | None = None,
        expires_in: int = 3600,
    ) -> str | None:
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

        except Exception as e:
            logger.error(f"Failed to create SSO session: {e}")
            return None

    def get_sso_session(self, session_token: str) -> dict[str, Any] | None:
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

        except Exception as e:
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

        except Exception as e:
            logger.error(f"Failed to cleanup sessions: {e}")
            return 0

    # ========================================================================
    # Issue #1815 Finding 2: Auth State TTL Methods
    # ========================================================================

    def _store_auth_state(
        self, state: str, code_verifier: str, provider_name: str, nonce: str | None = None
    ) -> None:
        """Store authentication state for verification with TTL.

        Requires the ``sso_auth_states`` table to already exist. In production it
        is created at startup from the authoritative schema files via
        ``schema_init.load_schema_from_file()`` (and by the
        ``20260703_002_add_sso_auth_states`` migration for pure-Alembic
        upgrades). Tests that exercise this path must run ``ensure_all_tables()``
        or ``get_ddl_statements()`` first — otherwise the INSERT below fails and
        is swallowed by the broad except, surfacing as a downstream SSO failure
        instead of a clear error (Issue #237 item 4, review note).

        Issue #1815 Finding 2: Added expires_at for TTL-based cleanup.
        """
        try:
            expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
                seconds=AUTH_STATE_TTL_SECONDS
            )
            self.db.execute(
                """
                INSERT INTO sso_auth_states (state, code_verifier, provider_name, nonce, expires_at)
                VALUES (?, ?, ?, ?, ?)
            """,
                (state, code_verifier, provider_name, nonce, expires_at),
            )

        except Exception as e:
            logger.error(f"Failed to store auth state: {e}")

    def _get_auth_state(self, state: str) -> dict[str, Any] | None:
        """Get authentication state, excluding expired entries.

        Issue #1815 Finding 2: Added expiry predicate to prevent replay attacks.
        """
        try:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            row = self.db.fetch_one(
                "SELECT * FROM sso_auth_states WHERE state = ? AND expires_at > ?",
                (state, now),
            )
            return dict(row) if row else None

        except Exception as e:
            logger.error(f"Failed to get auth state: {e}")
            return None

    def _delete_auth_state(self, state: str) -> None:
        """Delete authentication state."""
        try:
            self.db.execute("DELETE FROM sso_auth_states WHERE state = ?", (state,))

        except Exception as e:
            logger.error(f"Failed to delete auth state: {e}")

    def cleanup_expired_auth_states(self) -> int:
        """Delete expired auth state records in batches.

        Issue #1815 Finding 2: Batch deletion with dialect-specific SQL.

        Returns:
            int: Total number of deleted rows.
        """
        total_deleted = 0
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        try:
            while True:
                if self.db.is_postgresql:
                    # PostgreSQL: use subquery with LIMIT
                    cursor = self.db.execute(
                        f"""
                        DELETE FROM sso_auth_states
                        WHERE state IN (
                            SELECT state FROM sso_auth_states
                            WHERE expires_at < %s
                            LIMIT {CLEANUP_BATCH_SIZE}
                        )
                    """,
                        (now,),
                    )
                else:
                    # SQLite: use subquery with LIMIT (DELETE LIMIT not widely supported)
                    cursor = self.db.execute(
                        f"""
                        DELETE FROM sso_auth_states
                        WHERE state IN (
                            SELECT state FROM sso_auth_states
                            WHERE expires_at < ?
                            LIMIT {CLEANUP_BATCH_SIZE}
                        )
                    """,
                        (now,),
                    )

                deleted = cursor.rowcount
                total_deleted += deleted

                if deleted < CLEANUP_BATCH_SIZE:
                    break

                logger.debug(f"Deleted {deleted} expired auth states, continuing...")

        except Exception as e:
            logger.error(f"Failed to cleanup expired auth states: {e}")

        if total_deleted > 0:
            logger.info(f"Cleaned up {total_deleted} expired auth state records")

        return total_deleted


# ============================================================================
# Module-level cleanup task management (Issue #1815 Finding 2)
# ============================================================================


def _acquire_cleanup_lock() -> bool:
    """Try to acquire the file lock for cleanup task.

    Returns:
        bool: True if lock acquired, False otherwise.
    """
    import fcntl

    try:
        # Create lock file if not exists
        lock_file = open(_cleanup_lock_path, "w")
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Store file handle globally to keep lock
        global _cleanup_lock_file
        _cleanup_lock_file = lock_file
        return True
    except OSError:
        return False


def _release_cleanup_lock() -> None:
    """Release the file lock for cleanup task."""
    import fcntl

    global _cleanup_lock_file
    if "_cleanup_lock_file" in globals() and _cleanup_lock_file:
        try:
            fcntl.flock(_cleanup_lock_file.fileno(), fcntl.LOCK_UN)
            _cleanup_lock_file.close()
        except Exception:
            pass


def _cleanup_tick() -> None:
    """Single cleanup tick executed by timer."""
    global _shutdown_requested

    if _shutdown_requested:
        return

    try:
        # Try to acquire lock (single-process guarantee)
        if _acquire_cleanup_lock():
            try:
                manager = SSOManager()
                manager.cleanup_expired_auth_states()
            finally:
                _release_cleanup_lock()
    except Exception as e:
        logger.error(f"SSO auth state cleanup error: {e}")

    # Reschedule if not shutting down
    if not _shutdown_requested:
        global _cleanup_timer
        _cleanup_timer = threading.Timer(AUTH_STATE_CLEANUP_INTERVAL_SECONDS, _cleanup_tick)
        _cleanup_timer.daemon = True
        _cleanup_timer.start()


def init_sso_cleanup() -> None:
    """Initialize the SSO auth state cleanup task.

    Should be called during application startup via start_background_services().
    Uses file lock to ensure single-process execution in multi-worker deployments.
    """
    global _cleanup_timer, _shutdown_requested

    if _cleanup_timer is not None:
        logger.debug("SSO auth state cleanup already initialized")
        return

    _shutdown_requested = False
    _cleanup_timer = threading.Timer(AUTH_STATE_CLEANUP_INTERVAL_SECONDS, _cleanup_tick)
    _cleanup_timer.daemon = True
    _cleanup_timer.start()
    logger.info("SSO auth state cleanup task started")


def shutdown_sso_cleanup() -> None:
    """Gracefully shutdown the SSO auth state cleanup task.

    Called during application shutdown to stop the timer cleanly.
    """
    global _cleanup_timer, _shutdown_requested

    _shutdown_requested = True

    if _cleanup_timer:
        _cleanup_timer.cancel()
        _cleanup_timer = None

    logger.info("SSO auth state cleanup task stopped")
