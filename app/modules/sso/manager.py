"""
Open ACE - SSO Manager

Manages SSO providers and authentication sessions.
"""

import json
import logging
import secrets
import threading
from datetime import datetime, timedelta
from typing import Any, Optional

from app.modules.sso.oauth2 import OAuth2Provider
from app.modules.sso.oidc import (
    OIDCProvider,
    get_provider_class,
)
from app.modules.sso.provider import (
    SSOAuthResult,
    SSOProvider,
    SSOProviderConfig,
    get_provider_config,
)
from app.repositories.database import Database, is_postgresql

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
            cursor.execute(f"""
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
            """)

            # SSO identities table (links SSO users to local users)
            cursor.execute(f"""
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
            """)

            # SSO sessions table
            cursor.execute(f"""
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
            """)

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
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
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
                        datetime.utcnow(),
                    ),
                )
                conn.commit()

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
            "SELECT * FROM sso_providers WHERE name = ? AND is_active IS TRUE", (name,)
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

            return provider

        except Exception as e:
            logger.error(f"Failed to load SSO provider {name}: {e}")
            return None

    def list_providers(self, tenant_id: Optional[int] = None) -> list[dict[str, Any]]:
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
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE sso_providers SET is_active = FALSE WHERE name = ?", (name,))
                conn.commit()

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
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE sso_providers SET is_active = TRUE WHERE name = ?", (name,))
                conn.commit()

            return True

        except Exception as e:
            logger.error(f"Failed to enable provider: {e}")
            return False

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
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
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
                        json.dumps(provider_data) if provider_data else None,
                        datetime.utcnow(),
                        user_id,
                        json.dumps(provider_data) if provider_data else None,
                        datetime.utcnow(),
                    ),
                )
                conn.commit()

            logger.info(
                f"Linked SSO identity: {provider_name}:{provider_user_id} -> user:{user_id}"
            )
            return True

        except Exception as e:
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
        expires_at = datetime.utcnow() + timedelta(seconds=expires_in)

        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
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
                conn.commit()

            return session_token

        except Exception as e:
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
            (session_token, datetime.utcnow()),
        )

        if not row:
            return None

        return dict(row)

    def delete_sso_session(self, session_token: str) -> bool:
        """Delete an SSO session."""
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM sso_sessions WHERE session_token = ?", (session_token,))
                conn.commit()
                return True

        except Exception as e:
            logger.error(f"Failed to delete SSO session: {e}")
            return False

    def cleanup_expired_sessions(self) -> int:
        """Delete expired SSO sessions."""
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM sso_sessions WHERE expires_at < ?", (datetime.utcnow(),)
                )
                deleted = cursor.rowcount
                conn.commit()
                return deleted

        except Exception as e:
            logger.error(f"Failed to cleanup sessions: {e}")
            return 0

    def _store_auth_state(
        self, state: str, code_verifier: str, provider_name: str, nonce: Optional[str] = None
    ) -> None:
        """Store authentication state for verification."""
        # In production, use Redis with TTL
        # For now, we'll use a simple in-memory cache or database
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS sso_auth_states (
                        state TEXT PRIMARY KEY,
                        code_verifier TEXT NOT NULL,
                        provider_name TEXT NOT NULL,
                        nonce TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cursor.execute(
                    """
                    INSERT INTO sso_auth_states (state, code_verifier, provider_name, nonce)
                    VALUES (?, ?, ?, ?)
                """,
                    (state, code_verifier, provider_name, nonce),
                )
                conn.commit()

        except Exception as e:
            logger.error(f"Failed to store auth state: {e}")

    def _get_auth_state(self, state: str) -> Optional[dict[str, Any]]:
        """Get authentication state."""
        try:
            row = self.db.fetch_one("SELECT * FROM sso_auth_states WHERE state = ?", (state,))
            return dict(row) if row else None

        except Exception as e:
            logger.error(f"Failed to get auth state: {e}")
            return None

    def _delete_auth_state(self, state: str) -> None:
        """Delete authentication state."""
        try:
            with self.db.connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM sso_auth_states WHERE state = ?", (state,))
                conn.commit()

        except Exception as e:
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
